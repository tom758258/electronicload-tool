<#
Single, bounded IT8812C CLI hardware validation.
Default: the established validation modes, with common read-only safety prechecks.
-Modes selects any supported validation mode, including trace, cr-led, current-slow-rise,
trigger and input-timer. Omitted -Modes selects all default modes.
-Modes protection selects the separate Protection validation procedure.
-Modes sequence selects four bounded Fixed Software Sequence trials.
-Modes readback checks existing settings with Input OFF and without changing the instrument.
-Modes full (default) selects all trials. USB and ASRL use the same matrix.
-Modes workflow runs only dry-run and simulation, without an instrument.
-Modes measurement-extensions,auxiliary-controls,list-cc selects offline checks.
-LiveExtensions explicitly requests physical cases for those four modes.
-InputTimerOnly is a backward-compatible shortcut for the input-timer-only trial.
Requires approved source/DUT limits and interactive confirmation before hardware access.
-PlanOnly validates every planned CLI request without VISA or hardware access.
#>
[CmdletBinding()]
param(
    [string]$Target,
    [ValidateSet('usb', 'asrl')][string]$Connection = 'usb',
    [string]$Resource,
    [ValidateSet('system')][string]$Backend = 'system',
    [string]$Current = '0.1',
    [string[]]$Modes = @(),
    [switch]$LiveExtensions,
    [ValidateRange(1,7)][int]$ListScratchSlot = 1,
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
    [int]$TriggerObservationSeconds = 15,
    [int]$TriggerMaxOnSeconds = 60,
    [string]$TriggerIntervalSeconds = '5',
    [string]$TriggerListWidthA = '2',
    [string]$TriggerListWidthB = '2',
    [int]$TriggerListRepeat = 1,
    [string]$TriggerExternalSetup,
    [switch]$PlanOnly,
    [switch]$DebugArtifacts,
    [decimal]$ExpectedRiseSlew,
    [decimal]$ExpectedFallSlew,
    [decimal]$ExpectedVonThreshold,
    [ValidateSet('on','off')][string]$ExpectedVonLatch,
    [ValidateSet('on','off')][string]$ExpectedShort,
    [ValidateRange(2,16)][int]$ExpectedAverageCount,
    [string]$SerialBaudRate,
    [string]$SerialDataBits,
    [string]$SerialParity,
    [string]$SerialStopBits,
    [string]$SerialFlowControl,
    [string]$SerialReadTermination,
    [string]$SerialWriteTermination
)

$ErrorActionPreference = 'Stop'
$featureModes = @('cr-led', 'current-slow-rise')
$offlineModes = @('measurement-extensions', 'auxiliary-controls', 'list-cc')
$supportedModes = @('cc', 'cv', 'cr', 'cw', 'transient-cc', 'transient-cv', 'transient-cr', 'transient-cw',
                    'trace') + $featureModes + @('trigger', 'input-timer', 'protection', 'system-query',
                                              'readback', 'sequence', 'workflow') + $offlineModes
if ($InputTimerOnly) {
    if ($PSBoundParameters.ContainsKey('Modes')) { throw 'InputTimerOnly cannot be combined with Modes.' }
    $Modes = @('input-timer')
} elseif (-not $PSBoundParameters.ContainsKey('Modes')) {
    $Modes = @('full')
}
$requestedModes = @($Modes | ForEach-Object { $_ -split ',' } |
    ForEach-Object { $_.Trim().ToLowerInvariant() })
# Keep the established trial order and run each selected mode only once.
$selectedModes = @($supportedModes | Where-Object { $_ -in $requestedModes -or 'full' -in $requestedModes })
$hasValidationModes = @($selectedModes | Where-Object { $_ -ne 'cc' }).Count -gt 0
$traceSelected = 'trace' -in $selectedModes
$triggerSelected = 'trigger' -in $selectedModes
$timerSelected = 'input-timer' -in $selectedModes
$protectionSelected = 'protection' -in $selectedModes
$readbackSelected = 'readback' -in $selectedModes
$sequenceSelected = 'sequence' -in $selectedModes
$asrlSelected = $Connection -eq 'asrl'
$workflowSelected = 'workflow' -in $selectedModes
$extensionModes = $offlineModes + 'workflow'
$offlineOnly = -not $LiveExtensions -and @($selectedModes | Where-Object { $_ -notin $extensionModes }).Count -eq 0
$systemQuerySelected = 'system-query' -in $selectedModes
$serialFlags = [ordered]@{
    SerialBaudRate = '--serial-baud-rate'; SerialDataBits = '--serial-data-bits'
    SerialParity = '--serial-parity'; SerialStopBits = '--serial-stop-bits'
    SerialFlowControl = '--serial-flow-control'; SerialReadTermination = '--serial-read-termination'
    SerialWriteTermination = '--serial-write-termination'
}
$serialOverrides = [ordered]@{}
$serialArgs = @(foreach ($parameter in $serialFlags.Keys) {
    if ($PSBoundParameters.ContainsKey($parameter)) {
        $flag = $serialFlags[$parameter]
        $value = $PSBoundParameters[$parameter]
        $serialOverrides[($flag -replace '^--serial-', '' -replace '-', '_')] = $value
        $flag
        $value
    }
})
$connectionArgs = @('--backend', $Backend, '--model', $Target)
if ($Resource) { $connectionArgs += @('--resource', $Resource) }
if ($asrlSelected) { $connectionArgs += @('--validation-connection', 'asrl') + $serialArgs }
$featureSelected = @($selectedModes | Where-Object { $_ -in $featureModes }).Count -gt 0
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
$timerOnConfirmedMs = 0
$timerOnTiming = $null
$timerPowered = $false
$timerProcessStillRunning = $false
$timerArtifacts = @()
$timerFailure = $null
$timerExpiryEvidence = $null
$slowRiseEnableAttempted = $false
$slowRiseCommunicationTrusted = $false
$slowRiseCleanup = @()
$triggerScope = @('--hardware-validation', 'trigger')
$readbackScope = @('--hardware-validation', 'readback')
# The Trigger pulse needs an observable preset; the legacy Transient default is unchanged.
$script:triggerPulseWidth = $(if ($PSBoundParameters.ContainsKey('PulseWidth')) { $PulseWidth } else { '2' })
$script:originalTriggerSnapshot = $null
$script:triggerSourceMayHaveChanged = $false
$triggerTrials = @()
$triggerPlans = @()
$triggerTrusted = $true
$triggerOffConfirmed = $true
$triggerPowered = $false
$triggerProcessStillRunning = $false
$triggerArtifacts = @()
$protectionScope = @('--hardware-validation', 'protection')
$protectionPlans = @()
$protectionTrusted = $true
$protectionPowered = $false
$protectionArtifacts = @()
$protectionTrials = @(
    @{name='software-current'; settings=[ordered]@{software_current_enabled='on';software_current_limit='0.10';software_current_delay='1'}; flags=@('OC','PS')},
    @{name='software-power'; settings=[ordered]@{software_power_limit='0.50';software_power_delay='1'}; flags=@('OP','PS')},
    @{name='hardware-power'; settings=[ordered]@{hardware_power_limit='0.50'}; flags=@()}
)
$sequenceDocuments = $null
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
    validation_mode = $(if ($PlanOnly) { 'plan-only' } elseif ($offlineOnly) { 'simulate' } else { 'live' })
    product_live = 'IT8812C USB/System VISA read-only and fixed CC/Input LIVE open'
    per_command_deadline_ms = $perCommandDeadlineMs
    total_deadline_ms = $totalDeadlineMs
    script_current_ceiling_a = $null
    includes_validation_modes = $hasValidationModes
    declared_source_limits = $null
    results = @()
}

