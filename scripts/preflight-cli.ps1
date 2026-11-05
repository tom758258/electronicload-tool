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
        $manifest.worker_protocol.schema_versions[0] -ne 3) {
        throw 'Worker manifest check failed.'
    }
    Write-Host 'PASS: offline control commands and Worker manifest'
    exit 0
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 3
}
