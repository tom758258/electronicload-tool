<#
Single, bounded IT8812C CLI hardware validation.
Default: all eight modes, with common read-only safety prechecks.
-Modes selects cc, cv, cr, cw and/or transient-cc/cv/cr/cw (comma-separated or an array).
Requires approved source/DUT limits, write/ON gates and interactive confirmation.
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
    [double]$SourceCurrentLimitA,
    [double]$SourceVoltageLimitV,
    [string]$Voltage = '5',
    [string]$ResistanceLow = '10',
    [string]$ResistanceHigh = '1000',
    [string]$Power = '1',
    [string]$LevelA = '0.1',
    [string]$LevelB = '0.2',
    [string]$VoltageLevelA = '5',
    [string]$VoltageLevelB = '6',
    [string]$ResistanceLevelA = '10',
    [string]$ResistanceLevelB = '1000',
    [string]$PowerLevelA = '1',
    [string]$PowerLevelB = '2',
    [string]$RiseSlew = '0.1',
    [string]$FallSlew = '0.1',
    [string]$WidthA = '0.001',
    [string]$WidthB = '0.002',
    [string]$PulseWidth = '0.001',
    [switch]$PlanOnly,
    [switch]$ConfirmWrites,
    [switch]$EnableInputOn,
    [switch]$DebugArtifacts
)

$ErrorActionPreference = 'Stop'
$supportedModes = @('cc', 'cv', 'cr', 'cw', 'transient-cc', 'transient-cv', 'transient-cr', 'transient-cw')
$requestedModes = @($Modes | ForEach-Object { $_ -split ',' } |
    ForEach-Object { $_.Trim().ToLowerInvariant() })
