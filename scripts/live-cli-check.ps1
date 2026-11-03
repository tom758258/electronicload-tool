<#
Single, bounded IT8812C CLI hardware validation: read-only, fixed CC and explicit Input ON/OFF.
Requires approved safe hardware setup, explicit write/ON gates and one interactive Enter confirmation.
#>
[CmdletBinding()]
param(
    [string]$Target,
    [ValidateSet('usb')][string]$Connection = 'usb',
    [string]$Resource,
    [ValidateSet('system')][string]$Backend = 'system',
    [string]$SafetyConfig,
    [double]$Current = 0.1,
    [switch]$PlanOnly,
    [switch]$ConfirmWrites,
    [switch]$EnableInputOn,
    [switch]$DebugArtifacts
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv/Scripts/python.exe'
$cli = Join-Path $repo '.venv/Scripts/electronicload-tool.exe'
$perCommandDeadlineMs = 30000
$totalDeadlineMs = 240000
$currentText = $Current.ToString([Globalization.CultureInfo]::InvariantCulture)
$reportDirectory = $null
$watch = $null
$phase = 'plan'
$inputOnAttempted = $false
$commandIndex = 0
$report = [ordered]@{
    status = 'NOT RUN'
    hardware_touched = $false
    target = $Target
    connection = $Connection
    backend = $Backend
    resource = '<redacted>'
    runtime_confirmation = 'NOT VERIFIED'
    prechecks = [ordered]@{
        plan = 'NOT RUN'; preflight = 'NOT RUN'; identify = 'NOT RUN'
        runtime = 'NOT RUN'; runtime_manufacturer = $null
        safety = 'NOT RUN'; input_off = 'NOT RUN'
    }
    artifact_mode = $(if ($DebugArtifacts) { 'debug' } else { 'minimal' })
    validation_mode = $(if ($PlanOnly) { 'plan-only' } else { 'live' })
    product_live = 'read-only open; control pending LIVE acceptance'
    per_command_deadline_ms = $perCommandDeadlineMs
    total_deadline_ms = $totalDeadlineMs
    safety_max_current_a = $null
    plans = @()
    results = @(
        [ordered]@{ name = 'identify'; command = 'identify'; status = 'NOT RUN' },
        [ordered]@{ name = 'input-state'; command = 'input-state'; status = 'NOT RUN' },
        [ordered]@{ name = 'measure-off'; command = 'measure'; status = 'NOT RUN' },
        [ordered]@{ name = 'set-cc-high'; command = 'set-cc'; status = 'NOT RUN' },
        [ordered]@{ name = 'set-cc-low'; command = 'set-cc'; status = 'NOT RUN' },
        [ordered]@{ name = 'input-on'; command = 'input-on'; status = 'NOT RUN' },
        [ordered]@{ name = 'measure-on'; command = 'measure'; status = 'NOT RUN' },
        [ordered]@{ name = 'input-off'; command = 'input-off'; status = 'NOT RUN' },
        [ordered]@{ name = 'final-input-state'; command = 'input-state'; status = 'NOT RUN' }
    )
}

function Save-Report {
    if ($null -eq $script:reportDirectory) { return }
    $shareable = Join-Path $script:reportDirectory 'shareable'
    [void][IO.Directory]::CreateDirectory($shareable)
    $cases = @()
    foreach ($entry in $script:report.results) {
        if ($entry.name -eq 'identify') { continue }
        $case = [ordered]@{
            name = $entry.name
            command = $entry.command
            status = $entry.status
        }
        if ($entry.Contains('error_code')) { $case.error_code = $entry.error_code }
        if ($entry.status -eq 'PASS') {
            if ($entry.command -eq 'input-state' -or $entry.command -eq 'input-on' -or
                $entry.command -eq 'input-off') {
                $case.input_state = $entry.response.result.input_state
            }
            if ($entry.command -eq 'set-cc') {
                $case.range = $entry.response.result.range
                $case.current_a = $entry.response.result.current_a
            }
            if ($entry.command -eq 'measure') {
                $case.values = [ordered]@{
                    voltage_v = $entry.response.result.voltage_v
                    current_a = $entry.response.result.current_a
                    power_w = $entry.response.result.power_w
                }
            }
        }
        $cases += $case
    }
    $counts = [ordered]@{ total = $cases.Count; passed = 0; failed = 0; skipped = 0; not_run = 0 }
    foreach ($case in $cases) {
        switch ($case.status) {
            'PASS' { $counts.passed++ }
            'FAIL' { $counts.failed++ }
            'SKIP' { $counts.skipped++ }
            'NOT RUN' { $counts.not_run++ }
        }
    }
    $script:report['summary_counts'] = $counts
    $public = [ordered]@{
        target = $script:report.target
        connection = $script:report.connection
        backend = $script:report.backend
        resource = '<redacted>'
        validation_mode = $script:report.validation_mode
        artifact_mode = $script:report.artifact_mode
        hardware_touched = $script:report.hardware_touched
        overall_status = $script:report.status
        per_command_deadline_ms = $script:perCommandDeadlineMs
        total_deadline_ms = $script:totalDeadlineMs
        product_live = $script:report.product_live
        safety_max_current_a = $script:report.safety_max_current_a
        prechecks = $script:report.prechecks
        cases = $cases
        summary_counts = $counts
    }
    [IO.File]::WriteAllText((Join-Path $shareable 'report.json'),
        ($public | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    $lines = @(
        '# Electronic Load CLI Validation'
        ''
        "Overall: $($public.overall_status)"
        "Preflight: $($public.prechecks.preflight)"
        "Identify: $($public.prechecks.identify)"
        "VISA runtime: $($public.prechecks.runtime)"
        "Input OFF precheck: $($public.prechecks.input_off)"
    )
    foreach ($case in $cases) { $lines += "$($case.name): $($case.status)" }
    $lines += "Cases: $($counts.passed) PASS, $($counts.failed) FAIL, $($counts.skipped) SKIP, $($counts.not_run) NOT RUN"
    $lines += 'Resource: <redacted>'
    [IO.File]::WriteAllText((Join-Path $shareable 'summary.md'),
        ($lines -join [Environment]::NewLine) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false))
}

function Show-Summary {
    $counts = $script:report.summary_counts
    Write-Host "Summary: $($counts.total) total, $($counts.passed) PASS, $($counts.failed) FAIL, $($counts.skipped) SKIP, $($counts.not_run) NOT RUN"
    Write-Host "Overall: $($script:report.status)"
}

function Invoke-LiveCommand([string]$Command, [string]$CaseName,
                            [string[]]$ExtraArgs = @(), [switch]$Control,
                            [switch]$Cleanup) {
    $remaining = if ($Cleanup) { $script:perCommandDeadlineMs } else {
        [Math]::Min($script:perCommandDeadlineMs,
            $script:totalDeadlineMs - $script:watch.ElapsedMilliseconds)
    }
    if ($remaining -le 0) { throw 'Total CLI validation deadline expired.' }
    $arguments = @('-c', 'from electronicload_tool_cli.cli import main; raise SystemExit(main())',
        $Command, '--resource', $Resource, '--backend', $Backend, '--model', $Target,
        '--json') + $ExtraArgs
    if ($Control) { $arguments += '--validation-allow-pending-live-support' }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $python
    $start.Arguments = ($arguments | ForEach-Object { '"' + ($_ -replace '"','\"') + '"' }) -join ' '
    $start.WorkingDirectory = $repo
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $script:commandIndex++
    $sequence = $script:commandIndex
    $entry = $script:report.results | Where-Object { $_.name -eq $CaseName } | Select-Object -First 1
    if ($null -eq $entry) { throw "Unknown validation case: $CaseName" }
    $started = $false
    $output = ''
    $diagnostic = ''
    $exitStatus = 'NOT STARTED'
    try {
        [void]$process.Start()
        $started = $true
        $script:report.hardware_touched = $true
        $entry.status = 'FAIL'
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit([int]$remaining)) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
            $exitStatus = 'TIMEOUT; session/input state unconfirmed'
            $output = $stdout.GetAwaiter().GetResult()
            $diagnostic = $stderr.GetAwaiter().GetResult()
            $entry['error_code'] = 'deadline_expired'
            throw "$CaseName exceeded its deadline; physical Input state is unconfirmed."
        }
        $exitStatus = [string]$process.ExitCode
        $output = $stdout.GetAwaiter().GetResult()
        $diagnostic = $stderr.GetAwaiter().GetResult()
        try {
            $result = $output | ConvertFrom-Json -ErrorAction Stop
        } catch {
            $entry['error_code'] = 'invalid_cli_json'
            throw "$CaseName did not return valid CLI JSON."
        }
        if ($null -eq $result -or $null -eq $result.ok) {
            $entry['error_code'] = 'invalid_cli_json'
            throw "$CaseName returned an incomplete CLI result."
        }
        $entry['exit_code'] = $process.ExitCode
        if ($process.ExitCode -ne 0 -or $result.ok -ne $true) {
            $code = [string]$result.error.code
            $entry['error_code'] = $(if ($code -cmatch '^[a-z_]+$') { $code } else { 'execution_failed' })
            throw "$CaseName failed (exit $exitStatus, code $($entry.error_code))."
        }
        $entry.status = 'PASS'
        $entry['response'] = $result
        if ($null -ne $result.result.visa) {
            # Compare metadata before shortening library paths in shareable output.
            $entry['runtime_library'] = $result.result.visa.library
            $entry['runtime_manufacturer'] = $result.result.visa.manufacturer
        }
        return $result
    } finally {
        if ($started -and -not $process.HasExited) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
        }
        $process.Dispose()
        if ($DebugArtifacts) {
            $privateDir = Join-Path $script:reportDirectory 'private'
            [void][IO.Directory]::CreateDirectory($privateDir)
            $prefix = '{0:D2}-{1}' -f $sequence, $CaseName
            [IO.File]::WriteAllText((Join-Path $privateDir "$prefix.json"),
                $output, [Text.UTF8Encoding]::new($false))
            [IO.File]::WriteAllText((Join-Path $privateDir "$prefix.stderr.txt"),
                $diagnostic, [Text.UTF8Encoding]::new($false))
            [IO.File]::WriteAllText((Join-Path $privateDir "$prefix.exit.txt"),
                $exitStatus, [Text.UTF8Encoding]::new($false))
        }
        Save-Report
    }
}

