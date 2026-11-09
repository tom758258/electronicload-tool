[CmdletBinding()]
param([switch]$TransientOnly, [switch]$FixedAutoOnly, [switch]$FixedSlewVonShortOnly)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$cli = Join-Path $repo '.venv/Scripts/electronicload-tool.exe'
if (-not (Test-Path -LiteralPath $cli)) {
    $cli = Join-Path $repo '.venv/bin/electronicload-tool'
}

function Invoke-PreflightCli {
    param([string[]]$CommandArgs, [int]$ExpectedExit = 0)

    $output = & $cli @CommandArgs --json
    if ($LASTEXITCODE -ne $ExpectedExit) {
        throw "$($CommandArgs[0]) returned exit $LASTEXITCODE; expected $ExpectedExit."
    }
    return ($output -join "`n") | ConvertFrom-Json
}

try {
    if (($TransientOnly -and $FixedAutoOnly) -or
        ($FixedSlewVonShortOnly -and ($TransientOnly -or $FixedAutoOnly))) {
        throw 'Select only one focused preflight switch.'
    }
    if (-not (Test-Path -LiteralPath $cli)) {
        throw 'Sync the repository .venv with uv before running preflight.'
    }
    if (-not ($TransientOnly -or $FixedAutoOnly)) {
        $rise = '0.100000000000000000001'
        $threshold = '10.000000000000000000001'
        $slewArgs = @('set-cc', '--current', '1', '--range', 'auto',
                      '--rise-slew', $rise, '--fall-slew', '0.2')
        $vonArgs = @('set-von', '--voltage', $threshold, '--latch', 'off')
        foreach ($mode in @('--simulate', '--dry-run')) {
            $cc = Invoke-PreflightCli ($slewArgs + $mode)
            $von = Invoke-PreflightCli ($vonArgs + $mode)
            if ($cc.result.range -cne 'low' -or $cc.result.rise_slew_a_per_us -cne $rise -or
                $cc.result.fall_slew_a_per_us -cne '0.2' -or $von.result.threshold_v -cne $threshold -or
                $von.result.latch -cne 'off') {
                throw "Fixed CC Slew or VON settings failed in $mode."
            }
            if ($mode -eq '--simulate' -and $cc.result.input_state -cne 'OFF') {
                throw 'Fixed CC Slew setup must leave Input OFF.'
            }
            if ($mode -eq '--dry-run' -and ($cc.result.hardware_access -ne $false -or
                $von.result.hardware_access -ne $false)) {
                throw 'Fixed CC Slew and VON dry-run must report no hardware access.'
            }
        }

        $partialSlew = Invoke-PreflightCli @('set-cc', '--dry-run', '--current', '1',
                                           '--range', 'auto', '--rise-slew', $rise) 2
        $invalidSlew = Invoke-PreflightCli @('set-cc', '--dry-run', '--current', '1',
                                           '--range', 'auto', '--rise-slew', '0.26', '--fall-slew', '0.2') 2
        $partialVon = Invoke-PreflightCli @('set-von', '--dry-run', '--voltage', $threshold) 2
        $shortOn = Invoke-PreflightCli @('short-on', '--simulate') 2
        if ($partialSlew.error.code -cne 'argument_error' -or
            $invalidSlew.error.code -cne 'slew_out_of_range' -or
            $partialVon.error.code -cne 'argument_error' -or $shortOn.error.code -cne 'input_not_on') {
            throw 'Fixed CC Slew, VON or Short argument/state refusal failed.'
        }

        $von = Invoke-PreflightCli @('von-status', '--simulate')
        $short = Invoke-PreflightCli @('short-status', '--simulate')
        $shortOff = Invoke-PreflightCli @('short-off', '--simulate')
        $shortPlan = Invoke-PreflightCli @('short-on', '--dry-run')
        if ($von.result.threshold_v -cne '0' -or $von.result.latch -cne 'on' -or
            $short.result.short_state -cne 'OFF' -or $shortOff.result.short_state -cne 'OFF' -or
            $shortPlan.result.hardware_access -ne $false) {
            throw 'Standalone VON or Short check failed.'
        }

        $manifest = Invoke-PreflightCli @('manifest')
        if ($manifest.schema_version -ne 2 -or $manifest.worker_protocol.schema_versions[0] -ne 3) {
            throw 'Fixed CC Slew, VON and Short must retain Manifest 2 and Worker protocol 3.'
        }
        $commands = @('set-von', 'von-status', 'short-on', 'short-off', 'short-status')
        foreach ($command in $commands) {
            if ($manifest.commands.$command.kind -cne 'async_job') {
                throw "Manifest entry missing: $command."
            }
        }

        # The fictional resource exercises refusal only.
        foreach ($command in (@('set-cc') + $commands)) {
            $setting = switch ($command) {
                'set-cc' { $slewArgs }
                'set-von' { $vonArgs }
                default { $command }
            }
            $refusal = Invoke-PreflightCli (@($setting) + @('--validation-allow-pending-live-support',
                '--resource', 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR')) 2
            if ($refusal.error.code -cne 'unsupported_live_scope') {
                throw "$command LIVE refusal failed."
            }
        }
        Write-Host 'PASS: Fixed CC Slew, VON and Short preflight'
        if ($FixedSlewVonShortOnly) { exit 0 }
    }
    if (-not $TransientOnly) {
        $fixedCases = @(
            @{ Command = 'set-cc'; Field = 'current'; Low = '2.5'; High = '10' }
            @{ Command = 'set-cv'; Field = 'voltage'; Low = '12'; High = '24' }
            @{ Command = 'set-cr'; Field = 'resistance'; Low = '10'; High = '100' }
        )
        foreach ($case in $fixedCases) {
            $command = $case.Command
            foreach ($mode in @('simulate', 'dry-run')) {
                foreach ($range in @('low', 'high')) {
                    $value = $case[$range]
                    $setting = @(('--' + $case.Field), $value)
                    $output = & $cli $command "--$mode" @setting --range auto --json
                    $code = $LASTEXITCODE
                    $result = ($output -join "`n") | ConvertFrom-Json
                    if ($code -ne 0 -or $result.ok -ne $true -or $result.result.range -cne $range) {
                        throw "$command Auto $range $mode failed."
                    }
                    if ($mode -eq 'simulate' -and $result.result.input_state -cne 'OFF') {
                        throw "$command Auto must leave Input OFF."
                    }
                    if ($mode -eq 'dry-run' -and $result.result.hardware_access -ne $false) {
                        throw "$command Auto dry-run must not access hardware."
                    }
                    Write-Host "PASS: $command Auto $range $mode"
                }
            }
            $setting = @(('--' + $case.Field), $case.Low)
            $output = & $cli $command --dry-run @setting --range high --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            if ($code -ne 0 -or $result.result.range -cne 'high') {
                throw "$command explicit High must stay High."
            }
            $output = & $cli $command --dry-run @setting --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            if ($code -ne 2 -or $result.error.code -cne 'argument_error') {
                throw "$command Range must remain required."
            }
        }
        $fixedRefusals = @(
            @{ Command = 'set-cc'; Field = 'current'; Value = '6.0001' }
            @{ Command = 'set-cc'; Field = 'current'; Value = '1.0000000000000000001' }
            @{ Command = 'set-cv'; Field = 'voltage'; Value = '18.001' }
            @{ Command = 'set-cv'; Field = 'voltage'; Value = '12.000000000000000001' }
            @{ Command = 'set-cr'; Field = 'resistance'; Value = '0.049999999999999999' }
            @{ Command = 'set-cr'; Field = 'resistance'; Value = '7500.000000000000001' }
        )
        foreach ($case in $fixedRefusals) {
            $setting = @(('--' + $case.Field), $case.Value)
            $output = & $cli $case.Command --dry-run @setting --range auto --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            if ($code -ne 2 -or $result.ok -ne $false -or
                $result.error.code -cne 'no_matching_range' -or
                -not $result.error.details.ranges.low -or -not $result.error.details.ranges.high) {
                throw "$($case.Command) Auto must reject the original value $($case.Value)."
            }
            Write-Host "PASS: $($case.Command) Auto exact-value refusal"
        }
        # Fictional resource only: Auto must not bypass Product LIVE refusal.
        foreach ($case in @($fixedCases | Where-Object { $_.Command -ne 'set-cc' })) {
            foreach ($override in @($false, $true)) {
                $extraArgs = @()
                if ($override) { $extraArgs += '--validation-allow-pending-live-support' }
                $setting = @(('--' + $case.Field), $case.Low)
                $output = & $cli $case.Command @setting --range auto @extraArgs `
                    --resource 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR' --json
                $code = $LASTEXITCODE
                $result = ($output -join "`n") | ConvertFrom-Json
                if ($code -ne 2 -or $result.error.code -cne 'unsupported_live_scope') {
                    throw "$($case.Command) Auto LIVE refusal failed."
                }
            }
        }
        $manifest = ((& $cli manifest --json) -join "`n") | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $manifest.schema_version -ne 2 -or
            $manifest.worker_protocol.schema_versions[0] -ne 3) {
            throw 'Fixed Auto must retain Manifest 2 and Worker protocol 3.'
        }
        foreach ($case in $fixedCases) {
            $entry = $manifest.commands.($case.Command)
            if ($entry.kind -cne 'async_job' -or ($entry.range_options -join ',') -cne 'auto,low,high') {
                throw "$($case.Command) Manifest Range options failed."
            }
        }
        Write-Host 'PASS: Fixed Auto required Range, manual High, LIVE refusal and Manifest'
        if ($FixedAutoOnly) { exit 0 }
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
    $newTransientCases = @(
        @{Command='set-transient-cv';Function='CV';A='1';B='2';HighA='24';HighB='25'},
        @{Command='set-transient-cr';Function='CR';A='1';B='2';HighA='10';HighB='1000'},
        @{Command='set-transient-cw';Function='CW';A='0';B='250'}
    )
    foreach ($case in $newTransientCases) {
        foreach ($waveform in @('continuous', 'pulse', 'toggle')) {
            $setup = @('--transient-mode', $waveform, '--level-a', $case.A, '--level-b', $case.B)
            $expectedRange = 'low'
            if ($case.Function -eq 'CW') { $expectedRange = $null }
            else {
                $selection = $(if ($waveform -eq 'pulse') { 'low' } elseif ($waveform -eq 'toggle') { 'high' } else { 'auto' })
                $setup += @('--range', $selection)
                if ($selection -eq 'high') {
                    $setup += @('--level-a', $case.HighA, '--level-b', $case.HighB)
                    $expectedRange = 'high'
                }
            }
            if ($waveform -eq 'continuous') { $setup += @('--width-a', '0.0000205', '--width-b', '3600') }
            if ($waveform -eq 'pulse') { $setup += @('--pulse-width', '0.001000000000000000001') }
            foreach ($mode in @('simulate', 'dry-run')) {
                $output = & $cli $case.Command "--$mode" @setup --json
                $code = $LASTEXITCODE
                $result = ($output -join "`n") | ConvertFrom-Json
                if ($code -ne 0 -or $result.ok -ne $true -or
                    $result.result.range -cne $expectedRange -or $result.result.function -cne $case.Function) {
                    throw "$($case.Command) $waveform $mode failed."
                }
                if ($mode -eq 'simulate' -and ($result.result.enabled -ne $false -or
                    $result.result.mode -cne $waveform)) { throw 'Invalid Transient setup state.' }
                if ($mode -eq 'dry-run' -and $result.result.hardware_access -ne $false) {
                    throw 'Transient dry-run must not access hardware.'
                }
                Write-Host "PASS: $($case.Command) $waveform $mode"
            }
        }
        $setup = @('--transient-mode', 'toggle', '--level-a', $case.A, '--level-b', $case.B)
        if ($case.Function -ne 'CW') { $setup += @('--range', 'auto') }
        $refusals = @(
            @{Args=@('--rise-slew', '0.1');Code='argument_error'},
            @{Args=@('--level-a', 'NaN');Code='invalid_transient_value'},
            @{Args=@('--width-a', '0.001');Code='argument_error'},
            @{Args=@('--transient-mode', 'pulse', '--pulse-width', '0.000019');Code='width_out_of_range'}
        )
        if ($case.Function -eq 'CV') { $refusals += @{Args=@('--level-a','18.001');Code='no_matching_range'} }
        if ($case.Function -eq 'CR') { $refusals += @{Args=@('--level-a','5','--level-b','100');Code='no_matching_range'} }
        if ($case.Function -eq 'CW') {
            $refusals += @{Args=@('--range','auto');Code='argument_error'}
            $refusals += @{Args=@('--level-a','1.000000000000000001');Code='invalid_power_resolution'}
        }
        foreach ($refusal in $refusals) {
            $extras = $refusal.Args
            $output = & $cli $case.Command --dry-run @setup @extras --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            if ($code -ne 2 -or $result.error.code -cne $refusal.Code) {
                throw "$($case.Command) refusal failed: $($refusal.Code)."
            }
        }
        foreach ($override in @($false, $true)) {
            $extras = @()
            if ($override) { $extras += '--validation-allow-pending-live-support' }
            $output = & $cli $case.Command @setup @extras --resource 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR' --json
            $code = $LASTEXITCODE
            $result = ($output -join "`n") | ConvertFrom-Json
            if ($code -ne 2 -or $result.error.code -cne 'unsupported_live_scope') {
                throw "$($case.Command) LIVE refusal failed."
            }
        }
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
    foreach ($command in ($transientCommands + @('set-transient-cv', 'set-transient-cr', 'set-transient-cw'))) {
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
