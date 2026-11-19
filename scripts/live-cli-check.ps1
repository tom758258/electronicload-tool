<#
Single, bounded IT8812C CLI hardware validation.
Default: all eight modes, with common read-only safety prechecks.
-Modes selects cc, cv, cr, cw, transient-cc/cv/cr/cw, trace, cr-led, current-slow-rise and/or trigger.
-InputTimerOnly selects a separate bounded Fixed CC Timer trial; excluded from defaults.
Requires approved source/DUT limits and interactive confirmation before hardware access.
-PlanOnly validates every planned CLI request without VISA or hardware access.
#>
[CmdletBinding()]
param(
    [string]$Target,
    [ValidateSet('usb')][string]$Connection = 'usb',
    [string]$Resource,
    [ValidateSet('system')][string]$Backend = 'system',
    [string]$Current = '0.1',
    [string[]]$Modes = @('cc', 'cv', 'cr', 'cw', 'transient-cc', 'transient-cv', 'transient-cr', 'transient-cw'),
    [double]$SourceCurrentLimitA = 0.8,
    [double]$SourceVoltageLimitV = 5,
    [string]$Voltage = '3',
    [string]$ResistanceLow = '10',
    [string]$ResistanceHigh = '1000',
    [string]$Power = '1',
    [string]$LevelA = '0.1',
    [string]$LevelB = '0.2',
    [string]$VoltageLevelA = '3',
    [string]$VoltageLevelB = '4',
    [string]$ResistanceLevelA = '10',
    [string]$ResistanceLevelB = '1000',
    [string]$PowerLevelA = '1',
    [string]$PowerLevelB = '2',
    [string]$RiseSlew = '0.1',
    [string]$FallSlew = '0.1',
    [string]$WidthA = '0.001',
    [string]$WidthB = '0.002',
    [string]$PulseWidth = '0.001',
    [switch]$InputTimerOnly,
    [string]$TimerSeconds = '5',
    [int]$TriggerObservationSeconds,
    [int]$TriggerMaxOnSeconds,
    [string]$TriggerIntervalSeconds,
    [string]$TriggerListWidthA,
    [string]$TriggerListWidthB,
    [int]$TriggerListRepeat,
    [string]$TriggerExternalSetup,
    [switch]$PlanOnly,
    [switch]$DebugArtifacts
)

$ErrorActionPreference = 'Stop'
$featureModes = @('cr-led', 'current-slow-rise')
$supportedModes = @('cc', 'cv', 'cr', 'cw', 'transient-cc', 'transient-cv', 'transient-cr', 'transient-cw',
                    'trace') + $featureModes + @('trigger')
if ($InputTimerOnly) {
    if ($PSBoundParameters.ContainsKey('Modes')) { throw 'InputTimerOnly cannot be combined with Modes.' }
    $supportedModes = @('input-timer')
    $Modes = @('input-timer')
}
$requestedModes = @($Modes | ForEach-Object { $_ -split ',' } |
    ForEach-Object { $_.Trim().ToLowerInvariant() })
# Keep the established trial order and run each selected mode only once.
$selectedModes = @($supportedModes | Where-Object { $_ -in $requestedModes })
$hasValidationModes = @($selectedModes | Where-Object { $_ -ne 'cc' }).Count -gt 0
$traceSelected = 'trace' -in $selectedModes
$triggerSelected = 'trigger' -in $selectedModes
$featureSelected = @($selectedModes | Where-Object { $_ -in $featureModes }).Count -gt 0
$featureOnly = $featureSelected -and @($selectedModes | Where-Object { $_ -notin $featureModes }).Count -eq 0
$traceCommunicationUntrusted = $false
$traceFeedStopUnconfirmed = $false
$traceStartDeadlineMs = 120000
$traceRunName = 'readonly-live-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff') + '-' +
    [Guid]::NewGuid().ToString('N').Substring(0, 8)
$traceCsvPath = Join-Path $PSScriptRoot "../.tmp_tests/$traceRunName/trace-powered.csv"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv/Scripts/python.exe'
$cli = Join-Path $repo '.venv/Scripts/electronicload-tool.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $repo '.venv/bin/python'
    $cli = Join-Path $repo '.venv/bin/electronicload-tool'
}
$perCommandDeadlineMs = 30000
$totalDeadlineMs = $(if ($hasValidationModes) { 900000 } else { 240000 })
$currentText = $Current
$scriptCurrentCeilingA = 2.0
$reportDirectory = $null
$watch = $null
$phase = 'plan'
$inputOnAttempted = $false
$transientEnableAttempted = $false
$activeTransientScope = $null
$timerEnableAttempted = $false
$timerOnStartMs = 0
$slowRiseEnableAttempted = $false
$slowRiseCommunicationTrusted = $false
$slowRiseCleanup = @()
$triggerScope = @('--hardware-validation', 'trigger')
$triggerTrials = @()
$triggerPlans = @()
$triggerTrusted = $true
$triggerOffConfirmed = $true
$triggerPowered = $false
$triggerProcessStillRunning = $false
$triggerArtifacts = @()
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
        current = 'NOT RUN'; input_off = 'NOT RUN'
    }
    artifact_mode = $(if ($DebugArtifacts) { 'debug' } else { 'minimal' })
    validation_mode = $(if ($PlanOnly) { 'plan-only' } else { 'live' })
    product_live = 'IT8812C USB/System VISA read-only and fixed CC/Input LIVE open'
    per_command_deadline_ms = $perCommandDeadlineMs
    total_deadline_ms = $totalDeadlineMs
    script_current_ceiling_a = $null
    includes_validation_modes = $hasValidationModes
    declared_source_limits = $null
    plans = @()
    results = @()
}

if ($traceSelected) {
    $report['trace_start_deadline_ms'] = $traceStartDeadlineMs
    $report['trace_safe_shutdown_unconfirmed'] = $false
}
if ('current-slow-rise' -in $selectedModes) {
    $report['slow_rise_cleanup_unconfirmed'] = $false
}
if ($triggerSelected) {
    $report['trigger_restore_status'] = 'NOT RUN'
    $report['trigger_input_off_unconfirmed'] = $false
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
        if ($entry.Contains('arguments')) {
            $safeArguments = @($entry.arguments)
            if ($script:traceSelected -and $entry.command -eq 'trace-start') {
                $csvIndex = [Array]::IndexOf($safeArguments, '--csv')
                if ($csvIndex -ge 0 -and $csvIndex + 1 -lt $safeArguments.Count) {
                    $safeArguments[$csvIndex + 1] = '<redacted>'
                }
            }
            $case.arguments = $safeArguments
        }
        if ($entry.Contains('error_code')) { $case.error_code = $entry.error_code }
        if ($entry.Contains('expected_error')) { $case.expected_error = $entry.expected_error }
        if ($entry.Contains('expected_range')) { $case.expected_range = $entry.expected_range }
        if ($entry.Contains('condition')) { $case.condition = $entry.condition }
        if ($entry.Contains('origin_function')) { $case.origin_function = $entry.origin_function }
        foreach ($field in @('trace_source','trace_points','trace_interval_s','trace_sample_count',
                             'trace_csv_complete','operator_observed','trigger_source','trigger_interval_s',
                             'trigger_source_restore','trigger_interval_restore','list_selected')) {
            if ($entry.Contains($field)) { $case[$field] = $entry[$field] }
        }
        if ($entry.Contains('confirmed_input_state')) { $case.input_state = $entry.confirmed_input_state }
        if ($entry.status -eq 'PASS') {
            if ($entry.command -in @('input-state','input-on','input-off','input-timer-status',
                                     'trigger-configure','trigger-status')) {
                $case.input_state = $entry.response.result.input_state
            }
            if ($entry.command -in @('set-cc', 'set-cv', 'set-cr', 'set-cr-led', 'cr-led-status')) {
                $case.range = $entry.response.result.range
            }
            if ($entry.command -eq 'set-cc') {
                $case.current_a = $entry.response.result.current_a
                $case.input_state = $entry.response.result.input_state
                $case.function = $entry.response.result.function
            }
            foreach ($field in @('configuration', 'seconds', 'enabled', 'state_source', 'validation_only', 'validation_scope',
                                  'trigger_dispatched_at_utc', 'waveform_completion_known',
                                  'fixed_cr', 'led_enabled', 'vdrop_v', 'vdrop_max_v', 'resistance_ohm')) {
                if ($null -ne $entry.response.result.$field) {
                    $case[$field] = $entry.response.result.$field
                }
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
        includes_validation_modes = $script:report.includes_validation_modes
        declared_source_limits = $script:report.declared_source_limits
        script_current_ceiling_a = $script:report.script_current_ceiling_a
        prechecks = $script:report.prechecks
        cases = $cases
        summary_counts = $counts
    }
    if ($script:traceSelected) {
        $public['trace_start_deadline_ms'] = $script:traceStartDeadlineMs
        $public['trace_safe_shutdown_unconfirmed'] = $script:report.trace_safe_shutdown_unconfirmed
    }
    if ($script:report.Contains('slow_rise_cleanup_unconfirmed')) {
        $public['slow_rise_cleanup_unconfirmed'] = $script:report.slow_rise_cleanup_unconfirmed
    }
    if ($script:triggerSelected) {
        $public['trigger_restore_status'] = $script:report.trigger_restore_status
        $public['trigger_input_off_unconfirmed'] = $script:report.trigger_input_off_unconfirmed
        $public['trigger_observation_seconds'] = $script:TriggerObservationSeconds
        $public['trigger_max_on_seconds'] = $script:TriggerMaxOnSeconds
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
    if ($public.slow_rise_cleanup_unconfirmed) {
        $lines += 'Current Slow Rise OFF was not confirmed. Independent operator action is required.'
    }
    if ($public.trigger_input_off_unconfirmed) {
        $lines += 'Trigger Input OFF was not confirmed. Use the independent manual OFF procedure immediately.'
    }
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

function New-CliProcessInfo([string[]]$Arguments) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $python
    $start.Arguments = ($arguments | ForEach-Object { '"' + ($_ -replace '"','\"') + '"' }) -join ' '
    $start.WorkingDirectory = $repo
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    return $start
}

function Invoke-PlanCommand([string]$Command, [string[]]$ExtraArgs) {
    $arguments = @('-c', 'from electronicload_tool_cli.cli import main; raise SystemExit(main())',
        $Command, '--resource', $Resource, '--backend', $Backend, '--model', $Target,
        '--dry-run', '--json') + $ExtraArgs
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = New-CliProcessInfo $arguments
    $started = $false
    try {
        [void]$process.Start()
        $started = $true
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($script:perCommandDeadlineMs)) { throw 'Offline CLI plan timed out.' }
        $output = $stdout.GetAwaiter().GetResult()
        [void]$stderr.GetAwaiter().GetResult()
        $plan = $output | ConvertFrom-Json -ErrorAction Stop
        if ($process.ExitCode -ne 0 -or $plan.ok -ne $true) {
            throw "Offline plan failed for $Command ($($plan.error.code))."
        }
        return $plan
    } finally {
        if ($started -and -not $process.HasExited) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
        }
        $process.Dispose()
    }
}