function Fail-Case([string]$Name, [string]$Code, [string]$Message) {
    $case = $script:report.results | Where-Object { $_.name -eq $Name } | Select-Object -First 1
    if ($null -ne $case) {
        $case.status = 'FAIL'
        $case['error_code'] = $Code
    }
    throw $Message
}

function Assert-Runtime([object]$Result, [string]$CaseName) {
    if ($null -eq $Result.result.visa -or
        $Result.result.visa.library -cne $script:expectedLibrary -or
        $Result.result.visa.manufacturer -cne $script:expectedManufacturer) {
        Fail-Case $CaseName 'runtime_mismatch' 'VISA runtime evidence changed between commands; validation cannot pass.'
    }
}

function Assert-InputOff([object]$Result, [string]$CaseName) {
    if ($Result.result.input_state -cne 'OFF') {
        Fail-Case $CaseName 'input_not_off' 'Physical Input OFF was not confirmed; further writes are blocked.'
    }
}

try {
    if (-not $Target -or -not $Resource) {
        Write-Host 'BLOCKED: explicit -Target and -Resource are required.'
        exit 2
    }
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $cli)) {
        throw 'Sync the repository .venv with uv before running validation.'
    }
    if ([string]::IsNullOrWhiteSpace($SafetyConfig)) {
        $SafetyConfig = Join-Path $repo 'examples/safety-config.toml'
    }
    if (-not (Test-Path -LiteralPath $SafetyConfig)) {
        throw 'Safety Config file does not exist.'
    }
    $SafetyConfig = (Resolve-Path -LiteralPath $SafetyConfig).Path
    $limitOutput = & $python -c 'from electronicload_tool_core.safety import load_safety_config; from electronicload_tool_core.control import validate_current; import sys; s=load_safety_config(sys.argv[1]); validate_current(sys.argv[2],sys.argv[3],s); validate_current(sys.argv[2],sys.argv[4],s); print(s.max_current)' $SafetyConfig $currentText low high
    if ($LASTEXITCODE -ne 0) { throw 'Safety Config verification failed.' }
    $report.safety_max_current_a = [double]::Parse(
        [string]$limitOutput, [Globalization.CultureInfo]::InvariantCulture)
    $report.prechecks.safety = 'PASS'

    $highArgs = @('--current', $currentText, '--range', 'high', '--safety-config', $SafetyConfig)
    $lowArgs = @('--current', $currentText, '--range', 'low', '--safety-config', $SafetyConfig)
    $onArgs = @('--safety-config', $SafetyConfig, '--confirm')
    $planned = @(
        @{command='identify';args=@()},
        @{command='input-state';args=@()},
        @{command='measure';args=@()},
        @{command='set-cc';args=$highArgs},
        @{command='set-cc';args=$lowArgs},
        @{command='input-on';args=$onArgs},
        @{command='measure';args=@()},
        @{command='input-off';args=@()},
        @{command='input-state';args=@()}
    )
    foreach ($step in $planned) {
        $extras = @($step.args)
        $output = & $cli $step.command --model $Target --resource $Resource --backend $Backend @extras --dry-run --json
        $exitCode = $LASTEXITCODE
        $plan = ($output -join "`n") | ConvertFrom-Json
        if ($exitCode -ne 0 -or $plan.ok -ne $true) {
            Write-Host "BLOCKED: offline plan failed for $($step.command)."
            exit 2
        }
        $report.plans += $plan
    }
    $report.prechecks.plan = 'PASS'
    $name = 'readonly-live-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff') + '-' +
        [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $reportDirectory = Join-Path $repo ".tmp_tests/$name"
    [void][IO.Directory]::CreateDirectory($reportDirectory)
    Write-Host 'Plan: Identify, Input State, Measure, CC High/Low, Input ON, Measure, Input OFF, final Input State.'
    Write-Host "Current=$currentText A; Safety maximum=$($report.safety_max_current_a) A; USB System VISA."
    Write-Host 'No reset, SYST:ERR? consumption, automatic local switch, or implicit Input ON.'
    Write-Host 'An Input ON attempt requires an explicit OFF cleanup; session close does not turn Input OFF.'
    Write-Host 'V/I/P measure queries are separate; power freshness is not guaranteed.'
    if ($PlanOnly) {
        $report.status = 'PLAN ONLY'
        Save-Report
        Show-Summary
        Write-Host "PASS: no hardware accessed. Report: $reportDirectory/shareable/report.json"
        exit 0
    }
    if (-not $ConfirmWrites -or -not $EnableInputOn) {
        $report.status = 'BLOCKED'
        Save-Report
        Show-Summary
        Write-Host 'BLOCKED: complete LIVE CLI validation requires -ConfirmWrites and -EnableInputOn.'
        exit 2
    }
    if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) {
        $report.status = 'BLOCKED'
        Save-Report
        Show-Summary
        Write-Host 'BLOCKED: controlled LIVE validation requires an interactive console.'
        exit 2
    }
    & (Join-Path $PSScriptRoot 'preflight-cli.ps1')
    if ($LASTEXITCODE -ne 0) {
        $report.prechecks.preflight = 'FAIL'
        throw 'Preflight failed.'
    }
    $report.prechecks.preflight = 'PASS'
    Write-Host "Target: $Target; resource: $Resource; backend: $Backend"
    Write-Host 'Confirm safe, bounded source/DUT, current limit and a manual Input OFF contingency.'
    Write-Host 'This one run deliberately tests Input ON and then explicitly commands/verifies OFF.'
    $ack = Read-Host 'With approved hardware setup, press Enter to run all CLI cases (Ctrl+C to cancel)'
    if ($ack -cne '') {
        $report.status = 'BLOCKED'
        Save-Report
        Show-Summary
        Write-Host 'BLOCKED: operator did not confirm.'
        exit 2
    }
    if ($DebugArtifacts) {
        $privateDir = Join-Path $reportDirectory 'private'
        [void][IO.Directory]::CreateDirectory($privateDir)
        [IO.File]::WriteAllText((Join-Path $privateDir 'resource.txt'),
            $Resource, [Text.UTF8Encoding]::new($false))
    }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $phase = 'identify'
    Write-Host 'Running Identify precheck...'
    $identity = Invoke-LiveCommand -Command 'identify' -CaseName 'identify'
    $report.prechecks.identify = 'PASS'
    if ($null -eq $identity.result.visa -or
        [string]::IsNullOrWhiteSpace([string]$identity.result.visa.library)) {
        $report.prechecks.identify = 'FAIL'
        throw 'VISA runtime library metadata is missing.'
    }
    $expectedLibrary = $identity.result.visa.library
    $expectedManufacturer = $identity.result.visa.manufacturer
    $report.prechecks.runtime = 'OBSERVED (not operator-verified)'
    $report.prechecks.runtime_manufacturer = $expectedManufacturer
    $report.runtime_confirmation = 'not operator-verified'
    Write-Host "PASS Identify; VISA runtime manufacturer: $expectedManufacturer"

    $phase = 'cases'
    Write-Host 'Running Input State precheck...'
    $state = Invoke-LiveCommand -Command 'input-state' -CaseName 'input-state'
    Assert-Runtime $state 'input-state'
    if ($state.result.input_state -cne 'OFF') {
        $report.prechecks.input_off = 'BLOCKED'
        $report.status = 'BLOCKED'
        Fail-Case 'input-state' 'input_not_off' 'Input is not OFF; configuration and Input ON are blocked.'
    }
    $report.prechecks.input_off = 'PASS'
    Write-Host 'PASS Input State: OFF'

    Write-Host 'Running Measurement with Input OFF...'
    $measureOff = Invoke-LiveCommand -Command 'measure' -CaseName 'measure-off'
    Assert-Runtime $measureOff 'measure-off'
    Write-Host 'PASS Measurement (Input OFF)'

    Write-Host 'Running CC High-range configuration with Input OFF...'
    $high = Invoke-LiveCommand -Command 'set-cc' -CaseName 'set-cc-high' -ExtraArgs $highArgs -Control
    Assert-Runtime $high 'set-cc-high'
    if ($high.result.range -cne 'high' -or $high.result.input_state -cne 'OFF') {
        Fail-Case 'set-cc-high' 'readback_mismatch' 'High-range CC setting or Input OFF readback did not match.'
    }
    Write-Host 'PASS Set CC: High range'

    Write-Host 'Running CC Low-range configuration with Input OFF...'
    $low = Invoke-LiveCommand -Command 'set-cc' -CaseName 'set-cc-low' -ExtraArgs $lowArgs -Control
    Assert-Runtime $low 'set-cc-low'
    if ($low.result.range -cne 'low' -or $low.result.input_state -cne 'OFF') {
        Fail-Case 'set-cc-low' 'readback_mismatch' 'Low-range CC setting or Input OFF readback did not match.'
    }
    Write-Host 'PASS Set CC: Low range'

    # Treat even an uncertain ON write as an attempted ON. The cleanup path
    # must not assume an exception means the physical Input remains OFF.
    $inputOnAttempted = $true
    Write-Host 'Running explicitly authorized Input ON...'
    $on = Invoke-LiveCommand -Command 'input-on' -CaseName 'input-on' -ExtraArgs $onArgs -Control
    Assert-Runtime $on 'input-on'
    if ($on.result.input_state -cne 'ON') { Fail-Case 'input-on' 'input_on_not_confirmed' 'Input ON was not confirmed.' }
    Write-Host 'PASS Input ON'

    Write-Host 'Running Measurement with Input ON...'
    $measureOn = Invoke-LiveCommand -Command 'measure' -CaseName 'measure-on'
    Assert-Runtime $measureOn 'measure-on'
    Write-Host 'PASS Measurement (Input ON)'

    Write-Host 'Running explicit Input OFF...'
    $off = Invoke-LiveCommand -Command 'input-off' -CaseName 'input-off' -Control
    Assert-Runtime $off 'input-off'
    Assert-InputOff $off 'input-off'
    $inputOnAttempted = $false
    Write-Host 'PASS Input OFF'

    Write-Host 'Running final independent Input State check...'
    $final = Invoke-LiveCommand -Command 'input-state' -CaseName 'final-input-state'
    Assert-Runtime $final 'final-input-state'
    Assert-InputOff $final 'final-input-state'
    Write-Host 'PASS Final Input State: OFF'

    $report.status = 'PASS'
    Save-Report
    Show-Summary
    Write-Host "PASS: one-run CLI LIVE validation. Report: $reportDirectory/shareable/report.json"
    exit 0
} catch {
    if ($report.status -ne 'BLOCKED') {
        $report.status = $(if ($phase -eq 'cases') { 'FAIL' } else { 'BLOCKED' })
    }
    if ($DebugArtifacts -and $null -ne $reportDirectory) {
        $privateDir = Join-Path $reportDirectory 'private'
        [void][IO.Directory]::CreateDirectory($privateDir)
        [IO.File]::WriteAllText((Join-Path $privateDir 'failure.txt'),
            $_.Exception.ToString(), [Text.UTF8Encoding]::new($false))
    }
    Write-Host "Validation did not complete: $($_.Exception.Message)"
} finally {
    if ($inputOnAttempted) {
        $report.results += [ordered]@{
            name = 'cleanup-input-off'; command = 'input-off'; status = 'NOT RUN'
        }
        try {
            Write-Host 'Input ON was attempted: explicitly commanding OFF cleanup...'
            $cleanup = Invoke-LiveCommand -Command 'input-off' -CaseName 'cleanup-input-off' -Control -Cleanup
            Assert-InputOff $cleanup 'cleanup-input-off'
            $inputOnAttempted = $false
            Write-Host 'PASS Explicit Input OFF cleanup'
        } catch {
            $report.status = 'FAIL'
            Write-Host 'WARNING: physical Input OFF was not confirmed. Immediate operator action is required.'
        }
        if ($report.status -eq 'PASS') { $report.status = 'FAIL' }
    }
    Save-Report
    if ($null -ne $reportDirectory) {
        if ($report.status -ne 'PASS' -and $report.status -ne 'PLAN ONLY') { Show-Summary }
        Write-Host "Report: $reportDirectory/shareable/report.json"
        if ($DebugArtifacts) { Write-Host "Private debug logs: $reportDirectory/private (review before sharing)" }
    }
}
if ($report.status -eq 'PASS' -or $report.status -eq 'PLAN ONLY') { exit 0 }
if ($report.status -eq 'BLOCKED') { exit 2 }
exit 3
