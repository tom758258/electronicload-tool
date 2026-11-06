[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$cli = Join-Path $repo '.venv/Scripts/electronicload-tool.exe'

try {
    if (-not (Test-Path -LiteralPath $cli)) {
        throw 'Sync the repository .venv with uv before running preflight.'
    }
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
    $manifest = ((& $cli manifest --json) -join "`n") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $manifest.schema_version -ne 2 -or
        $manifest.worker_protocol.schema_versions[0] -ne 3 -or
        $manifest.commands.'set-cr'.kind -ne 'async_job') {
        throw 'Worker manifest check failed.'
    }
    Write-Host 'PASS: offline control commands and Worker manifest'
    exit 0
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 3
}