function Save-CommandArtifacts($Artifact) {
    $privateDir = Join-Path $script:reportDirectory 'private'
    [void][IO.Directory]::CreateDirectory($privateDir)
    [IO.File]::WriteAllText((Join-Path $privateDir "$($Artifact.prefix).json"),
        $Artifact.output, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $privateDir "$($Artifact.prefix).stderr.txt"),
        $Artifact.diagnostic, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $privateDir "$($Artifact.prefix).exit.txt"),
        $Artifact.exitStatus, [Text.UTF8Encoding]::new($false))
}

function Invoke-LiveCommand([string]$Command, [string]$CaseName,
                            [string[]]$ExtraArgs = @(),
                            [int]$DeadlineMs = 30000,
                            [int]$TerminationMs = 5000,
                            [string]$ExpectedError,
                            [switch]$AllowCrHandoff,
                            [switch]$Cleanup) {
    $remaining = if ($Cleanup) { [Math]::Min($DeadlineMs, $script:perCommandDeadlineMs) } else {
        [Math]::Min($DeadlineMs,
            $script:totalDeadlineMs - $script:watch.ElapsedMilliseconds)
    }
    if ($remaining -le 0) { throw 'Total CLI validation deadline expired.' }
    $arguments = @('-c', 'from electronicload_tool_cli.cli import main; raise SystemExit(main())',
        $Command, '--resource', $Resource, '--backend', $Backend, '--model', $Target,
        '--json') + $ExtraArgs
    $start = New-CliProcessInfo $arguments
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $script:commandIndex++
    $sequence = $script:commandIndex
    $entry = $script:report.results | Where-Object { $_.name -eq $CaseName } | Select-Object -First 1
    if ($null -eq $entry) { throw "Unknown validation case: $CaseName" }
    # Mixed-mode selections must not treat a preceding non-TRACE trial as a TRACE fault.
    $traceCase = $script:traceSelected -and
        ($Command -in @('trace-configure', 'trace-start') -or $CaseName.StartsWith('trace-'))
    $started = $false
    $output = ''
    $diagnostic = ''
    $exitStatus = 'NOT STARTED'
    $terminationAttempted = $false
    if ($script:slowRiseEnableAttempted) { $script:slowRiseCommunicationTrusted = $false }
    try {
        [void]$process.Start()
        $started = $true
        $script:report.hardware_touched = $true
        $entry.status = 'FAIL'
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit([int]$remaining)) {
            if ($traceCase) { $script:traceCommunicationUntrusted = $true }
            $terminationAttempted = $true
            $process.Kill()
            $exitStatus = 'TIMEOUT; session/input state unconfirmed'
            if ($process.WaitForExit($TerminationMs)) {
                $output = $stdout.GetAwaiter().GetResult()
                $diagnostic = $stderr.GetAwaiter().GetResult()
            }
            $entry['error_code'] = 'deadline_expired'
            throw "$CaseName exceeded its deadline; physical Input state is unconfirmed."
        }
        $exitStatus = [string]$process.ExitCode
        $output = $stdout.GetAwaiter().GetResult()
        $diagnostic = $stderr.GetAwaiter().GetResult()
        try {
            $result = $output | ConvertFrom-Json -ErrorAction Stop
        } catch {
            if ($traceCase) { $script:traceCommunicationUntrusted = $true }
            $entry['error_code'] = 'invalid_cli_json'
            throw "$CaseName did not return valid CLI JSON."
        }
        if ($null -eq $result -or $result.ok -isnot [bool]) {
            if ($traceCase) { $script:traceCommunicationUntrusted = $true }
            $entry['error_code'] = 'invalid_cli_json'
            throw "$CaseName returned an incomplete CLI result."
        }
        $entry['exit_code'] = $process.ExitCode
        if ($process.ExitCode -ne 0 -or $result.ok -ne $true) {
            $code = [string]$result.error.code
            $details = $result.error.details
            $entry['error_code'] = $(if ($code -cmatch '^[a-z_]+$') { $code } else { 'execution_failed' })
            if ($CaseName -ceq 'trigger-restore') {
                if ($details.trigger_readbacks.source -cin @('CONFIRMED','MISMATCH','UNCONFIRMED')) {
                    $entry['trigger_source_restore'] = $details.trigger_readbacks.source
                }
                if ($details.trigger_readbacks.interval_s -cin @('CONFIRMED','MISMATCH','UNCONFIRMED')) {
                    $entry['trigger_interval_restore'] = $details.trigger_readbacks.interval_s
                }
            }
            if ($CaseName.StartsWith('trigger-') -and $Command -ceq 'set-list-cc' -and
                $code -ceq 'readback_mismatch' -and $details.list_selected -is [bool] -and $details.list_selected) {
                $entry['list_selected'] = $true
            }
            if ($CaseName.StartsWith('trigger-') -and $result.ok -eq $false -and
                $null -eq $details.cleanup_errors -and $code -in @('readback_mismatch','unsafe_mode',
                    'input_not_off','input_not_on','input_timer_enabled','unsafe_trigger_source')) {
                $entry['trigger_exchange_confirmed'] = $true
                if ($code -eq 'input_not_off') {
                    $script:triggerOffConfirmed = $false
                    $script:report.trigger_input_off_unconfirmed = $true
                }
            }
            if ($script:slowRiseEnableAttempted -and $result.ok -eq $false -and
                $null -eq $details.cleanup_errors -and
                (($process.ExitCode -eq 2 -and $code -ceq 'current_slow_rise_enabled') -or
                 ($process.ExitCode -eq 3 -and $code -ceq 'readback_mismatch'))) {
                # These are device/guard readbacks with a clean session close.
                $script:slowRiseCommunicationTrusted = $true
            }
            if ($ExpectedError -and $process.ExitCode -eq 2 -and $result.ok -eq $false -and
                $code -ceq $ExpectedError -and $null -ne $result.error.details.completed_steps -and
                @($result.error.details.completed_steps).Count -eq 0) {
                $entry.status = 'PASS'
                return
            }
            if ($AllowCrHandoff -and $process.ExitCode -eq 2 -and $result.ok -eq $false -and
                $code -ceq 'unsupported_live_scope' -and $null -eq $details.cleanup_errors -and
                $details.before_remote -is [bool] -and $details.before_remote -and
                $details.function -cin @('RES', 'RESISTANCE') -and
                $null -ne $details.completed_steps -and @($details.completed_steps).Count -eq 0) {
                $entry.status = 'SKIP'
                $entry['origin_function'] = $details.function
                Write-Host "SKIP ${CaseName}: resistance origin confirmed before remote. Running planned CR to CC handoff."
                return $result
            }
            if ($traceCase -and $result.error.details.feed_stop_unconfirmed -eq $true) {
                $script:traceFeedStopUnconfirmed = $true
                $script:report.trace_safe_shutdown_unconfirmed = $true
            }
            if ($traceCase -and ($code -in @('trace_session_untrusted','query_failed',
                    'write_failed','visa_open_failed','visa_configure_failed','cleanup_failed') -or
                    $result.error.details.feed -ceq 'UNKNOWN')) {
                $script:traceCommunicationUntrusted = $true
            }
            throw "$CaseName failed (exit $exitStatus, code $($entry.error_code))."
        }
        if ($ExpectedError) {
            if ($script:slowRiseEnableAttempted) { Assert-Runtime $result $CaseName }
            Fail-Case $CaseName 'unexpected_success' 'The mode change succeeded despite the expected interlock.'
        }
        if ($script:traceSelected -and $Command -eq 'trace-start') {
            # ConvertFrom-Json can normalize ISO datetimes to the runner's local
            # timezone. Preserve the UTC+8 literal from the public CLI JSON.
            $stampMatch = [regex]::Match($output, '"read_timestamp_utc_plus_8"\s*:\s*"([^"]+)"')
            if (-not $stampMatch.Success) {
                $entry['error_code'] = 'trace_bad_timestamp'
                throw "$CaseName did not return a TRACE batch timestamp."
            }
            $result.result | Add-Member -MemberType NoteProperty -Name read_timestamp_utc_plus_8 `
                -Value $stampMatch.Groups[1].Value -Force
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
        if ($started -and -not $terminationAttempted -and -not $process.HasExited) {
            $process.Kill()
            [void]$process.WaitForExit($TerminationMs)
        }
        if ($script:triggerPowered -and $started -and -not $process.HasExited) {
            $script:triggerProcessStillRunning = $true
        }
        $process.Dispose()
        if ($DebugArtifacts) {
            $artifact = @{prefix=('{0:D2}-{1}' -f $sequence, $CaseName); output=$output;
                          diagnostic=$diagnostic; exitStatus=$exitStatus}
            if ($script:triggerPowered) { $script:triggerArtifacts += $artifact }
            else { Save-CommandArtifacts $artifact }
        }
        if (-not $script:triggerPowered) { Save-Report }
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
    if ($script:slowRiseEnableAttempted) { $script:slowRiseCommunicationTrusted = $true }
}

function Assert-InputOff([object]$Result, [string]$CaseName) {
    if ($Result.result.input_state -cne 'OFF') {
        if ($script:traceSelected) {
            $script:report.trace_safe_shutdown_unconfirmed = $true
        }
        Fail-Case $CaseName 'input_not_off' 'Physical Input OFF was not confirmed; further writes are blocked.'
    }
}

function New-TriggerPlan {
    # Only dry-run and noninteractive refusal may use example timings.
    $examples = @{
        TriggerObservationSeconds=5; TriggerMaxOnSeconds=60; TriggerIntervalSeconds='1'
        TriggerListWidthA='0.5'; TriggerListWidthB='0.5'; TriggerListRepeat=1
        LevelA='0.1'; LevelB='0.2'; RiseSlew='0.1'; FallSlew='0.1'; PulseWidth='0.1'
    }
    $offline = $PlanOnly -or [Console]::IsInputRedirected -or -not [Environment]::UserInteractive
    foreach ($name in $examples.Keys) {
        if (-not $script:PSBoundParameters.ContainsKey($name)) {
            if (-not $offline) { throw "Trigger LIVE requires an explicit -$name." }
            Set-Variable -Name $name -Value $examples[$name] -Scope Script
        }
    }
    if (-not $offline -and [string]::IsNullOrWhiteSpace($TriggerExternalSetup)) {
        throw 'Trigger LIVE requires -TriggerExternalSetup with the reviewed pulse source, electrical limits and wiring.'
    }
    if ($TriggerObservationSeconds -le 0 -or $TriggerMaxOnSeconds * 1000 -gt $totalDeadlineMs -or
        $TriggerMaxOnSeconds -le $TriggerObservationSeconds) {
        throw 'Maximum Trigger ON time must exceed the observation time and fit within the total deadline.'
    }
    foreach ($token in @($LevelA, $LevelB, $RiseSlew, $FallSlew, $PulseWidth,
                         $TriggerIntervalSeconds, $TriggerListWidthA, $TriggerListWidthB)) {
        if ($token -cnotmatch '^(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$') {
            throw 'Trigger levels, slews and times must be nonnegative JSON numbers.'
        }
    }
    if ([decimal]$LevelA -eq [decimal]$LevelB -or [decimal]$LevelA -gt [decimal]$SourceCurrentLimitA -or
        [decimal]$LevelB -gt [decimal]$SourceCurrentLimitA) {
        throw 'Trigger trials require distinct current levels within the declared source limit.'
    }
    if ($TriggerListRepeat -lt 1 -or $TriggerListRepeat -ge 65536 -or
        ([decimal]$TriggerListWidthA + [decimal]$TriggerListWidthB) * $TriggerListRepeat -gt $TriggerObservationSeconds) {
        throw 'The finite LIST must fit in the observation window.'
    }
    if ([decimal]$PulseWidth -ge [decimal]$TriggerIntervalSeconds -or
        2 * [decimal]$TriggerIntervalSeconds + [decimal]$PulseWidth -gt $TriggerObservationSeconds) {
        throw 'Allow time for at least two Timer periods and a distinct pulse shorter than the interval.'
    }
    $setTransient = @('--range','low','--transient-mode','pulse','--level-a',$LevelA,'--level-b',$LevelB,
        '--rise-slew',$RiseSlew,'--fall-slew',$FallSlew,'--pulse-width',$PulseWidth)
    $transientJson = '{"range":"low","transient_mode":"pulse","level_a":' + $LevelA +
        ',"level_b":' + $LevelB + ',"rise_slew":' + $RiseSlew + ',"fall_slew":' + $FallSlew +
        ',"pulse_width":' + $PulseWidth + '}'
    $listJson = '{"range":"low","repeat_count":' + $TriggerListRepeat + ',"steps":[' +
        '{"current_a":' + $LevelA + ',"slew_a_per_us":' + $RiseSlew + ',"width_s":' + $TriggerListWidthA + '},' +
        '{"current_a":' + $LevelB + ',"slew_a_per_us":' + $FallSlew + ',"width_s":' + $TriggerListWidthB + '}]}'
    $script:triggerPlans = @(@{name='trigger-snapshot';command='trigger-status';args=$triggerScope})
    foreach ($source in @('MANUAL','HOLD','TIMER','BUS','EXTERNAL')) {
        $prefix = 'trigger-' + $source.ToLowerInvariant()
        $isList = $source -in @('BUS','EXTERNAL')
        $function = if ($isList) { 'LIST' } else { 'CC' }
        $configJson = if ($isList) { $listJson } else { $transientJson }
        $sourceArgs = @('--source',$source)
        $triggerJson = '{"source":"' + $source + '"'
        if ($source -eq 'TIMER') {
            $sourceArgs += @('--interval-s',$TriggerIntervalSeconds)
            $triggerJson += ',"interval_s":' + $TriggerIntervalSeconds
        }
        $expected = @('--validation-settings-json', ('{"function":"' + $function +
            '","configuration":' + $configJson + ',"trigger":' + $triggerJson + '}}'))
        $script:triggerTrials += @{prefix=$prefix;source=$source;isList=$isList;interval=$TriggerIntervalSeconds}
        $script:triggerPlans += @{name="$prefix-configure";command='trigger-configure';args=($triggerScope + $sourceArgs)}
        if ($isList) {
            $script:triggerPlans += @{name="$prefix-setup";command='set-list-cc';
                args=($triggerScope + @('--arguments-json',$listJson))}
        } else {
            $script:triggerPlans += @{name="$prefix-setup";command='set-transient-cc';args=($triggerScope + $setTransient)}
            $script:triggerPlans += @{name="$prefix-enable";command='transient-enable';args=($triggerScope + $expected)}
        }
        $script:triggerPlans += @{name="$prefix-on";command='input-on';args=($triggerScope + $expected + @('--confirm'))}
        if ($source -in @('HOLD','BUS')) {
            $cmd = if ($isList) { 'list-trigger' } else { 'transient-trigger' }
            $script:triggerPlans += @{name="$prefix-dispatch";command=$cmd;args=($triggerScope + $expected)}
        }
        $script:triggerPlans += @{name="$prefix-off";command='input-off';args=$triggerScope}
        $script:triggerPlans += @{name="$prefix-observed";command='operator-observation';args=@()}
        $release = if ($isList) { 'list-exit' } else { 'transient-disable' }
        $script:triggerPlans += @{name="$prefix-release";command=$release;args=$triggerScope}
    }
    $script:triggerPlans += @{name='trigger-restore';command='trigger-configure';
        args=($triggerScope + @('--source','MANUAL','--interval-s','0.01'));
        condition='Plan uses example settings. LIVE requires original Source and Timer readback, trusted communication and confirmed Input OFF.'}
}

function Invoke-TriggerStep([string]$Name, [int]$DeadlineMs = 30000, [int]$TerminationMs = 5000, [switch]$Cleanup) {
    $step = $script:triggerPlans | Where-Object { $_.name -ceq $Name }
    $entry = $script:report.results | Where-Object { $_.name -ceq $Name }
    $exchangeConfirmed = $false
    try {
        $request = @{Command=$step.command; CaseName=$Name; ExtraArgs=$step.args;
                     DeadlineMs=[Math]::Min($DeadlineMs, $script:perCommandDeadlineMs)
                     TerminationMs=$TerminationMs; Cleanup=$Cleanup}
        $response = Invoke-LiveCommand @request
        Assert-Runtime $response $Name
        $exchangeConfirmed = $true
        if ($step.command -in @('trigger-configure','trigger-status')) {
            $entry['trigger_source'] = $response.result.source
            $entry['trigger_interval_s'] = $response.result.interval_s
        }
        return $response
    } finally {
        $script:triggerTrusted = $script:triggerTrusted -and
            ($exchangeConfirmed -or $entry.Contains('trigger_exchange_confirmed'))
    }
}

function Assert-TriggerOff($Response, [string]$Name) {
    $script:triggerOffConfirmed = $Response.result.input_state -ceq 'OFF'
    $script:report.trigger_input_off_unconfirmed = -not $script:triggerOffConfirmed
    Assert-InputOff $Response $Name
}

function Read-TriggerAnswer([string]$Prompt, [string]$CaseName, [switch]$DiscardPendingKeys) {
    if ($DiscardPendingKeys) {
        while ($script:watch.ElapsedMilliseconds -lt $script:totalDeadlineMs -and [Console]::KeyAvailable) {
            $null = [Console]::ReadKey($true)
        }
    }
    Write-Host $Prompt
    $answer = [Text.StringBuilder]::new()
    while ($script:watch.ElapsedMilliseconds -lt $script:totalDeadlineMs) {
        if (-not [Console]::KeyAvailable) {
            Start-Sleep -Milliseconds 50
            continue
        }
        $key = [Console]::ReadKey($true)
        if ($script:watch.ElapsedMilliseconds -ge $script:totalDeadlineMs) { break }
        if ($key.Key -eq [ConsoleKey]::Enter) {
            Write-Host ''
            return $answer.ToString()
        }
        if ($key.Key -eq [ConsoleKey]::Backspace) {
            if ($answer.Length -gt 0) {
                $answer.Length--
                Write-Host -NoNewline "`b `b"
            }
        } else {
            [void]$answer.Append($key.KeyChar)
            if (-not [char]::IsControl($key.KeyChar)) { Write-Host -NoNewline $key.KeyChar }
        }
    }
    Fail-Case $CaseName 'deadline_expired' 'The total CLI validation deadline expired while waiting for the operator.'
}

function Invoke-TriggerTrial($Trial) {
    $prefix = $Trial.prefix
    $armed = $false
    $awaitingObservation = $false
    $entry = $script:report.results | Where-Object { $_.name -ceq "$prefix-observed" }
    $setupEntry = $script:report.results | Where-Object { $_.name -ceq "$prefix-setup" }
    try {
        $configured = Invoke-TriggerStep "$prefix-configure"
        Assert-TriggerOff $configured "$prefix-configure"
        if ($configured.result.source -cne $Trial.source -or ($Trial.source -eq 'TIMER' -and
            [decimal]$configured.result.interval_s -ne [decimal]$Trial.interval)) {
            Fail-Case "$prefix-configure" 'readback_mismatch' 'Trigger Source or Timer did not match.'
        }
        $setup = Invoke-TriggerStep "$prefix-setup"
        $armed = $Trial.isList
        Assert-TriggerOff $setup "$prefix-setup"
        if (-not $Trial.isList) {
            $armed = $true
            Assert-TriggerOff (Invoke-TriggerStep "$prefix-enable") "$prefix-enable"
        }
        Write-Host "Trigger $($Trial.source), CLI readback confirmed. Observe the load current with the approved meter or capture instrument."
        if ($Trial.isList) {
            Write-Host "Expect LIST CC levels $LevelA A then $LevelB A for $TriggerListWidthA s / $TriggerListWidthB s, repeated $TriggerListRepeat time(s)."
        } else {
            Write-Host "Expect baseline B ($LevelB A), a pulse at A ($LevelA A) for $PulseWidth s, then a return to B."
        }
        switch ($Trial.source) {
            'MANUAL' { Write-Host 'When the observation starts, press Local, then Shift + . (Trigger) on the IT8812C.' }
            'EXTERNAL' {
                Write-Host 'Keep the source inactive until the observation starts. Apply one TTL low pulse (>10 us) to rear TRIG and its shared negative terminal.'
                Write-Host "Confirm the reviewed equipment and wiring: $TriggerExternalSetup"
            }
            'TIMER' { Write-Host "Watch for at least two Timer pulses at $($Trial.interval) s intervals. No operator pulse is required." }
            default { Write-Host 'The script will send one software trigger after Input ON is confirmed.' }
        }
        $awaitingObservation = $true
        $entry['operator_observed'] = 'Unconfirmed'
        $ack = Read-TriggerAnswer 'With Input OFF and capture ready, press Enter to start the bounded observation (Ctrl+C to cancel)' "$prefix-observed"
        if ($null -eq $ack -or $ack -cne '') {
            Fail-Case "$prefix-observed" 'operator_cancelled' 'Operator did not confirm readiness.'
        }
        $hardStop = $script:watch.ElapsedMilliseconds + $TriggerMaxOnSeconds * 1000
        if ($hardStop -gt $totalDeadlineMs) { throw 'Insufficient total time for a bounded Trigger trial.' }
        # Reserve part of the operator's extra time for OFF and for ending
        # a timed-out command before another VISA process can start.
        $marginMs = ($TriggerMaxOnSeconds - $TriggerObservationSeconds) * 1000
        $offReserveMs = [Math]::Min($perCommandDeadlineMs, [Math]::Floor($marginMs / 2))
        $terminationMs = [Math]::Min(5000, [Math]::Floor($offReserveMs / 2))
        $startOffBy = $hardStop - $offReserveMs - $terminationMs
        $onDeadline = $startOffBy - $script:watch.ElapsedMilliseconds - $TriggerObservationSeconds * 1000
        if ($onDeadline -le 0) { throw 'Insufficient time for Trigger Input ON and observation.' }
        $script:triggerOffConfirmed = $false
        $script:triggerPowered = $true
        try {
            $on = Invoke-TriggerStep "$prefix-on" -DeadlineMs $onDeadline -TerminationMs $terminationMs
            if ($on.result.input_state -cne 'ON') {
                Fail-Case "$prefix-on" 'input_on_not_confirmed' 'Trigger Input ON was not confirmed.'
            }
            $observeUntil = $script:watch.ElapsedMilliseconds + $TriggerObservationSeconds * 1000
            if ($observeUntil -gt $startOffBy) { throw 'Insufficient time remains to observe before Input OFF.' }
            Write-Host "Observation started for $($Trial.source)."
            if ($Trial.source -in @('HOLD','BUS')) {
                $dispatchBudget = $observeUntil - $script:watch.ElapsedMilliseconds
                $eventDuration = if ($Trial.isList) {
                    ([decimal]$TriggerListWidthA + [decimal]$TriggerListWidthB) * $TriggerListRepeat
                } else { [decimal]$PulseWidth }
                $dispatchBudget -= [Math]::Ceiling($eventDuration * 1000)
                if ($dispatchBudget -le 0) { throw 'Software trigger observation deadline expired.' }
                $null = Invoke-TriggerStep "$prefix-dispatch" -DeadlineMs $dispatchBudget -TerminationMs $terminationMs
            }
            $remaining = $observeUntil - $script:watch.ElapsedMilliseconds
            if ($remaining -gt 0) { Start-Sleep -Milliseconds $remaining }
        } finally {
            try {
                if (-not $script:triggerTrusted -or $script:triggerProcessStillRunning) {
                    Write-Host 'Trigger communication is untrusted. No new VISA session will be started.'
                } else {
                    $offBudget = [Math]::Min($offReserveMs, $hardStop - $script:watch.ElapsedMilliseconds - $terminationMs)
                    # If already late, still attempt emergency OFF once with the reserved timeout.
                    if ($offBudget -le 0) { $offBudget = $offReserveMs }
                    $off = Invoke-TriggerStep "$prefix-off" -DeadlineMs $offBudget -TerminationMs $terminationMs -Cleanup
                    Assert-TriggerOff $off "$prefix-off"
                    if ($script:watch.ElapsedMilliseconds -ge $hardStop) {
                        $offEntry = $script:report.results | Where-Object { $_.name -ceq "$prefix-off" }
                        $offEntry['confirmed_input_state'] = 'OFF'
                        Fail-Case "$prefix-off" 'deadline_expired' 'Input OFF was confirmed after the Trigger trial deadline.'
                    }
                }
            } finally {
                $script:triggerPowered = $false
                $script:report.trigger_input_off_unconfirmed = -not $script:triggerOffConfirmed
                if (-not $script:triggerOffConfirmed) {
                    Write-Host 'Trigger Input OFF was not confirmed. Use the independent manual OFF procedure immediately.'
                }
                foreach ($artifact in $script:triggerArtifacts) { Save-CommandArtifacts $artifact }
                $script:triggerArtifacts = @()
                Save-Report
            }
        }
        $answer = Read-TriggerAnswer "Input OFF confirmed. Did you observe the expected $($Trial.source) result? (Yes/No)" `
            "$prefix-observed" -DiscardPendingKeys
        $entry['operator_observed'] = if ($answer -ieq 'Yes') { 'Yes' } elseif ($answer -ieq 'No') { 'No' } else { 'Unconfirmed' }
        if ($answer -ine 'Yes') {
            Fail-Case "$prefix-observed" 'observation_unconfirmed' 'Operator did not confirm the expected physical trigger result.'
        }
        if ($script:watch.ElapsedMilliseconds -ge $totalDeadlineMs) {
            Fail-Case "$prefix-observed" 'deadline_expired' 'The total CLI validation deadline expired.'
        }
        $entry.status = 'PASS'
    } finally {
        if ($awaitingObservation -and $entry.status -eq 'NOT RUN') {
            $entry.status = 'FAIL'
            $entry['error_code'] = 'observation_incomplete'
        }
        $releaseRequired = $armed -or $setupEntry.Contains('list_selected')
        if ($releaseRequired -and $script:triggerTrusted -and $script:triggerOffConfirmed) {
            Assert-TriggerOff (Invoke-TriggerStep "$prefix-release" -Cleanup) "$prefix-release"
        } elseif ($releaseRequired -and $Trial.isList) {
            Write-Host 'LIST exit was not attempted. Confirm physical Input OFF and verify the load mode manually.'
        }
    }
}

