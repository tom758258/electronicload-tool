<#
Explicit, bounded IT8812C control validation. Pending Product LIVE support is not promoted.
LIVE writes require operator authorization, safe setup and an interactive Enter confirmation.
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
    [switch]$EnableInputOn,
    [switch]$DebugArtifacts
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
    safety_max_current_a = $null
    precheck_input_state = 'NOT RUN'
    cases = @()
}
$inputOnAttempted = $false
$watch = $null
$commandIndex = 0
$lastCommandError = $null
$currentText = $Current.ToString([Globalization.CultureInfo]::InvariantCulture)

function Save-Report {
    $path = Join-Path $script:reportDirectory 'shareable'
    [void][IO.Directory]::CreateDirectory($path)
    [IO.File]::WriteAllText((Join-Path $path 'report.json'),
        ($script:report | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    $summary = @('# Electronic Load Control Validation', '',
        "Stage: $($script:report.stage)", "Status: $($script:report.status)",
        "Input precheck: $($script:report.precheck_input_state)",
        "Hardware touched: $($script:report.hardware_touched)", 'Resource: <redacted>')
    foreach ($case in $script:report.cases) {
        $summary += "$($case.name): $($case.status)"
    }
    [IO.File]::WriteAllText((Join-Path $path 'summary.md'),
        ($summary -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
}

function Save-PrivateCommand([int]$Sequence, [string]$Command, [string]$Stdout,
                             [string]$Stderr, [string]$ExitStatus) {
    if (-not $DebugArtifacts) { return }
    $privateDir = Join-Path $script:reportDirectory 'private'
    [void][IO.Directory]::CreateDirectory($privateDir)
    $prefix = ('{0:D2}-{1}' -f $Sequence, $Command)
    [IO.File]::WriteAllText((Join-Path $privateDir "$prefix.stdout.txt"),
        $Stdout, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $privateDir "$prefix.stderr.txt"),
        $Stderr, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $privateDir "$prefix.exit.txt"),
        $ExitStatus, [Text.UTF8Encoding]::new($false))
}

function Invoke-CLI([string]$Command, [string[]]$Extra, [bool]$Live) {
    $arguments = @('-c', 'from electronicload_tool_cli.cli import main; raise SystemExit(main())',
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
    $script:commandIndex++
    $sequence = $script:commandIndex
    $script:lastCommandError = $null
    $rawOutput = ''
    $rawError = ''
    $exitStatus = 'NOT STARTED'
    try {
        [void]$proc.Start()
        if ($Live) { $script:report.hardware_touched = $true }
        $stdout = $proc.StandardOutput.ReadToEndAsync()
        $stderr = $proc.StandardError.ReadToEndAsync()
        if (-not $proc.WaitForExit([int]$remaining)) {
            $proc.Kill(); [void]$proc.WaitForExit(5000)
            $exitStatus = 'TIMEOUT; session/input state unconfirmed'
            $rawOutput = $stdout.GetAwaiter().GetResult()
            $rawError = $stderr.GetAwaiter().GetResult()
            throw "$Command exceeded its deadline; physical input/session state is unconfirmed."
        }
        $exitStatus = [string]$proc.ExitCode
        $rawOutput = $stdout.GetAwaiter().GetResult()
        $rawError = $stderr.GetAwaiter().GetResult()
        try {
            $payload = $rawOutput | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "$Command did not return valid CLI JSON (exit $exitStatus)."
        }
        if ($null -eq $payload -or $null -eq $payload.ok) {
            throw "$Command returned a missing/invalid CLI result (exit $exitStatus)."
        }
        if ($proc.ExitCode -ne 0 -or $payload.ok -ne $true) {
            $safeCode = [string]$payload.error.code
            if ($safeCode -cnotmatch '^[a-z_]+$') { $safeCode = 'execution_failed' }
            $script:lastCommandError = $safeCode
            throw "$Command failed (exit $exitStatus, code $safeCode)."
        }
        return $payload
    } finally {
        if ($proc.HasExited -eq $false) {
            $proc.Kill()
            [void]$proc.WaitForExit(5000)
        }
        $proc.Dispose()
        Save-PrivateCommand $sequence $Command $rawOutput $rawError $exitStatus
    }
}

try {
    if (-not $Resource) {
        $report.status = 'BLOCKED'
        Write-Host 'BLOCKED: an explicit -Resource is required, including PlanOnly.'
        exit 2
    }
    if (-not (Test-Path -LiteralPath $cli) -or -not (Test-Path -LiteralPath $python)) {
        throw 'Synchronize the repository .venv before validation.'
    }
    if ([string]::IsNullOrWhiteSpace($SafetyConfig)) {
        $SafetyConfig = Join-Path $repo 'examples/safety-config.toml'
    }
    if (-not (Test-Path -LiteralPath $SafetyConfig)) {
        throw 'Safety Config file does not exist.'
    }
    $SafetyConfig = (Resolve-Path -LiteralPath $SafetyConfig).Path
    $limitOutput = & $python -c 'from electronicload_tool_core.safety import load_safety_config; from electronicload_tool_core.control import validate_current; import sys; s=load_safety_config(sys.argv[1]); validate_current(sys.argv[2],sys.argv[3],s); print(s.max_current)' $SafetyConfig $currentText $Range
    if ($LASTEXITCODE -ne 0) { throw 'Safety Config verification failed.' }
    $report.safety_max_current_a = [double]::Parse(
        [string]$limitOutput, [Globalization.CultureInfo]::InvariantCulture)
    Write-Host "Safety Config: $SafetyConfig (max_current=$($report.safety_max_current_a) A)"
    $base = @('--current', $currentText, '--range', $Range, '--safety-config', $SafetyConfig)
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
    if ($PlanOnly) {
        $report.status = 'PLAN ONLY'
        Write-Host 'Plan-only validation PASS. No VISA or hardware writes performed.'
        exit 0
    }
    $report.cases = @()
    Write-Host 'Plan precheck PASS. No VISA or hardware writes performed so far.'
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
    # Observe the physical state before asking for the final operator confirmation.
    $state = Invoke-CLI 'input-state' @() $true
    $observed = [string]$state.result.input_state
    if ($observed -cnotin @('OFF', 'ON')) { $observed = 'UNKNOWN' }
    $report.precheck_input_state = $observed
    Write-Host "Physical Input precheck: $observed"
    $report.cases += [ordered]@{name='precheck-input-off';status=$(if ($observed -ceq 'OFF') {'PASS'} else {'BLOCKED'});input_state=$observed}
    Save-Report
    if ($observed -cne 'OFF') {
        $report.status = 'BLOCKED'
        Write-Host "BLOCKED: physical Input is $observed, not OFF; no automatic OFF was sent."
        exit 2
    }
    Write-Host "Stage=$Stage Current=$currentText A Range=$Range; max_current=$($report.safety_max_current_a) A"
    if ($Stage -eq 'input') {
        Write-Host 'This stage will deliberately turn the load ON, then command/verify OFF. Confirm bounded safe source/DUT and manual OFF contingency.'
    } else {
        Write-Host 'This stage writes CC settings while Input remains OFF.'
    }
    $ack = Read-Host 'Press Enter to start the approved validation, or Ctrl+C to cancel'
    if ($ack -cne '') {
        $report.status = 'BLOCKED'
        Write-Host 'BLOCKED: operator did not confirm with Enter.'
        exit 2
    }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    [void](Invoke-CLI 'set-cc' $base $true)
    $report.cases += [ordered]@{name='set-cc';status='PASS'}
    if ($Stage -eq 'input') {
        # Explicitly authorized ON/OFF test, not Worker Stop cleanup.
        $inputOnAttempted = $true
        [void](Invoke-CLI 'input-on' @('--safety-config',$SafetyConfig,'--confirm') $true)
        $report.cases += [ordered]@{name='input-on';status='PASS'}
        [void](Invoke-CLI 'input-off' @() $true)
        $inputOnAttempted = $false
        $report.cases += [ordered]@{name='input-off';status='PASS'}
    } else {
        $state = Invoke-CLI 'input-state' @() $true
        $observed = [string]$state.result.input_state
        if ($observed -cnotin @('OFF', 'ON')) { $observed = 'UNKNOWN' }
        $report.cases += [ordered]@{name='postcheck-input-off';status=$(if ($observed -ceq 'OFF') {'PASS'} else {'FAIL'});input_state=$observed}
        if ($observed -cne 'OFF') { throw "Input OFF postcheck failed: observed $observed." }
    }
    $report.status = 'PASS'
    Write-Host 'PASS: selected controlled validation stage.'
} catch {
    $report.status = 'FAIL'
    $safeError = if ($script:lastCommandError) { $script:lastCommandError } else { 'validation_failed' }
    $report.cases += [ordered]@{name='error';status='FAIL';error_code=$safeError}
    Write-Host "FAIL: $($_.Exception.Message)"
} finally {
    if ($inputOnAttempted) {
        try {
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
    if ($DebugArtifacts) {
        Write-Host "Private debug logs: $reportDirectory/private (inspect and redact before sharing)"
    }
}
if ($report.status -eq 'PASS' -or $report.status -eq 'PLAN ONLY') { exit 0 }
if ($report.status -eq 'BLOCKED') { exit 2 }
exit 3
