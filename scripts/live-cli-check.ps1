<#
Single, bounded IT8812C CLI hardware validation.
Default: the established validation modes, with common read-only safety prechecks.
-Modes selects any supported validation mode, including trace, cr-led, current-slow-rise,
trigger and input-timer. Omitted -Modes selects all default modes.
-Modes protection selects the separate Protection validation procedure.
-Modes sequence selects four bounded Fixed Software Sequence trials.
-Modes readback exercises settings with Input OFF and isolated terminals unless explicit Expected values request read-only diagnostics.
-Modes full (default) selects all trials. USB and ASRL use the same matrix.
-Modes workflow,measurement-extensions,auxiliary-controls,list-cc selects the corresponding hardware trials.
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
$extensionModes = @('measurement-extensions', 'auxiliary-controls', 'list-cc')
$supportedModes = @('cc', 'cv', 'cr', 'cw', 'transient-cc', 'transient-cv', 'transient-cr', 'transient-cw',
                    'trace') + $featureModes + @('trigger', 'input-timer', 'protection', 'system-query',
                                              'readback', 'sequence', 'workflow') + $extensionModes
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
$triggerManaged = $triggerSelected -or $traceSelected -or
    @($selectedModes | Where-Object { $_ -like 'transient-*' }).Count -gt 0