function Invoke-TriggerValidation {
    $snapshot = Invoke-TriggerStep 'trigger-snapshot'
    Assert-TriggerOff $snapshot 'trigger-snapshot'
    $source = $snapshot.result.source
    $interval = $snapshot.result.interval_s
    if ($source -cnotin @('MANUAL','BUS','HOLD','TIMER','EXTERNAL') -or
        $interval -isnot [string] -or [string]::IsNullOrWhiteSpace($interval)) {
        Fail-Case 'trigger-snapshot' 'invalid_response' 'Both original Trigger settings must be read before any change.'
    }
    try {
        foreach ($trial in $script:triggerTrials) { Invoke-TriggerTrial $trial }
    } finally {
        $script:report.trigger_restore_status = 'FAIL'
        $entry = $script:report.results | Where-Object { $_.name -ceq 'trigger-restore' }
        $entry['trigger_source_restore'] = 'UNCONFIRMED'
        $entry['trigger_interval_restore'] = 'UNCONFIRMED'
        if ($script:triggerTrusted -and $script:triggerOffConfirmed) {
            $restore = $script:triggerPlans | Where-Object { $_.name -ceq 'trigger-restore' }
            $restore.args = $triggerScope + @('--source',$source,'--interval-s',$interval)
            $entry.arguments = $restore.args
            try {
                $restored = Invoke-TriggerStep 'trigger-restore' -Cleanup
                $entry['trigger_source_restore'] = if ($restored.result.source -ceq $source) { 'CONFIRMED' } else { 'MISMATCH' }
                $entry['trigger_interval_restore'] = if ($null -eq $restored.result.interval_s) { 'UNCONFIRMED' }
                    elseif ([decimal]$restored.result.interval_s -eq [decimal]$interval) { 'CONFIRMED' } else { 'MISMATCH' }
                Assert-TriggerOff $restored 'trigger-restore'
                if ($entry.trigger_source_restore -cne 'CONFIRMED' -or $entry.trigger_interval_restore -cne 'CONFIRMED') {
                    Fail-Case 'trigger-restore' 'readback_mismatch' 'Original Trigger Setup was not confirmed.'
                }
                $script:report.trigger_restore_status = 'PASS'
            } finally {
                if ($script:report.trigger_restore_status -ne 'PASS') {
                    Write-Host "Trigger restoration status: Source $($entry.trigger_source_restore), Timer Interval $($entry.trigger_interval_restore). Operator action is required."
                }
            }
        } else {
            Write-Host 'Original Trigger Source and Timer Interval were not restored. Confirm physical OFF and restore them manually once communication is reliable.'
        }
    }
    if ($script:report.trigger_restore_status -ne 'PASS') {
        throw 'Original Trigger Source and Timer Interval were not confirmed.'
    }
    if ($script:watch.ElapsedMilliseconds -ge $totalDeadlineMs) {
        throw 'The total CLI validation deadline expired during Trigger cleanup.'
    }
}