# Keep the established trial order and run each selected mode only once.
$selectedModes = @($supportedModes | Where-Object { $_ -in $requestedModes })
$hasValidationModes = @($selectedModes | Where-Object { $_ -ne 'cc' }).Count -gt 0
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
        if ($entry.Contains('arguments')) { $case.arguments = $entry.arguments }
        if ($entry.Contains('error_code')) { $case.error_code = $entry.error_code }
        if ($entry.Contains('expected_range')) { $case.expected_range = $entry.expected_range }
        if ($entry.status -eq 'PASS') {
            if ($entry.command -eq 'input-state' -or $entry.command -eq 'input-on' -or
                $entry.command -eq 'input-off') {
                $case.input_state = $entry.response.result.input_state
            }
            if ($entry.command -in @('set-cc', 'set-cv', 'set-cr')) {
                $case.range = $entry.response.result.range
            }
            if ($entry.command -eq 'set-cc') {
                $case.current_a = $entry.response.result.current_a
            }
            foreach ($field in @('configuration', 'enabled', 'validation_only', 'validation_scope',
                                  'trigger_dispatched_at_utc', 'waveform_completion_known')) {
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

function Invoke-LiveCommand([string]$Command, [string]$CaseName,
                            [string[]]$ExtraArgs = @(),
                            [switch]$Cleanup) {
    $remaining = if ($Cleanup) { $script:perCommandDeadlineMs } else {
        [Math]::Min($script:perCommandDeadlineMs,
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
    if ($requestedModes.Count -eq 0 -or @($requestedModes | Where-Object { $_ -notin $supportedModes }).Count -gt 0) {
        throw 'Modes must contain one or more of: cc, cv, cr, cw, transient-cc, transient-cv, transient-cr, transient-cw.'
    }
    if (-not $Target -or -not $Resource) {
        Write-Host 'BLOCKED: explicit -Target and -Resource are required.'
        exit 2
    }
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $cli)) {
        throw 'Sync the repository .venv with uv before running validation.'
    }
    if ('cc' -in $selectedModes) {
        # Preserve the original token; both manual trials and the script ceiling must pass.
        & $python -c 'from electronicload_tool_core.control import validate_current; import sys; value = validate_current(sys.argv[1], sys.argv[2]); validate_current(sys.argv[1], sys.argv[3]); sys.exit(0 if 0 < value <= 2 else 2)' $currentText low high
        if ($LASTEXITCODE -ne 0) {
            throw 'Current must be positive, at most 2 A, and valid for both CC ranges without rounding.'
        }
        $report.prechecks.current = 'PASS'
    }
    $report.script_current_ceiling_a = $scriptCurrentCeilingA

    if ($hasValidationModes) {
        # Script-only operator envelope, not a substitute for source current limiting.
        if ([double]::IsNaN($SourceCurrentLimitA) -or [double]::IsInfinity($SourceCurrentLimitA) -or
            [double]::IsNaN($SourceVoltageLimitV) -or [double]::IsInfinity($SourceVoltageLimitV) -or
            $SourceCurrentLimitA -le 0 -or $SourceCurrentLimitA -gt 2 -or
            $SourceVoltageLimitV -le 0 -or $SourceVoltageLimitV -gt 18) {
            throw 'CV/CR/CW/Transient validation requires declared source limits: >0 to 2 A and >0 to 18 V.'
        }
        $report.declared_source_limits = [ordered]@{
            current_a = $SourceCurrentLimitA; voltage_v = $SourceVoltageLimitV
            verification = 'operator-declared; script does not configure or verify the source'
        }
    }
    if ('transient-cc' -in $selectedModes) {
        & $python -c 'from decimal import Decimal; import sys; a,b=map(Decimal,sys.argv[1:]); assert 0 <= a <= 2 and 0 <= b <= 2' $LevelA $LevelB
        if ($LASTEXITCODE -ne 0) { throw 'Transient levels must be within the script ceiling of 2 A.' }
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
            @{name='set-cc-high';command='set-cc';args=$highArgs},
            @{name='set-cc-auto';command='set-cc';args=$autoArgs},
            @{name='set-cc-low';command='set-cc';args=$lowArgs},
            @{name='input-on';command='input-on';args=$onArgs},
            @{name='measure-on';command='measure';args=@()},
            @{name='input-off';command='input-off';args=@()},
            @{name='final-input-state';command='input-state';args=@()}
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
    $report.results = @($planned | ForEach-Object {
        [ordered]@{name=$_.name;command=$_.command;arguments=$_.args;status='NOT RUN'}
    })
    foreach ($step in $planned) {
        $extras = @($step.args)
        $plan = Invoke-PlanCommand $step.command $extras
        if ($step.command -in @('set-cc', 'set-cv', 'set-cr', 'set-transient-cc', 'set-transient-cv', 'set-transient-cr') -and 'auto' -in $step.args) {
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
    $name = 'readonly-live-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff') + '-' +
        [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $reportDirectory = Join-Path $repo ".tmp_tests/$name"
    [void][IO.Directory]::CreateDirectory($reportDirectory)
    Write-Host "Plan: Identify, Input State, Measure; selected modes: $($selectedModes -join ', ')."
    Write-Host 'Every selected mode includes explicit ON/OFF and independent OFF confirmation.'
    Write-Host 'Fixed Auto adds Input OFF setup/readback with the existing values; no extra ON/OFF cycles.'
    if (@($selectedModes | Where-Object { $_.StartsWith('transient-') }).Count -gt 0) {
        Write-Host 'Transient: Continuous/Pulse/Toggle, Low/High/Auto, repeated triggers, control status and separate disable.'
        Write-Host 'Trigger/status/measure do not establish waveform shape, timing or completion.'
    }
    if ($hasValidationModes) {
        Write-Host "Declared source limits: $SourceCurrentLimitA A / $SourceVoltageLimitV V; configure these on the source first."
    }
    foreach ($step in $planned) { Write-Host ("  {0}: {1} {2}" -f $step.name, $step.command, ($step.args -join ' ')) }
    if ('cc' -in $selectedModes) { Write-Host "CC Current=$currentText A." }
    Write-Host "Script ceiling=$($report.script_current_ceiling_a) A; USB System VISA."
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
    Write-Host 'Each trial deliberately tests Input ON and explicitly commands/verifies OFF; source/DUT must tolerate every printed setting.'
    $ack = Read-Host 'With approved hardware setup, press Enter to run selected CLI cases (Ctrl+C to cancel)'
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
    foreach ($step in ($planned | Select-Object -Skip 1)) {
        Write-Host "Running $($step.name)..."
        # Mark uncertainty before dispatch, including subprocess/JSON/runtime failures.
        if ($step.command -eq 'input-on') { $inputOnAttempted = $true }
        if ($step.command -eq 'transient-enable') {
            $activeTransientScope = $step.args[[Array]::IndexOf($step.args, '--hardware-validation') + 1]
            $transientEnableAttempted = $true
        }
        $result = Invoke-LiveCommand -Command $step.command -CaseName $step.name -ExtraArgs $step.args
        Assert-Runtime $result $step.name
        if ($step.name -eq 'input-state' -and $result.result.input_state -cne 'OFF') {
            $report.prechecks.input_off = 'BLOCKED'
            $report.status = 'BLOCKED'
        }
        if ($step.command -in @('input-state', 'input-off', 'set-cc', 'set-cv', 'set-cr', 'set-cw',
                               'set-transient-cc', 'set-transient-cv', 'set-transient-cr', 'set-transient-cw',
                               'transient-enable', 'transient-disable')) {
            Assert-InputOff $result $step.name
        }
        if ($step.command -eq 'input-on' -and $result.result.input_state -cne 'ON') {
            Fail-Case $step.name 'input_on_not_confirmed' 'Input ON was not confirmed.'
        }
        $observedRange = $(if ($step.command -eq 'set-cc') { $result.result.range } else {
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
        if ($step.command -eq 'transient-disable') { $transientEnableAttempted = $false }
        if ($step.name -eq 'input-state') { $report.prechecks.input_off = 'PASS' }
        Write-Host "PASS $($step.name)"
    }

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
            $cleanup = Invoke-LiveCommand -Command 'input-off' -CaseName 'cleanup-input-off' -Cleanup
            Assert-Runtime $cleanup 'cleanup-input-off'
            Assert-InputOff $cleanup 'cleanup-input-off'
            $inputOnAttempted = $false
            Write-Host 'PASS Explicit Input OFF cleanup'
        } catch {
            $report.status = 'FAIL'
            Write-Host 'WARNING: physical Input OFF was not confirmed. Immediate operator action is required.'
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