$timerSelected = 'input-timer' -in $selectedModes
$protectionSelected = 'protection' -in $selectedModes
$readbackSelected = 'readback' -in $selectedModes
$sequenceSelected = 'sequence' -in $selectedModes
$asrlSelected = $Connection -eq 'asrl'
$workflowSelected = 'workflow' -in $selectedModes
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
$averageOriginal = $null
$averageChangeAttempted = $false
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
$script:triggerFixedOriginal = $null
$script:triggerFixedMayHaveChanged = $false
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
    validation_mode = $(if ($PlanOnly) { 'plan-only' } else { 'live' })
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
if ($triggerManaged) {
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
        if ($entry.Contains('error_diagnostics')) { $case.error_diagnostics = $entry.error_diagnostics }
        if ($entry.Contains('manual_action_required')) { $case.manual_action_required = $entry.manual_action_required }
        if ($entry.Contains('expected_error')) { $case.expected_error = $entry.expected_error }
        if ($entry.Contains('expected_range')) { $case.expected_range = $entry.expected_range }
        if ($entry.Contains('expected_readback')) { $case.expected_readback = $entry.expected_readback }
        if ($entry.Contains('readback_comparison')) { $case.readback_comparison = $entry.readback_comparison }
        if ($entry.Contains('condition')) { $case.condition = $entry.condition }
        if ($entry.Contains('deadline_ms')) { $case.deadline_ms = $entry.deadline_ms }
        foreach ($field in @('queries','planned_writes','conditional_writes')) {
            if ($entry.Contains($field)) { $case[$field] = $entry[$field] }
        }
        if ($entry.Contains('origin_function')) { $case.origin_function = $entry.origin_function }
        foreach ($field in @('trace_source','trace_points','trace_interval_s','trace_sample_count',
                             'trace_csv_complete','operator_observed','load_duration_confirmed','trigger_observation','trigger_source','trigger_interval_s',
                             'trigger_source_restore','trigger_interval_restore','trigger_dispatch_exchange','list_selected','automatic_off_observed','timer_expiry_bracket','reuse_from')) {
            if ($entry.Contains($field)) { $case[$field] = $entry[$field] }
        }
        if ($entry.Contains('confirmed_input_state')) { $case.input_state = $entry.confirmed_input_state }
        foreach ($field in @('protection_state','protection_condition','protection_events',
                             'protection_settings','protection_fixed_cc','protection_measurements',
                             'protection_write_progress')) {
            if ($entry.Contains($field)) { $case[$field] = $entry[$field] }
        }
        $partialEvidence = $entry.name -ceq 'timer-expiry' -or $entry.error_code -ceq 'list_save_unconfirmed' -or
            $entry.Contains('load_duration_confirmed')
        if ($entry.status -eq 'PASS' -or $entry.readback_comparison -in @('OBSERVED ONLY','MISMATCH') -or
            ($partialEvidence -and $null -ne $entry.response)) {
            if ($entry.command -in @('input-state','input-on','input-off','input-timer-status',
                                     'trigger-configure','trigger-status')) {
                $case.input_state = $entry.response.result.input_state
            }
            if ($entry.name -cne 'trigger-fixed-restore' -and $entry.command -in @('set-cc', 'set-cv', 'set-cr', 'set-cr-led', 'cr-led-status')) {
                $case.range = $entry.response.result.range
            }
            if ($entry.command -eq 'set-cc') {
                if ($entry.name -cne 'trigger-fixed-restore') { $case.current_a = $entry.response.result.current_a }
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
                    if ($field -ceq 'average_count' -and $entry.name -ceq 'validation-average-original') { continue }
                    if ($null -ne $entry.response.result.$field) {
                        $case[$field] = $entry.response.result.$field
                    }
                }
            }
            if ($entry.execution -ceq 'live' -and $entry.command -cin @('cc-timed-load','cc-discharge','contributor-settings')) {
                foreach ($field in @('input_state','cleanup','on_write_started_ns','off_write_started_ns','off_confirmed_ns','duration_s',
                                     'current_a','measured_current_a','capacity_ah','trial','restored','short_state',
                                     'short_on_confirmed','saved_slot','a_width_s','b_width_s','save_confirmed','recall_confirmed','exited_list',
                                     'load_evidence_source','load_evidence_tail_ns','load_evidence_resolution_ns','current_samples','expected_setpoint',
                                     'expected_level_a','expected_width_s','expected_average_count')) {
                    if ($null -ne $entry.response.result.$field) { $case[$field] = $entry.response.result.$field }
                }
            }
            if ($entry.command -eq 'error') {
                $case.scpi_error_code = $entry.response.result.error_code
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
                    if ($field -ceq 'average_count' -and $entry.name -cin @('validation-average-original','validation-average-restore')) { continue }
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
    if ($script:triggerManaged) {
        $public['trigger_restore_status'] = $script:report.trigger_restore_status
        $public['trigger_input_off_unconfirmed'] = $script:report.trigger_input_off_unconfirmed
    }
    if ($script:triggerSelected) {
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

function Invoke-OfflineCommand([string]$Command, [string[]]$ExtraArgs = @()) {
    $arguments = @('-c', 'from electronicload_tool_cli.cli import main; raise SystemExit(main())',
        $Command, '--dry-run', '--json') + $ExtraArgs + $script:connectionArgs
    $execution = Invoke-OfflineProcess $arguments
    $plan = $execution.stdout | ConvertFrom-Json -ErrorAction Stop
    if ($execution.exit_code -ne 0 -or $plan.ok -ne $true) {
        throw "Offline command failed for $Command ($($plan.error.code))."
    }
    return $plan
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

function Get-SettingsFailureEvidence($ErrorResult) {
    $evidence = [ordered]@{}
    if ($ErrorResult.code -cnotin @('invalid_response','invalid_range_readback','list_baseline_unrestorable',
                                   'readback_mismatch','settings_restore_unconfirmed')) { return $evidence }
    $details = $ErrorResult.details
    if ([string]$details.query -cmatch '^[A-Z][A-Z0-9:]*\?( [0-9]{1,2})?$') { $evidence.query = $details.query }
    foreach ($field in @('step','observed','expected','minimum','maximum')) {
        if ([string]$details.$field -cmatch '^[+-]?[0-9]+(\.[0-9]+)?([Ee][+-]?[0-9]+)?$') {
            $evidence[$field] = $details.$field
        }
    }
    if ($details.range -cin @('low','high')) { $evidence.range = $details.range }
    if ($details.field -cin @('current_a','voltage_v','resistance_ohm','power_w')) { $evidence.field = $details.field }
    if ($details.no_settings_written -eq $true) { $evidence.no_settings_written = $true }
    if ($details.parse_failure -is [bool] -and $details.parse_failure) { $evidence.parse_failure = $true }
    if ($details.list_phase -cin @('B baseline','A before baseline Recall','Baseline Recall','A','B before final Recall',
                                 'Final Recall','Automatic readback')) {
        $evidence.list_phase = $details.list_phase
    }
    return $evidence
}

function Get-WorkflowFailureEvidence($Details) {
    $evidence = [ordered]@{}
    foreach ($field in @('input_state','on_write_started_ns','off_write_started_ns','off_confirmed_ns','off_reply_received_ns',
                         'load_duration_confirmed','load_evidence_tail_ns','load_evidence_resolution_ns','current_samples',
                         'sample_count','capacity_ah','initial_voltage_v','stop_reason','elapsed_s')) {
        if ($null -ne $Details.$field) { $evidence[$field] = $Details.$field }
    }
    foreach ($sampleName in @('last_measurement','failed_measurement')) {
        $sample = [ordered]@{}
        foreach ($field in @('voltage_v','current_a','power_w','resistance_ohm')) {
            if ($null -ne $Details.$sampleName.$field -and
                [string]$Details.$sampleName.$field -cmatch '^[+-]?[0-9]+(\.[0-9]+)?([Ee][+-]?[0-9]+)?$') {
                $sample[$field] = $Details.$sampleName.$field
            }
        }
        if ($sampleName -ceq 'failed_measurement' -and $Details.$sampleName.input_state -cin @('ON','OFF')) {
            $sample.input_state = $Details.$sampleName.input_state
        }
        if ($sample.Count) { $evidence[$sampleName] = $sample }
    }
    if ([string]$Details.original_error.code -cmatch '^[a-z_]+$') {
        $evidence.original_error_code = $Details.original_error.code
    }
    if ($null -ne $Details.cleanup) {
        $cleanup = [ordered]@{}
        foreach ($field in @('required','attempted','off_confirmed')) {
            if ($Details.cleanup.$field -is [bool]) { $cleanup[$field] = $Details.cleanup.$field }
        }
        if ($Details.cleanup.reason -ceq 'session_untrusted') { $cleanup.reason = 'session_untrusted' }
        if ([string]$Details.cleanup.error.code -cmatch '^[a-z_]+$') {
            $cleanup.error_code = $Details.cleanup.error.code
        }
        if ($cleanup.Count) { $evidence.cleanup = $cleanup }
    }
    return $evidence
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
    $triggerFixedPrepare = $CaseName -ceq 'trigger-fixed-prepare'
    $baselineHandoff = $isolatedSettings -or $triggerFixedPrepare
    $externalCapture = $Command -ceq 'trigger-observe' -and $CaseName -ceq 'trigger-external-observed'
    $captureReady = $false
    if ($workflowLoad -and $remaining -lt 10000) { throw 'Insufficient time remains for a bounded Workflow load trial.' }
    $observeOn = $timerOn -or $workflowLoad
    $notification = if ($timerOn) { 'Timer' } else { 'Workflow' }
    if ($timerOn) {
        $launcher = 'import sys; from electronicload_tool_cli.cli import main; raise SystemExit(main(timer_on_write_started=lambda ns: print("Timer INP ON write started:", ns, file=sys.stderr, flush=True)))'
    } elseif ($workflowLoad) {
        $launcher = 'import os, sys, threading; from electronicload_tool_cli.cli import main; stop=threading.Event(); threading.Thread(target=lambda: (os.read(sys.stdin.fileno(), 1), stop.set()), daemon=True).start(); raise SystemExit(main(contributor_stop=stop, workflow_on_write_started=lambda ns: print("Workflow INP ON write started:", ns, file=sys.stderr, flush=True)))'
    } elseif ($baselineHandoff) {
        $launcher = @'
import os, sys, threading
from electronicload_tool_cli.cli import main, _show_settings_baseline

stop = threading.Event()

def handoff(baseline):
    _show_settings_baseline(baseline)
    if os.read(sys.stdin.fileno(), 1) != b'A':
        stop.set()
        return
    threading.Thread(target=lambda: (os.read(sys.stdin.fileno(), 1), stop.set()), daemon=True).start()

raise SystemExit(main(contributor_stop=stop, settings_on_baseline=handoff))
'@
    } elseif ($DebugArtifacts -and $Command -in @('protection-set','protection-status')) {
        $launcher = 'import logging; logging.basicConfig(); logging.getLogger("electronicload_tool_core.protection").setLevel(logging.DEBUG); ' + $launcher
    }
    # Use one absolute deadline for LIST admission and the process wait,
    # including startup and private baseline handoff.
    $listSettingsDeadlineNs = 0L
    if ($isolatedSettings -and $CaseName -cin @('list-status','list-cc-settings')) {
        $listSettingsDeadlineNs = (Get-TimerMonotonicNs) + [long]$remaining * 1000000L
        $ExtraArgs += @('--deadline-ns', [string]$listSettingsDeadlineNs)
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
    $earlyDiagnostic = [Text.StringBuilder]::new()
    $baselineShown = $false
    $exitStatus = 'NOT STARTED'
    $terminationAttempted = $false
    $exchangeParsed = $false
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
        if ($observeOn -or $baselineHandoff -or $externalCapture) {
            # Read live notifications before waiting for the child to exit.
            $notificationStartNs = if ($observeOn) { $dispatchNs } else { Get-TimerMonotonicNs }
            $commandDeadlineNs = if ($listSettingsDeadlineNs) { $listSettingsDeadlineNs }
                else { $notificationStartNs + [long]$remaining * 1000000L }
            while ($true) {
                $read = $process.StandardError.ReadLineAsync()
                $remaining = Get-TimerRemainingMs $commandDeadlineNs
                if (-not $read.Wait($remaining)) { break }
                $line = $read.GetAwaiter().GetResult()
                if ($null -eq $line) { break }
                [void]$earlyDiagnostic.AppendLine($line)
                if ($baselineHandoff -and $line -cmatch '\ASettings recovery baseline \(private\): \{.+\}\z') {
                    # Display the recovery data before allowing any instrument writes. Keep it out of shareable reports.
                    Write-Host $line
                    $baselineShown = $true
                    if ((Get-TimerMonotonicNs) -ge $commandDeadlineNs) { break }
                    if ($triggerFixedPrepare) {
                        $original = $line.Substring('Settings recovery baseline (private): '.Length) | ConvertFrom-Json -ErrorAction Stop
                        if ($original.range -cnotin @('low','high') -or
                            $original.current_a -isnot [string] -or $original.selector_a -isnot [string] -or
                            [decimal]$original.current_a -lt 0 -or [decimal]$original.current_a -gt 2 -or
                            [decimal]$original.selector_a -lt [decimal]$original.current_a) {
                            Fail-Case $CaseName 'invalid_response' 'Invalid private Fixed CC recovery snapshot.'
                        }
                        $script:triggerFixedOriginal = $original
                        # Mark possible mutation before releasing the child.
                        $script:triggerFixedMayHaveChanged = $true
                    }
                    $process.StandardInput.BaseStream.WriteByte(65)
                    $process.StandardInput.BaseStream.Flush()
                    $stderr = $process.StandardError.ReadToEndAsync()
                    break
                }
                if ($externalCapture -and $line -ceq 'Trigger capture ready: apply external pulse now.') {
                    Write-Host $line
                    $captureReady = $true
                    $stderr = $process.StandardError.ReadToEndAsync()
                    break
                }
                if ($observeOn -and $line -cmatch "\A$notification INP ON write started: ([0-9]{1,19})\z") {
                    if (-not [long]::TryParse($Matches[1], [ref]$timerWriteStartedNs) -or
                        $timerWriteStartedNs -lt $dispatchNs -or $timerWriteStartedNs -gt (Get-TimerMonotonicNs)) {
                        if ($script:triggerManaged) { $script:triggerTrusted = $false }
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
            if ($script:triggerManaged) { $script:triggerTrusted = $false }
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
            if ($script:triggerManaged) { $script:triggerTrusted = $false }
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
            if ($script:triggerManaged) { $script:triggerTrusted = $false }
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
        $exchangeParsed = $true
        $entry['exit_code'] = $process.ExitCode
        if ($process.ExitCode -ne 0 -or $result.ok -ne $true) {
            $code = [string]$result.error.code
            $details = $result.error.details
            $entry['error_code'] = $(if ($code -cmatch '^[a-z_]+$') { $code } else { 'execution_failed' })
            # Explicitly select diagnostic evidence. Never publish arbitrary
            # exception text, nested errors, VISA data or recovery baselines.
            $evidence = [ordered]@{}
            if ($isolatedSettings -or $Command -cin @('list-status','fixed-slew-status','trigger-observe') -or
                ($Command -ceq 'set-list-cc' -and $CaseName.StartsWith('trigger-'))) {
                $evidence = Get-SettingsFailureEvidence $result.error
                if ($code -ceq 'settings_restore_unconfirmed') {
                    foreach ($field in @('original_error','restoration_error')) {
                        $nested = $details.$field
                        if ([string]$nested.code -cmatch '^[a-z_]+$') {
                            $safe = Get-SettingsFailureEvidence $nested
                            if ($field -ceq 'restoration_error' -and $nested.code -ceq 'readback_mismatch') {
                                # A restore mismatch can contain private original settings.
                                $safe.Remove('expected')
                                $safe.Remove('observed')
                            }
                            $safe.error_code = $nested.code
                            $evidence[$field] = $safe
                        }
                    }
                }
            }
            if ($Command -ceq 'trigger-observe' -and $details.trigger_source -cin @('HOLD','BUS','TIMER','EXTERNAL')) {
                foreach ($field in @('trigger_source','observation_phase','software_trigger_write_attempted',
                                     'software_trigger_write_confirmed','sample_count','samples','fixed_baseline_class')) {
                    if ($null -ne $details.$field) { $evidence[$field] = $details.$field }
                }
            }
            if ($workflowLoad) { $evidence = Get-WorkflowFailureEvidence $details }
            if ($Command -ceq 'protection-clear' -and $null -ne $details.condition.raw) {
                $evidence.condition = $details.condition
            }
            if ($result.error.message -cin @('Fixed readback differs from the expected setting.',
                    'Original CC selector cannot safely restore the setpoint.',
                    'Current slew is outside the bounds for the reported range.',
                    'Invalid LIST count.', 'LIST step readback is outside model limits.',
                    'The original LIST current range cannot be restored.',
                    'Pre-trigger current baseline was not observed.', 'Observation deadline reached.',
                    'No time remains for the complete software Trigger observation.',
                    'Current samples did not establish the expected waveform.',
                    'Current sample arrived after the deadline.', 'LIST status arrived after the deadline.',
                    'LIST observation encountered a protection fault.', 'LIST was already running before the trial.',
                    'Timed Load lost current or Input ON.', 'Timed Load sample coverage is insufficient.',
                    'Timed Load final sample is too far from scheduled OFF.',
                    'OFF schedule or final sample coverage is insufficient.',
                    'Protection remains active after Clear.', 'Input turned ON after Protection Clear.')) {
                $evidence.message = $result.error.message
            }
            if ($evidence.Count) { $entry['error_diagnostics'] = $evidence }
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
            if ($script:triggerManaged -and ($code -in @('query_failed','write_failed',
                    'visa_open_failed','visa_configure_failed','cleanup_failed','trace_session_untrusted',
                    'identity_mismatch','invalid_identity') -or $null -ne $details.cleanup_errors -or
                    $process.ExitCode -notin @(2,3) -or $result.ok -ne $false -or
                    $code -cnotmatch '^[a-z_]+$')) {
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
                    'input_not_off','input_not_on','input_timer_enabled','unsafe_trigger_source',
                    'trigger_observation_unconfirmed')) {
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
        if ($baselineHandoff -and -not $baselineShown) {
            Fail-Case $CaseName 'settings_baseline_unconfirmed' 'The original settings were not displayed before the trial.'
        }
        if ($workflowLoad) {
            $entry['error_diagnostics'] = Get-WorkflowFailureEvidence $result.result
            $finished = $result.result.off_confirmed_ns
            if (-not $timerWriteStartedNs -or $result.result.on_write_started_ns -ne $timerWriteStartedNs -or
                $null -eq $finished -or $finished -lt $timerWriteStartedNs -or $finished -gt $TimerObservationDeadlineNs -or
                $result.result.cleanup.off_confirmed -ne $true -or $result.result.input_state -cne 'OFF') {
                $entry['manual_action_required'] = $true
                $entry.error_diagnostics.message = 'The CLI did not confirm Input OFF within the loading deadline.'
                Write-Host "FAIL ${CaseName}: use the independent hardware cutoff now. Input OFF is unconfirmed."
                Fail-Case $CaseName 'workflow_cleanup_unconfirmed' 'The CLI did not confirm Input OFF within the loading deadline.'
            }
            if ($Command -ceq 'cc-timed-load') {
                $entry['load_duration_confirmed'] = $false
                $offWrite = $result.result.off_write_started_ns
                if ($null -eq $offWrite -or $offWrite -lt ($timerWriteStartedNs + 5000000000L) -or $offWrite -gt $finished) {
                    $entry.error_diagnostics.message = 'The SCPI Input OFF time does not confirm the 5 s load schedule.'
                    Fail-Case $CaseName 'workflow_load_unconfirmed' 'The SCPI Input OFF time does not confirm the 5 s load schedule.'
                }
                if ($result.result.load_duration_confirmed -ne $true -or
                    $result.result.load_evidence_source -cne 'instrument_current_samples' -or
                    $null -eq $result.result.load_evidence_tail_ns -or
                    $result.result.load_evidence_tail_ns -lt 0 -or
                    $result.result.load_evidence_tail_ns -gt 1000000000L -or
                    $result.result.load_evidence_resolution_ns -ne 1000000000L -or
                    @($result.result.current_samples).Count -lt 2) {
                    $entry.error_diagnostics.message = 'Automatic sampled current evidence is insufficient.'
                    Fail-Case $CaseName 'workflow_load_unconfirmed' 'Automatic sampled current evidence is insufficient.'
                }
                $entry['load_duration_confirmed'] = $true
            }
            $entry.Remove('error_diagnostics')
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
        if ($externalCapture -and -not $captureReady) {
            Fail-Case $CaseName 'trigger_observation_unconfirmed' 'External capture readiness was not confirmed.'
        }
        $entry.status = if ($workflowLoad -or $entry.Contains('readback_comparison') -or $CaseName -ceq 'timer-expiry' -or
            ($Command -ceq 'trigger-observe' -and $CaseName.EndsWith('-observed'))) {
            'NOT RUN'
        } else { 'PASS' }
        if ($CaseName -ceq 'list-cc-settings' -and $result.result.save_confirmed -ne $true) {
            $entry.status = 'NOT RUN'
            $entry['error_code'] = 'list_save_unconfirmed'
        }
        $entry['response'] = $result
        if ($null -ne $result.result.visa) {
            # Compare metadata before shortening library paths in shareable output.
            $entry['runtime_library'] = $result.result.visa.library
            $entry['runtime_manufacturer'] = $result.result.visa.manufacturer
        }
        return $result
    } finally {
        # An interrupted child without a complete response cannot authorize a new session.
        if ($started -and -not $exchangeParsed) {
            if ($script:triggerManaged) { $script:triggerTrusted = $false }
            if ($traceCase) { $script:traceCommunicationUntrusted = $true }
        }
        if (($workflowLoad -or $baselineHandoff) -and $started -and -not $terminationAttempted -and -not $process.HasExited) {
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
                elseif ($listSettingsDeadlineNs) { Get-TimerRemainingMs $listSettingsDeadlineNs }
                elseif ($baselineHandoff) { $DeadlineMs } else { 10000 }
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
                          diagnostic=$earlyDiagnostic.ToString() + $diagnostic; exitStatus=$exitStatus}
            if ($script:triggerPowered) { $script:triggerArtifacts += $artifact }
            elseif ($script:protectionPowered) { $script:protectionArtifacts += $artifact }
            elseif ($script:timerPowered) { $script:timerArtifacts += $artifact }
            else { Save-CommandArtifacts $artifact }
        }
        if (-not ($script:triggerPowered -or $script:protectionPowered -or $script:timerPowered)) { Save-Report }
    }
}

function Confirm-WorkflowLoad([string]$CaseName) {
    $entry = $script:report.results | Where-Object { $_.name -ceq $CaseName }
    $result = $entry.response.result
    if ($result.input_state -cne 'OFF' -or $result.cleanup.off_confirmed -ne $true -or
        ($CaseName -ceq 'workflow-cc-timed-load' -and
            ($result.load_duration_confirmed -ne $true -or
             $result.load_evidence_source -cne 'instrument_current_samples' -or
             $null -eq $result.load_evidence_tail_ns -or
             $result.load_evidence_tail_ns -lt 0 -or
             $result.load_evidence_tail_ns -gt 1000000000L -or
             $result.load_evidence_resolution_ns -ne 1000000000L -or
             @($result.current_samples).Count -lt 2)) -or
        ($CaseName -ceq 'workflow-cc-discharge' -and
            ($result.stop_reason -cne 'capacity_cutoff' -or $result.capacity_ah -lt 0.00010))) {
        $entry['error_diagnostics'] = Get-WorkflowFailureEvidence $result
        $entry.error_diagnostics.message = 'Automatic Workflow evidence is insufficient.'
        Fail-Case $CaseName 'workflow_load_unconfirmed' 'Automatic Workflow evidence is insufficient.'
    }
    $entry.status = 'PASS'
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
        if ($script:triggerManaged) { $script:triggerTrusted = $false }
        Fail-Case $CaseName 'runtime_mismatch' 'VISA runtime evidence changed between commands; validation cannot pass.'
    }
    if ($script:slowRiseEnableAttempted) { $script:slowRiseCommunicationTrusted = $true }
}

function Assert-InputOff([object]$Result, [string]$CaseName) {
    if ($script:triggerManaged) {
        $script:triggerOffConfirmed = $Result.result.input_state -ceq 'OFF'
        $script:report.trigger_input_off_unconfirmed = -not $script:triggerOffConfirmed
    }
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
        # Start a single observation process immediately after ON confirmation;
        # no repeated interpreter startup or VISA identity/open in the sample loop.
        $lastOnNs = 0L
        $lastOnCompletedNs = 0L
        if ($nowNs -ge $deadlineNs) {
            Fail-Case $Step.name 'timer_expiry_unconfirmed' 'Timer ON confirmation exhausted the observation window.'
        }
        $settings = @{seconds=[int]$TimerSeconds;on_write_started_ns=$on.on_write_started_ns;
            on_confirmed_ns=$on.on_confirmed_ns} | ConvertTo-Json -Compress
        $result = Invoke-LiveCommand -Command $Step.command -CaseName $Step.name `
            -ExtraArgs @('--hardware-validation','input-timer','--validation-settings-json',$settings) `
            -DeadlineMs $script:perCommandDeadlineMs -TimerObservationDeadlineNs $deadlineNs
        Assert-Runtime $result $Step.name
        if ($entry.observed_at_ns -gt $deadlineNs) {
            Fail-Case $Step.name 'timer_expiry_unconfirmed' 'Timer observation returned after the deadline.'
        }
        foreach ($status in @($result.result.timer_observations)) {
            if ($null -eq $status) { break }
            $query = $status.timer_timing
            $observedMs = if ($null -ne $query.query_completed_ns) {
                $entry.observed_at_ms - [Math]::Floor(($entry.observed_at_ns - $query.query_completed_ns) / 1000000)
            } else { $null }
            $observations += [ordered]@{
                elapsed_ms = $(if ($null -ne $observedMs) { $observedMs - $script:timerOnStartMs })
                since_on_confirmed_ms = $(if ($null -ne $observedMs) { $observedMs - $script:timerOnConfirmedMs })
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
                $query.query_started_ns -lt $lastOnCompletedNs -or
                $query.query_completed_ns -lt $query.query_started_ns -or
                $query.query_completed_ns -gt $entry.observed_at_ns) {
                Fail-Case $Step.name 'timer_timing_unconfirmed' 'The SCPI Input state query timing was not confirmed.'
            }
            # Post-expiry Timer enable retention is reported as observed.
            if ($status.input_state -ceq 'OFF') {
                if ($query.query_completed_ns -lt $earliestExpiryNs) {
                    Fail-Case $Step.name 'timer_expiry_early' 'Input was observed OFF before the earliest possible Timer expiry.'
                }
                # Functional evidence may straddle expiry, but it may not
                # exceed the approved one-second observation uncertainty.
                $widthNs = $query.query_completed_ns - $lastOnNs
                $entry['timer_expiry_bracket'] = [ordered]@{
                    last_on_started_ns = $lastOnNs
                    last_on_completed_ns = $lastOnCompletedNs
                    first_off_started_ns = $query.query_started_ns
                    first_off_completed_ns = $query.query_completed_ns
                    expected_expiry_ns = $earliestExpiryNs
                    uncertainty_ns = $widthNs
                    resolution_ns = 1000000000L
                }
                if ($lastOnNs -le 0 -or $lastOnCompletedNs -gt $query.query_started_ns -or
                    $widthNs -lt 0 -or $widthNs -gt 1000000000L) {
                    Fail-Case $Step.name 'timer_automatic_off_unconfirmed' 'The first OFF bracket exceeds the approved observation uncertainty or lacks ON evidence.'
                }
                $entry['automatic_off_observed'] = $true
                return
            } else {
                if (-not $status.enabled) {
                    Fail-Case $Step.name 'readback_mismatch' 'Timer was disabled while Input was ON.'
                }
                $lastOnNs = $query.query_started_ns
                $lastOnCompletedNs = $query.query_completed_ns
            }
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
    $entry = $script:report.results | Where-Object { $_.name -ceq 'timer-expiry' }
    if ($entry.automatic_off_observed -ne $true) {
        Fail-Case 'timer-expiry' 'timer_automatic_off_unconfirmed' 'Automatic OFF timing evidence was not confirmed.'
    }
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
    $snapshotPlan = @{name='trigger-snapshot';command='trigger-status';args=$triggerScope}
    $restorePlan = @{name='trigger-restore';command='trigger-configure';
        args=($triggerScope + @('--source','MANUAL','--interval-s','0.01'));
        condition='Plan uses example settings. LIVE requires original Source and Timer readback, trusted communication and confirmed Input OFF.'}
    $script:triggerPlans = @($snapshotPlan)
    if (-not $triggerSelected) {
        $script:triggerPlans += $restorePlan
        return
    }
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
    $script:triggerPlans += @{name='trigger-fixed-prepare';command='set-cc';execution='live';
        args=($triggerScope + @('--current','0','--range','low','--validation-settings-json','{"fixed_cc_baseline":"prepare"}'));
        condition='Input OFF; save private original Fixed CC and establish zero baseline'}
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
        $baselineJson = if ($source -ceq 'HOLD') { ',"fixed_cc_baseline_a":0' } else { '' }
        $expected = @('--validation-settings-json', ('{"function":"' + $function +
            '","configuration":' + $configJson + ',"trigger":' + $triggerJson + '}' + $baselineJson + '}'))
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
        $captureArgs = $triggerScope + $expected + @('--observation-seconds',[string]$TriggerObservationSeconds,'--confirm')
        if ($source -in @('HOLD','BUS')) {
            $script:triggerPlans += @{name="$prefix-dispatch";command='trigger-observe';args=$captureArgs;execution='live';
                condition='Software Trigger write exchange (not proof of physical dispatch); single-session capture also supplies waveform evidence to the observed case.'}
            $observation = @{name="$prefix-observed";command='automatic-observation';args=@();
                condition='Use sampled waveform evidence and confirmed OFF from this trial.'}
        } else {
            $observation = @{name="$prefix-observed";command='trigger-observe';args=$captureArgs;execution='live';
                condition='Sample current within the observation window; EXTERNAL requires equipment confirmation with Input OFF before ON and a physical pulse at capture readiness.'}
        }
        if ($source -in @('HOLD','BUS')) {
            $script:triggerPlans += @{name="$prefix-off";command='input-off';args=$triggerScope}
            $script:triggerPlans += $observation
        } else {
            $script:triggerPlans += $observation
            $script:triggerPlans += @{name="$prefix-off";command='input-off';args=$triggerScope}
        }
        $release = if ($isList) { 'list-exit' } else { 'transient-disable' }
        $script:triggerPlans += @{name="$prefix-release";command=$release;args=$triggerScope}
    }
    $script:triggerPlans += @{name='trigger-fixed-restore';command='set-cc';execution='live';
        args=($triggerScope + @('--current','0','--range','low','--validation-settings-json','{"fixed_cc_baseline":"restore","selector_a":"0"}'));
        condition='Trusted communication, confirmed OFF, restore private original Fixed CC'}
    $script:triggerPlans += $restorePlan
}

function Invoke-TriggerStep([string]$Name, [int]$DeadlineMs = 30000, [int]$TerminationMs = 5000,
                            [long]$ObservationDeadlineNs = 0, [switch]$Cleanup, [string[]]$ArgsOverride) {
    $step = $script:triggerPlans | Where-Object { $_.name -ceq $Name }
    $entry = $script:report.results | Where-Object { $_.name -ceq $Name }
    $exchangeConfirmed = $false
    try {
        $extra = if ($PSBoundParameters.ContainsKey('ArgsOverride')) { $ArgsOverride } else { $step.args }
        if ($step.command -ceq 'trigger-observe') {
            $extra += @('--deadline-ns',[string]$ObservationDeadlineNs)
        }
        $request = @{Command=$step.command; CaseName=$Name; ExtraArgs=$extra;
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

function Test-SourceConnectedTrials([string[]]$Modes) {
    $connectedModes = @('cc','cv','cr','cw','transient-cc','transient-cv','transient-cr','transient-cw',
        'trace','trigger','input-timer','protection','sequence','workflow')
    return (@($Modes | Where-Object { $_ -in $connectedModes }).Count -gt 0)
}

function Confirm-OperatorStart([string[]]$Modes, $Isolated, [bool]$ConnectedTrials) {
    Write-Host "Operator resource check (LIVE): Target: $Target; Resource: $Resource; Backend: $Backend"
    Write-Host 'Physical checks before pressing Enter:'
    # Only source-connected trials require the external source to be prepared.
    if ($ConnectedTrials) {
        Write-Host "- Confirm the external source is manually set to $SourceVoltageLimitV V with a $SourceCurrentLimitA A current limit."
        Write-Host '- Authorize the verified Average count 2 measurement baseline with Input OFF before source-connected trials; restore the original count after successful trials unless Measurement Extensions is selected.'
    }
    Write-Host '- Confirm physical load Input OFF, safe wiring/DUT and tolerance of every selected setting (use -Verbose or the report).'
    Write-Host '- Confirm no other application controls this instrument and an independent manual Input OFF procedure is available.'
    if ('input-timer' -in $Modes) {
        Write-Host '- Input Timer: prepare the independent 10 s hardware cutoff for the actual SCPI Input ON write of the 5 s Timer trial. It must not intervene during the 6 s observation.'
    }
    if ('workflow' -in $Modes) {
        Write-Host '- Workflow: use the dedicated 5 V / 0.8 A source and 0.1 A CC trials. Prepare the independent 10 s hardware cutoff for each Timed Load and Discharge SCPI Input ON write, including OFF confirmation.'
    }
    if ('input-timer' -in $Modes -or 'workflow' -in $Modes) {
        Write-Host '- The cutoff must be armed for every selected Timer/Workflow load trial, including any required automatic rearming between trials. Its clock must start no later than the actual SCPI ON write, not at this Enter, and include OFF confirmation. Do not manually change the source or load or intervene during the observation except for safety. Cancel if this setup cannot enforce the limit for every trial without a readiness pause.'
    }
    if ('protection' -in $Modes) {
        Write-Host '- Protection: confirm a dedicated 5 V / 0.8 A source is ON, no important DUT, manual cutoff ready, and Timer, Short, Slow Rise and Transient OFF. The script can prepare Fixed CC from Fixed CV or CW while Input is OFF.'
        Write-Host '- Protection: after each software trip, turn OFF and isolate the external source when instructed; reconnect and energize the same source when instructed for the next powered trial. Keep the same source and wiring for the hardware-power baseline and limited observation.'
    }
    if ('sequence' -in $Modes) {
        Write-Host '- Sequence: authorize all four bounded Fixed Sequence Input ON operations (CC, CV, CR, CW) using the dedicated source and reviewed VISA runtime; the script checks the runtime and each trial.'
    }
    if ('measurement-extensions' -in $Modes) {
        Write-Host '- Confirm the Average count may be set to 8 and left at that setting.'
    }
    if ('trace' -in $Modes -or 'trigger' -in $Modes -or
        @($Modes | Where-Object { $_ -like 'transient-*' }).Count -gt 0) {
        Write-Host '- Authorize temporary Trigger Source settings with Input OFF and restoration of the original Source and Timer Interval when safe. Keep external trigger signals inactive outside the EXTERNAL trial.'
        if ('trigger' -in $Modes) { Write-Host '- Authorize a temporary zero Fixed CC baseline and exact restoration of its saved setpoint and selector when safe.' }
    }
    if ('trigger' -in $Modes) {
        Write-Host '- Confirm external On/Off and analog programming are disabled and external trigger wiring was reviewed while de-energized.'
        Write-Host "- EXTERNAL Trigger equipment will be confirmed with Input OFF immediately before its trial: $TriggerExternalSetup"
        Write-Host '- Verify rear TRIG and shared negative wiring and compatible TTL signal levels. Keep the signal inactive until each EXTERNAL capture-ready message, then apply one TTL low pulse (>10 us).'
    } elseif ('workflow' -in $Modes -or $Isolated.Count) {
        Write-Host '- Confirm external On/Off and analog programming are disabled.'
    }
    if ($Isolated.Count) {
        if ($ConnectedTrials) {
            Write-Host '- Keep the source connected for the powered trials. When prompted at the transition, physically disconnect the source and DUT and keep them disconnected for the isolated batch.'
        } else {
            Write-Host '- Physically disconnect the source and DUT before pressing Enter and keep them disconnected throughout the isolated settings batch.'
        }
        if (@($Isolated | Where-Object { $_.name -ceq 'fixed-status-cc' }).Count) {
            Write-Host '- Automatic Readback temporarily applies known settings, including Short with isolated terminals, and verifies restoration. Readback itself does not overwrite a LIST slot.'
        }
        if (@($Isolated | Where-Object { $_.name -ceq 'list-status' }).Count) {
            Write-Host '- Automatic LIST Readback: an empty working LIST (Step=0, Count=0) is initialized to a verified two-step baseline, retained after validation; valid existing LISTs are restored.'
        }
        if (@($Isolated | Where-Object { $_.name -ceq 'fixed-slew-status' -or $_.name -ceq 'transient-status' -or $_.name -ceq 'auxiliary-controls-slew' }).Count) {
            Write-Host '- Slew validation: when an original instrument SCPI slew exceeds the conservative write limit, the test installs and retains a safe writable baseline instead of writing the original value back.'
        }
        if (@($Isolated | Where-Object { $_.name -ceq 'list-cc-settings' }).Count) {
            Write-Host "- LIST CC: the initial Enter authorizes overwriting Scratch Slot $ListScratchSlot; the slot retains test configuration A. Existing valid working LIST settings are restored. An empty working LIST (Step=0, Count=0) is initialized to a verified two-step test baseline and retains that baseline after the run. Cancel if this slot or the empty buffer must be preserved."
        }
        if (@($Isolated | Where-Object { $_.name -ceq 'list-cc-settings' -or
                ($_.name -like 'auxiliary-controls-*' -and -not $_.ContainsKey('reuse_from')) }).Count) {
            Write-Host '- For independent Auxiliary/LIST trials, Fixed CC with Short, Transient and Timer OFF is required at the isolated batch; the existing guarded mixed-run handoff applies when planned.'
        }
    }
    if ('workflow' -in $Modes -or @($Modes | Where-Object { $_ -notin (@('readback','system-query','workflow') + $extensionModes + $featureModes) }).Count) {
        Write-Host '- Selected trials may deliberately turn Input ON; the script explicitly commands and verifies OFF, but cannot guarantee OFF after communication failures.'
    }
    if ('protection' -in $Modes) {
        $script:watch = [Diagnostics.Stopwatch]::StartNew()
        return (Read-ValidationAnswer 'After completing these checks, press Enter to run selected CLI cases (Ctrl+C to cancel)' 'identify' -DiscardPendingKeys)
    }
    return (Read-Host 'After completing these checks, press Enter to run selected CLI cases (Ctrl+C to cancel)')
}

function Confirm-IsolatedBatch([bool]$AlreadyConfirmed, [bool]$ConnectedTrials, [string]$CaseName) {
    if (-not $AlreadyConfirmed -and $ConnectedTrials) {
        # A real physical transition cannot be authorized as already completed
        # at the start of a powered run. Slot consent was given at startup.
        $answer = Read-ValidationAnswer 'Physically disconnect the source and DUT now. Keep both disconnected for the isolated settings batch. Press Enter when disconnected (Ctrl+C to cancel)' $CaseName -DiscardPendingKeys
        if ($null -eq $answer -or $answer -cne '') {
            Fail-Case $CaseName 'operator_confirmation_required' 'Physical isolation was not confirmed.'
        }
    }
    return $true
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
                @{name="$prefix-baseline-on";command='operator-observation';args=@();
                   condition='reconnect and energize source after physical isolation, if required'},
                @{name="$prefix-baseline-trial";command='input-on';args=$set + @('--protection-trial','hardware-power-baseline')},
                @{name="$prefix-baseline-off";command='input-off';args=@()}
            )
        }
        $script:protectionPlans += @(
            @{name="$prefix-set";command='protection-set';args=$set + $trialArgs},
            @{name="$prefix-on";command='operator-observation';args=@();
               condition='reconnect and energize source after physical isolation, if required'},
            @{name="$prefix-trial";command='input-on';args=$set + @('--protection-trial',$trial.name)}
        )
        $off = @{name="$prefix-off";command='input-off';args=@()}
        if ($trial.flags.Count -gt 0) {
            $off.condition = 'cleanup if software protection did not turn Input OFF'
        }
        $script:protectionPlans += $off
        $script:protectionPlans += @{name="$prefix-events";command='protection-status';args=$script:protectionScope + @('--events')}
        if ($trial.flags.Count -gt 0) {
            $script:protectionPlans += @{name="$prefix-source-off";command='operator-observation';args=@()}
        }
        $script:protectionPlans += @(
            @{name="$prefix-restore-guard";command='input-state';args=@()},
            @{name="$prefix-restore-settings";command='protection-set';args=$set + $trialArgs;
              condition='use the original values read before the trial'},
            @{name="$prefix-restore-cc";command='set-cc';args=@('--current','0.20','--range','low');
              condition='use the original Fixed CC settings'}
        )
        if ($trial.flags.Count -gt 0) {
            $script:protectionPlans += @{name="$prefix-clear";command='protection-clear';args=$set;
                condition='physical isolation confirmed; original thresholds and Fixed CC restored'}
        }
        $script:protectionPlans += @{name="$prefix-final";command='protection-status';args=$status}
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

function Invoke-PoweredProtectionTrial([string]$Prefix, [string]$Trial, [switch]$ExplicitOff,
                                       [switch]$ReconnectSource) {
    if ($ReconnectSource) {
        Confirm-ProtectionOperator "$Prefix-on" "Reconnect and energize the same dedicated 5 V / 0.8 A source with the manual cutoff ready. Press Enter when the physical operation is complete (Ctrl+C to cancel)."
    } else {
        # The initial Enter covers the unchanged source. No physical action.
        ($script:report.results | Where-Object { $_.name -ceq "$Prefix-on" }).status = 'SKIP'
    }
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

function Restore-Protection([string]$Prefix, $Original, $Fields, [bool]$RestoreCc,
                            [switch]$ClearAfterRestore) {
    $script:report.protection_restore_unconfirmed = $true
    if (-not $script:protectionTrusted -or $script:report.protection_input_off_unconfirmed) {
        Write-Host 'Protection restoration needs trusted VISA and confirmed Input OFF. Verify manually.'
        return
    }
    $null = Invoke-ProtectionStep "$Prefix-restore-guard"
    if ($Fields.Count -gt 0) {
        $settings = [ordered]@{}
        foreach ($field in $Fields) { $settings[$field] = $Original.settings.$field }
        # IT8812C factory hardware protection MAX is 260 W. Restore an
        # original MAX with the existing named default, without admitting an
        # ordinary operator-supplied numeric 260 W setting (limit remains 250 W).
        if ($Fields.Count -eq 1 -and $Fields[0] -ceq 'hardware_power_limit' -and
            [decimal]$settings.hardware_power_limit -eq 260) {
            $arguments = $script:protectionScope + @('--confirm','--restore-defaults','hardware-power')
        } else {
            $arguments = $script:protectionScope + @('--confirm') + @(Get-ProtectionSetArgs $settings)
        }
        $null = Invoke-ProtectionStep "$Prefix-restore-settings" -ArgsOverride $arguments
    }
    if ($RestoreCc) {
        $arguments = @('--current',$Original.fixed_cc.current_a,'--range',$Original.fixed_cc.range)
        $null = Invoke-ProtectionStep "$Prefix-restore-cc" -ArgsOverride $arguments
    }
    $clearFailure = $null
    if ($ClearAfterRestore) {
        try {
            $null = Invoke-ProtectionStep "$Prefix-clear"
        } catch {
            $clearFailure = $_
            if (-not $script:protectionTrusted -or $script:report.protection_input_off_unconfirmed) { throw }
        }
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
    if ($ClearAfterRestore -and ($null -eq $final.result.condition.raw -or
        @($final.result.condition.flags | Where-Object { $_ -cin @('VF','OC','OP','OT','RRV','LRV','OV','PS') }).Count -gt 0)) {
        Fail-Case "$Prefix-final" 'protection_still_active' 'Protection fault remains after restoration and Clear.'
    }
    if ($null -ne $clearFailure) { throw $clearFailure }
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

    $sourceIsolated = $false
    foreach ($trial in $script:protectionTrials) {
        $prefix = "protection-$($trial.name)"
        $script:report.protection_trials[$trial.name] = 'FAIL'
        $original = Invoke-ProtectionStep "$prefix-original"
        $changeCc = $original.result.fixed_cc.range -cne 'low' -or
            [decimal]$original.result.fixed_cc.current_a -ne 0.20
        $fields = @()
        $clearAfterRestore = $false
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
                $baseline = Invoke-PoweredProtectionTrial "$prefix-baseline" 'hardware-power-baseline' -ExplicitOff -ReconnectSource:$sourceIsolated
                $sourceIsolated = $false
                if ($baseline.input_state -cne 'ON' -or 'PS' -cin $baseline.condition.flags -or
                    $baseline.voltage_v -lt 4.75 -or $baseline.voltage_v -gt 5.25 -or
                    $baseline.current_a -lt 0.18 -or $baseline.current_a -gt 0.22) {
                    Fail-Case "$prefix-baseline-trial" 'power_baseline_unconfirmed' 'The source did not sustain approximately 0.20 A at 5 V before limiting.'
                }
            }
            $fields = @($trial.settings.Keys)
            $null = Invoke-ProtectionStep "$prefix-set"
            $observed = Invoke-PoweredProtectionTrial $prefix $trial.name -ExplicitOff:($trial.flags.Count -eq 0) -ReconnectSource:$sourceIsolated
            $sourceIsolated = $false
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
                Confirm-ProtectionOperator "$prefix-source-off" 'Turn OFF and isolate the external source now. Press Enter only after the source is isolated and the physical fault condition is removed (Ctrl+C to cancel).'
                $sourceIsolated = $true
                $clearAfterRestore = $true
            }
        } finally {
            Restore-Protection $prefix $original.result $fields $changeCc -ClearAfterRestore:$clearAfterRestore
        }
        if ($script:report.protection_restore_unconfirmed) {
            Fail-Case "$prefix-final" 'protection_restore_unconfirmed' 'Protection restoration was not confirmed.'
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
        Write-Host "Trigger $($Trial.source), CLI readback confirmed. The script will sample the instrument current automatically."
        if ($Trial.isList) {
            Write-Host "Expect LIST CC levels $LevelA A then $LevelB A for $TriggerListWidthA s / $TriggerListWidthB s, repeated $TriggerListRepeat time(s)."
        } elseif ($Trial.source -ceq 'HOLD') {
            Write-Host "Expect Fixed CC baseline 0 A, a pulse at A ($LevelA A) for $($script:triggerPulseWidth) s, then B ($LevelB A)."
        } else {
            Write-Host "Expect baseline B ($LevelB A), a pulse at A ($LevelA A) for $($script:triggerPulseWidth) s, then a return to B."
        }
        switch ($Trial.source) {
            'EXTERNAL' {
                Write-Host 'Keep the trigger signal inactive until the capture-ready message. Then apply one TTL low pulse (>10 us) to rear TRIG and its shared negative terminal.'
                Write-Host "Confirm the reviewed equipment and wiring: $TriggerExternalSetup"
            }
            'TIMER' { Write-Host "The script will check at least two Timer pulses at $($Trial.interval) s intervals." }
            default { Write-Host 'The script will send one software trigger after Input ON is confirmed.' }
        }
        $awaitingObservation = $true
        if ($Trial.source -ceq 'EXTERNAL') {
            $ack = Read-ValidationAnswer 'With Input OFF, confirm the reviewed external trigger equipment is ready. Press Enter to begin (Ctrl+C to cancel)' "$prefix-observed" -DiscardPendingKeys
            if ($null -eq $ack -or $ack -cne '') {
                Fail-Case "$prefix-observed" 'operator_cancelled' 'External trigger equipment was not confirmed.'
            }
        }
        $hardStop = $script:watch.ElapsedMilliseconds + $TriggerMaxOnSeconds * 1000
        if ($hardStop -gt $totalDeadlineMs) { throw 'Insufficient total time for a bounded Trigger trial.' }
        # Reserve part of the operator's extra time for OFF and for ending
        # a timed-out command before another VISA process can start.
        $marginMs = ($TriggerMaxOnSeconds - $TriggerObservationSeconds) * 1000
        $offReserveMs = [Math]::Min($perCommandDeadlineMs, [Math]::Floor($marginMs / 2))
        $terminationMs = [Math]::Min(5000, [Math]::Floor($offReserveMs / 2))
        $startOffBy = $hardStop - $offReserveMs - $terminationMs
        $observationWindowMs = $TriggerObservationSeconds * 1000
        $onDeadline = $startOffBy - $script:watch.ElapsedMilliseconds - $observationWindowMs
        if ($Trial.source -ceq 'TIMER' -and $onDeadline -gt 0) {
            # Allocate guard/startup time from the existing trial budget, never
            # from the reserved OFF or process termination time.
            $guardAllowance = [Math]::Max(0,[Math]::Min(5000,[Math]::Min(
                [Math]::Floor($onDeadline / 2),$perCommandDeadlineMs - $observationWindowMs)))
            $observationWindowMs += [int]$guardAllowance
            $onDeadline -= [int]$guardAllowance
        }
        if ($onDeadline -le 0) { throw 'Insufficient time for Trigger Input ON and observation.' }
        $script:triggerOffConfirmed = $false
        $script:triggerPowered = $true
        try {
            $on = Invoke-TriggerStep "$prefix-on" -DeadlineMs $onDeadline -TerminationMs $terminationMs
            if ($on.result.input_state -cne 'ON') {
                Fail-Case "$prefix-on" 'input_on_not_confirmed' 'Trigger Input ON was not confirmed.'
            }
            $observeUntil = $script:watch.ElapsedMilliseconds + $observationWindowMs
            if ($observeUntil -gt $startOffBy) { throw 'Insufficient time remains to observe before Input OFF.' }
            Write-Host "Observation started for $($Trial.source)."
            $captureName = if ($Trial.source -in @('HOLD','BUS')) { "$prefix-dispatch" } else { "$prefix-observed" }
            $captureBudget = [Math]::Min($script:perCommandDeadlineMs, $observeUntil - $script:watch.ElapsedMilliseconds)
            if ($captureBudget -le 500) { throw 'Insufficient time remains for automatic Trigger observation.' }
            $captureDeadline = (Get-TimerMonotonicNs) + ([long]$captureBudget - 500) * 1000000L
            $capture = Invoke-TriggerStep $captureName -DeadlineMs $captureBudget -TerminationMs $terminationMs `
                -ObservationDeadlineNs $captureDeadline
            if ($Trial.source -cin @('HOLD','BUS')) {
                $dispatch = $script:report.results | Where-Object { $_.name -ceq $captureName }
                $dispatch['trigger_dispatch_exchange'] = 'CONFIRMED'
            }
            $remaining = $observeUntil - $script:watch.ElapsedMilliseconds
            if ($remaining -gt 0 -and $Trial.source -cne 'TIMER') { Start-Sleep -Milliseconds $remaining }
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
        $evidence = $capture.result.observation
        $requiredEvents = if ($Trial.source -ceq 'TIMER') { 2 } elseif ($Trial.isList) { $TriggerListRepeat } else { 1 }
        if ($capture.result.validation_only -ne $true -or $capture.result.validation_scope -cne 'trigger' -or
            $capture.result.trigger_source -cne $Trial.source -or $capture.result.waveform_completion_known -ne $true -or
            $evidence.source -cne 'instrument_current_samples' -or $evidence.sample_count -lt 3 -or
            $evidence.events_confirmed -lt $requiredEvents -or
            ($Trial.source -ceq 'HOLD' -and $evidence.fixed_baseline_class -cne 'zero') -or
            ($Trial.isList -and $evidence.list_run_completed -ne $true) -or -not $script:triggerOffConfirmed) {
            Fail-Case "$prefix-observed" 'trigger_observation_unconfirmed' 'Automatic Trigger waveform or OFF evidence was insufficient.'
        }
        $entry['trigger_observation'] = $evidence
        $entry.response = $capture
        if ($script:watch.ElapsedMilliseconds -ge $totalDeadlineMs) {
            Fail-Case "$prefix-observed" 'deadline_expired' 'The total CLI validation deadline expired.'
        }
        $entry.status = 'PASS'
    } finally {
        if ($awaitingObservation -and $entry.status -eq 'NOT RUN') {
            $dispatch = if ($Trial.source -cin @('HOLD','BUS')) {
                $script:report.results | Where-Object { $_.name -ceq "$prefix-dispatch" }
            }
            if ($null -ne $dispatch -and $dispatch.status -ceq 'FAIL') {
                $diagnostics = $dispatch.error_diagnostics
                if ($diagnostics.software_trigger_write_confirmed -eq $true -or
                    $diagnostics.software_trigger_write_attempted -eq $false) {
                    # A failed observation/preflight is not an independent failed dispatch.
                    $entry.status = 'FAIL'
                    $entry['error_code'] = $dispatch.error_code
                    $entry['error_diagnostics'] = $diagnostics
                    if ($diagnostics.software_trigger_write_confirmed -eq $true) {
                        $dispatch.status = 'PASS'
                        $dispatch['trigger_dispatch_exchange'] = 'CONFIRMED'
                    } else {
                        $dispatch.status = 'NOT RUN'
                        $dispatch['trigger_dispatch_exchange'] = 'NOT ATTEMPTED'
                    }
                    $dispatch.Remove('error_code')
                    $dispatch.Remove('error_diagnostics')
                } else {
                    # Failed or uncertain write: no separate waveform verdict.
                    $dispatch['trigger_dispatch_exchange'] = if ($diagnostics.software_trigger_write_attempted -eq $true) {
                        'UNCONFIRMED'
                    } else { 'UNKNOWN' }
                    $entry['error_code'] = 'observation_blocked_by_dispatch'
                }
            } else {
                $entry.status = 'FAIL'
                $entry['error_code'] = 'observation_incomplete'
            }
        }
        $releaseRequired = $armed -or $setupEntry.Contains('list_selected')
        if ($releaseRequired -and $script:triggerTrusted -and $script:triggerOffConfirmed) {
            Assert-TriggerOff (Invoke-TriggerStep "$prefix-release" -Cleanup) "$prefix-release"
        } elseif ($releaseRequired -and $Trial.isList) {
            Write-Host 'LIST exit was not attempted. Confirm physical Input OFF and verify the load mode manually.'
        }
    }
}

function Assert-TriggerSnapshot($Snapshot) {
    if ($null -eq $Snapshot) { throw 'Original Trigger snapshot was not captured before mode changes.' }
    Assert-ReadbackFields $Snapshot.result @{state_source='instrument_readback';validation_only=$true;
        validation_scope='trigger';input_state='OFF'} 'trigger-snapshot'
    if ($Snapshot.result.source -cnotin @('MANUAL','BUS','HOLD','TIMER','EXTERNAL') -or
        $Snapshot.result.interval_s -isnot [string] -or [string]::IsNullOrWhiteSpace($Snapshot.result.interval_s)) {
        Fail-Case 'trigger-snapshot' 'invalid_response' 'Both original Trigger settings must be read before any change.'
    }
}

function Test-TriggerRestoreSafe {
    return ($script:triggerTrusted -and -not $script:traceCommunicationUntrusted -and
        -not $script:traceFeedStopUnconfirmed -and -not $script:triggerProcessStillRunning -and
        -not $script:timerProcessStillRunning -and -not $script:inputOnAttempted -and
        -not $script:timerEnableAttempted -and -not $script:transientEnableAttempted -and
        -not $script:slowRiseEnableAttempted -and -not $script:report.trace_safe_shutdown_unconfirmed -and
        -not $script:report.sequence_input_off_unconfirmed -and -not $script:report.protection_input_off_unconfirmed)
}

function Restore-TriggerFixedCc {
    if (-not $script:triggerFixedMayHaveChanged) { return }
    $entry = $script:report.results | Where-Object { $_.name -ceq 'trigger-fixed-restore' }
    if (-not (Test-TriggerRestoreSafe) -or -not $script:triggerOffConfirmed -or $null -eq $script:triggerFixedOriginal) {
        $entry['manual_action_required'] = $true
        Write-Host 'Fixed CC restoration blocked. Confirm physical OFF and restore the private snapshot manually when communication is reliable.'
        return
    }
    try {
        $original = $script:triggerFixedOriginal
        $restoreJson = @{fixed_cc_baseline='restore';selector_a=[string]$original.selector_a} | ConvertTo-Json -Compress
        $restoreArguments = $triggerScope + @('--current',[string]$original.current_a,'--range',[string]$original.range,
            '--validation-settings-json',$restoreJson)
        $restored = Invoke-TriggerStep 'trigger-fixed-restore' -Cleanup -ArgsOverride $restoreArguments
        Assert-TriggerOff $restored 'trigger-fixed-restore'
        Assert-ReadbackFields $restored.result @{validation_only=$true;validation_scope='trigger';
            range=[string]$original.range;current_a=[decimal]$original.current_a;
            range_readback_a=[decimal]$original.selector_a} 'trigger-fixed-restore'
        $script:triggerFixedMayHaveChanged = $false
    } catch {
        $entry['manual_action_required'] = $true
        throw
    }
}

function Invoke-TriggerValidation([switch]$RestoreOnly) {
    $snapshot = $script:originalTriggerSnapshot
    Assert-TriggerSnapshot $snapshot
    $source = $snapshot.result.source
    $interval = $snapshot.result.interval_s
    try {
        if (-not $RestoreOnly) {
            if (-not (Test-TriggerRestoreSafe) -or -not $script:triggerOffConfirmed) {
                throw 'Trigger validation blocked by uncertain safety state.'
            }
            if ($script:triggerSelected) {
                $baseline = Invoke-TriggerStep 'trigger-fixed-prepare'
                Assert-TriggerOff $baseline 'trigger-fixed-prepare'
                Assert-ReadbackFields $baseline.result @{range='low';current_a=[decimal]0;
                    range_readback_a=[decimal]0;validation_only=$true;validation_scope='trigger'} 'trigger-fixed-prepare'
                if (-not $script:triggerFixedMayHaveChanged -or $null -eq $script:triggerFixedOriginal) {
                    Fail-Case 'trigger-fixed-prepare' 'settings_baseline_unconfirmed' 'Private Fixed CC snapshot was not retained.'
                }
                $original = $script:triggerFixedOriginal
                Assert-ReadbackFields $baseline.result.original_fixed_cc @{range=[string]$original.range;
                    current_a=[decimal]$original.current_a;selector_a=[decimal]$original.selector_a} 'trigger-fixed-prepare'
            }
            foreach ($trial in $script:triggerTrials) { Invoke-TriggerTrial $trial }
        }
    } finally {
        try { Restore-TriggerFixedCc } finally {
            $script:report.trigger_restore_status = 'FAIL'
            $entry = $script:report.results | Where-Object { $_.name -ceq 'trigger-restore' }
            $entry['trigger_source_restore'] = 'UNCONFIRMED'
            $entry['trigger_interval_restore'] = 'UNCONFIRMED'
            if ((Test-TriggerRestoreSafe) -and $script:triggerOffConfirmed) {
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
                $entry['manual_action_required'] = $true
                Write-Host 'Original Trigger Source and Timer Interval were not restored. Confirm physical OFF and restore them manually once communication is reliable.'
            }
        }
    }
    if ($script:report.trigger_restore_status -ne 'PASS') {
        throw 'Original Trigger Source and Timer Interval were not confirmed.'
    }
    if ($script:triggerFixedMayHaveChanged) { throw 'Original Fixed CC was not confirmed.' }
    if ($script:watch.ElapsedMilliseconds -ge $totalDeadlineMs) {
        throw 'The total CLI validation deadline expired during Trigger cleanup.'
    }
}

function Restore-TriggerAfterFailure {
    # Do not repeat an attempted restore or start a new session on uncertain state.
    if (-not $script:triggerManaged -or -not $script:triggerSourceMayHaveChanged -or
        $null -eq $script:originalTriggerSnapshot -or $script:report.trigger_restore_status -ne 'NOT RUN') { return }
    $script:report.status = 'FAIL'
    if (Test-TriggerRestoreSafe) {
        $script:report.results += [ordered]@{name='cleanup-trigger-input-state';command='input-state';status='NOT RUN'}
        try {
            $state = Invoke-LiveCommand -Command 'input-state' -CaseName 'cleanup-trigger-input-state' -Cleanup
            Assert-Runtime $state 'cleanup-trigger-input-state'
            Assert-TriggerOff $state 'cleanup-trigger-input-state'
            Invoke-TriggerValidation -RestoreOnly
            Write-Host 'PASS Original Trigger Source and Timer Interval restored after an earlier mode failed.'
        } catch {
            if ($script:report.trigger_restore_status -ne 'PASS') {
                $script:report.trigger_restore_status = 'FAIL'
            }
            Write-Host 'WARNING: Trigger cleanup after an earlier mode failure did not complete; check the individual Source/Timer restoration evidence.'
        }
    } else {
        $script:report.trigger_restore_status = 'FAIL'
        Write-Host 'WARNING: Trigger Source and Timer Interval restoration blocked by uncertain safety state; operator action required.'
    }
    if ($script:report.trigger_restore_status -ne 'PASS') {
        $entry = $script:report.results | Where-Object { $_.name -ceq 'trigger-restore' }
        $entry['manual_action_required'] = $true
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

function Invoke-SelectedPreflight([string[]]$Modes, [string]$PreflightScript) {
    $preflightModes = [ordered]@{
        'input-timer'='InputTimerOnly'; trace='TraceOnly'; trigger='TriggerOnly';
        protection='ProtectionOnly'; 'system-query'='SystemQueryOnly'; readback='ReadbackOnly';
        sequence='SequenceOnly'; 'cr-led'='CrLedSlowRiseOnly'; 'current-slow-rise'='CrLedSlowRiseOnly'
    }
    $preflightChecks = @(foreach ($mode in $Modes) {
        if ($preflightModes.Contains($mode)) { $preflightModes[$mode] }
    })
    $runBase = @($Modes | Where-Object { $_ -in @('cc','cv','cr','cw') -or $_ -like 'transient-*' }).Count -gt 0
    if (-not $runBase -and -not $preflightChecks.Count) { return }

    # Mark an attempted check as failed until every applicable check succeeds.
    $script:report.prechecks.preflight = 'FAIL'
    if ($runBase) {
        & $PreflightScript -BaseOnly
        if ($LASTEXITCODE -ne 0) { throw 'Base preflight failed.' }
    }
    foreach ($check in ($preflightChecks | Select-Object -Unique)) {
        $selection = @{ $check = $true }
        & $PreflightScript @selection
        if ($LASTEXITCODE -ne 0) { throw "$check preflight failed." }
    }
    $script:report.prechecks.preflight = 'PASS'
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
    if ($SourceVoltageLimitV -ne 5 -or $SourceCurrentLimitA -ne 0.8) {
        if ($sequenceSelected -or $protectionSelected) {
            throw 'Sequence and Protection require a dedicated 5 V / 0.8 A source.'
        }
        if ($workflowSelected) {
            throw 'Contributor Workflow load trials require a dedicated 5 V / 0.8 A source.'
        }
    }
    if (-not $Target -or -not $Resource) {
        Write-Host 'BLOCKED: explicit -Target and -Resource are required.'
        exit 2
    }
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $cli)) {
        throw 'Sync the repository .venv with uv before running validation.'
    }
    if ('cc' -in $selectedModes -or $traceSelected -or $timerSelected -or $featureSelected) {
        # Preserve the original token; both manual trials and the script ceiling must pass.
        & $python -c 'from electronicload_tool_core.control import validate_current; import sys; value = validate_current(sys.argv[1], sys.argv[2]); validate_current(sys.argv[1], sys.argv[3]); sys.exit(0 if 0 < value <= 2 else 2)' $currentText low high
        if ($LASTEXITCODE -ne 0) {
            throw 'Current must be positive, at most 2 A, and valid for both CC ranges without rounding.'
        }
        $report.prechecks.current = 'PASS'
    }
    $report.script_current_ceiling_a = if ($selectedModes.Count -eq 1 -and $protectionSelected) { 0.20 }
        elseif ($selectedModes.Count -eq 1 -and $sequenceSelected) { 0.10 } else { $scriptCurrentCeilingA }
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
    if ($systemQuerySelected) {
        foreach ($command in @('scpi-version','display-mode','error')) {
            $planned += @{name=$command;command=$command;args=@('--hardware-validation','system-query')}
        }
    }
    $isolated = @()
    $legacyReadback = @(@('ExpectedRiseSlew','ExpectedFallSlew','ExpectedVonThreshold','ExpectedVonLatch',
        'ExpectedShort','ExpectedAverageCount') | Where-Object { $PSBoundParameters.ContainsKey($_) }).Count -gt 0
    if ($readbackSelected -and $legacyReadback) {
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
    if ($readbackSelected -and -not $legacyReadback) {
        $planned += @{name='readback';command='readback';args=$readbackScope;independent_snapshot=$true;
                      readback_expected=@{input_state='OFF'}}
        $readbackTrials = [ordered]@{
            'fixed-status-cc'='fixed-cc'; 'fixed-status-cv'='fixed-cv';
            'fixed-status-cr'='fixed-cr'; 'fixed-status-cw'='fixed-cw';
            'transient-status'='transient'; 'list-status'='list'; 'fixed-slew-status'='slew';
            'von-status'='von'; 'short-status'='short'; 'average-status'='average'
        }
        foreach ($name in $readbackTrials.Keys) {
            $step = @{name=$name;command='contributor-settings';
                args=@('--trial',$readbackTrials[$name],'--readback-trial','--confirm-isolated');execution='live';isolated=$true;
                expected=@{input_state='OFF';restored=$true};
                condition='Automatic known-setting readback and verified restoration with physically isolated terminals and Input OFF.'}
            if ($name -ceq 'list-status') {
                $step.condition += ' Initialize a verified two-step baseline if the LIST is empty; refuse before writing if the 30 s verification and restoration budget is insufficient.'
            }
            $isolated += $step
        }
    }
    # Keep initial diagnostics and explicit legacy Expected readback unchanged.
    # All source-connected trials use the same measurement baseline, including
    # standalone and mixed selections. Preserve the original count privately.
    $averageBaselineRequired = Test-SourceConnectedTrials $selectedModes
    $averageEndSteps = @()
    if ($averageBaselineRequired) {
        $averageSetupArgs = @('--hardware-validation','measurement-extensions','--count','2','--confirm')
        $planned += @(
            @{name='validation-average-original';command='average-status';args=$readbackScope;execution='live';
              expected=@{state_source='instrument_readback'};average_snapshot=$true;
              condition='Input OFF; record original Average privately before the measurement baseline'},
            @{name='validation-average-baseline';command='set-average';args=$averageSetupArgs;execution='live';
              expected=@{average_count=2};average_baseline=$true;
              condition='Input OFF; verify Average 2 before source-connected trials'}
        )
        if ('measurement-extensions' -notin $selectedModes) {
            # Validate a representative legal count offline; the actual count
            # is supplied only from the verified snapshot during LIVE.
            $averageEndSteps += @{name='validation-average-restore';command='set-average';args=$averageSetupArgs;
                execution='live';average_end=$true;condition='After successful source-connected trials, Input OFF; restore and verify the original Average'}
        }
    }
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
              condition='5 V / 0.8 A source, Fixed CC Low, 5 s at 0.1 A with bounded current/Input sampling before scheduled OFF; at most 1 s sample uncertainty; independent cutoff within 10 s of SCPI Input ON including OFF confirmation'},
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
            @{name='measurement-extensions-discharge';command='measure-discharge';args=$scope;execution='live'}
        )
        $averageTrial = @{name='measurement-extensions-average';command='set-average';args=$scope + @('--count','8','--confirm');
            expected=@{average_count=8};execution='live';condition='Input OFF and operator approves leaving Average at 8'}
        if ($averageBaselineRequired) {
            # Keep the explicit leave-at-8 contract, but only after all
            # source-connected trials so it cannot contaminate their baseline.
            $averageTrial.average_end = $true
            $averageTrial.condition = 'After successful source-connected trials; ' + $averageTrial.condition
            $averageEndSteps += $averageTrial
        } else {
            $planned += $averageTrial
        }
    }
    if ('auxiliary-controls' -in $selectedModes) {
        $isolated += @(foreach ($trial in @('von','short','slew')) {
            @{name="auxiliary-controls-$trial";command='contributor-settings';
              args=@('--trial',$trial,'--confirm-isolated');execution='live';isolated=$true;
              expected=@{input_state='OFF';restored=$true};
              condition='Source and DUT physically disconnected, Fixed CC with Input, Short, Transient and Timer OFF; verify and restore settings.'}
        })
    }
    if ('list-cc' -in $selectedModes) {
        $isolated += @{name='list-cc-settings';command='contributor-settings';
            args=@('--trial','list-cc','--slot',[string]$ListScratchSlot,'--confirm-isolated');
            execution='live';isolated=$true;deadline_ms=300000;
            expected=@{input_state='OFF';restored=$true;recall_confirmed=$true;exited_list=$true};
            condition="Source and DUT isolated, Fixed CC with Input, Short, Transient and Timer OFF. Authorize overwriting slot $ListScratchSlot at the initial Enter. Initialize and retain a two-step working baseline only if the LIST is initially empty. Establish and verify B as the slot baseline, Save A, verify Recall A and restore the verified working baseline. Refuse before writing if the remaining 300 s cannot cover verification and restoration."}
    }
    # Isolated cases are appended after source-connected and Trigger trials.
    # Reuse successful readback trials for identical Auxiliary cases.
    if ($readbackSelected -and -not $legacyReadback) {
        $reuse = @{'auxiliary-controls-von'='von-status'; 'auxiliary-controls-short'='short-status';
                   'auxiliary-controls-slew'='fixed-slew-status'}
        foreach ($step in $isolated) {
            if ($reuse.ContainsKey($step.name)) {
                $step.reuse_from = $reuse[$step.name]
                $step.condition = "Reuse successful $($step.reuse_from) evidence in this isolated batch."
            }
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
            @{name='timer-expiry';command='input-timer-status';
              args=($timerScope + @('--validation-settings-json', ('{"seconds":' + [int]$TimerSeconds + '}')));
              condition='Single-session observation using live SCPI ON timestamps. Require timed ON and first OFF within Timer seconds plus one second from SCPI ON write start, with at most 1 s observation uncertainty; definite early OFF fails. Authorize a cutoff that cannot intervene during observation'},
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
        # TRACE software capture must not depend on the instrument's stored Trigger Source.
        $planned += @{name='trace-trigger-hold';command='trigger-configure';args=($triggerScope + @('--source','HOLD'));
            execution='live';expected=@{source='HOLD';state_source='instrument_readback'};
            condition='Input OFF; verify HOLD and preserve the original Timer Interval before TRACE captures'}
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
    if ($triggerManaged) { New-TriggerPlan }
    if ($protectionSelected) { New-ProtectionPlan }
    # Readback restores the preceding selection. Hand off only before the
    # first independent isolated trial, not before the Readback batch.
    $standalone = @($isolated | Where-Object {
        $_.name -cin @('auxiliary-controls-von','auxiliary-controls-short','auxiliary-controls-slew','list-cc-settings') -and
        -not $_.ContainsKey('reuse_from')
    })
    if ($standalone.Count -gt 0 -and -not $triggerSelected) {
        $lastSetup = $planned | Where-Object {
            ($_.command -cmatch '^set-(cc|cv|cr|cw|cr-led|transient-(cc|cv|cr|cw))$' -and
             -not $_.ContainsKey('expected_error')) -or $_.command -ceq 'sequence'
        } | Select-Object -Last 1
        $lastFunction = if ($lastSetup.command -ceq 'sequence') { $lastSetup.name -replace '^sequence-', '' }
            elseif ($lastSetup.command) { $lastSetup.command -replace '^set-(transient-)?', '' }
            else { 'cc' }
        if ($lastFunction -cin @('cv','cr','cw')) {
            $handoff = @{name='isolated-fixed-cc-handoff';command='contributor-settings';
                args=@('--trial','fixed-cc-handoff','--origin-function',$lastFunction.ToUpperInvariant(),'--confirm-isolated');execution='live';isolated=$true;
                expected=@{input_state='OFF';function='CC'};
                condition='After successful powered trials and physical isolation, select Fixed CC without changing setpoints; require Fixed mode and all interlocks OFF.'}
            $firstStandalone = $standalone[0].name
            $isolated = @(foreach ($step in $isolated) {
                if ($step.name -ceq $firstStandalone) { $handoff }
                $step
            })
        }
    }
    $planned += $averageEndSteps + $isolated
    foreach ($step in ($planned | Where-Object { $_.ContainsKey('cr_handoff_after') })) {
        $step.condition = "$($step.cr_handoff_after) refused a resistance origin before remote"
    }
    # Capture Trigger before any legacy Transient, TRACE or Trigger trial can change it.
    $allSteps = @($planned | Select-Object -First 3) +
        @($triggerPlans | Select-Object -First 1) +
        @($planned | Select-Object -Skip 3 -First ([Math]::Max(0, $protectionIndex - 3))) +
        $protectionPlans + @($planned | Select-Object -Skip $protectionIndex | Where-Object { -not $_.isolated -and -not $_.average_end }) +
        $slowRiseCleanup + @($triggerPlans | Select-Object -Skip 1) + $averageEndSteps + $isolated
    $report.results = @($allSteps | ForEach-Object {
        $entry = [ordered]@{name=$_.name;command=$_.command;arguments=$_.args;status='NOT RUN'}
        if ($_.ContainsKey('execution')) { $entry['execution'] = $_.execution }
        if ($_.ContainsKey('reuse_from')) { $entry['reuse_from'] = $_.reuse_from }
        if ($_.ContainsKey('expected_error')) { $entry['expected_error'] = $_.expected_error }
        if ($_.ContainsKey('condition')) { $entry['condition'] = $_.condition }
        if ($_.ContainsKey('deadline_ms')) { $entry['deadline_ms'] = $_.deadline_ms }
        if ($_.ContainsKey('expected_readback')) { $entry['expected_readback'] = $_.expected_readback }
        if ($_.ContainsKey('check_readback_settings')) { $entry['readback_comparison'] = 'NOT RUN' }
        $entry
    })
    $plannedRanges = @{}
    $offlinePlans = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    foreach ($step in $allSteps) {
        if ($step.command -in @('operator-observation','automatic-observation')) { continue }
        $extras = @($step.args)
        $key = ConvertTo-Json -InputObject (@($step.command) + $extras) -Compress
        if (-not $offlinePlans.ContainsKey($key)) {
            $offlinePlans[$key] = Invoke-OfflineCommand $step.command $extras
        }
        $plan = $offlinePlans[$key]
        if ($step.execution -ceq 'live') {
            $entry = $report.results | Where-Object { $_.name -ceq $step.name }
            foreach ($field in @('queries','planned_writes','conditional_writes')) {
                if ($step.ContainsKey('reuse_from')) { $entry[$field] = @() }
                elseif ($null -ne $plan.result.$field) { $entry[$field] = @($plan.result.$field) }
            }
        }
        if ($step.name -ceq 'trigger-fixed-restore') {
            $entry.arguments = $triggerScope + @('--current','<original>','--range','<original>',
                '--validation-settings-json','{"fixed_cc_baseline":"restore","selector_a":"<original>"}')
            $entry.planned_writes = @('SYST:REM','CURR 0','CURR:RANG <original>')
            $entry.conditional_writes = @(@{condition='original Fixed CC is nonzero';writes=@('CURR <original>')})
        }
        if ($step.name -ceq 'validation-average-restore') {
            # The report describes the conditional original-value restore,
            # never the actual private recovery count or the dry-run stand-in.
            $entry.arguments = @('--hardware-validation','measurement-extensions','--count','<original>','--confirm')
            $entry.planned_writes = @('SYST:REM','SENS:AVER:COUN <original>')
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
    Write-Host "Mode: $(if ($PlanOnly) { 'PLAN ONLY' } else { 'LIVE' })"
    Write-Host "Selected modes: $($selectedModes -join ', ')."
    Write-Host 'Plan: Identify, confirm Input OFF and measure before physical trials.'
    if ($averageBaselineRequired) {
        Write-Host 'Measurement baseline: Average 2 for all source-connected trials; verified before loading.'
        Write-Host 'On successful completion, restore the original Average or leave 8 when Measurement Extensions is selected.'
    }
    if ($systemQuerySelected) { Write-Host 'System Query: read version, display mode and one Error Queue entry.' }
    if ($readbackSelected) {
        if ($legacyReadback) {
            Write-Host 'Read-only diagnostics requested by Expected parameters; omit them for automatic isolated Readback.'
        } else {
            Write-Host 'Readback automatically sets known values, compares independent readbacks, and restores settings in the isolated batch.'
        }
    }
    if ($sequenceSelected) {
        Write-Host 'Sequence: one batch confirmation for four Fixed trials, each with one ON, measurement and confirmed OFF.'
    }
    if ($protectionSelected) {
        Write-Host 'Protection: software trips are verified from instrument readback. After each trip, turn OFF and isolate the external source and remove the physical fault before confirming Clear.'
        Write-Host 'Protection: reconnect and energize the same source only when prompted for the next powered trial; the hardware-power baseline and limit trial share the same powered source without another confirmation.'
    }
    if ($traceSelected) {
        Write-Host 'TRACE: verified HOLD before four Input OFF batches and one bounded Fixed CC ON batch with explicit OFF.'
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
    if ($triggerManaged) {
        Write-Host 'Trigger settings: save the original Source and Timer Interval before changes; restore both when safe after the selected trials.'
    }
    if ($triggerSelected) {
        Write-Host 'Trigger: four source trials (HOLD, TIMER, BUS, EXTERNAL) with automatic sampled observation, explicit OFF and restoration of original Source and Timer.'
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
    Write-Host "External source (operator-managed): $SourceVoltageLimitV V / $SourceCurrentLimitA A current limit."
    Write-Host 'The script does not configure, measure or verify the external source.'
    foreach ($step in $allSteps) {
        $condition = $(if ($step.condition) { " [if $($step.condition)]" } else { '' })
        $displayArgs = if ($step.name -ceq 'validation-average-restore') {
            @('--hardware-validation','measurement-extensions','--count','<original>','--confirm')
        } else { $step.args }
        Write-Verbose ("  {0}: {1} {2}{3}" -f $step.name, $step.command, ($displayArgs -join ' '), $condition)
    }
    if ('cc' -in $selectedModes) { Write-Host "CC Current=$currentText A." }
    Write-Host "Script ceiling=$($report.script_current_ceiling_a) A; $Connection System VISA."
    Write-Host 'No reset, automatic local switch, or implicit Input ON.'
    Write-Host 'An Input ON attempt requires an explicit OFF cleanup; session close does not turn Input OFF.'
    Write-Host 'V/I/P measure queries are separate; power freshness is not guaranteed.'
    if ($PlanOnly) {
        $report.status = 'PLAN ONLY'
        Save-Report
        Show-Summary
        Write-Host 'PLAN ONLY COMPLETE: no hardware accessed.'
        exit 0
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
    Invoke-SelectedPreflight -Modes $selectedModes -PreflightScript (Join-Path $PSScriptRoot 'preflight-cli.ps1')
    $connectedTrials = Test-SourceConnectedTrials $selectedModes
    $ack = Confirm-OperatorStart $selectedModes $isolated $connectedTrials
    if ($ack -cne '' -or (($triggerManaged -or $protectionSelected) -and $null -eq $ack)) {
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
    $isolatedBatchConfirmed = $false
    $triggerCompleted = $false
    for ($i = 1; $i -le $planned.Count; $i++) {
        if ($protectionSelected -and $i -eq $protectionIndex) { Invoke-ProtectionValidation $readbackSelection }
        if ($i -eq $planned.Count) { break }
        $step = $planned[$i]
        if (($step.isolated -or $step.average_end) -and $triggerManaged -and -not $triggerCompleted) {
            Invoke-TriggerValidation
            $triggerCompleted = $true
        }
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
            $timerOnStartMs = $watch.ElapsedMilliseconds
            $timerPowered = $true
        }
        $liveArgs = $step.args
        if ($step.average_baseline -or $step.average_end) {
            if ($null -eq $averageOriginal) {
                Fail-Case $step.name 'readback_mismatch' 'Original Average was not confirmed.'
            }
            if ($step.average_baseline) { $averageChangeAttempted = $true }
            if ($step.average_end -and ($inputOnAttempted -or $timerEnableAttempted -or
                $transientEnableAttempted -or $slowRiseEnableAttempted -or $traceCommunicationUntrusted -or
                ($triggerManaged -and (-not $script:triggerTrusted -or -not $script:triggerOffConfirmed)) -or
                $report.sequence_input_off_unconfirmed -or $report.protection_input_off_unconfirmed)) {
                Fail-Case $step.name 'input_not_off' 'Average finalization blocked by uncertain safety state.'
            }
            if ($step.name -ceq 'validation-average-restore') {
                $liveArgs = @('--hardware-validation','measurement-extensions','--count',[string]$averageOriginal,'--confirm')
            }
        }
        if ($step.isolated -and -not $isolatedBatchConfirmed) {
            $isolatedBatchConfirmed = Confirm-IsolatedBatch $isolatedBatchConfirmed $connectedTrials $step.name
        }
        if ($step.ContainsKey('reuse_from')) {
            $prior = $report.results | Where-Object { $_.name -ceq $step.reuse_from }
            if ($prior.status -cne 'PASS' -or $prior.response.result.restored -ne $true -or
                $prior.response.result.input_state -cne 'OFF') {
                Fail-Case $step.name 'readback_mismatch' 'Reusable isolated readback evidence was not confirmed.'
            }
            $entry = $report.results | Where-Object { $_.name -ceq $step.name }
            $entry.response = $prior.response
            $entry.status = 'PASS'
            continue
        }
        # Mark uncertainty before dispatch, including subprocess/JSON/runtime failures.
        if ($step.command -eq 'input-on') {
            $inputOnAttempted = $true
            if ($triggerManaged) {
                $script:triggerOffConfirmed = $false
                $report.trigger_input_off_unconfirmed = $true
            }
        }
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
        if ($triggerManaged -and ($step.name -ceq 'trace-trigger-hold' -or
                $step.command -in @('set-transient-cc','set-transient-cv','set-transient-cr','set-transient-cw'))) {
            # TRACE and legacy Transient can write HOLD; record uncertainty before dispatch.
            Assert-TriggerSnapshot $script:originalTriggerSnapshot
            $script:triggerSourceMayHaveChanged = $true
        }
        if ($protectionSelected -and $step.name -ceq 'input-state') {
            $report.protection_input_off_unconfirmed = $true
        }
        if ($step.command -ceq 'sequence') {
            $report.sequence_input_off_unconfirmed = $true
        }
        if ($step.name -eq 'input-state') { $report.prechecks.input_off = 'FAIL' }
        $result = Invoke-LiveCommand -Command $step.command -CaseName $step.name `
            -ExtraArgs $liveArgs -DeadlineMs $commandDeadline -ExpectedError $step.expected_error `
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
        if ($step.average_snapshot) {
            $count = $result.result.average_count
            if (($count -isnot [int] -and $count -isnot [long]) -or $count -lt 2 -or $count -gt 16) {
                Fail-Case $step.name 'invalid_response' 'Original Average was not a supported integer count.'
            }
            $averageOriginal = $count
            Write-Host "Private recovery baseline: original Average count = $averageOriginal. Keep this console record private."
        }
        if ($step.average_end) {
            if ($step.name -ceq 'validation-average-restore') {
                Assert-ReadbackFields $result.result @{average_count=$averageOriginal} $step.name
            }
            $averageChangeAttempted = $false
        }
        if ($step.workflow_load) {
            Confirm-WorkflowLoad $step.name
            # The Workflow changed Fixed selection; Protection must read
            # its current state instead of reusing the pre-Workflow snapshot.
            $readbackSelection = $null
        }
        if ($step.name -ceq 'list-cc-settings' -and $result.result.save_confirmed -ne $true) {
            Write-Host 'NOT RUN list-cc-settings: Save confirmation evidence was missing.'
            continue
        }
        if ($protectionSelected -and $step.name -ceq 'input-state') {
            $report.protection_input_off_unconfirmed = $result.result.input_state -cne 'OFF'
        }
        if ($step.name -ceq 'trace-trigger-hold') {
            Assert-TriggerOff $result $step.name
            Assert-ReadbackFields $result.result @{interval_s=[decimal]$script:originalTriggerSnapshot.result.interval_s} $step.name
            $entry = $report.results | Where-Object { $_.name -ceq $step.name }
            $entry['trigger_source'] = $result.result.source
            $entry['trigger_interval_s'] = $result.result.interval_s
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
        if ($step.name -ceq 'transient-status' -and $step.ContainsKey('readback_expected')) {
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
        if ($step.name -eq 'measure-off' -and $triggerManaged) {
            $script:originalTriggerSnapshot = Invoke-TriggerStep 'trigger-snapshot'
            Assert-TriggerOff $script:originalTriggerSnapshot 'trigger-snapshot'
            Assert-TriggerSnapshot $script:originalTriggerSnapshot
        }
        Write-Host "PASS $($step.name)"
    }

    foreach ($step in $slowRiseCleanup) {
        $entry = $report.results | Where-Object { $_.name -eq $step.name } | Select-Object -First 1
        $entry.status = 'SKIP'
    }
    if ($triggerManaged -and -not $triggerCompleted) { Invoke-TriggerValidation }
    if ($report.results.error_code -contains 'protection_default_out_of_bounds') {
        throw 'Protection defaults validation is incomplete because the instrument MAX exceeded the allowed bounds.'
    }
    if ($report.results.error_code -contains 'list_save_unconfirmed') {
        throw 'LIST validation is incomplete because Save was not confirmed.'
    }
    if ($averageChangeAttempted) {
        throw 'Average finalization was not confirmed.'
    }
    $report.status = if ($readbackSelected -and ($report.results.readback_comparison -contains 'OBSERVED ONLY')) {
        'OBSERVED ONLY'
    } else { 'PASS' }
    Save-Report
    Show-Summary
    if ($report.status -eq 'OBSERVED ONLY') {
        Write-Host 'Readback complete. Only comparisons with supplied expected values were verified.'
    } else {
        Write-Host 'PASS: applicable CLI LIVE cases.'
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
    $validationInterrupted = ($triggerManaged -or $protectionSelected -or $sequenceSelected -or $asrlSelected -or $averageChangeAttempted) -and
        $phase -in @('identify','cases') -and $report.status -eq 'NOT RUN'
    if ($validationInterrupted) { $report.status = 'FAIL' }
    if ($inputOnAttempted) {
        $report.results += [ordered]@{
            name = 'cleanup-input-off'; command = 'input-off'; status = 'NOT RUN'
        }
        if ($timerProcessStillRunning -or $traceCommunicationUntrusted -or ($triggerManaged -and -not $script:triggerTrusted)) {
            $report.status = 'FAIL'
            if ($traceCommunicationUntrusted) { $report.trace_safe_shutdown_unconfirmed = $true }
            if ($triggerManaged) { $report.trigger_input_off_unconfirmed = $true }
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
        if ($timerProcessStillRunning -or $traceCommunicationUntrusted -or ($triggerManaged -and -not $script:triggerTrusted)) {
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
        if ($traceCommunicationUntrusted -or ($triggerManaged -and -not $script:triggerTrusted)) {
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
            $transientEnableAttempted = $false
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
            (-not $triggerManaged -or $script:triggerTrusted)) {
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
    Restore-TriggerAfterFailure
    if ($averageChangeAttempted) {
        # Never initiate an additional VISA session just to restore the
        # measurement baseline after a failed or interrupted run.
        $report.status = 'FAIL'
        Write-Host 'WARNING: Average may have changed; automatic finalization did not complete. After independently confirming Input OFF and safe communication, recover the original count from the private console baseline.'
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