function Assert-TraceResult([object]$Result, [hashtable]$Step) {
    $actual = $Result.result
    $expected = $Step.trace_expected
    $name = $Step.name
    $count = [int]($expected.points / $(if ($expected.source -ceq 'TWO') { 2 } else { 1 }))
    $readInterval = [decimal]::Parse([string]$actual.interval_s,
        [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture)
    if ($actual.validation_only -ne $true -or $actual.validation_scope -cne 'trace' -or
        $actual.source -cne $expected.source -or $actual.points -ne $expected.points -or
        $actual.sample_count -ne $count -or $readInterval -ne [decimal]$expected.interval) {
        Fail-Case $name 'trace_readback_mismatch' 'TRACE result differs from the configured batch.'
    }
    $entry = $script:report.results | Where-Object { $_.name -eq $name } | Select-Object -First 1
    $entry['trace_source'] = $actual.source
    $entry['trace_points'] = [int]$actual.points
    $entry['trace_interval_s'] = [string]$actual.interval_s
    $entry['trace_sample_count'] = [int]$actual.sample_count
    if ($Step.command -eq 'trace-configure') { return }
    $samples = @($actual.samples)
    if ($samples.Count -ne $count) {
        Fail-Case $name 'trace_incomplete' 'TRACE returned an incomplete batch.'
    }
    $stamp = [datetimeoffset]::Parse([string]$actual.read_timestamp_utc_plus_8)
    if ($stamp.Offset.TotalHours -ne 8) {
        Fail-Case $name 'trace_bad_timestamp' 'TRACE batch timestamp must be UTC+8.'
    }
    $fields = $(if ($expected.source -ceq 'TWO') { @('voltage_v','current_a') }
                elseif ($expected.source -ceq 'VOLT') { @('voltage_v') } else { @('current_a') })
    for ($i = 0; $i -lt $samples.Count; $i++) {
        if ($samples[$i].sample_index -ne $i + 1) {
            Fail-Case $name 'trace_bad_index' 'TRACE sample index mismatch.'
        }
        $names = @($samples[$i].PSObject.Properties.Name | Where-Object { $_ -ne 'sample_index' })
        if ((@($names | Sort-Object) -join ',') -cne (@($fields | Sort-Object) -join ',')) {
            Fail-Case $name 'trace_bad_fields' 'TRACE returned fields for the wrong source.'
        }
        foreach ($field in $fields) {
            $value = [double]$samples[$i].$field
            if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) {
                Fail-Case $name 'trace_bad_value' 'TRACE returned non-finite data.'
            }
        }
    }
    if ($Step.ContainsKey('trace_csv') -and $Step.trace_csv) {
        if (-not (Test-Path -LiteralPath $script:traceCsvPath -PathType Leaf)) {
            Fail-Case $name 'trace_csv_missing' 'TRACE CSV was not created.'
        }
        $rows = @(Import-Csv -LiteralPath $script:traceCsvPath)
        $required = @('read_timestamp_utc_plus_8','sample_index','voltage_v','current_a',
                      'source','points','interval_s','sample_count',
                      'estimated_elapsed_s','estimated_delta_t_s')
        $headers = @((Get-Content -LiteralPath $script:traceCsvPath -TotalCount 1) -split ',')
        if ($rows.Count -ne $count -or ($headers -join ',') -cne ($required -join ',')) {
            Fail-Case $name 'trace_csv_mismatch' 'TRACE CSV columns/rows do not match.'
        }
        for ($i = 0; $i -lt $rows.Count; $i++) {
            if ($rows[$i].sample_index -ne [string]($i + 1) -or
                $rows[$i].source -cne $expected.source -or
                [decimal]$rows[$i].points -ne $expected.points -or
                [decimal]$rows[$i].interval_s -ne [decimal]$expected.interval -or
                $rows[$i].read_timestamp_utc_plus_8 -cne $actual.read_timestamp_utc_plus_8) {
                Fail-Case $name 'trace_csv_mismatch' 'TRACE CSV does not match JSON settings.'
            }
            foreach ($field in @('voltage_v','current_a')) {
                if ([double]$rows[$i].$field -ne [double]$samples[$i].$field) {
                    Fail-Case $name 'trace_csv_mismatch' 'TRACE CSV differs from JSON measurements.'
                }
            }
        }
        if ($rows[0].estimated_delta_t_s -ne '' -or
            [decimal]$rows[0].estimated_elapsed_s -ne 0) {
            Fail-Case $name 'trace_csv_mismatch' 'TRACE initial estimated times are incorrect.'
        }
        $entry['trace_csv_complete'] = $true
    }
}