if ($sequenceSelected) {
    $report['sequence_input_off_unconfirmed'] = $false
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
if ($protectionSelected) {
    $report['protection_input_off_unconfirmed'] = $false
    $report['protection_restore_unconfirmed'] = $false
    $report['protection_trials'] = [ordered]@{}
    foreach ($trial in $protectionTrials) { $report.protection_trials[$trial.name] = 'NOT RUN' }
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
        if ($entry.Contains('execution')) { $case.execution = $entry.execution }
        if ($entry.Contains('arguments')) {
            $safeArguments = @($entry.arguments)
            if ($script:traceSelected -and $entry.command -eq 'trace-start') {
                $csvIndex = [Array]::IndexOf($safeArguments, '--csv')
                if ($csvIndex -ge 0 -and $csvIndex + 1 -lt $safeArguments.Count) {
                    $safeArguments[$csvIndex + 1] = '<redacted>'
                }
            }
            if ($entry.command -ceq 'sequence') {
                $fileIndex = [Array]::IndexOf($safeArguments, '--file')
                $safeArguments[$fileIndex + 1] = '<redacted>'
            }
            $case.arguments = $safeArguments
        }
        foreach ($field in @('sequence_plan','sequence_evidence')) {
            if ($entry.Contains($field)) { $case[$field] = $entry[$field] }
        }
        if ($entry.Contains('error_code')) { $case.error_code = $entry.error_code }
        if ($entry.Contains('manual_action_required')) { $case.manual_action_required = $entry.manual_action_required }
        if ($entry.Contains('expected_error')) { $case.expected_error = $entry.expected_error }
        if ($entry.Contains('expected_range')) { $case.expected_range = $entry.expected_range }
        if ($entry.Contains('expected_readback')) { $case.expected_readback = $entry.expected_readback }
        if ($entry.Contains('readback_comparison')) { $case.readback_comparison = $entry.readback_comparison }
        if ($entry.Contains('condition')) { $case.condition = $entry.condition }
        foreach ($field in @('queries','planned_writes')) {
            if ($entry.Contains($field)) { $case[$field] = $entry[$field] }
        }
        if ($entry.Contains('origin_function')) { $case.origin_function = $entry.origin_function }
        foreach ($field in @('trace_source','trace_points','trace_interval_s','trace_sample_count',
                             'trace_csv_complete','operator_observed','trigger_source','trigger_interval_s',
                             'trigger_source_restore','trigger_interval_restore','list_selected')) {
            if ($entry.Contains($field)) { $case[$field] = $entry[$field] }
        }
        if ($entry.Contains('confirmed_input_state')) { $case.input_state = $entry.confirmed_input_state }
        foreach ($field in @('protection_state','protection_condition','protection_events',
                             'protection_settings','protection_fixed_cc','protection_measurements',
                             'protection_write_progress')) {
            if ($entry.Contains($field)) { $case[$field] = $entry[$field] }
        }
        if ($entry.status -eq 'PASS' -or $entry.readback_comparison -in @('OBSERVED ONLY','MISMATCH') -or
            ($entry.name -ceq 'timer-expiry' -and $null -ne $entry.response)) {
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
                                  'fixed_cr', 'led_enabled', 'vdrop_v', 'vdrop_max_v', 'resistance_ohm',
                                  'scpi_version', 'display_mode', 'stop_reason', 'sample_count')) {
                if ($null -ne $entry.response.result.$field) {
                    $case[$field] = $entry.response.result.$field
                }
            }
            if ($entry.command -in @('readback','fixed-status','fixed-slew-status','von-status',
                                     'short-status','average-status','trace-status','list-status','transient-status')) {
                foreach ($field in @('input_state','active_mode','function','range','current_a','voltage_v',
                                     'power_w','rise_slew_a_per_us','fall_slew_a_per_us','threshold_v',
                                     'latch','short_state','average_count','source','points','interval_s',
                                     'delay_s','filter_enabled','feed','selected')) {
                    if ($null -ne $entry.response.result.$field) {
                        $case[$field] = $entry.response.result.$field
                    }
                }
            }
            if ($entry.execution -ceq 'live' -and $entry.command -cin @('cc-timed-load','cc-discharge','contributor-settings')) {
                foreach ($field in @('input_state','cleanup','on_write_started_ns','off_confirmed_ns','duration_s',
                                     'current_a','measured_current_a','capacity_ah','trial','restored','short_state',
                                     'short_on_confirmed','saved_slot','a_width_s','b_width_s','recall_confirmed','exited_list')) {
                    if ($null -ne $entry.response.result.$field) { $case[$field] = $entry.response.result.$field }
                }
            }
            if ($entry.command -eq 'scpi-error-next') {
                $case.scpi_error_code = $entry.response.result.error_code
            }
            if ($entry.execution -eq 'simulate' -and $entry.response.result.input_state) {
                $case.input_state = $entry.response.result.input_state
            }
            if ($entry.command -eq 'measure') {
                $case.values = [ordered]@{
                    voltage_v = $entry.response.result.voltage_v
                    current_a = $entry.response.result.current_a
                    power_w = $entry.response.result.power_w
                }
            }
            if ($entry.execution -ceq 'live') {
                foreach ($field in @('voltage_min_v','voltage_max_v','current_min_a','current_max_a',
                                     'capacity_ah','discharge_time_s','average_count','last_measurement')) {
                    if ($null -ne $entry.response.result.$field) {
                        $case[$field] = $entry.response.result.$field
                    }
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
        modes = $script:selectedModes
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
    if ($script:asrlSelected -and $script:serialOverrides.Count) {
        $public['serial_overrides'] = $script:serialOverrides
    }
    if ($script:sequenceSelected) {
        $public['sequence_input_off_unconfirmed'] = $script:report.sequence_input_off_unconfirmed
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
    if ($script:protectionSelected) {
        $public['protection_input_off_unconfirmed'] = $script:report.protection_input_off_unconfirmed
        $public['protection_restore_unconfirmed'] = $script:report.protection_restore_unconfirmed
        $public['protection_trials'] = $script:report.protection_trials
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
    if ($script:asrlSelected) {
        $lines += "Connection: $($public.connection) / $($public.backend)"
        $lines += "Product LIVE: $($public.product_live)"
    }
    if ($public.slow_rise_cleanup_unconfirmed) {
        $lines += 'Current Slow Rise OFF was not confirmed. Independent operator action is required.'
    }
    if ($public.trigger_input_off_unconfirmed) {
        $lines += 'Trigger Input OFF was not confirmed. Use the independent manual OFF procedure immediately.'
    }
    if ($public.protection_input_off_unconfirmed) {
        $lines += 'Protection Input OFF is unconfirmed. Use the independent manual OFF procedure immediately.'
    }
    if ($public.protection_restore_unconfirmed) {
        $lines += 'Protection restoration is unconfirmed. Verify the original settings manually after confirming Input OFF.'
    }
    if ($script:protectionSelected) {
        foreach ($trial in $public.protection_trials.Keys) {
            $lines += "Protection ${trial}: $($public.protection_trials[$trial])"
        }
    }
    foreach ($case in $cases) {
        $reason = if ($case.status -ceq 'NOT RUN' -and $case.error_code) { " ($($case.error_code))" } else { '' }
        $lines += "$($case.name): $($case.status)$reason"
    }
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

function New-LiveCliProcess([string[]]$Arguments) {
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = New-CliProcessInfo $Arguments
    $process.StartInfo.RedirectStandardInput = $Arguments[1].Contains('contributor_stop=')
    return $process
}

function Invoke-OfflineProcess([string[]]$Arguments) {
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = New-CliProcessInfo $arguments
    $started = $false
    try {
        [void]$process.Start()
        $started = $true
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($script:perCommandDeadlineMs)) { throw 'Offline command timed out.' }
        return @{
            exit_code=$process.ExitCode
            stdout=$stdout.GetAwaiter().GetResult()
            stderr=$stderr.GetAwaiter().GetResult()
        }
    } finally {
        if ($started -and -not $process.HasExited) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
        }
        $process.Dispose()
    }
}

function Invoke-OfflineCommand([string]$Command, [string[]]$ExtraArgs = @(), [switch]$NoConnection, [switch]$Simulate) {
    $mode = if ($Simulate) { '--simulate' } else { '--dry-run' }
    $arguments = @('-c', 'from electronicload_tool_cli.cli import main; raise SystemExit(main())',
        $Command, $mode, '--json') + $ExtraArgs
    if ($NoConnection) { $arguments += @('--model', $Target) }
    else { $arguments += $script:connectionArgs }
    $execution = Invoke-OfflineProcess $arguments
    $plan = $execution.stdout | ConvertFrom-Json -ErrorAction Stop
    if ($execution.exit_code -ne 0 -or $plan.ok -ne $true) {
        throw "Offline command failed for $Command ($($plan.error.code))."
    }
    return $plan
}

function Invoke-OfflineTests($Step, $Entry) {
    $base = Join-Path $script:reportDirectory "$($Step.name)-pytest"
    $resultsFile = Join-Path $base 'results.xml'
    # JUnit records results for selected tests only. Check the complete collection before running it.
    $launcher = @'
import pytest


class CompleteOfflineSelection:
    def __init__(self):
        self.collected = set()

    def pytest_itemcollected(self, item):
        self.collected.add(item.nodeid)

    @pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session):
        excluded = self.collected.difference(item.nodeid for item in session.items)
        if excluded:
            pytest.exit(
                f'{len(excluded)} tests excluded from the offline test selection',
                returncode=pytest.ExitCode.NO_TESTS_COLLECTED,
            )


raise SystemExit(pytest.main(plugins=[CompleteOfflineSelection()]))
'@
    $arguments = @('-c',$launcher,'-q','-rs','-p','no:cacheprovider',
        '--basetemp',$base,'--junitxml',$resultsFile) + $Step.args
    try {
        $execution = Invoke-OfflineProcess $arguments
        if ($DebugArtifacts) {
            $privateDir = Join-Path $script:reportDirectory 'private'
            [void][IO.Directory]::CreateDirectory($privateDir)
            [IO.File]::WriteAllText((Join-Path $privateDir "$($Step.name).txt"),
                ($execution.stdout + $execution.stderr), [Text.UTF8Encoding]::new($false))
        }
        if ($execution.exit_code -notin @(0, 5)) {
            Fail-Case $Step.name 'offline_checks_failed' "Offline Simulator checks failed ($($execution.exit_code))."
        }
        $suite = if (Test-Path -LiteralPath $resultsFile -PathType Leaf) {
            $results = [xml](Get-Content -LiteralPath $resultsFile -Raw)
            $results.testsuites.testsuite
        }
        if ($execution.exit_code -eq 5 -or [int]$suite.tests -le 0) {
            $Entry.status = 'NOT RUN'
            $Entry['error_code'] = 'offline_checks_not_run'
            throw 'Offline Simulator checks have no recorded test executions.'
        }
        if ([int]$suite.failures -gt 0 -or [int]$suite.errors -gt 0) {
            Fail-Case $Step.name 'offline_checks_failed' 'Offline Simulator checks recorded failures or errors.'
        }
        if ([int]$suite.skipped -gt 0) {
            $Entry.status = 'SKIP'
            $Entry['error_code'] = 'offline_checks_skipped'
            throw "Offline Simulator checks skipped $($suite.skipped) of $($suite.tests) tests."
        }
        $Entry.status = 'PASS'
    } finally {
        Remove-Item -LiteralPath $resultsFile -Force -ErrorAction SilentlyContinue
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

function Get-ProtectionWriteProgress($Details) {
    if ($Details.confirmed -isnot [pscustomobject] -or
        $Details.unconfirmed -isnot [array] -or $Details.not_attempted -isnot [array]) { return }

    # Keep only setting names and typed readbacks in the shareable report.
    $settings = @('software_current_enabled','software_current_limit','software_current_delay',
                  'software_power_limit','software_power_delay','hardware_power_limit')
    $confirmed = [ordered]@{}
    foreach ($field in $settings) {
        $value = $Details.confirmed.$field
        $valid = if ($field -ceq 'software_current_enabled') { $value -is [bool] } else {
            $value -is [string] -and $value -cmatch '\A[+-]?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?\z'
        }
        if ($valid) { $confirmed[$field] = $value }
    }
    return [ordered]@{
        confirmed = $confirmed
        unconfirmed = @($Details.unconfirmed | Where-Object { $_ -is [string] -and $_ -cin $settings })
        not_attempted = @($Details.not_attempted | Where-Object { $_ -is [string] -and $_ -cin $settings })
    }
}

function Invoke-LiveCommand([string]$Command, [string]$CaseName,
                            [string[]]$ExtraArgs = @(),
                            [int]$DeadlineMs = 30000,
                            [int]$TerminationMs = 5000,
                            [long]$TimerObservationDeadlineNs = 0,
                            [string]$ExpectedError,
                            [switch]$AllowCrHandoff,
                            [switch]$AllowProtectionDefaultRefusal,
                            [switch]$Cleanup) {
    $remaining = if ($Cleanup) { [Math]::Min($DeadlineMs, $script:perCommandDeadlineMs) } else {
        [Math]::Min($DeadlineMs,
            $script:totalDeadlineMs - $script:watch.ElapsedMilliseconds)
    }
    if ($remaining -le 0) { throw 'Total CLI validation deadline expired.' }
    $launcher = 'from electronicload_tool_cli.cli import main; raise SystemExit(main())'
    $timerOn = $CaseName -ceq 'timer-on'
    $workflowLoad = $Command -cin @('cc-timed-load', 'cc-discharge')
    $isolatedSettings = $Command -ceq 'contributor-settings'
    if ($workflowLoad -and $remaining -lt 10000) { throw 'Insufficient time remains for a bounded Workflow load trial.' }
    $observeOn = $timerOn -or $workflowLoad
    $notification = if ($timerOn) { 'Timer' } else { 'Workflow' }
    if ($timerOn) {
        $launcher = 'import sys; from electronicload_tool_cli.cli import main; raise SystemExit(main(timer_on_write_started=lambda ns: print("Timer INP ON write started:", ns, file=sys.stderr, flush=True)))'
    } elseif ($workflowLoad) {
        $launcher = 'import os, sys, threading; from electronicload_tool_cli.cli import main; stop=threading.Event(); threading.Thread(target=lambda: (os.read(sys.stdin.fileno(), 1), stop.set()), daemon=True).start(); raise SystemExit(main(contributor_stop=stop, workflow_on_write_started=lambda ns: print("Workflow INP ON write started:", ns, file=sys.stderr, flush=True)))'
    } elseif ($isolatedSettings) {
        $launcher = 'import os, sys, threading; from electronicload_tool_cli.cli import main; stop=threading.Event(); threading.Thread(target=lambda: (os.read(sys.stdin.fileno(), 1), stop.set()), daemon=True).start(); raise SystemExit(main(contributor_stop=stop))'
    } elseif ($DebugArtifacts -and $Command -in @('protection-set','protection-status')) {
        $launcher = 'import logging; logging.basicConfig(); logging.getLogger("electronicload_tool_core.protection").setLevel(logging.DEBUG); ' + $launcher
    }
    $arguments = @('-c', $launcher,
        $Command, '--json') + $script:connectionArgs + $ExtraArgs
    $process = New-LiveCliProcess $arguments
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
    $timerWriteStartedNs = 0L
    $timerDiagnostic = [Text.StringBuilder]::new()
    $exitStatus = 'NOT STARTED'
    $terminationAttempted = $false
    if ($script:slowRiseEnableAttempted) { $script:slowRiseCommunicationTrusted = $false }
    try {
        if ($TimerObservationDeadlineNs -and (Get-TimerMonotonicNs) -ge $TimerObservationDeadlineNs) {
            Fail-Case $CaseName 'timer_expiry_unconfirmed' 'The Timer observation window closed before the next query.'
        }
        if ($observeOn) { $dispatchNs = Get-TimerMonotonicNs }
        [void]$process.Start()
        $started = $true
        $script:report.hardware_touched = $true
        $entry.status = 'FAIL'
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $null
        if ($observeOn) {
            # Read the write-start notification while the child still owns the VISA session.
            $commandDeadlineNs = $dispatchNs + [long]$remaining * 1000000L
            while ($true) {
                $read = $process.StandardError.ReadLineAsync()
                $remaining = Get-TimerRemainingMs $commandDeadlineNs
                if (-not $read.Wait($remaining)) { break }
                $line = $read.GetAwaiter().GetResult()
                if ($null -eq $line) { break }
                [void]$timerDiagnostic.AppendLine($line)
                if ($line -cmatch "\A$notification INP ON write started: ([0-9]{1,19})\z") {
                    if (-not [long]::TryParse($Matches[1], [ref]$timerWriteStartedNs) -or
                        $timerWriteStartedNs -lt $dispatchNs -or $timerWriteStartedNs -gt (Get-TimerMonotonicNs)) {
                        if ($script:triggerSelected) { $script:triggerTrusted = $false }
                        $code = if ($timerOn) { 'timer_timing_unconfirmed' } else { 'workflow_timing_unconfirmed' }
                        if ($workflowLoad) {
                            $entry['manual_action_required'] = $true
                            Write-Host "FAIL ${CaseName}: use the independent hardware cutoff now. Input timing is unconfirmed."
                        }
                        Fail-Case $CaseName $code 'Invalid SCPI Input ON write-start notification.'
                    }
                    $TimerObservationDeadlineNs = if ($timerOn) {
                        Get-TimerObservationDeadlineNs $timerWriteStartedNs
                    } else { $timerWriteStartedNs + 10000000000L }
                    $stderr = $process.StandardError.ReadToEndAsync()
                    break
                }
                if ($remaining -le 0) { break }
            }
            # Once the Workflow has dispatched ON, reserve its complete OFF
            # deadline even if the general run budget expires during cleanup.
            $remaining = if ($workflowLoad -and $TimerObservationDeadlineNs) {
                Get-TimerRemainingMs $TimerObservationDeadlineNs
            } else { Get-TimerRemainingMs $commandDeadlineNs }
        } else {
            $stderr = $process.StandardError.ReadToEndAsync()
        }
        $timerLimited = $false
        if ($TimerObservationDeadlineNs) {
            $observationMs = Get-TimerRemainingMs $TimerObservationDeadlineNs
            $timerLimited = $observationMs -le $remaining
            $remaining = [Math]::Min($remaining, $observationMs)
        }
        if (-not $process.WaitForExit([int]$remaining)) {
            if ($traceCase) { $script:traceCommunicationUntrusted = $true }
            if ($script:triggerSelected) { $script:triggerTrusted = $false }
            $entry['error_code'] = if ($workflowLoad) { 'workflow_cleanup_unconfirmed' }
                elseif ($timerLimited) { 'timer_expiry_unconfirmed' } else { 'deadline_expired' }
            $exitStatus = 'TIMEOUT; session/input state unconfirmed'
            if ($TimerObservationDeadlineNs -or $workflowLoad) {
                # A stuck child may outlast the independent cutoff. Notify the operator before waiting for exit.
                $script:report.status = 'FAIL'
                Write-Host "FAIL ${CaseName}: physical Input OFF is unconfirmed. Use the independent hardware cutoff now. Do not wait for the CLI process to exit."
            }
            if ($workflowLoad) { $entry['manual_action_required'] = $true }
            if ($isolatedSettings) {
                $entry['manual_action_required'] = $true
                Write-Host "FAIL ${CaseName}: keep the terminals isolated. Verify Input OFF and complete restoration at the instrument."
            }
            $terminationAttempted = $true
            $process.Kill()
            if ($process.WaitForExit($TerminationMs)) {
                if ($stdout.IsCompleted -or -not ($script:timerPowered -or $workflowLoad)) { $output = $stdout.GetAwaiter().GetResult() }
                if ($null -ne $stderr -and ($stderr.IsCompleted -or -not ($script:timerPowered -or $workflowLoad))) {
                    $diagnostic = $stderr.GetAwaiter().GetResult()
                }
            }
            throw "$CaseName exceeded its deadline; physical Input state is unconfirmed."
        }
        $exitStatus = [string]$process.ExitCode
        if ($timerOn -and $timerLimited -and (Get-TimerMonotonicNs) -ge $TimerObservationDeadlineNs) {
            if ($script:triggerSelected) { $script:triggerTrusted = $false }
            Fail-Case $CaseName 'timer_expiry_unconfirmed' 'Input ON did not finish within the Timer observation window.'
        }
        if ($CaseName -ceq 'timer-expiry') {
            $entry['observed_at_ns'] = Get-TimerMonotonicNs
            $entry['observed_at_ms'] = $script:watch.ElapsedMilliseconds
        }
        $output = $stdout.GetAwaiter().GetResult()
        if ($null -ne $stderr) { $diagnostic = $stderr.GetAwaiter().GetResult() }
        try {
            $result = $output | ConvertFrom-Json -ErrorAction Stop
            if ($null -eq $result -or $result.ok -isnot [bool]) {
                throw 'Incomplete CLI response.'
            }
        } catch {
            if ($traceCase) { $script:traceCommunicationUntrusted = $true }
            if ($script:triggerSelected) { $script:triggerTrusted = $false }
            if ($workflowLoad -or $isolatedSettings) {
                $entry['manual_action_required'] = $true
                if ($workflowLoad) {
                    Write-Host "FAIL ${CaseName}: use the independent hardware cutoff now. Input OFF is unconfirmed."
                } else {
                    Write-Host "FAIL ${CaseName}: keep the terminals isolated. Verify Input OFF and restoration at the instrument."
                }
            }
            $entry['error_code'] = 'invalid_cli_json'
            throw "$CaseName did not return valid, complete CLI JSON."
        }
        $entry['exit_code'] = $process.ExitCode
        if ($process.ExitCode -ne 0 -or $result.ok -ne $true) {
            $code = [string]$result.error.code
            $details = $result.error.details
            $entry['error_code'] = $(if ($code -cmatch '^[a-z_]+$') { $code } else { 'execution_failed' })
            if (($workflowLoad -or $isolatedSettings) -and $details.manual_action_required -eq $true) {
                $entry['manual_action_required'] = $true
                if ($workflowLoad) {
                    Write-Host "FAIL ${CaseName}: use the independent hardware cutoff now. Input OFF is unconfirmed."
                } else {
                    Write-Host "FAIL ${CaseName}: keep the terminals isolated. Verify Input OFF and complete restoration at the instrument."
                }
            }
            if ($Command -ceq 'sequence' -and $result.ok -eq $false) {
                Save-SequenceEvidence $entry $details
            }
            if ($Command -ceq 'protection-set' -and $result.ok -eq $false) {
                $progress = Get-ProtectionWriteProgress $details
                if ($null -ne $progress) { $entry['protection_write_progress'] = $progress }
            }
            if ($AllowProtectionDefaultRefusal -and $process.ExitCode -eq 3 -and $result.ok -eq $false -and
                $code -ceq 'invalid_response' -and $null -eq $details.cleanup_errors -and
                $details.default_rejected_before_write -is [bool] -and $details.default_rejected_before_write -and
                $details.input_state -ceq 'OFF' -and @($details.confirmed.PSObject.Properties).Count -eq 0 -and
                @($details.unconfirmed).Count -eq 0 -and @($details.not_attempted).Count -gt 0) {
                $entry.status = 'NOT RUN'
                $entry.error_code = 'protection_default_out_of_bounds'
                $entry['protection_state'] = 'OFF'
                $entry['protection_exchange_confirmed'] = $true
                return $result
            }
            if ($CaseName.StartsWith('protection-') -and $result.ok -eq $false -and
                $null -eq $details.cleanup_errors) {
                if ($details.input_state -cin @('ON','OFF')) {
                    $entry['protection_state'] = $details.input_state
                } elseif ($code -in @('already_on','input_not_off')) {
                    $entry['protection_state'] = 'ON'
                }
                if (-not $details.operator_action_required -and
                    $code -in @('readback_mismatch','protection_trial_interlock','protection_still_active',
                                'protection_clear_unexpected_on')) {
                    $entry['protection_exchange_confirmed'] = $true
                    if ($Command -ceq 'input-on' -and -not $details.input_on_attempted) {
                        $entry['protection_on_refused'] = $true
                    }
                }
            }
            if ($script:triggerSelected -and ($code -in @('query_failed','write_failed',
                    'visa_open_failed','visa_configure_failed','cleanup_failed','trace_session_untrusted') -or
                    $null -ne $details.cleanup_errors)) {
                $script:triggerTrusted = $false
            }
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
        if ($timerOn -and (-not $timerWriteStartedNs -or
            $result.result.timer_timing.on_write_started_ns -ne $timerWriteStartedNs)) {
            Fail-Case $CaseName 'timer_timing_unconfirmed' 'The CLI result did not confirm the signaled SCPI Input ON write time.'
        }
        if ($workflowLoad) {
            $finished = $result.result.off_confirmed_ns
            if (-not $timerWriteStartedNs -or $result.result.on_write_started_ns -ne $timerWriteStartedNs -or
                $null -eq $finished -or $finished -lt $timerWriteStartedNs -or $finished -gt $TimerObservationDeadlineNs -or
                $result.result.cleanup.off_confirmed -ne $true -or $result.result.input_state -cne 'OFF') {
                $entry['manual_action_required'] = $true
                Write-Host "FAIL ${CaseName}: use the independent hardware cutoff now. Input OFF is unconfirmed."
                Fail-Case $CaseName 'workflow_cleanup_unconfirmed' 'The CLI did not confirm Input OFF within the loading deadline.'
            }
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
        $entry.status = if ($workflowLoad -or $entry.Contains('readback_comparison') -or $CaseName -ceq 'timer-expiry') {
            'NOT RUN'
        } else { 'PASS' }
        $entry['response'] = $result
        if ($null -ne $result.result.visa) {
            # Compare metadata before shortening library paths in shareable output.
            $entry['runtime_library'] = $result.result.visa.library
            $entry['runtime_manufacturer'] = $result.result.visa.manufacturer
        }
        return $result
    } finally {
        if (($workflowLoad -or $isolatedSettings) -and $started -and -not $terminationAttempted -and -not $process.HasExited) {
            $entry['manual_action_required'] = $true
            if ($workflowLoad) {
                Write-Host "STOP ${CaseName}: use the independent hardware cutoff now if Input OFF is not confirmed."
            } else {
                Write-Host "STOP ${CaseName}: keep the terminals isolated and confirm restoration before reconnecting."
            }
            # PowerShell may cancel its pipeline before the isolated child sees Stop.
            # Allow the CLI that owns VISA to perform its own bounded OFF cleanup.
            try { $process.StandardInput.Close() } catch { }
            $stopWait = if ($TimerObservationDeadlineNs) { Get-TimerRemainingMs $TimerObservationDeadlineNs }
                elseif ($isolatedSettings) { $DeadlineMs } else { 10000 }
            [void]$process.WaitForExit($stopWait)
        }
        if ($started -and -not $terminationAttempted -and -not $process.HasExited) {
            $process.Kill()
            [void]$process.WaitForExit($TerminationMs)
        }
        if ($script:triggerPowered -and $started -and -not $process.HasExited) {
            $script:triggerProcessStillRunning = $true
        }
        if ($script:timerPowered -and $started -and -not $process.HasExited) {
            $script:timerProcessStillRunning = $true
        }
        $process.Dispose()
        if ($DebugArtifacts) {
            $artifact = @{prefix=('{0:D2}-{1}' -f $sequence, $CaseName); output=$output;
                          diagnostic=$timerDiagnostic.ToString() + $diagnostic; exitStatus=$exitStatus}
            if ($script:triggerPowered) { $script:triggerArtifacts += $artifact }
            elseif ($script:protectionPowered) { $script:protectionArtifacts += $artifact }
            elseif ($script:timerPowered) { $script:timerArtifacts += $artifact }
            else { Save-CommandArtifacts $artifact }
        }
        if (-not ($script:triggerPowered -or $script:protectionPowered -or $script:timerPowered)) { Save-Report }
    }
}

function Fail-Case([string]$Name, [string]$Code, [string]$Message) {
    $case = $script:report.results | Where-Object { $_.name -eq $Name } | Select-Object -First 1
    if ($null -ne $case) {
        $case.status = 'FAIL'
        $case['error_code'] = $Code
        if ($case.Contains('readback_comparison') -and $Code -ceq 'readback_mismatch') {
            $case.readback_comparison = 'MISMATCH'
        }
    }
    throw $Message
}

function Assert-Runtime([object]$Result, [string]$CaseName) {
    if ($null -eq $Result.result.visa -or
        $Result.result.visa.library -cne $script:expectedLibrary -or
        $Result.result.visa.manufacturer -cne $script:expectedManufacturer) {
        if ($script:triggerSelected) { $script:triggerTrusted = $false }
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

function Get-TimerMonotonicNs {
    # Stopwatch and Python monotonic_ns use the system-wide monotonic clock
    # (QueryPerformanceCounter on Windows, CLOCK_MONOTONIC on Linux).
    [long]([decimal][Diagnostics.Stopwatch]::GetTimestamp() * 1000000000L /
        [Diagnostics.Stopwatch]::Frequency)
}

function Get-TimerRemainingMs([long]$DeadlineNs) {
    [int][Math]::Max(0, [Math]::Floor(($DeadlineNs - (Get-TimerMonotonicNs)) / 1000000))
}

function Get-TimerObservationDeadlineNs([long]$WriteStartedNs) {
    $WriteStartedNs + ([long][int]$TimerSeconds + 1) * 1000000000L
}

function Invoke-TimerExpiry($Step) {
    $entry = $script:report.results | Where-Object { $_.name -ceq $Step.name }
    $observations = @()
    try {
        $on = $script:timerOnTiming
        $nowNs = Get-TimerMonotonicNs
        if ($null -eq $on.on_write_started_ns -or $null -eq $on.on_confirmed_ns -or
            $on.on_write_started_ns -le 0 -or $on.on_confirmed_ns -lt $on.on_write_started_ns -or
            $on.on_confirmed_ns -gt $nowNs) {
            Fail-Case $Step.name 'timer_timing_unconfirmed' 'The SCPI Input ON timing was not confirmed.'
        }
        $earliestExpiryNs = $on.on_write_started_ns + [long][int]$TimerSeconds * 1000000000L
        $deadlineNs = Get-TimerObservationDeadlineNs $on.on_write_started_ns
        # Start one second before expiry so a definite early OFF can be rejected.
        $delay = [Math]::Floor(($earliestExpiryNs - 1000000000L - $nowNs) / 1000000)
        if ($delay -gt 0) { Start-Sleep -Milliseconds $delay }
        while ((Get-TimerMonotonicNs) -lt $deadlineNs) {
            $result = Invoke-LiveCommand -Command $Step.command -CaseName $Step.name `
                -ExtraArgs $Step.args -DeadlineMs $script:perCommandDeadlineMs `
                -TimerObservationDeadlineNs $deadlineNs
            Assert-Runtime $result $Step.name
            $status = $result.result
            $now = $entry.observed_at_ms
            $query = $status.timer_timing
            $observations += [ordered]@{
                elapsed_ms = $now - $script:timerOnStartMs
                since_on_confirmed_ms = $now - $script:timerOnConfirmedMs
                query_started_ns = $query.query_started_ns
                query_completed_ns = $query.query_completed_ns
                seconds = $status.seconds
                enabled = $status.enabled
                input_state = $status.input_state
            }
            Write-Host "Timer expiry at $($observations[-1].elapsed_ms) ms from ON dispatch: Timer $($status.seconds) s, enabled=$($status.enabled), Input $($status.input_state)."
            if ($status.seconds -ne [int]$TimerSeconds -or $status.enabled -isnot [bool] -or
                $status.input_state -cnotin @('ON','OFF')) {
                Fail-Case $Step.name 'readback_mismatch' 'Timer expiry status did not match the configured trial.'
            }
            if ($entry.observed_at_ns -gt $deadlineNs) { break }
            if ($null -eq $query.query_started_ns -or $null -eq $query.query_completed_ns -or
                $query.query_started_ns -lt $on.on_confirmed_ns -or
                $query.query_completed_ns -lt $query.query_started_ns -or
                $query.query_completed_ns -gt $entry.observed_at_ns) {
                Fail-Case $Step.name 'timer_timing_unconfirmed' 'The SCPI Input state query timing was not confirmed.'
            }
            # Post-expiry Timer enable retention is reported as observed.
            if ($status.input_state -ceq 'OFF') {
                if ($query.query_completed_ns -lt $earliestExpiryNs) {
                    Fail-Case $Step.name 'timer_expiry_early' 'Input was observed OFF before the earliest possible Timer expiry.'
                }
                if ($query.query_started_ns -ge $earliestExpiryNs) {
                    return
                }
            }
            $delay = [Math]::Min(150, [Math]::Floor(($deadlineNs - (Get-TimerMonotonicNs)) / 1000000))
            if ($delay -gt 0) { Start-Sleep -Milliseconds $delay }
        }
        Fail-Case $Step.name 'timer_expiry_unconfirmed' 'Input OFF was not confirmed within the Timer validation window.'
    } finally {
        if ($DebugArtifacts) {
            $script:timerExpiryEvidence = @{
                elapsed_ms=$script:watch.ElapsedMilliseconds - $script:timerOnStartMs
                cli_dispatched_ms=$script:timerOnStartMs; cli_confirmed_ms=$script:timerOnConfirmedMs
                scpi_on=$script:timerOnTiming; observations=$observations
            }
        }
    }
}

function Confirm-TimerExpiry {
    $answer = Read-ValidationAnswer 'After explicit OFF, type AUTO and press Enter only if Timer switched Input OFF without manual or external intervention during the observation. Otherwise press Enter to fail the trial' 'timer-expiry' -DiscardPendingKeys
    if ($answer -cne 'AUTO') {
        Fail-Case 'timer-expiry' 'timer_automatic_off_unconfirmed' 'Automatic OFF without intervention was not confirmed.'
    }
    $entry = $script:report.results | Where-Object { $_.name -ceq 'timer-expiry' }
    $entry.status = 'PASS'
    Write-Host 'PASS timer-expiry'
}

function Assert-ReadbackFields($Actual, [hashtable]$Expected, [string]$Name) {
    foreach ($field in $Expected.Keys) {
        $value = $Actual.$field
        $wanted = $Expected[$field]
        $matches = if ($null -eq $value) { $false }
            elseif ($wanted -is [bool]) { $value -is [bool] -and $value -eq $wanted }
            elseif ($wanted -is [string]) { $value -is [string] -and $value -ceq $wanted }
            else {
                try { $value -isnot [bool] -and [decimal]$value -eq $wanted }
                catch { $false }
            }
        if (-not $matches) {
            Fail-Case $Name 'readback_mismatch' "Independent readback differs for $field."
        }
    }
}

function New-TransientReadbackFields([string]$Function, [hashtable]$Settings) {
    $unit = @{CC='a';CV='v';CR='ohm';CW='w'}[$Function]
    $fields = @{transient_mode=$Settings.transient_mode}
    foreach ($key in @('level_a','level_b','width_a','width_b','pulse_width','rise_slew','fall_slew')) {
        if (-not $Settings.ContainsKey($key)) { continue }
        $field = switch -Wildcard ($key) {
            'level_*' { "${key}_$unit" }
            '*slew' { "${key}_a_per_us" }
            default { "${key}_s" }
        }
        $fields[$field] = [decimal]$Settings[$key]
    }
    return $fields
}

function New-TriggerPlan {
    # The same bounded preset is used for PlanOnly and separately approved interactive LIVE.
    # A description cannot replace the operator's actual equipment and wiring checks.
    if ([string]::IsNullOrWhiteSpace($TriggerExternalSetup)) {
        $script:TriggerExternalSetup = 'Operator must verify rear TRIG/common wiring, TTL low pulse >10 us, and disabled external On/Off/analog programming.'
    }
    if ($TriggerObservationSeconds -le 0 -or $TriggerMaxOnSeconds * 1000 -gt $totalDeadlineMs -or
        $TriggerMaxOnSeconds -le $TriggerObservationSeconds) {
        throw 'Maximum Trigger ON time must exceed the observation time and fit within the total deadline.'
    }
    foreach ($token in @($LevelA, $LevelB, $RiseSlew, $FallSlew, $script:triggerPulseWidth,
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
    if ([decimal]$script:triggerPulseWidth -ge [decimal]$TriggerIntervalSeconds -or
        2 * [decimal]$TriggerIntervalSeconds + [decimal]$script:triggerPulseWidth -gt $TriggerObservationSeconds) {
        throw 'Allow time for at least two Timer periods and a distinct pulse shorter than the interval.'
    }
    $setTransient = @('--range','low','--transient-mode','pulse','--level-a',$LevelA,'--level-b',$LevelB,
        '--rise-slew',$RiseSlew,'--fall-slew',$FallSlew,'--pulse-width',$script:triggerPulseWidth)
    $transientJson = '{"range":"low","transient_mode":"pulse","level_a":' + $LevelA +
        ',"level_b":' + $LevelB + ',"rise_slew":' + $RiseSlew + ',"fall_slew":' + $FallSlew +
        ',"pulse_width":' + $script:triggerPulseWidth + '}'
    $listJson = '{"range":"low","repeat_count":' + $TriggerListRepeat + ',"steps":[' +
        '{"current_a":' + $LevelA + ',"slew_a_per_us":' + $RiseSlew + ',"width_s":' + $TriggerListWidthA + '},' +
        '{"current_a":' + $LevelB + ',"slew_a_per_us":' + $FallSlew + ',"width_s":' + $TriggerListWidthB + '}]}'
    $script:triggerPlans = @(@{name='trigger-snapshot';command='trigger-status';args=$triggerScope})
    foreach ($source in @('HOLD','TIMER','BUS','EXTERNAL')) {
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
        $script:triggerPlans += @{name="$prefix-source-status";command='trigger-status';args=$triggerScope}
        if ($isList) {
            $script:triggerPlans += @{name="$prefix-setup";command='set-list-cc';
                args=($triggerScope + @('--arguments-json',$listJson))}
            $script:triggerPlans += @{name="$prefix-readback";command='list-status';args=$readbackScope}
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

function Read-ValidationAnswer([string]$Prompt, [string]$CaseName, [switch]$DiscardPendingKeys) {
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

function Get-ProtectionSetArgs($Settings) {
    foreach ($field in $Settings.Keys) {
        '--' + $field.Replace('_', '-')
        if ($Settings[$field] -is [bool]) {
            if ($Settings[$field]) { 'on' } else { 'off' }
        } else { [string]$Settings[$field] }
    }
}

function Save-SequenceEvidence($Entry, $Result) {
    $offConfirmed = $Result.input_state -ceq 'OFF' -and $Result.cleanup.off_confirmed -eq $true
    $script:report.sequence_input_off_unconfirmed = -not $offConfirmed
    $Entry.sequence_evidence = [ordered]@{
        completed_steps = $Result.completed_steps
        measurements = $Result.measurements
        input_states = $Result.input_states
        input_state = $(if ($Result.input_state -cin @('ON','OFF')) { $Result.input_state } else { 'UNKNOWN' })
        off_confirmed = $offConfirmed
        cleanup_attempted = $Result.cleanup.attempted -eq $true
        failed_step = $Result.failed_step
        original_error_code = $Result.original_error.code
    }
}

function New-SequencePlan {
    $script:sequenceDocuments = Join-Path $repo ".tmp_tests/$traceRunName-sequence"
    [void][IO.Directory]::CreateDirectory($script:sequenceDocuments)
    $setups = [ordered]@{
        cc = @{current=0.1;range='auto'}
        cv = @{voltage=3;range='auto'}
        cr = @{resistance=1000;range='auto'}
        cw = @{power=0.10}
    }
    foreach ($mode in $setups.Keys) {
        $file = Join-Path $script:sequenceDocuments "$mode.json"
        $document = @{version=1;loop_count=1;steps=@(
            @{action="set-$mode";arguments=$setups[$mode]},
            @{action='input-on';arguments=@{}},
            @{action='input-state';arguments=@{}},
            @{action='wait';arguments=@{seconds=0.25}},
            @{action='measure';arguments=@{}},
            @{action='input-off';arguments=@{}},
            @{action='input-state';arguments=@{}}
        )}
        [IO.File]::WriteAllText($file, ($document | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
        @{name="sequence-$mode";command='sequence';args=@('--file',$file,'--hardware-validation','sequence','--confirm')}
    }
}

function New-ProtectionPlan {
    $set = $script:protectionScope + @('--confirm')
    $status = $script:protectionScope + @('--validation-snapshot')
    $cc = @('--current','0.20','--range','low')
    $script:protectionPlans += @(
        @{name='protection-start-state';command='readback';args=$script:readbackScope;
          condition='reuse the independent Readback snapshot when selected'},
        @{name='protection-prepare-cc';command='set-cc';args=$cc;
          condition='Input and Short OFF confirmed in Fixed CV or CW'},
        @{name='protection-prepare-cc-readback';command='fixed-status';args=$script:readbackScope;
          condition='verify Fixed CC after a preparation write'}
    )
    foreach ($trial in $script:protectionTrials) {
        $prefix = "protection-$($trial.name)"
        $trialArgs = @(Get-ProtectionSetArgs $trial.settings)
        $script:protectionPlans += @(
            @{name="$prefix-original";command='protection-status';args=$status},
            @{name="$prefix-cc";command='set-cc';args=$cc;
              condition='original Fixed CC does not match 0.20 A Low'}
        )
        if ($trial.name -ceq 'hardware-power') {
            $script:protectionPlans += @(
                @{name="$prefix-baseline-on";command='operator-observation';args=@()},
                @{name="$prefix-baseline-trial";command='input-on';args=$set + @('--protection-trial','hardware-power-baseline')},
                @{name="$prefix-baseline-off";command='input-off';args=@()}
            )
        }
        $script:protectionPlans += @(
            @{name="$prefix-set";command='protection-set';args=$set + $trialArgs},
            @{name="$prefix-on";command='operator-observation';args=@()},
            @{name="$prefix-trial";command='input-on';args=$set + @('--protection-trial',$trial.name)}
        )
        $off = @{name="$prefix-off";command='input-off';args=@()}
        if ($trial.flags.Count -gt 0) {
            $off.condition = 'cleanup if software protection did not turn Input OFF'
        }
        $script:protectionPlans += $off
        $script:protectionPlans += @{name="$prefix-events";command='protection-status';args=$script:protectionScope + @('--events')}
        if ($trial.flags.Count -gt 0) {
            $script:protectionPlans += @(
                @{name="$prefix-source-off";command='operator-observation';args=@()},
                @{name="$prefix-clear";command='protection-clear';args=$set}
            )
        }
        $script:protectionPlans += @(
            @{name="$prefix-restore-guard";command='input-state';args=@()},
            @{name="$prefix-restore-settings";command='protection-set';args=$set + $trialArgs;
              condition='use the original values read before the trial'},
            @{name="$prefix-restore-cc";command='set-cc';args=@('--current','0.20','--range','low');
              condition='use the original Fixed CC settings'},
            @{name="$prefix-final";command='protection-status';args=$status}
        )
    }
    foreach ($group in @('software-power', 'hardware-power')) {
        $prefix = "protection-default-$group"
        $example = @('--hardware-power-limit','0.50')
        if ($group -eq 'software-power') { $example = @('--software-power-limit','0.50','--software-power-delay','1') }
        $script:protectionPlans += @(
            @{name="$prefix-original";command='protection-status';args=$status},
            @{name="$prefix-set";command='protection-set';args=$set + @('--restore-defaults',$group)},
            @{name="$prefix-restore-guard";command='input-state';args=@()},
            @{name="$prefix-restore-settings";command='protection-set';args=$set + $example;
              condition='use the original values read before restoring defaults'},
            @{name="$prefix-final";command='protection-status';args=$status}
        )
    }
}

function Invoke-ProtectionStep([string]$Name, [string[]]$ArgsOverride, [switch]$Cleanup,
                               [switch]$AllowDefaultRefusal) {
    $step = $script:protectionPlans | Where-Object { $_.name -ceq $Name }
    $entry = $script:report.results | Where-Object { $_.name -ceq $Name }
    $extra = $step.args
    if ($PSBoundParameters.ContainsKey('ArgsOverride')) {
        $extra = $ArgsOverride
        $entry.arguments = $extra
    }
    $confirmed = $false
    $entry['protection_state'] = 'UNKNOWN'
    $script:report.protection_input_off_unconfirmed = $true
    try {
        $response = Invoke-LiveCommand -Command $step.command -CaseName $Name -ExtraArgs $extra `
            -Cleanup:$Cleanup -AllowProtectionDefaultRefusal:$AllowDefaultRefusal
        if ($AllowDefaultRefusal -and $entry.error_code -ceq 'protection_default_out_of_bounds') {
            return $response
        }
        Assert-Runtime $response $Name
        $confirmed = $true
        $fields = @{input_state='protection_state';condition='protection_condition';events='protection_events';
                    settings='protection_settings';applied='protection_settings';fixed_cc='protection_fixed_cc'}
        foreach ($field in $fields.Keys) {
            if ($null -ne $response.result.$field) { $entry[$fields[$field]] = $response.result.$field }
        }
        if ($null -ne $response.result.power_w) {
            $entry['protection_measurements'] = [ordered]@{
                voltage_v=$response.result.voltage_v;current_a=$response.result.current_a;power_w=$response.result.power_w
            }
            if ($null -ne $response.result.calculated_power_w) {
                $entry.protection_measurements['calculated_power_w'] = $response.result.calculated_power_w
            }
        }
        if ($step.command -cne 'input-on') { Assert-InputOff $response $Name }
        return $response
    } finally {
        $script:report.protection_input_off_unconfirmed = $entry.protection_state -cne 'OFF'
        $script:protectionTrusted = $script:protectionTrusted -and
            ($confirmed -or $entry.Contains('protection_exchange_confirmed'))
    }
}

function Confirm-ProtectionOperator([string]$Name, [string]$Prompt) {
    $answer = Read-ValidationAnswer $Prompt $Name -DiscardPendingKeys
    if ($null -eq $answer -or $answer -cne '') {
        Fail-Case $Name 'confirmation_required' 'The operator did not confirm the Protection step.'
    }
    ($script:report.results | Where-Object { $_.name -ceq $Name }).status = 'PASS'
}

function Invoke-PoweredProtectionTrial([string]$Prefix, [string]$Trial, [switch]$ExplicitOff) {
    Confirm-ProtectionOperator "$Prefix-on" "Confirm the same dedicated source is ON at 5 V / 0.8 A, Input is OFF and manual cutoff is ready. Press Enter for $Trial at 0.20 A (Ctrl+C to cancel)."
    $script:protectionPowered = $true
    try {
        return (Invoke-ProtectionStep "$Prefix-trial").result
    } finally {
        try {
            $entry = $script:report.results | Where-Object { $_.name -ceq "$Prefix-trial" }
            if ($script:protectionTrusted -and -not $entry.protection_on_refused -and
                ($ExplicitOff -or $script:report.protection_input_off_unconfirmed)) {
                $null = Invoke-ProtectionStep "$Prefix-off" -Cleanup
            }
        } finally {
            $script:protectionPowered = $false
        }
    }
}

function Restore-Protection([string]$Prefix, $Original, $Fields, [bool]$RestoreCc) {
    $script:report.protection_restore_unconfirmed = $true
    if (-not $script:protectionTrusted -or $script:report.protection_input_off_unconfirmed) {
        Write-Host 'Protection restoration needs trusted VISA and confirmed Input OFF. Verify manually.'
        return
    }
    $null = Invoke-ProtectionStep "$Prefix-restore-guard"
    if ($Fields.Count -gt 0) {
        $settings = [ordered]@{}
        foreach ($field in $Fields) { $settings[$field] = $Original.settings.$field }
        $arguments = $script:protectionScope + @('--confirm') + @(Get-ProtectionSetArgs $settings)
        $null = Invoke-ProtectionStep "$Prefix-restore-settings" -ArgsOverride $arguments
    }
    if ($RestoreCc) {
        $arguments = @('--current',$Original.fixed_cc.current_a,'--range',$Original.fixed_cc.range)
        $null = Invoke-ProtectionStep "$Prefix-restore-cc" -ArgsOverride $arguments
    }
    $final = Invoke-ProtectionStep "$Prefix-final"
    if ($final.result.fixed_cc.range -cne $Original.fixed_cc.range -or
        [decimal]$final.result.fixed_cc.current_a -ne [decimal]$Original.fixed_cc.current_a) {
        Fail-Case "$Prefix-final" 'readback_mismatch' 'Fixed CC restoration did not match its original settings.'
    }
    foreach ($field in $Original.settings.PSObject.Properties.Name) {
        $matches = if ($Original.settings.$field -is [bool]) {
            $final.result.settings.$field -is [bool] -and $final.result.settings.$field -eq $Original.settings.$field
        } else { [decimal]$final.result.settings.$field -eq [decimal]$Original.settings.$field }
        if (-not $matches) {
            Fail-Case "$Prefix-final" 'readback_mismatch' 'Protection restoration did not match its original settings.'
        }
    }
    $script:report.protection_restore_unconfirmed = $false
}

function Invoke-ProtectionValidation($Selection) {
    if ($null -eq $Selection) {
        $Selection = (Invoke-ProtectionStep 'protection-start-state').result
    } else {
        ($script:report.results | Where-Object { $_.name -ceq 'protection-start-state' }).status = 'SKIP'
    }
    Assert-InputOff @{result=$Selection} 'protection-start-state'
    if ($Selection.active_mode -cne 'fixed' -or $Selection.function -cnotin @('CC','CV','CW')) {
        Fail-Case 'protection-start-state' 'unsafe_mode' 'Protection requires Fixed CC or a guarded setup from Fixed CV or CW.'
    }
    if ($Selection.function -ceq 'CC') {
        foreach ($name in @('protection-prepare-cc','protection-prepare-cc-readback')) {
            ($script:report.results | Where-Object { $_.name -ceq $name }).status = 'SKIP'
        }
    } else {
        if ($Selection.short_state -cne 'OFF') {
            Fail-Case 'protection-start-state' 'unsafe_mode' 'Short must be OFF before preparing Fixed CC for Protection.'
        }
        $null = Invoke-ProtectionStep 'protection-prepare-cc'
        $prepared = Invoke-ProtectionStep 'protection-prepare-cc-readback'
        Assert-ReadbackFields $prepared.result @{active_mode='fixed';function='CC';range='low';
            current_a=[decimal]0.20;input_state='OFF';state_source='instrument_readback'} 'protection-prepare-cc-readback'
    }

    foreach ($trial in $script:protectionTrials) {
        $prefix = "protection-$($trial.name)"
        $script:report.protection_trials[$trial.name] = 'FAIL'
        $original = Invoke-ProtectionStep "$prefix-original"
        $changeCc = $original.result.fixed_cc.range -cne 'low' -or
            [decimal]$original.result.fixed_cc.current_a -ne 0.20
        $fields = @()
        try {
            $script:report.protection_restore_unconfirmed = $true
            if ($changeCc) {
                $null = Invoke-ProtectionStep "$prefix-cc"
            } else {
                foreach ($name in @("$prefix-cc","$prefix-restore-cc")) {
                    ($script:report.results | Where-Object { $_.name -ceq $name }).status = 'SKIP'
                }
            }
            if ($trial.name -ceq 'hardware-power') {
                $baseline = Invoke-PoweredProtectionTrial "$prefix-baseline" 'hardware-power-baseline' -ExplicitOff
                if ($baseline.input_state -cne 'ON' -or 'PS' -cin $baseline.condition.flags -or
                    $baseline.voltage_v -lt 4.75 -or $baseline.voltage_v -gt 5.25 -or
                    $baseline.current_a -lt 0.18 -or $baseline.current_a -gt 0.22) {
                    Fail-Case "$prefix-baseline-trial" 'power_baseline_unconfirmed' 'The source did not sustain approximately 0.20 A at 5 V before limiting.'
                }
            }
            $fields = @($trial.settings.Keys)
            $null = Invoke-ProtectionStep "$prefix-set"
            $observed = Invoke-PoweredProtectionTrial $prefix $trial.name -ExplicitOff:($trial.flags.Count -eq 0)
            if ($trial.flags.Count -gt 0) {
                if ($observed.input_state -cne 'OFF' -or
                    @($trial.flags | Where-Object { $_ -cnotin $observed.condition.flags }).Count -gt 0) {
                    if (($trial.name -eq 'software-current' -and $observed.current_a -le 0.10) -or
                        ($trial.name -eq 'software-power' -and $observed.power_w -le 0.50)) {
                        Fail-Case "$prefix-trial" 'protection_exposure_not_proven' 'Measurements did not establish exposure above the software threshold.'
                    }
                    Fail-Case "$prefix-trial" 'protection_trip_unconfirmed' 'Software trip and Input OFF were not both confirmed.'
                }
                ($script:report.results | Where-Object { $_.name -ceq "$prefix-off" }).status = 'SKIP'
            } else {
                $calculated = $observed.calculated_power_w
                if ($observed.input_state -cne 'ON' -or 'PS' -cin $observed.condition.flags -or
                    $observed.voltage_v -lt 4.75 -or $observed.voltage_v -gt 5.25 -or
                    $calculated -lt 0.45 -or $calculated -gt 0.55 -or
                    $calculated -ge $baseline.calculated_power_w) {
                    Fail-Case "$prefix-trial" 'power_limit_unconfirmed' 'Measurements did not confirm the 0.50 W hardware limit.'
                }
            }
            $null = Invoke-ProtectionStep "$prefix-events"
            if ($trial.flags.Count -gt 0) {
                Confirm-ProtectionOperator "$prefix-source-off" 'Verify the trip on the load, turn OFF and isolate the external source, and press Enter only after confirming the fault condition is removed (Ctrl+C to cancel).'
                $null = Invoke-ProtectionStep "$prefix-clear"
            }
        } finally {
            Restore-Protection $prefix $original.result $fields $changeCc
        }
        $script:report.protection_trials[$trial.name] = 'PASS'
    }
    foreach ($group in @('software-power', 'hardware-power')) {
        $prefix = "protection-default-$group"
        $original = Invoke-ProtectionStep "$prefix-original"
        $fields = @('hardware_power_limit')
        if ($group -eq 'software-power') { $fields = @('software_power_limit','software_power_delay') }
        $refused = $false
        try {
            $result = Invoke-ProtectionStep "$prefix-set" -AllowDefaultRefusal
            $refused = $result.ok -eq $false
            if ($refused) {
                Write-Host "Protection $group defaults blocked before any write. See private diagnostics for the MAX query."
            }
        } finally {
            if (-not $refused) { Restore-Protection $prefix $original.result $fields $false }
        }
    }
}

function Invoke-TriggerTrial($Trial) {
    $prefix = $Trial.prefix
    $armed = $false
    $awaitingObservation = $false
    $entry = $script:report.results | Where-Object { $_.name -ceq "$prefix-observed" }
    $setupEntry = $script:report.results | Where-Object { $_.name -ceq "$prefix-setup" }
    try {
        Assert-TriggerOff (Invoke-TriggerStep "$prefix-configure") "$prefix-configure"
        $observed = Invoke-TriggerStep "$prefix-source-status"
        Assert-TriggerOff $observed "$prefix-source-status"
        if ($observed.result.source -cne $Trial.source -or ($Trial.source -eq 'TIMER' -and
            [decimal]$observed.result.interval_s -ne [decimal]$Trial.interval)) {
            Fail-Case "$prefix-source-status" 'readback_mismatch' 'Trigger Source or Timer did not match.'
        }
        $setup = Invoke-TriggerStep "$prefix-setup"
        $armed = $Trial.isList
        Assert-TriggerOff $setup "$prefix-setup"
        if ($Trial.isList) {
            $name = "$prefix-readback"
            $readback = (Invoke-TriggerStep $name).result
            if ($readback.input_state -cne 'OFF') {
                $script:triggerOffConfirmed = $false
                $script:report.trigger_input_off_unconfirmed = $true
            }
            Assert-ReadbackFields $readback @{state_source='instrument_readback';selected=$true;input_state='OFF'} $name
            $list = $readback.configuration
            Assert-ReadbackFields $list @{range='low';repeat_count=$TriggerListRepeat;step_count=2} $name
            if ($list.steps.Count -ne 2) { Fail-Case $name 'readback_mismatch' 'Expected two LIST steps.' }
            Assert-ReadbackFields $list.steps[0] @{current_a=[decimal]$LevelA;
                slew_a_per_us=[decimal]$RiseSlew;width_s=[decimal]$TriggerListWidthA} $name
            Assert-ReadbackFields $list.steps[1] @{current_a=[decimal]$LevelB;
                slew_a_per_us=[decimal]$FallSlew;width_s=[decimal]$TriggerListWidthB} $name
        } else {
            $armed = $true
            Assert-TriggerOff (Invoke-TriggerStep "$prefix-enable") "$prefix-enable"
        }
        Write-Host "Trigger $($Trial.source), CLI readback confirmed. Observe the IT8812C panel current, or an approved capture instrument if needed."
        if ($Trial.isList) {
            Write-Host "Expect LIST CC levels $LevelA A then $LevelB A for $TriggerListWidthA s / $TriggerListWidthB s, repeated $TriggerListRepeat time(s)."
        } else {
            Write-Host "Expect baseline B ($LevelB A), a pulse at A ($LevelA A) for $($script:triggerPulseWidth) s, then a return to B."
        }
        switch ($Trial.source) {
            'EXTERNAL' {
                Write-Host 'Keep the source inactive until the observation starts. Apply one TTL low pulse (>10 us) to rear TRIG and its shared negative terminal.'
                Write-Host "Confirm the reviewed equipment and wiring: $TriggerExternalSetup"
            }
            'TIMER' { Write-Host "Watch for at least two Timer pulses at $($Trial.interval) s intervals. No operator pulse is required." }
            default { Write-Host 'The script will send one software trigger after Input ON is confirmed.' }
        }
        $awaitingObservation = $true
        $entry['operator_observed'] = 'Unconfirmed'
        $ack = Read-ValidationAnswer 'With Input OFF and capture ready, press Enter to start the bounded observation (Ctrl+C to cancel)' "$prefix-observed"
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
                } else { [decimal]$script:triggerPulseWidth }
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
        $answer = Read-ValidationAnswer "Input OFF confirmed. Did you observe the expected $($Trial.source) result? (Yes/No)" `
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

function Invoke-TriggerValidation([switch]$RestoreOnly) {
    $snapshot = $script:originalTriggerSnapshot
    if ($null -eq $snapshot) { throw 'Original Trigger snapshot was not captured before mode changes.' }
    Assert-TriggerOff $snapshot 'trigger-snapshot'
    $source = $snapshot.result.source
    $interval = $snapshot.result.interval_s
    if ($source -cnotin @('MANUAL','BUS','HOLD','TIMER','EXTERNAL') -or
        $interval -isnot [string] -or [string]::IsNullOrWhiteSpace($interval)) {
        Fail-Case 'trigger-snapshot' 'invalid_response' 'Both original Trigger settings must be read before any change.'
    }
    try {
        if (-not $RestoreOnly) {
            foreach ($trial in $script:triggerTrials) { Invoke-TriggerTrial $trial }
        }
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
    if ($Step.command -ceq 'trace-status') {
        Assert-ReadbackFields $actual @{state_source='instrument_readback'; source=$expected.source;
            points=$expected.points; interval_s=[decimal]$expected.interval; delay_s=0;
            filter_enabled=$false; feed='NEVER'} $name
        return
    }
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
    if ($requestedModes.Count -eq 0 -or
        @($requestedModes | Where-Object { $_ -notin ($supportedModes + 'full') }).Count -gt 0 -or
        ('full' -in $requestedModes -and $requestedModes.Count -ne 1)) {
        throw "Select full or one or more of: $($supportedModes -join ', ')."
    }
    if ($serialArgs.Count -and -not $asrlSelected) {
        throw 'Serial parameters require -Connection asrl.'
    }
    $workflowLoadSelected = $LiveExtensions -and $workflowSelected
    if ($SourceVoltageLimitV -ne 5 -or $SourceCurrentLimitA -ne 0.8) {
        if ($sequenceSelected -or $protectionSelected) {
            throw 'Sequence and Protection require a dedicated 5 V / 0.8 A source.'
        }
        if ($workflowLoadSelected) {
            throw 'Contributor Workflow load trials require a dedicated 5 V / 0.8 A source.'
        }
    }
    if ($LiveExtensions -and -not @($selectedModes | Where-Object { $_ -in $extensionModes }).Count) {
        throw '-LiveExtensions requires a selection including an extension or Workflow mode.'
    }
    if (-not $Target -or (-not $Resource -and -not $offlineOnly)) {
        Write-Host 'BLOCKED: explicit -Target and, for hardware cases, -Resource are required.'
        exit 2
    }
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $cli)) {
        throw 'Sync the repository .venv with uv before running validation.'
    }
    if ($offlineOnly -and ($Resource -or $serialArgs.Count)) {
        # Validate any supplied connection without including it in Workflow commands.
        $null = Invoke-OfflineCommand 'identify'
    }
    if ('cc' -in $selectedModes -or $traceSelected -or $timerSelected -or $featureSelected) {
        # Preserve the original token; both manual trials and the script ceiling must pass.
        & $python -c 'from electronicload_tool_core.control import validate_current; import sys; value = validate_current(sys.argv[1], sys.argv[2]); validate_current(sys.argv[1], sys.argv[3]); sys.exit(0 if 0 < value <= 2 else 2)' $currentText low high
        if ($LASTEXITCODE -ne 0) {
            throw 'Current must be positive, at most 2 A, and valid for both CC ranges without rounding.'
        }
        $report.prechecks.current = 'PASS'
    }
    if (-not $offlineOnly) {
        $report.script_current_ceiling_a = if ($selectedModes.Count -eq 1 -and $protectionSelected) { 0.20 }
            elseif ($selectedModes.Count -eq 1 -and $sequenceSelected) { 0.10 } else { $scriptCurrentCeilingA }
    }
    if ($timerSelected) {
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
    if (('cc' -in $selectedModes -or $traceSelected -or $timerSelected -or $featureSelected) -and
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
    if ($offlineOnly) { $planned = @() }
    if ($systemQuerySelected) {
        foreach ($command in @('scpi-version','display-mode','scpi-error-next')) {
            $planned += @{name=$command;command=$command;args=@('--hardware-validation','system-query')}
        }
    }
    if ($readbackSelected) {
        $baselineParameters = @{
            'fixed-slew-status' = @{rise_slew_a_per_us='ExpectedRiseSlew';fall_slew_a_per_us='ExpectedFallSlew'}
            'von-status' = @{threshold_v='ExpectedVonThreshold';latch='ExpectedVonLatch'}
            'short-status' = @{short_state='ExpectedShort'}
            'average-status' = @{average_count='ExpectedAverageCount'}
        }
        $planned += @{name='readback';command='readback';args=$readbackScope;independent_snapshot=$true;
                      readback_expected=@{input_state='OFF'}}
        foreach ($function in @('CC','CV','CR','CW')) {
            $planned += @{name="fixed-status-$($function.ToLowerInvariant())";command='fixed-status';args=$readbackScope;
                          readback_mode='fixed';readback_function=$function;
                          readback_expected=@{active_mode='fixed';function=$function;input_state='OFF'};
                          condition="Existing Fixed $function selection"}
        }
        $planned += @(
            @{name='transient-status';command='transient-status';args=$readbackScope;readback_mode='transient';
              readback_expected=@{enabled=$true;input_state='OFF'};condition='Existing Transient selection'},
            @{name='list-status';command='list-status';args=$readbackScope;readback_mode='list';
              readback_expected=@{selected=$true;input_state='OFF'};condition='Existing LIST selection'}
        )
        foreach ($command in @('fixed-slew-status','von-status','short-status','average-status')) {
            $expected = @{}
            $step = @{name=$command;command=$command;args=$readbackScope;readback_expected=$expected;
                      check_readback_settings=$true}
            if ($command -eq 'fixed-slew-status') {
                $step.readback_mode = 'fixed'
                $step.readback_function = 'CC'
                $step.condition = 'Existing Fixed CC selection'
                $expected += @{active_mode='fixed';function='CC';input_state='OFF'}
                $step.readback_expected = $expected
            }
            $baseline = @{}
            foreach ($field in $baselineParameters[$command].Keys) {
                $parameter = $baselineParameters[$command][$field]
                if ($PSBoundParameters.ContainsKey($parameter)) { $baseline[$field] = $PSBoundParameters[$parameter] }
            }
            if ($baseline.Count -eq $baselineParameters[$command].Count) {
                if ($baseline.ContainsKey('latch')) { $baseline.latch = $baseline.latch.ToLowerInvariant() }
                if ($baseline.ContainsKey('short_state')) { $baseline.short_state = $baseline.short_state.ToUpperInvariant() }
                $expected += $baseline
                $step.readback_expected = $expected
                $step.expected_readback = $baseline
            } else {
                $step.condition = (@($step.condition, 'Settings are observed only without all expected values') |
                    Where-Object { $_ }) -join '; '
            }
            $planned += $step
        }
    }
    $pendingLive = @()
    $isolatedNeedsSeparateRun = $false
    if ($LiveExtensions) {
        if ($workflowSelected) {
            $scope = @('--hardware-validation','workflow')
            $planned += @(
                @{name='workflow-measure-log';command='measure-log';args=$scope + @('--interval-s','0.1','--count','2');
                  expected=@{stop_reason='completed';sample_count=2;input_state='OFF'};execution='live'},
                @{name='workflow-measure-until';command='measure-until';args=$scope + @('--interval-s','0.1',
                  '--metric','voltage','--operator','ge','--threshold','1','--timeout-s','2');
                  expected=@{stop_reason='condition_met';input_state='OFF'};execution='live';
                  condition='Observed voltage at least 1 V with Input OFF'}
            )
            $ccArgs = $scope + @('--current','0.1','--range','low','--confirm')
            $planned += @(
                @{name='workflow-cc-timed-load';command='cc-timed-load';args=$ccArgs + @('--duration-s','5');
                  execution='live';workflow_load=$true;expected=@{stop_reason='duration_elapsed';input_state='OFF'};
                  condition='5 V / 0.8 A source, Fixed CC Low, 5 s at 0.1 A, independent cutoff within 10 s of SCPI Input ON including OFF confirmation'},
                @{name='workflow-cc-discharge';command='cc-discharge';args=$ccArgs + @('--interval-s','0.5',
                  '--cutoff-capacity-ah','0.00010','--cutoff-voltage','4.5','--timeout-s','6');
                  execution='live';workflow_load=$true;expected=@{stop_reason='capacity_cutoff';input_state='OFF'};
                  condition='Controlled 5 V / 0.8 A source, 0.1 A discharge, independent cutoff within 10 s including OFF confirmation'}
            )
        }
        if ('measurement-extensions' -in $selectedModes) {
            $scope = @('--hardware-validation','measurement-extensions')
            $planned += @(
                @{name='measurement-extensions-min-max';command='measure-min-max';args=$scope;execution='live'},
                @{name='measurement-extensions-discharge';command='measure-discharge';args=$scope;execution='live'},
                @{name='measurement-extensions-average';command='set-average';args=$scope + @('--count','8','--confirm');
                  expected=@{average_count=8};execution='live';condition='Input OFF and operator approves leaving Average at 8'}
            )
        }
        $isolated = @()
        if ('auxiliary-controls' -in $selectedModes) {
            $isolated = @(foreach ($trial in @('von','short','slew')) {
                @{name="auxiliary-controls-$trial";command='contributor-settings';
                  args=@('--trial',$trial,'--confirm-isolated');execution='live';isolated=$true;
                  expected=@{input_state='OFF';restored=$true};
                  condition='Source and DUT physically disconnected, Fixed CC with Input OFF. Verify test values and restored settings.'}
            })
        }
        if ('list-cc' -in $selectedModes) {
            $isolated += @{name='list-cc-settings';command='contributor-settings';
                args=@('--trial','list-cc','--slot',[string]$ListScratchSlot,'--confirm-isolated');
                execution='live';isolated=$true;deadline_ms=300000;
                expected=@{input_state='OFF';restored=$true;recall_confirmed=$true;exited_list=$true};
                condition="Fixed CC, source and DUT isolated, Input OFF. Operator must permit overwriting slot $ListScratchSlot. Validate A, B, Recall A and restore the original working buffer."}
        }
        $isolationCompatible = @('auxiliary-controls','list-cc','measurement-extensions','readback','system-query')
        $isolatedNeedsSeparateRun = $isolated.Count -gt 0 -and
            @($selectedModes | Where-Object { $_ -notin $isolationCompatible }).Count -gt 0
        if ($isolatedNeedsSeparateRun) {
            foreach ($step in $isolated) {
                $step.not_run_reason = 'settings_require_isolated_run'
                $step.condition = "$($step.condition) Run separately from source-connected or mode-changing trials."
            }
            $pendingLive += $isolated
        } else {
            $planned += $isolated
        }
    }
    # Read existing configuration before any setup, then prepare and finish Protection
    # before Sequence or the other trials can change the function.
    $protectionIndex = $planned.Count
    if ($sequenceSelected) { $planned += @(New-SequencePlan) }
    if ('cc' -in $selectedModes) {
        $planned += @(
            @{name='set-cc-high';command='set-cc';args=$highArgs;allow_cr_handoff=$true},
            @{name='cc-handoff-from-cr';command='set-cc';args=($highArgs + @('--hardware-validation','cr-led'));
              cr_handoff_after='set-cc-high'},
            @{name='set-cc-high-readback';command='fixed-status';args=$readbackScope;
              readback_expected=@{function='CC';range='high';current_a=[decimal]$currentText;input_state='OFF'}},
            @{name='set-cc-auto';command='set-cc';args=$autoArgs},
            @{name='set-cc-low';command='set-cc';args=$lowArgs},
            @{name='set-cc-low-readback';command='fixed-status';args=$readbackScope;
              readback_expected=@{function='CC';range='low';current_a=[decimal]$currentText;input_state='OFF'}},
            @{name='input-on';command='input-on';args=$onArgs},
            @{name='measure-on';command='measure';args=@()},
            @{name='input-off';command='input-off';args=@()},
            @{name='final-input-state';command='input-state';args=@()}
        )
    }
    if ($timerSelected) {
        $timerScope = @('--hardware-validation', 'input-timer')
        $timerExpected = '{"current":' + $currentText + ',"range":"low","seconds":' + [int]$TimerSeconds + '}'
        $planned += @(
            @{name='timer-initial-disable';command='input-timer-disable';args=$timerScope},
            @{name='timer-cc-setup';command='set-cc';args=$lowArgs},
            @{name='timer-setup';command='set-input-timer';args=($timerScope + @('--seconds', $TimerSeconds))},
            @{name='timer-enable';command='input-timer-enable';args=$timerScope},
            @{name='timer-status-off';command='input-timer-status';args=$timerScope},
            @{name='timer-on';command='input-on';args=($timerScope + @('--confirm', '--validation-settings-json', $timerExpected));
              condition='LIVE requires the 5 s Timer and an independent 10 s cutoff armed no later than the SCPI ON write'},
            @{name='timer-expiry';command='input-timer-status';args=$timerScope;
              condition='Observe Input OFF by Timer seconds plus one second from the SCPI ON write start; after explicit OFF, confirm no manual or external intervention'},
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
            $function = ($trial.scope -split '-')[-1].ToUpperInvariant()
            if ($trial.scope.StartsWith('transient-')) {
                $transientReadback = New-TransientReadbackFields $function $trial.settings
                $planned += @{name="$prefix-enable";command='transient-enable';args=$expectedArgs}
            } else {
                $field = @{CV='voltage_v';CR='resistance_ohm';CW='power_w'}[$function]
                $token = @{CV='voltage';CR='resistance';CW='power'}[$function]
                $check = @{function=$function;input_state='OFF';$field=[decimal]$trial.settings[$token]}
                if ($null -ne $trial.settings.range) { $check.range = $trial.settings.range }
                $planned += @{name="$prefix-readback";command='fixed-status';args=$readbackScope;
                              readback_expected=$check}
            }
            $planned += @{name="$prefix-on";command='input-on';args=($expectedArgs + @('--confirm'))}
            if ($trial.scope.StartsWith('transient-')) {
                $planned += @{name="$prefix-trigger-1";command='transient-trigger';args=$expectedArgs}
                $planned += @{name="$prefix-trigger-2";command='transient-trigger';args=$expectedArgs}
                $planned += @{name="$prefix-status-on";command='transient-status';args=$readbackScope;
                              readback_expected=@{enabled=$true;function=$function;input_state='ON'};
                              transient_expected=$transientReadback;range_from="$prefix-setup"}
            }
            $planned += @{name="$prefix-measure";command='measure';args=@()}
            $planned += @{name="$prefix-off";command='input-off';args=@()}
            if ($trial.scope.StartsWith('transient-')) {
                $planned += @{name="$prefix-disable";command='transient-disable';args=$scopeArgs}
                $planned += @{name="$prefix-status-off";command='transient-status';args=$readbackScope;
                              readback_expected=@{enabled=$false;function=$function;input_state='OFF'};
                              transient_expected=$transientReadback;range_from="$prefix-setup"}
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
            $planned += @{name="$($trial.name)-status";command='trace-status';
                          args=@('--hardware-validation','trace');trace_expected=$trial}
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
    if ($protectionSelected) { New-ProtectionPlan }
    foreach ($step in ($planned | Where-Object { $_.ContainsKey('cr_handoff_after') })) {
        $step.condition = "$($step.cr_handoff_after) refused a resistance origin before remote"
    }
    $workflowPlans = @()
    if ($workflowSelected -and -not $LiveExtensions) {
        $workflowPlans = @(
            @{name='workflow-measure-log';command='measure-log';args=@('--interval-s','0.01','--count','1');
              stop_reason='completed';sample_count=1},
            @{name='workflow-measure-until';command='measure-until';args=@('--interval-s','0.01','--metric','voltage',
              '--operator','ge','--threshold','1','--timeout-s','1');stop_reason='condition_met';sample_count=1},
            @{name='workflow-cc-timed-load';command='cc-timed-load';args=@('--current','0.1','--duration-s','0.01','--confirm');
              stop_reason='duration_elapsed';confirm_off=$true},
            @{name='workflow-cc-discharge';command='cc-discharge';args=@('--current','0.1','--cutoff-voltage','3',
              '--cutoff-capacity-ah','0.00000001','--interval-s','0.01','--timeout-s','2','--confirm');
              stop_reason='capacity_cutoff';confirm_off=$true}
        )
    }
    $extensionPlans = @()
    if ('measurement-extensions' -in $selectedModes -and -not $LiveExtensions) {
        $extensionPlans += @(
            @{name='measurement-extensions-min-max';command='measure-min-max';args=@();execution='dry-run'},
            @{name='measurement-extensions-discharge';command='measure-discharge';args=@();execution='dry-run'},
            @{name='measurement-extensions-average';command='set-average';args=@('--count','8');execution='dry-run'},
            @{name='measurement-extensions-simulation';command='pytest';execution='simulate';args=@(
                'tests/test_readonly_cli.py::test_native_measurement_cli_selection',
                'tests/test_readonly_cli.py::test_native_measurement_cli_invalid_metric',
                'tests/test_readonly_core.py::test_native_measurement_failure_has_query_and_no_fallback',
                'tests/test_control_core.py::test_average_native_write_readback_and_query_while_on',
                'tests/test_control_core.py::test_average_requires_confirmed_off_without_writes',
                'tests/test_control_cli.py::test_average_cli_invalid_count')}
        )
    }
    if ('auxiliary-controls' -in $selectedModes -and -not $LiveExtensions) {
        $extensionPlans += @(
            @{name='auxiliary-controls-von';command='set-von';args=@('--voltage','10','--latch','on');execution='dry-run'},
            @{name='auxiliary-controls-short-on';command='short-on';args=@();execution='dry-run'},
            @{name='auxiliary-controls-short-off';command='short-off';args=@();execution='dry-run'},
            @{name='auxiliary-controls-slew';command='set-cc';args=@('--current','1','--range','low',
                '--rise-slew','0.1','--fall-slew','0.2');execution='dry-run'},
            @{name='auxiliary-controls-simulation';command='pytest';execution='simulate';args=@(
                'tests/test_control_core.py::test_von_requires_input_off_and_is_retained_across_fixed_modes',
                'tests/test_control_core.py::test_short_fixed_modes_keep_input_and_settings_independent',
                'tests/test_control_core.py::test_fixed_cc_slew_setup_and_preview',
                'tests/test_control_cli.py::test_fixed_slew_von_short_cli_refusal')}
        )
    }
    if ('list-cc' -in $selectedModes -and -not $LiveExtensions) {
        $listDocument = '{"range":"auto","repeat_count":1,"steps":[{"current_a":1,"slew_a_per_us":0.1,"width_s":0.001},{"current_a":2,"slew_a_per_us":0.2,"width_s":0.002}]}'
        $extensionPlans += @(
            @{name='list-cc-setup';command='set-list-cc';args=@('--arguments-json',$listDocument);execution='dry-run'},
            @{name='list-cc-save';command='list-save';args=@('--slot','1');execution='dry-run'},
            @{name='list-cc-recall';command='list-recall';args=@('--slot','1');execution='dry-run'},
            @{name='list-cc-exit';command='list-exit';args=@();execution='dry-run'},
            @{name='list-cc-simulation';command='pytest';execution='simulate';args=@(
                'tests/test_list_core.py::test_list_cycle_save_recall_overwrite_and_snapshot_isolation',
                'tests/test_list_core.py::test_list_invalid_slots_are_rejected',
                'tests/test_list_core.py::test_list_setup_requires_confirmed_off',
                'tests/test_list_cli.py::test_list_inline_and_utf8_file_have_same_result',
                'tests/test_list_cli.py::test_list_standalone_simulation_keeps_fresh_state')}
        )
    }
    # Capture Trigger before any legacy Transient can write HOLD.
    $allSteps = $extensionPlans + $workflowPlans + @($planned | Select-Object -First 3) +
        @($triggerPlans | Select-Object -First 1) +
        @($planned | Select-Object -Skip 3 -First ([Math]::Max(0, $protectionIndex - 3))) +
        $protectionPlans + @($planned | Select-Object -Skip $protectionIndex) + $slowRiseCleanup +
        @($triggerPlans | Select-Object -Skip 1) + $pendingLive
    $report.results = @($allSteps | ForEach-Object {
        $entry = [ordered]@{name=$_.name;command=$_.command;arguments=$_.args;status='NOT RUN'}
        if ($_.name -like 'workflow-*') { $entry['execution'] = 'simulate' }
        if ($_.ContainsKey('execution')) { $entry['execution'] = $_.execution }
        if ($_ -in $pendingLive) {
            if (-not $_.ContainsKey('args')) { $entry.arguments = @() }
            $entry['execution'] = 'live'
            $entry['error_code'] = if ($_.not_run_reason) { $_.not_run_reason } else { 'live_procedure_unapproved' }
        }
        if ($_.ContainsKey('expected_error')) { $entry['expected_error'] = $_.expected_error }
        if ($_.ContainsKey('condition')) { $entry['condition'] = $_.condition }
        if ($_.ContainsKey('expected_readback')) { $entry['expected_readback'] = $_.expected_readback }
        if ($_.ContainsKey('check_readback_settings')) { $entry['readback_comparison'] = 'NOT RUN' }
        $entry
    })
    $plannedRanges = @{}
    $offlinePlans = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    foreach ($step in $allSteps) {
        if ($step.command -in @('operator-observation','pytest') -or
            ($step -in $pendingLive -and -not $step.ContainsKey('args'))) { continue }
        $extras = @($step.args)
        $noConnection = $step.execution -in @('dry-run','simulate') -or
            ($step.name -like 'workflow-*' -and $step.execution -cne 'live')
        $key = ConvertTo-Json -InputObject (@($noConnection, $step.command) + $extras) -Compress
        if (-not $offlinePlans.ContainsKey($key)) {
            $offlinePlans[$key] = Invoke-OfflineCommand $step.command $extras -NoConnection:$noConnection
        }
        $plan = $offlinePlans[$key]
        if ($step.execution -ceq 'live') {
            $entry = $report.results | Where-Object { $_.name -ceq $step.name }
            foreach ($field in @('queries','planned_writes')) {
                if ($null -ne $plan.result.$field) { $entry[$field] = @($plan.result.$field) }
            }
        }
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
        if ($step.ContainsKey('range_from') -and $plannedRanges.ContainsKey($step.range_from)) {
            $step.transient_expected.range = $plannedRanges[$step.range_from]
        }
        if ($step.command -in @('set-transient-cc','set-transient-cv','set-transient-cr')) {
            $plannedRanges[$step.name] = $plan.result.expected_configuration.range
        }
        if ($step.command -ceq 'sequence') {
            $entry = $report.results | Where-Object { $_.name -eq $step.name } | Select-Object -First 1
            $entry.sequence_plan = $plan.result.steps
        }
    }
    $report.prechecks.plan = 'PASS'
    $reportDirectory = Join-Path $repo ".tmp_tests/$traceRunName"
    [void][IO.Directory]::CreateDirectory($reportDirectory)
    Write-Host "ElectronicLoad Tool - CLI Validation"
    Write-Host "Target: $Target | Connection: $Connection | Backend: $Backend | Resource: <redacted>"
    Write-Host "Mode: $(if ($PlanOnly) { 'PLAN ONLY' } elseif ($offlineOnly) { 'SIMULATE' } else { 'LIVE' })"
    Write-Host "Selected modes: $($selectedModes -join ', ')."
    if (-not $offlineOnly) { Write-Host 'Plan: Identify, confirm Input OFF and measure before physical trials.' }
    if ($workflowPlans.Count) { Write-Host 'Workflow: four simulated cases with no VISA or source access.' }
    if ($pendingLive.Count) {
        Write-Host 'Some selected LIVE cases require a separate run and will remain NOT RUN.'
    }
    if ($systemQuerySelected) { Write-Host 'System Query: read version, display mode and one Error Queue entry.' }
    if ($readbackSelected) {
        Write-Host 'Readback queries existing settings with Input OFF before any setup. Inapplicable modes are skipped.'
        Write-Host 'Use known front-panel settings as expected values. Incomplete comparisons are recorded as NOT RUN.'
    }
    if ($sequenceSelected) {
        Write-Host 'Sequence: four Fixed trials, each with a separate confirmation, one ON, measurement and confirmed OFF.'
    }
    if ($protectionSelected) {
        Write-Host 'Protection: independent software current trip, software power trip and hardware power limit trials at 0.20 A.'
        Write-Host 'Protection confirms Fixed CC with Input OFF before trials. Each trial requires Enter before Input ON. Software Clear also requires source isolation and a second Enter.'
    }
    if ($traceSelected) {
        Write-Host 'TRACE: four Input OFF batches and one bounded Fixed CC ON batch with explicit OFF.'
        Write-Host "TRACE Start process deadline: $traceStartDeadlineMs ms. No auto reconnect."
    }
    if ('cc' -in $selectedModes -or $traceSelected) {
        Write-Host 'CC setup from CR: conditional contributor handoff with Input OFF and the Core LED OFF interlock.'
    }
    if (@($selectedModes | Where-Object { $_ -in @('cc','cv','cr','cw','input-timer') -or $_ -like 'transient-*' }).Count -gt 0) {
        Write-Host 'Other selected modes retain explicit ON/OFF and independent OFF confirmation.'
    }
    if ($featureSelected) {
        Write-Host 'CR-LED and Current Slow Rise trials keep Input OFF and verify configuration readback.'
    }
    if ($triggerSelected) {
        Write-Host 'Trigger: four source trials (HOLD, TIMER, BUS, EXTERNAL) with manual observation, explicit OFF and restoration of original Source and Timer.'
        Write-Host "Each Trigger observation is $TriggerObservationSeconds s. The Input ON/OFF budget is $TriggerMaxOnSeconds s per trial."
    }
    if ('current-slow-rise' -in $selectedModes) {
        Write-Host 'Slow Rise requires a non-resistance starting function and expects Fixed CV setup to be refused while enabled.'
        Write-Host 'If the Slow Rise trial stops before confirmed Disable, cleanup requires trusted communication and confirmed Input OFF.'
    }
    if (@($selectedModes | Where-Object { $_ -in @('cc','cv','cr') }).Count -gt 0) {
        Write-Host 'Fixed Auto adds Input OFF setup/readback with the existing values; no extra ON/OFF cycles.'
    }
    if (@($selectedModes | Where-Object { $_.StartsWith('transient-') }).Count -gt 0) {
        Write-Host 'Transient: Continuous/Pulse/Toggle, Low/High/Auto, repeated triggers, control status and separate disable.'
        Write-Host 'Trigger/status/measure do not establish waveform shape, timing or completion.'
    }
    if (-not $offlineOnly) {
        Write-Host "External source (operator-managed): $SourceVoltageLimitV V / $SourceCurrentLimitA A current limit."
        Write-Host 'The script does not configure, measure or verify the external source.'
    }
    foreach ($step in $allSteps) {
        $condition = $(if ($step.condition) { " [if $($step.condition)]" } else { '' })
        Write-Verbose ("  {0}: {1} {2}{3}" -f $step.name, $step.command, ($step.args -join ' '), $condition)
    }
    if ('cc' -in $selectedModes) { Write-Host "CC Current=$currentText A." }
    if (-not $offlineOnly) {
        Write-Host "Script ceiling=$($report.script_current_ceiling_a) A; $Connection System VISA."
        Write-Host 'No reset, automatic local switch, or implicit Input ON.'
        Write-Host 'An Input ON attempt requires an explicit OFF cleanup; session close does not turn Input OFF.'
        Write-Host 'V/I/P measure queries are separate; power freshness is not guaranteed.'
    }
    if ($PlanOnly) {
        $report.status = 'PLAN ONLY'
        Save-Report
        Show-Summary
        Write-Host 'PLAN ONLY COMPLETE: no hardware accessed.'
        exit 0
    }
    foreach ($step in $extensionPlans) {
        $phase = 'cases'
        $entry = $report.results | Where-Object { $_.name -ceq $step.name }
        $entry.status = 'FAIL'
        if ($step.command -ceq 'pytest') { Invoke-OfflineTests $step $entry }
        else { $entry.status = 'PASS' }
    }
    $phase = 'plan'
    if ($offlineOnly -or (-not [Console]::IsInputRedirected -and [Environment]::UserInteractive)) {
        foreach ($step in $workflowPlans) {
            $phase = 'cases'
            $entry = $report.results | Where-Object { $_.name -ceq $step.name }
            try {
                $result = Invoke-OfflineCommand $step.command $step.args -NoConnection -Simulate
                if ($result.result.stop_reason -cne $step.stop_reason -or
                    ($step.ContainsKey('sample_count') -and $result.result.sample_count -ne $step.sample_count) -or
                    ($step.confirm_off -and ($result.result.input_state -cne 'OFF' -or
                                            -not $result.result.cleanup.off_confirmed))) {
                    Fail-Case $step.name 'workflow_result_mismatch' 'Simulation did not match the expected completion.'
                }
                $entry['response'] = $result
                $entry.status = 'PASS'
            } catch {
                $entry.status = 'FAIL'
                throw
            }
        }
        if ($offlineOnly) {
            $report.status = 'PASS'
            Save-Report
            Show-Summary
            if ($workflowSelected -and -not $extensionPlans.Count) {
                Write-Host 'PASS: Workflow offline validation.'
            } else {
                Write-Host 'PASS: selected offline validation.'
            }
            exit 0
        }
        $phase = 'plan'
    }
    if ($timerSelected -and [int]$TimerSeconds -ne 5) {
        $report.status = 'BLOCKED'
        Save-Report
        Show-Summary
        Write-Host 'BLOCKED: the independent 10 s hardware cutoff applies only to the 5 s Timer trial. Review other settings with PlanOnly.'
        exit 2
    }
    if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) {
        $report.status = 'BLOCKED'
        Save-Report
        Show-Summary
        Write-Host 'BLOCKED: controlled LIVE validation requires an interactive console.'
        exit 2
    }
    $preflightScript = Join-Path $PSScriptRoot 'preflight-cli.ps1'
    $preflightModes = [ordered]@{
        'input-timer'='InputTimerOnly'; trace='TraceOnly'; trigger='TriggerOnly';
        protection='ProtectionOnly'; 'system-query'='SystemQueryOnly'; readback='ReadbackOnly';
        sequence='SequenceOnly'; 'cr-led'='CrLedSlowRiseOnly'; 'current-slow-rise'='CrLedSlowRiseOnly'
    }
    $preflightChecks = @(foreach ($mode in $selectedModes) {
        if ($preflightModes.Contains($mode)) { $preflightModes[$mode] }
    })
    if (@($selectedModes | Where-Object { $_ -in @('cc','cv','cr','cw') -or $_ -like 'transient-*' }).Count) {
        & $preflightScript -BaseOnly
        if ($LASTEXITCODE -ne 0) { throw 'Base preflight failed.' }
    }
    foreach ($check in ($preflightChecks | Select-Object -Unique)) {
        $selection = @{ $check = $true }
        & $preflightScript @selection
        if ($LASTEXITCODE -ne 0) {
            $report.prechecks.preflight = 'FAIL'
            throw "$check preflight failed."
        }
    }
    $report.prechecks.preflight = 'PASS'
    Write-Host "Operator resource check (LIVE): Target: $Target; Resource: $Resource; Backend: $Backend"
    Write-Host 'Physical checks before pressing Enter:'
    if (@($planned | Where-Object isolated).Count) {
        Write-Host '- For the isolated settings trials, physically disconnect the source and DUT and leave Input OFF.'
    } else {
        Write-Host "- Confirm the external source is manually set to $SourceVoltageLimitV V with a $SourceCurrentLimitA A current limit."
    }
    Write-Host '- Confirm physical load Input OFF, safe wiring/DUT and tolerance of every selected setting (use -Verbose or the report).'
    Write-Host '- Confirm no other application controls this instrument and an independent manual Input OFF procedure is available.'
    if ($protectionSelected) {
        Write-Host '- Confirm Timer, Short, Slow Rise and Transient OFF for Protection. The script can prepare Fixed CC from Fixed CV or CW while Input is OFF.'
    }
    if ($isolatedNeedsSeparateRun) {
        Write-Host '- Run Auxiliary Controls and LIST settings separately with the source and DUT physically disconnected.'
    }
    if ($LiveExtensions -and 'measurement-extensions' -in $selectedModes) {
        Write-Host '- Confirm the Average count may be set to 8 and left at that setting.'
    }
    if ($triggerSelected) {
        Write-Host '- Confirm external On/Off and analog programming are disabled and external trigger wiring was reviewed while de-energized.'
    } elseif ($workflowLoadSelected -or @($planned | Where-Object isolated).Count) {
        Write-Host '- Confirm external On/Off and analog programming are disabled.'
    }
    if ($workflowLoadSelected -or @($selectedModes | Where-Object { $_ -notin (@('readback','system-query','workflow') + $offlineModes + $featureModes) }).Count) {
        Write-Host '- Selected trials may deliberately turn Input ON; the script explicitly commands and verifies OFF, but cannot guarantee OFF after communication failures.'
    }
    $ack = if ($protectionSelected) {
        $watch = [Diagnostics.Stopwatch]::StartNew()
        Read-ValidationAnswer 'Confirm a dedicated source and no important DUT. Press Enter to start (Ctrl+C to cancel)' 'identify' -DiscardPendingKeys
    } else {
        Read-Host 'After completing these checks, press Enter to run selected CLI cases (Ctrl+C to cancel)'
    }
    if ($ack -cne '' -or (($triggerSelected -or $protectionSelected) -and $null -eq $ack)) {
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
    if ($null -eq $watch) { $watch = [Diagnostics.Stopwatch]::StartNew() }
    $phase = 'identify'
    Write-Host 'Running Identify precheck...'
    $identity = Invoke-LiveCommand -Command 'identify' -CaseName 'identify' -ExtraArgs $planned[0].args
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
    $readbackSelection = $null
    for ($i = 1; $i -le $planned.Count; $i++) {
        if ($protectionSelected -and $i -eq $protectionIndex) { Invoke-ProtectionValidation $readbackSelection }
        if ($i -eq $planned.Count) { break }
        $step = $planned[$i]
        if ($step.ContainsKey('readback_mode') -and
            ($readbackSelection.active_mode -cne $step.readback_mode -or
             ($step.readback_function -and $readbackSelection.function -cne $step.readback_function))) {
            $entry = $report.results | Where-Object { $_.name -ceq $step.name }
            $entry.status = 'SKIP'
            Write-Host "SKIP $($step.name): current instrument selection is outside this case."
            continue
        }
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
        if ($step.name -ceq 'timer-on') {
            $answer = Read-ValidationAnswer 'Verify the approved current-limited source and arm the independent 10 s hardware cutoff before SCPI Input ON. Never start the cutoff clock after the ON write. Press Enter only when ready' $step.name -DiscardPendingKeys
            if ($null -eq $answer -or $answer -cne '') {
                Fail-Case $step.name 'operator_confirmation_required' 'The Timer hardware cutoff was not confirmed.'
            }
            $timerOnStartMs = $watch.ElapsedMilliseconds
            $timerPowered = $true
        }
        if ($step.workflow_load) {
            $answer = Read-ValidationAnswer 'Confirm a controlled 5 V source limited to 0.8 A. Arm the independent cutoff so Input cannot remain ON beyond 10 s from the start of the SCPI ON write, including cleanup. Press Enter only when ready' $step.name -DiscardPendingKeys
            if ($null -eq $answer -or $answer -cne '') {
                Fail-Case $step.name 'operator_confirmation_required' 'The independent Workflow cutoff was not confirmed.'
            }
        }
        if ($step.isolated) {
            $confirmation = if ($step.name -ceq 'list-cc-settings') { "OVERWRITE $ListScratchSlot" } else { 'ISOLATED' }
            $answer = Read-ValidationAnswer "Confirm the source and DUT are physically disconnected and Input is OFF. Type $confirmation to allow this settings trial" $step.name -DiscardPendingKeys
            if ($answer -cne $confirmation) {
                Fail-Case $step.name 'operator_confirmation_required' 'Isolation or LIST slot overwrite was not confirmed.'
            }
        }
        # Mark uncertainty before dispatch, including subprocess/JSON/runtime failures.
        if ($step.command -eq 'input-on') { $inputOnAttempted = $true }
        if ($step.command -eq 'input-timer-enable') { $timerEnableAttempted = $true }
        if ($step.command -eq 'current-slow-rise-enable') { $slowRiseEnableAttempted = $true }
        if ($step.name -eq 'timer-expiry') {
            Invoke-TimerExpiry $step
            continue
        }
        if ($step.command -eq 'transient-enable') {
            $activeTransientScope = $step.args[[Array]::IndexOf($step.args, '--hardware-validation') + 1]
            $transientEnableAttempted = $true
        }
        $commandDeadline = $(if ($traceSelected -and $step.command -eq 'trace-start') {
            $traceStartDeadlineMs
        } elseif ($step.deadline_ms) { $step.deadline_ms } else { $perCommandDeadlineMs })
        if ($triggerSelected -and $step.command -in @('set-transient-cc', 'set-transient-cv',
                'set-transient-cr', 'set-transient-cw')) {
            # Legacy validation writes TRIG:SOUR HOLD; mark before a possibly partial write.
            $script:triggerSourceMayHaveChanged = $true
        }
        if ($protectionSelected -and $step.name -ceq 'input-state') {
            $report.protection_input_off_unconfirmed = $true
        }
        if ($step.command -ceq 'sequence') {
            $answer = Read-ValidationAnswer "Confirm dedicated source, reviewed VISA runtime and safe $($step.name) trial. Press Enter to allow one Input ON" $step.name -DiscardPendingKeys
            if ($null -eq $answer -or $answer -cne '') {
                Fail-Case $step.name 'operator_confirmation_required' 'Sequence trial was not confirmed.'
            }
            $report.sequence_input_off_unconfirmed = $true
        }
        if ($step.name -eq 'input-state') { $report.prechecks.input_off = 'FAIL' }
        $result = Invoke-LiveCommand -Command $step.command -CaseName $step.name `
            -ExtraArgs $step.args -DeadlineMs $commandDeadline -ExpectedError $step.expected_error `
            -AllowCrHandoff:($step.allow_cr_handoff -eq $true)
        if ($step.command -ceq 'sequence') {
            $entry = $report.results | Where-Object { $_.name -eq $step.name } | Select-Object -First 1
            Save-SequenceEvidence $entry $result.result
            if ($report.sequence_input_off_unconfirmed -or $result.result.completed_steps -ne 7 -or
                @($result.result.measurements).Count -ne 1 -or
                ($result.result.input_states.input_state -join ',') -cne 'ON,OFF') {
                Fail-Case $step.name 'sequence_result_mismatch' 'Sequence measurements or Input states did not match the trial.'
            }
        }
        if ($step.allow_cr_handoff -and $result.ok -eq $false) { continue }
        if ($step.ContainsKey('expected_error')) {
            Write-Host "PASS $($step.name): confirmed refusal ($($step.expected_error))."
            continue
        }
        Assert-Runtime $result $step.name
        if ($step.execution -ceq 'live') {
            $scopeIndex = [Array]::IndexOf($step.args, '--hardware-validation')
            $validationScope = if ($step.isolated) { 'contributor-settings' } else { $step.args[$scopeIndex + 1] }
            Assert-ReadbackFields $result.result @{validation_only=$true;
                validation_scope=$validationScope} $step.name
            if ($step.ContainsKey('expected')) {
                Assert-ReadbackFields $result.result $step.expected $step.name
            }
        }
        if ($step.workflow_load) {
            $answer = Read-ValidationAnswer 'Verify Input is OFF and the source remained on throughout the trial without manual or independent cutoff. Press Enter to confirm' $step.name -DiscardPendingKeys
            if ($null -eq $answer -or $answer -cne '') {
                Fail-Case $step.name 'workflow_load_unconfirmed' 'The operator did not confirm uninterrupted loading.'
            }
            $entry = $report.results | Where-Object { $_.name -ceq $step.name }
            $entry.status = 'PASS'
            $entry['operator_observed'] = $true
        }
        if ($protectionSelected -and $step.name -ceq 'input-state') {
            $report.protection_input_off_unconfirmed = $result.result.input_state -cne 'OFF'
        }
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
        if ($timerSelected -and $step.command -in @('set-input-timer', 'input-timer-enable', 'input-timer-disable')) {
            Assert-InputOff $result $step.name
        }
        if ($step.command -eq 'input-timer-status') {
            if ($result.result.seconds -ne [int]$TimerSeconds -or
                ($step.name -eq 'timer-status-off' -and $result.result.enabled -ne $true) -or
                ($step.name -eq 'timer-status-disabled' -and $result.result.enabled -ne $false)) {
                Fail-Case $step.name 'readback_mismatch' 'Timer status did not match the configured settings.'
            }
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
        if ($step.name -eq 'timer-on') {
            $timerOnConfirmedMs = $watch.ElapsedMilliseconds
            $timerOnTiming = $result.result.timer_timing
        }
        $observedRange = $(if ($null -ne $result.result.range) { $result.result.range } else {
            $result.result.configuration.range
        })
        if ($step.ContainsKey('expected_range') -and $observedRange -cne $step.expected_range) {
            Fail-Case $step.name 'readback_mismatch' 'Auto Range differs from the Core plan.'
        }
        if ($step.independent_snapshot) { $readbackSelection = $result.result }
        if ($step.name -ceq 'transient-status') {
            $step.readback_expected.function = $readbackSelection.function
        }
        if ($step.ContainsKey('readback_expected')) {
            $step.readback_expected.state_source = 'instrument_readback'
            Assert-ReadbackFields $result.result $step.readback_expected $step.name
        }
        if ($step.ContainsKey('check_readback_settings')) {
            $entry = $report.results | Where-Object { $_.name -eq $step.name } | Select-Object -First 1
            if (-not $step.ContainsKey('expected_readback')) {
                $entry.readback_comparison = 'OBSERVED ONLY'
                Write-Host "NOT RUN $($step.name): observed values without a complete expected setting."
                continue
            }
            $entry.readback_comparison = 'MATCHED'
            $entry.status = 'PASS'
        }
        if ($step.ContainsKey('transient_expected')) {
            Assert-ReadbackFields $result.result.configuration $step.transient_expected $step.name
        }
        if ($step.command -eq 'input-off') { $inputOnAttempted = $false }
        if ($step.name -ceq 'timer-off') { $timerPowered = $false }
        if ($step.command -eq 'input-timer-disable') { $timerEnableAttempted = $false }
        if ($step.name -ceq 'timer-final-state') { Confirm-TimerExpiry }
        if ($step.command -eq 'transient-disable') { $transientEnableAttempted = $false }
        if ($step.name -eq 'slow-rise-status-disabled') { $slowRiseEnableAttempted = $false }
        if ($step.name -eq 'input-state') { $report.prechecks.input_off = 'PASS' }
        if ($step.name -eq 'measure-off' -and $triggerSelected) {
            $script:originalTriggerSnapshot = Invoke-TriggerStep 'trigger-snapshot'
            Assert-TriggerOff $script:originalTriggerSnapshot 'trigger-snapshot'
        }
        Write-Host "PASS $($step.name)"
    }

    foreach ($step in $slowRiseCleanup) {
        $entry = $report.results | Where-Object { $_.name -eq $step.name } | Select-Object -First 1
        $entry.status = 'SKIP'
    }
    if ($triggerSelected) { Invoke-TriggerValidation }
    if ($report.results.error_code -contains 'protection_default_out_of_bounds') {
        throw 'Protection defaults validation is incomplete because the instrument MAX exceeded the allowed bounds.'
    }
    if ($pendingLive.Count) { throw 'LIVE validation is incomplete. Review the NOT RUN cases and their required procedures.' }
    $report.status = if ($readbackSelected -and ($report.results.readback_comparison -contains 'OBSERVED ONLY')) {
        'OBSERVED ONLY'
    } else { 'PASS' }
    Save-Report
    Show-Summary
    if ($report.status -eq 'OBSERVED ONLY') {
        Write-Host 'Readback complete. Only comparisons with supplied expected values were verified.'
    } else {
        Write-Host 'PASS: applicable CLI LIVE cases and selected offline cases.'
    }
    exit 0
} catch {
    if ($report.status -ne 'BLOCKED') {
        $report.status = $(if ($phase -eq 'cases') { 'FAIL' } else { 'BLOCKED' })
    }
    if ($timerPowered) {
        $timerFailure = $_
    } elseif ($DebugArtifacts -and $null -ne $reportDirectory) {
        $privateDir = Join-Path $reportDirectory 'private'
        [void][IO.Directory]::CreateDirectory($privateDir)
        [IO.File]::WriteAllText((Join-Path $privateDir 'failure.txt'),
            $_.Exception.ToString(), [Text.UTF8Encoding]::new($false))
    }
    if (-not $timerPowered) { Write-Host "Validation did not complete: $($_.Exception.Message)" }
} finally {
    # PowerShell pipeline interruption can bypass catch and the exit after finally.
    $validationInterrupted = ($triggerSelected -or $protectionSelected -or $sequenceSelected -or $asrlSelected) -and
        $phase -in @('identify','cases') -and $report.status -eq 'NOT RUN'
    if ($validationInterrupted) { $report.status = 'FAIL' }
    if ($inputOnAttempted) {
        $report.results += [ordered]@{
            name = 'cleanup-input-off'; command = 'input-off'; status = 'NOT RUN'
        }
        if ($timerProcessStillRunning -or $traceCommunicationUntrusted -or ($triggerSelected -and -not $script:triggerTrusted)) {
            $report.status = 'FAIL'
            if ($traceCommunicationUntrusted) { $report.trace_safe_shutdown_unconfirmed = $true }
            if ($triggerSelected) { $report.trigger_input_off_unconfirmed = $true }
            Write-Host 'WARNING: VISA communication untrusted: no new Session or blind OFF cleanup; independent operator action required.'
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
        if ($timerProcessStillRunning -or $traceCommunicationUntrusted -or ($triggerSelected -and -not $script:triggerTrusted)) {
            $report.status = 'FAIL'
            Write-Host 'WARNING: Timer cleanup blocked by untrusted communication; operator action required.'
        } else { try {
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
    }
    if ($transientEnableAttempted) {
        # Disable is separate from OFF. Never attempt it with unconfirmed Input state.
        $report.results += [ordered]@{name='cleanup-input-state';command='input-state';status='NOT RUN'}
        $report.results += [ordered]@{name='cleanup-transient-disable';command='transient-disable';status='NOT RUN'}
        if ($traceCommunicationUntrusted -or ($triggerSelected -and -not $script:triggerTrusted)) {
            $report.status = 'FAIL'
            Write-Host 'WARNING: Transient cleanup blocked by untrusted communication; operator action required.'
        } else { try {
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
    }
    if ($slowRiseEnableAttempted) {
        $report.status = 'FAIL'
        $report.slow_rise_cleanup_unconfirmed = $true
        if ($slowRiseCommunicationTrusted -and -not $traceCommunicationUntrusted -and
            (-not $triggerSelected -or $script:triggerTrusted)) {
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
    # Legacy Transient can change Source before the Trigger trials begin.
    # Restore the original settings only after trusted Input OFF cleanup.
    if ($triggerSelected -and $script:triggerSourceMayHaveChanged -and
        $null -ne $script:originalTriggerSnapshot -and $report.trigger_restore_status -eq 'NOT RUN') {
        $report.status = 'FAIL'
        if ($script:triggerTrusted -and -not $traceCommunicationUntrusted -and
            -not $inputOnAttempted -and -not $timerEnableAttempted -and
            -not $slowRiseEnableAttempted -and -not $report.sequence_input_off_unconfirmed -and
            -not $report.protection_input_off_unconfirmed) {
            $report.results += [ordered]@{name='cleanup-trigger-input-state';command='input-state';status='NOT RUN'}
            try {
                $state = Invoke-LiveCommand -Command 'input-state' -CaseName 'cleanup-trigger-input-state' -Cleanup
                Assert-Runtime $state 'cleanup-trigger-input-state'
                Assert-TriggerOff $state 'cleanup-trigger-input-state'
                Invoke-TriggerValidation -RestoreOnly
                Write-Host 'PASS Original Trigger Source and Timer Interval restored after an earlier mode failed.'
            } catch {
                if ($report.trigger_restore_status -ne 'PASS') {
                    $report.trigger_restore_status = 'FAIL'
                }
                Write-Host 'WARNING: Trigger cleanup after an earlier mode failure did not complete; check the individual Source/Timer restoration evidence.'
            }
        } else {
            $report.trigger_restore_status = 'FAIL'
            Write-Host 'WARNING: Trigger Source and Timer Interval restoration blocked by uncertain safety state; operator action required.'
        }
    }
    if ($sequenceSelected -and $report.sequence_input_off_unconfirmed) {
        Write-Host 'Sequence Input OFF is unconfirmed. Use the independent manual OFF procedure immediately.'
    }
    if ($null -ne $sequenceDocuments) {
        Remove-Item -LiteralPath $sequenceDocuments -Recurse -Force -ErrorAction SilentlyContinue
    }
    foreach ($artifact in $protectionArtifacts) { Save-CommandArtifacts $artifact }
    foreach ($artifact in $timerArtifacts) { Save-CommandArtifacts $artifact }
    if ($null -ne $timerExpiryEvidence) {
        [IO.File]::WriteAllText((Join-Path $privateDir 'timer-expiry-timing.json'),
            (ConvertTo-Json -InputObject $timerExpiryEvidence), [Text.UTF8Encoding]::new($false))
    }
    if ($null -ne $timerFailure) {
        if ($DebugArtifacts -and $null -ne $reportDirectory) {
            [IO.File]::WriteAllText((Join-Path $privateDir 'failure.txt'),
                $timerFailure.Exception.ToString(), [Text.UTF8Encoding]::new($false))
        }
        Write-Host "Validation did not complete: $($timerFailure.Exception.Message)"
    }
    if ($protectionSelected -and $report.protection_input_off_unconfirmed) {
        Write-Host 'Protection Input OFF is unconfirmed. Use the independent manual OFF procedure immediately.'
    }
    Save-Report
    if ($null -ne $reportDirectory) {
        if ($report.status -notin @('PASS','PLAN ONLY','OBSERVED ONLY')) { Show-Summary }
        Write-Host "Report: $reportDirectory/shareable/report.json"
        if ($DebugArtifacts) { Write-Host "Private debug logs: $reportDirectory/private (review before sharing)" }
    }
    if ($validationInterrupted) {
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
