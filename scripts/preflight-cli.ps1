[CmdletBinding()]
param([switch]$TransientOnly)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$cli = Join-Path $repo '.venv/Scripts/electronicload-tool.exe'
if (-not (Test-Path -LiteralPath $cli)) {
    $cli = Join-Path $repo '.venv/bin/electronicload-tool'
}

try {
    if (-not (Test-Path -LiteralPath $cli)) {
        throw 'Sync the repository .venv with uv before running preflight.'
    }
    if (-not $TransientOnly) {
    foreach ($command in @('identify', 'input-state', 'measure')) {
        foreach ($mode in @('simulate', 'dry-run')) {
            $output = & $cli $command "--$mode" --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            if ($code -ne 0 -or $result.ok -ne $true -or $result.mode -ne $mode) {
                throw "$command $mode failed."
            }
            Write-Host "PASS: $command $mode JSON"
        }
    }
    $output = & $cli measure --json
    $code = $LASTEXITCODE
    $result = ($output -join "`n") | ConvertFrom-Json
    if ($code -ne 2 -or $result.ok -ne $false -or $result.error.code -ne 'missing_resource') {
        throw 'Missing-resource refusal failed.'
    }
    Write-Host 'PASS: LIVE requires an explicit resource'
    $output = & $cli identify --dry-run --backend '@py' --json
    $code = $LASTEXITCODE
    $result = ($output -join "`n") | ConvertFrom-Json
    if ($code -ne 2 -or $result.ok -ne $false -or $result.error.code -ne 'unsupported_backend') {
        throw 'Backend refusal failed.'
    }
    Write-Host 'PASS: backend restriction'
    foreach ($mode in @('simulate','dry-run')) {
        $output = & $cli set-cc "--$mode" --current 1.5 --range low --json
        $result = ($output -join "`n") | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $result.ok -ne $true) { throw "set-cc $mode failed." }
        Write-Host "PASS: set-cc $mode"
        $output = & $cli set-cv "--$mode" --voltage 5 --range low --json
        $result = ($output -join "`n") | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $result.ok -ne $true) { throw "set-cv $mode failed." }
        Write-Host "PASS: set-cv $mode"
        $output = & $cli input-off "--$mode" --json
        $result = ($output -join "`n") | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $result.ok -ne $true) { throw "input-off $mode failed." }
        Write-Host "PASS: input-off $mode"
    }
    foreach ($range in @('low', 'high')) {
        $resistance = if ($range -eq 'low') { '1.23456789' } else { '123.456789' }
        foreach ($mode in @('simulate', 'dry-run')) {
            $output = & $cli set-cr "--$mode" --resistance $resistance --range $range --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            $expected = [double]::Parse($resistance, [Globalization.CultureInfo]::InvariantCulture)
            if ($code -ne 0 -or $result.ok -ne $true -or $result.mode -ne $mode -or
                $result.result.range -ne $range -or $result.result.resistance_ohm -ne $expected) {
                throw "set-cr $range $mode readback failed."
            }
            if ($mode -eq 'simulate' -and $result.result.input_state -ne 'OFF') {
                throw 'CR configuration must leave simulated Input OFF.'
            }
            if ($mode -eq 'dry-run' -and $result.result.hardware_access -ne $false) {
                throw 'CR dry-run must not access hardware.'
            }
            Write-Host "PASS: set-cr $range $mode"
        }
    }
    $output = & $cli set-cr --dry-run --resistance 10.0001 --range low --json
    $code = $LASTEXITCODE
    $result = ($output -join "`n") | ConvertFrom-Json
    if ($code -ne 2 -or $result.error.code -ne 'resistance_out_of_range') {
        throw 'CR range refusal failed.'
    }
    $output = & $cli set-cr --dry-run --resistance 5 --json
    $code = $LASTEXITCODE
    $result = ($output -join "`n") | ConvertFrom-Json
    if ($code -ne 2 -or $result.error.code -ne 'argument_error') {
        throw 'CR explicit range requirement failed.'
    }
    # Fictional resource only: Core must reject CR before any VISA session opens.
    foreach ($override in @($false, $true)) {
        $extraArgs = @()
        if ($override) { $extraArgs += '--validation-allow-pending-live-support' }
        $output = & $cli set-cr --resource 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR' `
            --resistance 5 --range low @extraArgs --json
        $code = $LASTEXITCODE
        $result = ($output -join "`n") | ConvertFrom-Json
        if ($code -ne 2 -or $result.error.code -ne 'unsupported_live_scope') {
            throw 'CR LIVE refusal failed.'
        }
    }
    Write-Host 'PASS: CR argument and LIVE refusal'
    foreach ($power in @('0', '12.34', '250')) {
        foreach ($mode in @('simulate', 'dry-run')) {
            $output = & $cli set-cw "--$mode" --power $power --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            $expected = [double]::Parse($power, [Globalization.CultureInfo]::InvariantCulture)
            if ($code -ne 0 -or $result.ok -ne $true -or $result.command -ne 'set-cw' -or
                $result.mode -ne $mode -or $result.result.power_w -ne $expected -or
                $result.result.PSObject.Properties.Name -contains 'range') {
                throw "set-cw $power W $mode readback failed."
            }
            if ($mode -eq 'simulate' -and ($result.result.input_state -ne 'OFF' -or
                $result.result.function_mode -ne 'FIX' -or $result.result.function -ne 'POW')) {
                throw 'CW configuration must confirm Fixed CW and leave simulated Input OFF.'
            }
            if ($mode -eq 'dry-run' -and $result.result.hardware_access -ne $false) {
                throw 'CW dry-run must not access hardware.'
            }
            Write-Host "PASS: set-cw $power W $mode"
        }
    }
    $cwRefusals = @(
        @{ Arguments = @(); Code = 'argument_error'; Name = 'required power' }
        @{ Arguments = @('--power', '5', '--range', 'low'); Code = 'argument_error'; Name = 'no range' }
        @{ Arguments = @('--power', '-0.01'); Code = 'power_out_of_range'; Name = 'negative power' }
        @{ Arguments = @('--power', '250.01'); Code = 'power_out_of_range'; Name = 'maximum power' }
        @{ Arguments = @('--power', '250.000000000000000001'); Code = 'power_out_of_range'; Name = 'exact upper bound' }
        @{ Arguments = @('--power', '12.345'); Code = 'invalid_power_resolution'; Name = 'power step' }
        @{ Arguments = @('--power', '12.340000000000000001'); Code = 'invalid_power_resolution'; Name = 'no rounding' }
        @{ Arguments = @('--power', 'NaN'); Code = 'invalid_power'; Name = 'nonfinite power' }
        @{ Arguments = @('--power', 'Infinity'); Code = 'invalid_power'; Name = 'infinite power' }
        @{ Arguments = @('--power', 'text'); Code = 'invalid_power'; Name = 'nonnumeric power' }
    )
    foreach ($case in $cwRefusals) {
        $cwArgs = $case.Arguments
        $output = & $cli set-cw --dry-run @cwArgs --json
        $code = $LASTEXITCODE
        $result = ($output -join "`n") | ConvertFrom-Json
        if ($code -ne 2 -or $result.ok -ne $false -or $result.command -ne 'set-cw' -or
            $result.error.code -ne $case.Code) {
            throw "CW $($case.Name) refusal failed."
        }
        Write-Host "PASS: CW $($case.Name) refusal"
    }
    # Fictional resource only: CW must fail before VISA opens, even with the override.
    foreach ($override in @($false, $true)) {
        $extraArgs = @()
        if ($override) { $extraArgs += '--validation-allow-pending-live-support' }
        $output = & $cli set-cw --resource 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR' `
            --power 5 @extraArgs --json
        $code = $LASTEXITCODE
        $result = ($output -join "`n") | ConvertFrom-Json
        if ($code -ne 2 -or $result.ok -ne $false -or $result.command -ne 'set-cw' -or
            $result.error.code -ne 'unsupported_live_scope') {
            throw 'CW LIVE refusal failed.'
        }
        Write-Host "PASS: CW LIVE refusal (override: $override)"
    }
    $output = & $cli input-on --simulate --confirm --json
    $result = ($output -join "`n") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $result.result.input_state -ne 'ON') {
        throw 'Simulated explicit Input ON failed.'
    }
    $output = & $cli input-on --simulate --json
    $result = ($output -join "`n") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 2 -or $result.error.code -ne 'confirmation_required') {
        throw 'Input ON confirmation refusal failed.'
    }
    }
    # Transient-only selection keeps local verification scoped to this feature.
    $transientBase = @('--range', 'auto', '--level-a', '1', '--level-b', '2',
        '--rise-slew', '0.1', '--fall-slew', '0.1')
    foreach ($transientMode in @('continuous', 'pulse', 'toggle')) {
        $widths = switch ($transientMode) {
            'continuous' { @('--width-a', '0.00002', '--width-b', '3600') }
            'pulse' { @('--pulse-width', '0.001') }
            'toggle' { @() }
        }
        foreach ($mode in @('simulate', 'dry-run')) {
            $output = & $cli set-transient-cc "--$mode" --transient-mode $transientMode @transientBase @widths --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            if ($code -ne 0 -or $result.ok -ne $true -or $result.result.range -ne 'low') {
                throw "Transient $transientMode $mode setup failed."
            }
            if ($mode -eq 'simulate' -and ($result.result.enabled -ne $false -or
                $result.result.configured -ne $true -or $result.result.mode -ne $transientMode)) {
                throw 'Transient simulated setup state failed.'
            }
            if ($mode -eq 'dry-run' -and $result.result.hardware_access -ne $false) {
                throw 'Transient dry-run must not access hardware.'
            }
            Write-Host "PASS: Transient $transientMode $mode"
        }
    }
    $output = & $cli set-transient-cc --simulate --transient-mode toggle @transientBase `
        --rise-slew '0.3000000000000000001' --json
    $code = $LASTEXITCODE
    $result = ($output -join "`n") | ConvertFrom-Json
    if ($code -ne 0 -or $result.result.range -ne 'high' -or
        $result.result.configuration.rise_slew_a_per_us -cne '0.3000000000000000001') {
        throw 'Transient Auto High fallback or exact slew reporting failed.'
    }
    Write-Host 'PASS: Transient Auto High fallback and exact slew reporting'
    $transientRefusals = @(
        @{ Arguments = @('--level-a', '1.000000000000000001'); Code = 'no_matching_range' }
        @{ Arguments = @('--range', 'low', '--fall-slew', '0.251'); Code = 'slew_out_of_range' }
        @{ Arguments = @('--level-b', '61'); Code = 'no_matching_range' }
        @{ Arguments = @('--rise-slew', 'NaN'); Code = 'invalid_transient_value' }
        @{ Arguments = @('--width-a', '0.001'); Code = 'argument_error' }
        @{ Arguments = @('--transient-mode', 'pulse'); Code = 'argument_error' }
        @{ Arguments = @('--transient-mode', 'pulse', '--pulse-width', '0.0000205'); Code = 'invalid_width_resolution' }
    )
    foreach ($case in $transientRefusals) {
        $caseArgs = $case.Arguments
        $output = & $cli set-transient-cc --dry-run --transient-mode toggle @transientBase @caseArgs --json
        $code = $LASTEXITCODE
        $result = ($output -join "`n") | ConvertFrom-Json
        if ($code -ne 2 -or $result.ok -ne $false -or $result.error.code -ne $case.Code) {
            throw "Transient argument refusal failed: $($case.Code)."
        }
        Write-Host "PASS: Transient argument refusal $($case.Code)"
    }
    $transientCommands = @('set-transient-cc', 'transient-enable', 'transient-trigger',
        'transient-disable', 'transient-status')
    foreach ($command in $transientCommands) {
        $setup = @()
        if ($command -eq 'set-transient-cc') { $setup = @('--transient-mode', 'toggle') + $transientBase }
        foreach ($override in @($false, $true)) {
            $extraArgs = @()
            if ($override) { $extraArgs += '--validation-allow-pending-live-support' }
            $output = & $cli $command @setup --resource 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR' @extraArgs --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            if ($code -ne 2 -or $result.error.code -ne 'unsupported_live_scope') {
                throw "Transient LIVE refusal failed: $command."
            }
            Write-Host "PASS: $command LIVE refusal (override: $override)"
        }
    }
    foreach ($command in @('transient-enable', 'transient-trigger', 'transient-disable', 'transient-status')) {
        $output = & $cli $command --dry-run --json
        $code = $LASTEXITCODE
        $result = ($output -join "`n") | ConvertFrom-Json
        if ($code -ne 0 -or $result.result.hardware_access -ne $false) {
            throw "Transient control dry-run failed: $command."
        }
        Write-Host "PASS: $command dry-run"
    }
    $output = & $cli transient-status --simulate --json
    $code = $LASTEXITCODE
    $result = ($output -join "`n") | ConvertFrom-Json
    if ($code -ne 0 -or $result.result.configured -ne $false -or $result.result.enabled -ne $false) {
        throw 'Standalone Transient simulation must start unconfigured.'
    }
    Write-Host 'PASS: standalone Transient status'
    $manifest = ((& $cli manifest --json) -join "`n") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $manifest.schema_version -ne 2 -or
        $manifest.worker_protocol.schema_versions[0] -ne 3 -or
        $manifest.commands.'set-cr'.kind -ne 'async_job' -or
        $manifest.commands.'set-cw'.kind -ne 'async_job') {
        throw 'Worker manifest check failed.'
    }
    foreach ($command in $transientCommands) {
        if ($manifest.commands.$command.kind -ne 'async_job') {
            throw "Transient Manifest entry missing: $command."
        }
    }
    Write-Host 'PASS: offline control commands and Worker manifest'
    exit 0
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 3
}