try {
    if ($requestedModes.Count -eq 0 -or @($requestedModes | Where-Object { $_ -notin $supportedModes }).Count -gt 0) {
        throw "Modes must contain one or more of: $($supportedModes -join ', ')."
    }
    if ($triggerSelected -and @($selectedModes | Where-Object { $_.StartsWith('transient-') }).Count -gt 0) {
        throw 'Run Trigger validation separately from legacy Transient modes to preserve the original Trigger Source.'
    }
    if (-not $Target -or -not $Resource) {
        Write-Host 'BLOCKED: explicit -Target and -Resource are required.'
        exit 2
    }
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $cli)) {
        throw 'Sync the repository .venv with uv before running validation.'
    }
    if ('cc' -in $selectedModes -or $traceSelected -or $InputTimerOnly -or $featureSelected) {
        # Preserve the original token; both manual trials and the script ceiling must pass.
        & $python -c 'from electronicload_tool_core.control import validate_current; import sys; value = validate_current(sys.argv[1], sys.argv[2]); validate_current(sys.argv[1], sys.argv[3]); sys.exit(0 if 0 < value <= 2 else 2)' $currentText low high
        if ($LASTEXITCODE -ne 0) {
            throw 'Current must be positive, at most 2 A, and valid for both CC ranges without rounding.'
        }
        $report.prechecks.current = 'PASS'
    }
    $report.script_current_ceiling_a = $scriptCurrentCeilingA
    if ($InputTimerOnly) {
        & $python -c 'import sys; from electronicload_tool_core.control import validate_input_timer; value=validate_input_timer(int(sys.argv[1])); sys.exit(0 if 3 <= value <= 10 else 2)' $TimerSeconds
        if ($LASTEXITCODE -ne 0) { throw 'Timer validation seconds must be an integer from 3 through 10.' }
    }

    # Script-only operator-declared envelope; this does not set or read back the source.
    if ([double]::IsNaN($SourceCurrentLimitA) -or [double]::IsInfinity($SourceCurrentLimitA) -or
        [double]::IsNaN($SourceVoltageLimitV) -or [double]::IsInfinity($SourceVoltageLimitV) -or
        $SourceCurrentLimitA -le 0 -or $SourceCurrentLimitA -gt 2 -or
        $SourceVoltageLimitV -le 0 -or $SourceVoltageLimitV -gt 18) {
        throw 'Declared source limits must be >0 to 2 A and >0 to 18 V.'
    }
    $report.declared_source_limits = [ordered]@{
        current_a = $SourceCurrentLimitA; voltage_v = $SourceVoltageLimitV
        verification = 'operator-declared; script does not configure or verify the source'
    }
    if (('cc' -in $selectedModes -or $traceSelected -or $InputTimerOnly -or $featureSelected) -and
        [decimal]$currentText -gt [decimal]$SourceCurrentLimitA) {
        throw 'CC current must not exceed the declared source current limit.'
    }
    if ('transient-cc' -in $selectedModes) {
        & $python -c 'from decimal import Decimal; import sys; a,b=map(Decimal,sys.argv[1:]); assert 0 <= a <= 2 and 0 <= b <= 2' $LevelA $LevelB
        if ($LASTEXITCODE -ne 0) { throw 'Transient levels must be within the script ceiling of 2 A.' }
        if ([decimal]$LevelA -gt [decimal]$SourceCurrentLimitA -or
            [decimal]$LevelB -gt [decimal]$SourceCurrentLimitA) {
            throw 'Transient CC levels must not exceed the declared source current limit.'
        }
    }
    $highArgs = @('--current', $currentText, '--range', 'high')
    $lowArgs = @('--current', $currentText, '--range', 'low')
    $autoArgs = @('--current', $currentText, '--range', 'auto')
    $onArgs = @('--confirm')
    $planned = @(
        @{name='identify';command='identify';args=@()},
        @{name='input-state';command='input-state';args=@()},
        @{name='measure-off';command='measure';args=@()}
    )
    if ('cc' -in $selectedModes) {
        $planned += @(
            @{name='set-cc-high';command='set-cc';args=$highArgs;allow_cr_handoff=$true},
            @{name='cc-handoff-from-cr';command='set-cc';args=($highArgs + @('--hardware-validation','cr-led'));
              cr_handoff_after='set-cc-high'},
            @{name='set-cc-auto';command='set-cc';args=$autoArgs},
            @{name='set-cc-low';command='set-cc';args=$lowArgs},
            @{name='input-on';command='input-on';args=$onArgs},
            @{name='measure-on';command='measure';args=@()},
            @{name='input-off';command='input-off';args=@()},
            @{name='final-input-state';command='input-state';args=@()}
        )
    }
    if ($InputTimerOnly) {
        $timerScope = @('--hardware-validation', 'input-timer')
        $timerExpected = '{"current":' + $currentText + ',"range":"low","seconds":' + [int]$TimerSeconds + '}'
        $planned += @(
            @{name='timer-initial-disable';command='input-timer-disable';args=$timerScope},
            @{name='timer-cc-setup';command='set-cc';args=$lowArgs},
            @{name='timer-setup';command='set-input-timer';args=($timerScope + @('--seconds', $TimerSeconds))},
            @{name='timer-enable';command='input-timer-enable';args=$timerScope},
            @{name='timer-status-off';command='input-timer-status';args=$timerScope},
            @{name='timer-on';command='input-on';args=($timerScope + @('--confirm', '--validation-settings-json', $timerExpected))},
            @{name='timer-expiry';command='input-timer-status';args=$timerScope},
            @{name='timer-off';command='input-off';args=@()},
            @{name='timer-disable';command='input-timer-disable';args=$timerScope},
            @{name='timer-status-disabled';command='input-timer-status';args=$timerScope},
            @{name='timer-final-state';command='input-state';args=@()}
        )
    }
    if ($hasValidationModes) {
        $trials = @(
            @{name='cv-low';scope='cv';settings=@{voltage=$Voltage;range='low'}},
            @{name='cv-high';scope='cv';settings=@{voltage=$Voltage;range='high'}},
            @{name='cr-low';scope='cr';settings=@{resistance=$ResistanceLow;range='low'}},
            @{name='cr-high';scope='cr';settings=@{resistance=$ResistanceHigh;range='high'}},
            @{name='cw';scope='cw';settings=@{power=$Power}}
        )
        foreach ($item in @(@('continuous','auto'), @('pulse','low'), @('toggle','high'),
                             @('continuous','high'), @('pulse','auto-high'))) {
            $configuration = @{
                transient_mode=$item[0]; range=$item[1]; level_a=$LevelA; level_b=$LevelB
                rise_slew=$RiseSlew; fall_slew=$FallSlew
            }
            if ($item[1] -eq 'auto-high') {
                # Explicit extra trial: 0.5 A/us requires High while levels stay bounded.
                $configuration.range = 'auto'
                $configuration.rise_slew = '0.5'
                $configuration.fall_slew = '0.5'
            }
            if ($item[0] -eq 'continuous') { $configuration.width_a=$WidthA; $configuration.width_b=$WidthB }
            if ($item[0] -eq 'pulse') { $configuration.pulse_width=$PulseWidth }
            $trials += @{name="transient-$($item[0])-$($item[1])";scope='transient-cc';settings=$configuration}
        }
        # Three trials per new scope share the established control sequence.
        # CR's default A/B span High; use explicit High for the manual trials.
        foreach ($scope in @('transient-cv', 'transient-cr', 'transient-cw')) {
            if ($scope -notin $selectedModes) { continue }
            foreach ($waveform in @('continuous', 'pulse', 'toggle')) {
                $configuration = @{transient_mode=$waveform}
                switch ($scope) {
                    'transient-cv' {
                        $configuration.level_a=$VoltageLevelA; $configuration.level_b=$VoltageLevelB
                        $configuration.range=$(if ($waveform -eq 'continuous') { 'auto' } elseif ($waveform -eq 'pulse') { 'low' } else { 'high' })
                    }
                    'transient-cr' {
                        $configuration.level_a=$ResistanceLevelA; $configuration.level_b=$ResistanceLevelB
                        $configuration.range=$(if ($waveform -eq 'continuous') { 'auto' } else { 'high' })
                    }
                    'transient-cw' {
                        $configuration.level_a=$PowerLevelA; $configuration.level_b=$PowerLevelB
                    }
                }
                if ($waveform -eq 'continuous') { $configuration.width_a=$WidthA; $configuration.width_b=$WidthB }
                if ($waveform -eq 'pulse') { $configuration.pulse_width=$PulseWidth }
                $trials += @{name="$scope-$waveform";scope=$scope;settings=$configuration}
            }
        }
        foreach ($trial in ($trials | Where-Object { $_.scope -in $selectedModes })) {
            $prefix = $trial.name
            $scopeArgs = @('--hardware-validation', $trial.scope)
            $setupArgs = @($scopeArgs)
            foreach ($key in ($trial.settings.Keys | Sort-Object)) {
                $setupArgs += @(('--' + ($key -replace '_','-')), ([string]$trial.settings[$key]))
            }
            if ($prefix -in @('cv-low', 'cr-low', 'cr-high')) {
                # Reuse the approved values, adding OFF-only setup without extra ON/OFF cycles.
                $autoSetupArgs = @($setupArgs)
                $autoSetupArgs[[Array]::IndexOf($autoSetupArgs, '--range') + 1] = 'auto'
                $autoName = $(if ($trial.scope -eq 'cv') { 'cv-auto' } else {
                    $prefix -replace '^cr-', 'cr-auto-'
                })
                $planned += @{name="$autoName-setup";command="set-$($trial.scope)";args=$autoSetupArgs}
            }
            $expectedArgs = $scopeArgs + @('--validation-settings-json',
                ($trial.settings | ConvertTo-Json -Compress))
            $planned += @{name="$prefix-setup";command="set-$($trial.scope)";args=$setupArgs}
            if ($trial.scope.StartsWith('transient-')) {
                $planned += @{name="$prefix-enable";command='transient-enable';args=$expectedArgs}
            }
            $planned += @{name="$prefix-on";command='input-on';args=($expectedArgs + @('--confirm'))}
            if ($trial.scope.StartsWith('transient-')) {
                $planned += @{name="$prefix-trigger-1";command='transient-trigger';args=$expectedArgs}
                $planned += @{name="$prefix-trigger-2";command='transient-trigger';args=$expectedArgs}
                $planned += @{name="$prefix-status-on";command='transient-status';args=$scopeArgs}
            }
            $planned += @{name="$prefix-measure";command='measure';args=@()}
            $planned += @{name="$prefix-off";command='input-off';args=@()}
            if ($trial.scope.StartsWith('transient-')) {
                $planned += @{name="$prefix-disable";command='transient-disable';args=$scopeArgs}
                $planned += @{name="$prefix-status-off";command='transient-status';args=$scopeArgs}
            }
            $planned += @{name="$prefix-final-state";command='input-state';args=@()}
        }
    }
    if ($traceSelected) {
        $trialCases = @(
            @{name='trace-voltage';source='VOLT';points=10;interval='1'},
            @{name='trace-current';source='CURR';points=10;interval='1'},
            @{name='trace-paired';source='TWO';points=40;interval='1'},
            @{name='trace-short-interval';source='VOLT';points=10;interval='0.001'},
            @{name='trace-powered';source='TWO';points=10;interval='1'}
        )
        foreach ($trial in $trialCases) {
            if ($trial.name -eq 'trace-powered') {
                $planned += @{name='trace-powered-setup-cc';command='set-cc';args=$lowArgs;allow_cr_handoff=$true}
                $planned += @{name='trace-powered-cc-handoff-from-cr';command='set-cc';
                              args=($lowArgs + @('--hardware-validation','cr-led'));
                              cr_handoff_after='trace-powered-setup-cc'}
            }
            $setupArgs = @('--hardware-validation','trace','--source',$trial.source,
                           '--points',([string]$trial.points),'--interval-s',$trial.interval)
            $planned += @{name="$($trial.name)-configure";command='trace-configure';
                          args=$setupArgs;trace_expected=$trial}
            if ($trial.name -eq 'trace-powered') {
                $planned += @{name='trace-powered-on';command='input-on';args=$onArgs}
            }
            $startArgs = @('--hardware-validation','trace')
            $startStep = @{name="$($trial.name)-start";command='trace-start';
                           args=$startArgs;trace_expected=$trial}
            if ($trial.name -eq 'trace-powered') {
                $startStep.args += @('--csv',$traceCsvPath,'--csv-format','full',
                                     '--csv-time-columns','elapsed,delta')
                $startStep.trace_csv = $true
            }
            $planned += $startStep
            if ($trial.name -eq 'trace-powered') {
                $planned += @{name='trace-powered-off';command='input-off';args=@()}
                $planned += @{name='trace-powered-final-state';command='input-state';args=@()}
            }
        }
    }
    if ('cr-led' -in $selectedModes) {
        $ledScope = @('--hardware-validation', 'cr-led')
        # Both resistance and Vdrop are fixed for this Input OFF trial. The
        # native Core path asks the instrument for its Vdrop maximum.
        $cr = @('--resistance', '50', '--range', 'auto') + $ledScope
        $led = $cr + @('--vdrop', '1')
        $planned += @(
            @{name='cr-led-set';command='set-cr-led';args=$led},
            @{name='cr-led-status-on';command='cr-led-status';args=$ledScope;led=$true;fixed_cr=$true},
            @{name='cr-led-plain-cr';command='set-cr';args=$cr},
            @{name='cr-led-status-off';command='cr-led-status';args=$ledScope;led=$false;fixed_cr=$true},
            @{name='cr-led-set-again';command='set-cr-led';args=$led},
            @{name='cr-led-leave-for-cc';command='set-cc';args=($lowArgs + $ledScope)},
            @{name='cr-led-status-after-cc';command='cr-led-status';args=$ledScope;led=$false;fixed_cr=$false},
            @{name='cr-led-final-input-state';command='input-state';args=@()}
        )
    }
    if ('current-slow-rise' -in $selectedModes) {
        $slowScope = @('--hardware-validation', 'current-slow-rise')
        $slowCc = $lowArgs + $slowScope
        $planned += @(
            @{name='slow-rise-set-cc';command='set-cc';args=$slowCc},
            @{name='slow-rise-enable';command='current-slow-rise-enable';args=$slowScope;slow=$true},
            @{name='slow-rise-explicit-input-off';command='input-off';args=@()},
            @{name='slow-rise-status-after-off';command='current-slow-rise-status';args=$slowScope;slow=$true},
            @{name='slow-rise-repeat-cc';command='set-cc';args=$slowCc},
            @{name='slow-rise-status-after-cc';command='current-slow-rise-status';args=$slowScope;slow=$true},
            @{name='slow-rise-block-cv';command='set-cv';expected_error='current_slow_rise_enabled';
              args=@('--voltage','0.1','--range','low','--hardware-validation','cv')},
            @{name='slow-rise-disable';command='current-slow-rise-disable';args=$slowScope;slow=$false},
            @{name='slow-rise-status-disabled';command='current-slow-rise-status';args=$slowScope;slow=$false},
            @{name='slow-rise-final-input-state';command='input-state';args=@()}
        )
        $slowRiseCleanup = @(
            @{name='cleanup-slow-rise-input-state';command='input-state';args=@();
              condition='Slow Rise Enable attempted, normal OFF readback incomplete, and communication trusted'},
            @{name='cleanup-slow-rise-disable';command='current-slow-rise-disable';args=$slowScope;
              condition='Cleanup Input OFF confirmed'},
            @{name='cleanup-slow-rise-status';command='current-slow-rise-status';args=$slowScope;
              condition='Cleanup Disable confirmed with Input OFF'}
        )
    }
    if ($triggerSelected) { New-TriggerPlan }
    foreach ($step in ($planned | Where-Object { $_.ContainsKey('cr_handoff_after') })) {
        $step.condition = "$($step.cr_handoff_after) refused a resistance origin before remote"
    }
    $allSteps = $planned + $slowRiseCleanup + $triggerPlans
    $report.results = @($allSteps | ForEach-Object {
        $entry = [ordered]@{name=$_.name;command=$_.command;arguments=$_.args;status='NOT RUN'}
        if ($_.ContainsKey('expected_error')) { $entry['expected_error'] = $_.expected_error }
        if ($_.ContainsKey('condition')) { $entry['condition'] = $_.condition }
        $entry
    })
    foreach ($step in $allSteps) {
        if ($step.command -eq 'operator-observation') { continue }
        $extras = @($step.args)
        $plan = Invoke-PlanCommand $step.command $extras
        if ($step.command -in @('set-cc', 'set-cv', 'set-cr', 'set-cr-led',
                                'set-transient-cc', 'set-transient-cv', 'set-transient-cr') -and 'auto' -in $step.args) {
            # The Core plan owns selection; the script only compares later physical readback.
            $resolved = $(if ($step.command -eq 'set-cc') { $plan.result.range } else {
                $plan.result.expected_configuration.range
            })
            if ($resolved -cnotin @('low', 'high')) { throw 'Auto plan did not resolve a Range.' }
            $step.expected_range = $resolved
            $entry = $report.results | Where-Object { $_.name -eq $step.name } | Select-Object -First 1
            $entry['expected_range'] = $resolved
        }
        $report.plans += $plan
    }
    $report.prechecks.plan = 'PASS'
    $reportDirectory = Join-Path $repo ".tmp_tests/$traceRunName"
    [void][IO.Directory]::CreateDirectory($reportDirectory)
    Write-Host "ElectronicLoad Tool - CLI Validation"
    Write-Host "Target: $Target | Connection: $Connection | Backend: $Backend | Resource: <redacted>"
    Write-Host "Mode: $(if ($PlanOnly) { 'PLAN ONLY' } else { 'LIVE' })"
    Write-Host "Selected modes: $($selectedModes -join ', ')."
    Write-Host "Plan: Identify, Input State, Measure."
    if ($traceSelected) {
        Write-Host 'TRACE: four Input OFF batches and one bounded Fixed CC ON batch with explicit OFF.'
        Write-Host "TRACE Start process deadline: $traceStartDeadlineMs ms. No auto reconnect."
    }
    if ('cc' -in $selectedModes -or $traceSelected) {
        Write-Host 'CC setup from CR: conditional contributor handoff with Input OFF and the Core LED OFF interlock.'
    }
    if (@($selectedModes | Where-Object { $_ -ne 'trace' -and $_ -notin $featureModes }).Count -gt 0) {
        Write-Host 'Other selected modes retain explicit ON/OFF and independent OFF confirmation.'
    }
    if ($featureSelected) {
        Write-Host 'CR-LED and Current Slow Rise trials keep Input OFF and verify configuration readback.'
    }
    if ($triggerSelected) {
        Write-Host 'Trigger: five source trials with manual observation, explicit OFF and restoration of original Source and Timer.'
        Write-Host "Each Trigger observation is $TriggerObservationSeconds s. The Input ON/OFF budget is $TriggerMaxOnSeconds s per trial."
    }
    if ('current-slow-rise' -in $selectedModes) {
        Write-Host 'Slow Rise requires a non-resistance starting function and expects Fixed CV setup to be refused while enabled.'
        Write-Host 'If the Slow Rise trial stops before confirmed Disable, cleanup requires trusted communication and confirmed Input OFF.'
    }
    Write-Host 'Fixed Auto adds Input OFF setup/readback with the existing values; no extra ON/OFF cycles.'
    if (@($selectedModes | Where-Object { $_.StartsWith('transient-') }).Count -gt 0) {
        Write-Host 'Transient: Continuous/Pulse/Toggle, Low/High/Auto, repeated triggers, control status and separate disable.'
        Write-Host 'Trigger/status/measure do not establish waveform shape, timing or completion.'
    }
    Write-Host "External source (operator-managed): $SourceVoltageLimitV V / $SourceCurrentLimitA A current limit."
    Write-Host 'The script does not configure, measure or verify the external source.'
    foreach ($step in $allSteps) {
        $condition = $(if ($step.condition) { " [if $($step.condition)]" } else { '' })
        Write-Verbose ("  {0}: {1} {2}{3}" -f $step.name, $step.command, ($step.args -join ' '), $condition)
    }
    if ('cc' -in $selectedModes) { Write-Host "CC Current=$currentText A." }
    Write-Host "Script ceiling=$($report.script_current_ceiling_a) A; USB System VISA."
    Write-Host 'No reset, SYST:ERR? consumption, automatic local switch, or implicit Input ON.'
    Write-Host 'An Input ON attempt requires an explicit OFF cleanup; session close does not turn Input OFF.'
    Write-Host 'V/I/P measure queries are separate; power freshness is not guaranteed.'
    if ($PlanOnly) {
        $report.status = 'PLAN ONLY'
        Save-Report
        Show-Summary
        Write-Host 'PLAN ONLY COMPLETE: no hardware accessed.'
        exit 0
    }
    if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) {
        $report.status = 'BLOCKED'
        Save-Report
        Show-Summary
        Write-Host 'BLOCKED: controlled LIVE validation requires an interactive console.'
        exit 2
    }
    if ($InputTimerOnly) {
        & (Join-Path $PSScriptRoot 'preflight-cli.ps1') -InputTimerOnly
    } elseif ($selectedModes.Count -eq 1 -and $traceSelected) {
        & (Join-Path $PSScriptRoot 'preflight-cli.ps1') -TraceOnly
    } elseif ($selectedModes.Count -eq 1 -and $triggerSelected) {
        & (Join-Path $PSScriptRoot 'preflight-cli.ps1') -TriggerOnly
    } elseif ($featureOnly) {
        & (Join-Path $PSScriptRoot 'preflight-cli.ps1') -CrLedSlowRiseOnly
    } else {
        & (Join-Path $PSScriptRoot 'preflight-cli.ps1')
    }
    if ($LASTEXITCODE -ne 0) {
        $report.prechecks.preflight = 'FAIL'
        throw 'Preflight failed.'
    }
    $report.prechecks.preflight = 'PASS'
    Write-Host "Operator resource check (LIVE): Target: $Target; Resource: $Resource; Backend: $Backend"
    Write-Host 'Physical checks before pressing Enter:'
    Write-Host "- Confirm the external source is manually set to $SourceVoltageLimitV V with a $SourceCurrentLimitA A current limit."
    Write-Host '- Confirm physical load Input OFF, safe wiring/DUT and tolerance of every selected setting (use -Verbose or the report).'
    Write-Host '- Confirm no other application controls this instrument and an independent manual Input OFF procedure is available.'
    if ($triggerSelected) {
        Write-Host '- Confirm external On/Off and analog programming are disabled and external trigger wiring was reviewed while de-energized.'
    }
    Write-Host '- Selected trials may deliberately turn Input ON; the script explicitly commands and verifies OFF, but cannot guarantee OFF after communication failures.'
    $ack = Read-Host 'After completing these checks, press Enter to run selected CLI cases (Ctrl+C to cancel)'
    if ($ack -cne '' -or ($triggerSelected -and $null -eq $ack)) {
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
    foreach ($step in ($planned | Select-Object -Skip 1)) {
        if ($step.ContainsKey('cr_handoff_after')) {
            $origin = $report.results | Where-Object { $_.name -eq $step.cr_handoff_after } | Select-Object -First 1
            if (-not $origin.Contains('origin_function')) {
                $entry = $report.results | Where-Object { $_.name -eq $step.name } | Select-Object -First 1
                $entry.status = 'SKIP'
                Write-Host "SKIP $($step.name): regular CC setup succeeded."
                continue
            }
        }
        Write-Host "Running $($step.name)..."
        # Mark uncertainty before dispatch, including subprocess/JSON/runtime failures.
        if ($step.command -eq 'input-on') { $inputOnAttempted = $true }
        if ($step.name -eq 'timer-on') { $timerOnStartMs = $watch.ElapsedMilliseconds }
        if ($step.command -eq 'input-timer-enable') { $timerEnableAttempted = $true }
        if ($step.command -eq 'current-slow-rise-enable') { $slowRiseEnableAttempted = $true }
        if ($step.name -eq 'timer-expiry') {
            # Wait from before ON dispatch; never infer OFF from elapsed time.
            $waitMs = ([int]$TimerSeconds + 1) * 1000 - ($watch.ElapsedMilliseconds - $timerOnStartMs)
            if ($waitMs -gt 0) { Start-Sleep -Milliseconds $waitMs }
        }
        if ($step.command -eq 'transient-enable') {
            $activeTransientScope = $step.args[[Array]::IndexOf($step.args, '--hardware-validation') + 1]
            $transientEnableAttempted = $true
        }
        $commandDeadline = $(if ($traceSelected -and $step.command -eq 'trace-start') {
            $traceStartDeadlineMs
        } else { $perCommandDeadlineMs })
        $result = Invoke-LiveCommand -Command $step.command -CaseName $step.name `
            -ExtraArgs $step.args -DeadlineMs $commandDeadline -ExpectedError $step.expected_error `
            -AllowCrHandoff:($step.allow_cr_handoff -eq $true)
        if ($step.allow_cr_handoff -and $result.ok -eq $false) { continue }
        if ($step.ContainsKey('expected_error')) {
            Write-Host "PASS $($step.name): confirmed refusal ($($step.expected_error))."
            continue
        }
        Assert-Runtime $result $step.name
        if ($step.ContainsKey('trace_expected')) { Assert-TraceResult $result $step }
        if ($step.ContainsKey('led') -and
            ($result.result.led_enabled -cne $step.led -or $result.result.fixed_cr -cne $step.fixed_cr -or
             [decimal]$result.result.vdrop_v -ne 1 -or $result.result.resistance_ohm -ne 50 -or
             $result.result.range -cne 'high')) {
            Fail-Case $step.name 'readback_mismatch' 'CR-LED status differs from the expected settings.'
        }
        if ($step.ContainsKey('slow') -and
            ($result.result.enabled -isnot [bool] -or $result.result.enabled -cne $step.slow)) {
            Fail-Case $step.name 'readback_mismatch' 'Current Slow Rise state differs from the expected setting.'
        }
        if ($InputTimerOnly -and $step.command -in @('set-input-timer', 'input-timer-enable', 'input-timer-disable')) {
            Assert-InputOff $result $step.name
        }
        if ($step.command -eq 'input-timer-status') {
            if ($result.result.seconds -ne [int]$TimerSeconds -or
                ($step.name -eq 'timer-status-off' -and $result.result.enabled -ne $true) -or
                ($step.name -eq 'timer-status-disabled' -and $result.result.enabled -ne $false)) {
                Fail-Case $step.name 'readback_mismatch' 'Timer status did not match the configured settings.'
            }
            # Enabled after expiry is reported as observed, without assuming retention.
            Assert-InputOff $result $step.name
        }
        if ($step.name -eq 'input-state' -and $result.result.input_state -cne 'OFF') {
            $report.prechecks.input_off = 'BLOCKED'
            $report.status = 'BLOCKED'
        }
        if ($step.command -in @('input-state', 'input-off', 'set-cc', 'set-cv', 'set-cr', 'set-cr-led', 'set-cw',
                               'current-slow-rise-enable', 'current-slow-rise-disable',
                               'set-transient-cc', 'set-transient-cv', 'set-transient-cr', 'set-transient-cw',
                               'transient-enable', 'transient-disable')) {
            Assert-InputOff $result $step.name
        }
        if ($step.command -eq 'input-on' -and $result.result.input_state -cne 'ON') {
            Fail-Case $step.name 'input_on_not_confirmed' 'Input ON was not confirmed.'
        }
        $observedRange = $(if ($null -ne $result.result.range) { $result.result.range } else {
            $result.result.configuration.range
        })
        if ($step.ContainsKey('expected_range') -and $observedRange -cne $step.expected_range) {
            Fail-Case $step.name 'readback_mismatch' 'Auto Range differs from the Core plan.'
        }
        if ($step.command -eq 'transient-status') {
            $expectedOn = $step.name.EndsWith('-status-on')
            $expectedState = $(if ($expectedOn) { 'ON' } else { 'OFF' })
            if ($result.result.enabled -ne $expectedOn -or $result.result.input_state -cne $expectedState) {
                Fail-Case $step.name 'readback_mismatch' 'Transient/Input control status did not match.'
            }
        }
        if ($step.command -eq 'input-off') { $inputOnAttempted = $false }
        if ($step.command -eq 'input-timer-disable') { $timerEnableAttempted = $false }
        if ($step.command -eq 'transient-disable') { $transientEnableAttempted = $false }
        if ($step.name -eq 'slow-rise-status-disabled') { $slowRiseEnableAttempted = $false }
        if ($step.name -eq 'input-state') { $report.prechecks.input_off = 'PASS' }
        Write-Host "PASS $($step.name)"
    }

    foreach ($step in $slowRiseCleanup) {
        $entry = $report.results | Where-Object { $_.name -eq $step.name } | Select-Object -First 1
        $entry.status = 'SKIP'
    }
    if ($triggerSelected) { Invoke-TriggerValidation }
    $report.status = 'PASS'
    Save-Report
    Show-Summary
    Write-Host 'PASS: one-run CLI LIVE validation.'
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
    # PowerShell pipeline interruption can bypass catch and the exit after finally.
    $triggerInterrupted = $triggerSelected -and $phase -in @('identify','cases') -and $report.status -eq 'NOT RUN'
    if ($triggerInterrupted) { $report.status = 'FAIL' }
    if ($inputOnAttempted) {
        $report.results += [ordered]@{
            name = 'cleanup-input-off'; command = 'input-off'; status = 'NOT RUN'
        }
        if ($traceCommunicationUntrusted) {
            $report.status = 'FAIL'
            $report.trace_safe_shutdown_unconfirmed = $true
            Write-Host 'WARNING: TRACE VISA untrusted: no new Session or blind OFF cleanup; independent operator action required.'
        } else { try {
            Write-Host 'Input ON was attempted: explicitly commanding OFF cleanup...'
            $cleanup = Invoke-LiveCommand -Command 'input-off' -CaseName 'cleanup-input-off' -Cleanup
            Assert-Runtime $cleanup 'cleanup-input-off'
            Assert-InputOff $cleanup 'cleanup-input-off'
            $inputOnAttempted = $false
            if ($traceSelected -and -not $traceFeedStopUnconfirmed) {
                $report.trace_safe_shutdown_unconfirmed = $false
            }
            Write-Host 'PASS Explicit Input OFF cleanup'
        } catch {
            $report.status = 'FAIL'
            if ($traceSelected) { $report.trace_safe_shutdown_unconfirmed = $true }
            Write-Host 'WARNING: physical Input OFF was not confirmed. Immediate operator action is required.'
        }
        if ($report.status -eq 'PASS') { $report.status = 'FAIL' }
        }
    }
    if ($traceCommunicationUntrusted) {
        $report.status = 'FAIL'
        $report.trace_safe_shutdown_unconfirmed = $true
        Write-Host 'WARNING: TRACE VISA state untrusted; further I/O forbidden. Use independent manual safety procedure.'
    }
    if ($timerEnableAttempted) {
        $report.results += [ordered]@{name='cleanup-timer-state';command='input-state';status='NOT RUN'}
        $report.results += [ordered]@{name='cleanup-timer-disable';command='input-timer-disable';status='NOT RUN'}
        try {
            $state = Invoke-LiveCommand -Command 'input-state' -CaseName 'cleanup-timer-state' -Cleanup
            Assert-Runtime $state 'cleanup-timer-state'
            Assert-InputOff $state 'cleanup-timer-state'
            $disabled = Invoke-LiveCommand -Command 'input-timer-disable' -CaseName 'cleanup-timer-disable' `
                -ExtraArgs @('--hardware-validation','input-timer') -Cleanup
            Assert-Runtime $disabled 'cleanup-timer-disable'
            Assert-InputOff $disabled 'cleanup-timer-disable'
        } catch {
            $report.status = 'FAIL'
            Write-Host 'WARNING: Timer disable cleanup was not confirmed. Operator action is required.'
        }
        if ($report.status -eq 'PASS') { $report.status = 'FAIL' }
    }
    if ($transientEnableAttempted) {
        # Disable is separate from OFF. Never attempt it with unconfirmed Input state.
        $report.results += [ordered]@{name='cleanup-input-state';command='input-state';status='NOT RUN'}
        $report.results += [ordered]@{name='cleanup-transient-disable';command='transient-disable';status='NOT RUN'}
        try {
            $state = Invoke-LiveCommand -Command 'input-state' -CaseName 'cleanup-input-state' -Cleanup
            Assert-Runtime $state 'cleanup-input-state'
            Assert-InputOff $state 'cleanup-input-state'
            $disabled = Invoke-LiveCommand -Command 'transient-disable' -CaseName 'cleanup-transient-disable' `
                -ExtraArgs @('--hardware-validation',$activeTransientScope) -Cleanup
            Assert-Runtime $disabled 'cleanup-transient-disable'
            Assert-InputOff $disabled 'cleanup-transient-disable'
            Write-Host 'PASS Explicit Transient Disable cleanup after confirmed OFF'
        } catch {
            $report.status = 'FAIL'
            Write-Host 'WARNING: Transient disable cleanup was not confirmed. Operator action is required.'
        }
        if ($report.status -eq 'PASS') { $report.status = 'FAIL' }
    }
    if ($slowRiseEnableAttempted) {
        $report.status = 'FAIL'
        $report.slow_rise_cleanup_unconfirmed = $true
        if ($slowRiseCommunicationTrusted -and -not $traceCommunicationUntrusted) {
            try {
                foreach ($step in $slowRiseCleanup) {
                    $result = Invoke-LiveCommand -Command $step.command -CaseName $step.name `
                        -ExtraArgs $step.args -Cleanup
                    Assert-Runtime $result $step.name
                    if ($step.command -ne 'current-slow-rise-status') { Assert-InputOff $result $step.name }
                    if ($step.command -ne 'input-state' -and
                        ($result.result.enabled -isnot [bool] -or $result.result.enabled -ne $false)) {
                        Fail-Case $step.name 'readback_mismatch' 'Current Slow Rise OFF was not confirmed.'
                    }
                }
                $report.slow_rise_cleanup_unconfirmed = $false
                Write-Host 'PASS Explicit Current Slow Rise Disable cleanup after confirmed Input OFF.'
            } catch {
                Write-Host 'Current Slow Rise cleanup stopped at a failed check.'
            }
        } else {
            Write-Host 'Current Slow Rise cleanup skipped because communication is untrusted.'
        }
        if ($report.slow_rise_cleanup_unconfirmed) {
            Write-Host 'WARNING: Current Slow Rise OFF was not confirmed. Independent operator action is required.'
        }
    }
    Save-Report
    if ($null -ne $reportDirectory) {
        if ($report.status -ne 'PASS' -and $report.status -ne 'PLAN ONLY') { Show-Summary }
        Write-Host "Report: $reportDirectory/shareable/report.json"
        if ($DebugArtifacts) { Write-Host "Private debug logs: $reportDirectory/private (review before sharing)" }
    }
    if ($triggerInterrupted) {
        # A stopped -File pipeline can discard a script-level exit. Exit only
        # the dedicated file host, and only after cleanup and report writes.
        if ([string]::IsNullOrEmpty($MyInvocation.Line) -and $MyInvocation.InvocationName -eq $PSCommandPath) {
            [Environment]::Exit(3)
        }
        $global:LASTEXITCODE = 3
        exit 3
    }
}
if ($report.status -eq 'PASS' -or $report.status -eq 'PLAN ONLY') { exit 0 }
if ($report.status -eq 'BLOCKED') { exit 2 }
exit 3
