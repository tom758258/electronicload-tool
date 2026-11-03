<#
Explicitly opt-in, bounded IT8812C control validation. Does not promote Product LIVE
support. Do not run this script without hardware authorization and safe setup.
#>
[CmdletBinding()]
param(
    [ValidateSet('itech-it8812c')][string]$Target = 'itech-it8812c',
    [ValidateSet('usb')][string]$Connection = 'usb',
    [string]$Resource,
    [ValidateSet('system')][string]$Backend = 'system',
    [string]$SafetyConfig,
    [ValidateSet('low','high')][string]$Range = 'low',
    [double]$Current = 0.1,
    [ValidateSet('cc','input')][string]$Stage = 'cc',
    [switch]$PlanOnly,
    [switch]$ConfirmWrites,
    [switch]$EnableInputOn
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv/Scripts/python.exe'
$cli = Join-Path $repo '.venv/Scripts/electronicload-tool.exe'
$reportDirectory = Join-Path $repo ('.tmp_tests/control-live-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
$report = [ordered]@{
    target = $Target
    resource = '<redacted>'
    stage = $Stage
    plan_only = [bool]$PlanOnly
    hardware_touched = $false
    status = 'NOT RUN'
    cases = @()
}
$inputOnAttempted = $false
$watch = $null

function Save-Report {
    $path = Join-Path $script:reportDirectory 'shareable'
    [void][IO.Directory]::CreateDirectory($path)
    [IO.File]::WriteAllText((Join-Path $path 'report.json'),
        ($script:report | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    $summary = @('# Electronic Load Control Validation', '',
        "Stage: $($script:report.stage)", "Status: $($script:report.status)",
        "Hardware touched: $($script:report.hardware_touched)", 'Resource: <redacted>')
    foreach ($case in $script:report.cases) {
        $summary += "$($case.name): $($case.status)"
    }
    [IO.File]::WriteAllText((Join-Path $path 'summary.md'),
        ($summary -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
}

function Invoke-CLI([string]$Command, [string[]]$Extra, [bool]$Live) {
    $arguments = @('-c','from electronicload_tool_cli.cli import main; raise SystemExit(main())',
        $Command, '--resource', $Resource, '--backend', $Backend, '--model', $Target,
        '--json') + $Extra
    if ($Live) { $arguments += '--validation-allow-pending-live-support' }
    else { $arguments += '--dry-run' }
    $remaining = if ($script:watch) {
        [Math]::Min(30000, 120000 - $script:watch.ElapsedMilliseconds)
    } else { 30000 }
    if ($remaining -le 0) { throw 'Total validation deadline expired.' }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $python
    $start.Arguments = ($arguments | ForEach-Object { '"' + ($_ -replace '"','\"') + '"' }) -join ' '
    $start.WorkingDirectory = $repo
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $proc = [Diagnostics.Process]::new()
    $proc.StartInfo = $start
    try {
        [void]$proc.Start()
        if ($Live) { $script:report.hardware_touched = $true }
        $stdout = $proc.StandardOutput.ReadToEndAsync()
        $stderr = $proc.StandardError.ReadToEndAsync()
        if (-not $proc.WaitForExit([int]$remaining)) {
            $proc.Kill(); [void]$proc.WaitForExit(5000)
            throw "$Command exceeded its deadline; physical input/session state is unconfirmed."
        }
        $payload = $stdout.GetAwaiter().GetResult() | ConvertFrom-Json
        if ($proc.ExitCode -ne 0 -or -not $payload.ok) {
            throw "$Command failed (exit $($proc.ExitCode), code $($payload.error.code))."
        }
        return $payload
    } finally {
        if (-not $proc.HasExited) { $proc.Kill(); [void]$proc.WaitForExit(5000) }
        $proc.Dispose()
    }
}

try {
    if (-not $Resource -or -not $SafetyConfig) {
        Write-Host 'BLOCKED: -Resource and -SafetyConfig are required, including PlanOnly.'
        exit 2
    }
    if (-not (Test-Path -LiteralPath $cli) -or -not (Test-Path -LiteralPath $python)) {
        throw 'Synchronize the repository .venv before validation.'
    }
    if (-not (Test-Path -LiteralPath $SafetyConfig)) {
        throw 'Safety Config file does not exist.'
    }
    & $python -c 'from electronicload_tool_core.safety import load_safety_config; from electronicload_tool_core.control import validate_current; import sys; validate_current(sys.argv[2], sys.argv[3], load_safety_config(sys.argv[1]))' $SafetyConfig ([string]$Current) $Range
    if ($LASTEXITCODE -ne 0) { throw 'Safety Config verification failed.' }
    $base = @('--current', [string]$Current, '--range', $Range,
              '--safety-config', $SafetyConfig)
    $planned = @(
        @{name='input-state'; args=@()},
        @{name='set-cc'; args=$base}
    )
    if ($Stage -eq 'input') {
        $planned += @{name='input-on'; args=@('--safety-config',$SafetyConfig,'--confirm')}
        $planned += @{name='input-off'; args=@()}
    } else {
        $planned += @{name='input-state'; args=@()}
    }
    foreach ($case in $planned) {
        [void](Invoke-CLI $case.name $case.args $false)
        $report.cases += [ordered]@{name=$case.name;status='PLAN ONLY'}
    }
    $report.status = 'PLAN ONLY'
    Save-Report
    Write-Host 'Plan-only validation PASS. No VISA or hardware writes performed.'
    if ($PlanOnly) { exit 0 }
    $report.status = 'NOT RUN'
    if (-not $ConfirmWrites -or ($Stage -eq 'input' -and -not $EnableInputOn)) {
        $report.status = 'BLOCKED'
        Write-Host 'BLOCKED: explicit -ConfirmWrites and input-stage -EnableInputOn are required.'
        exit 2
    }
    if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) {
        $report.status = 'BLOCKED'
        Write-Host 'BLOCKED: controlled LIVE validation requires an interactive console.'
        exit 2
    }
    $word = if ($Stage -eq 'input') {'INPUT ON'} else {'CC ONLY'}
    Write-Host "Stage=$Stage Current=$Current A Range=$Range; read-only identify and physical Input OFF are required."
    $ack = Read-Host "Confirm approved setup and type '$word' to run (Ctrl+C cancels)"
    if ($ack -cne $word) { $report.status = 'BLOCKED'; Write-Host 'BLOCKED: no operator confirmation.'; exit 2 }
    $report.cases = @()
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $state = Invoke-CLI 'input-state' @() $true
    if ($state.result.input_state -ne 'OFF') {
        throw 'Precheck refused: physical Input state is not OFF; no automatic OFF will be sent.'
    }
    $report.cases += [ordered]@{name='precheck-input-off';status='PASS'}
    [void](Invoke-CLI 'set-cc' $base $true)
    $report.cases += [ordered]@{name='set-cc';status='PASS'}
    if ($Stage -eq 'input') {
        # This is an explicitly authorized ON/OFF validation sequence, not Worker Stop cleanup.
        $inputOnAttempted = $true
        [void](Invoke-CLI 'input-on' @('--safety-config',$SafetyConfig,'--confirm') $true)
        $report.cases += [ordered]@{name='input-on';status='PASS'}
        [void](Invoke-CLI 'input-off' @() $true)
        $inputOnAttempted = $false
        $report.cases += [ordered]@{name='input-off';status='PASS'}
    } else {
        $state = Invoke-CLI 'input-state' @() $true
        if ($state.result.input_state -ne 'OFF') { throw 'Input was not confirmed OFF.' }
        $report.cases += [ordered]@{name='postcheck-input-off';status='PASS'}
    }
    $report.status = 'PASS'
    Write-Host 'PASS: selected controlled validation stage.'
} catch {
    $report.status = 'FAIL'
    $report.cases += [ordered]@{name='error';status='FAIL';message='See operator terminal for diagnostics.'}
    Write-Host "FAIL: $($_.Exception.Message)"
} finally {
    if ($inputOnAttempted) {
        try {
            # The approved test procedure expressly attempts OFF after an ON attempt.
            [void](Invoke-CLI 'input-off' @() $true)
            $report.cases += [ordered]@{name='explicit-off-cleanup';status='PASS'}
        } catch {
            $report.cases += [ordered]@{name='explicit-off-cleanup';status='FAIL'}
            $report.status = 'FAIL'
            Write-Host 'WARNING: physical Input OFF was not confirmed. Operator action is required.'
        }
    }
    Save-Report
    Write-Host "Report: $reportDirectory/shareable/report.json"
}
if ($report.status -eq 'PASS') { exit 0 }
exit 3
