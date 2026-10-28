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
    exit 0
} catch {
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 3
}
