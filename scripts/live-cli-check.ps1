[CmdletBinding()]
param(
    [string]$Target,
    [ValidateSet('usb')][string]$Connection = 'usb',
    [string]$Resource,
    [string]$Backend = 'system',
    [switch]$PlanOnly,
    [switch]$DebugArtifacts
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv/Scripts/python.exe'
$cli = Join-Path $repo '.venv/Scripts/electronicload-tool.exe'
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
    }
    artifact_mode = $(if ($DebugArtifacts) { 'debug' } else { 'minimal' })
    validation_mode = $(if ($PlanOnly) { 'plan-only' } else { 'live' })
    product_live = 'IT8812C USB/System VISA read-only open'
    per_command_deadline_ms = 30000
    total_deadline_ms = 90000
    plans = @()
    results = @(
        [ordered]@{ command = 'identify'; status = 'NOT RUN' },
        [ordered]@{ command = 'input-state'; status = 'NOT RUN' },
        [ordered]@{ command = 'measure'; status = 'NOT RUN' }
    )
}
$reportDirectory = $null
$watch = $null

function Save-Report {
    if ($null -eq $script:reportDirectory) { return }
    $shareable = Join-Path $script:reportDirectory 'shareable'
    [void][IO.Directory]::CreateDirectory($shareable)
    $state = $script:report.results | Where-Object { $_.command -eq 'input-state' } | Select-Object -First 1
    $measurement = $script:report.results | Where-Object { $_.command -eq 'measure' } | Select-Object -First 1
    $cases = @(
        [ordered]@{
            name = 'input-state'
            status = $state.status
            input_state = $(if ($state.status -eq 'PASS') { $state.response.result.input_state } else { $null })
            error_code = $state.error_code
        }
        [ordered]@{
            name = 'measure'
            status = $measurement.status
            values = $(if ($measurement.status -eq 'PASS') {
                [ordered]@{
                    voltage_v = $measurement.response.result.voltage_v
                    current_a = $measurement.response.result.current_a
                    power_w = $measurement.response.result.power_w
                }
            } else { $null })
            error_code = $measurement.error_code
        }
    )
    $counts = [ordered]@{ total = 2; passed = 0; failed = 0; skipped = 0; not_run = 0 }
    foreach ($item in $cases) {
        switch ($item.status) {
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
        per_command_deadline_ms = 30000
        total_deadline_ms = 90000
        product_live = $script:report.product_live
        prechecks = $script:report.prechecks
        cases = $cases
        summary_counts = $counts
    }
    [IO.File]::WriteAllText((Join-Path $shareable 'report.json'),
        ($public | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    $lines = @(
        '# Electronic Load Read-only Validation'
        ''
        "Overall: $($public.overall_status)"
        "Preflight: $($public.prechecks.preflight)"
        "Identify: $($public.prechecks.identify)"
        "VISA runtime: $($public.prechecks.runtime)"
        "[1/2] Input State: $($state.status)"
        "[2/2] Measurement (V/I/P): $($measurement.status)"
        "Cases: $($counts.passed) PASS, $($counts.failed) FAIL, $($counts.skipped) SKIP, $($counts.not_run) NOT RUN"
        'Resource: <redacted>'
    )
    [IO.File]::WriteAllText((Join-Path $shareable 'summary.md'),
        ($lines -join [Environment]::NewLine) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false))
}

function Show-Summary {
    $counts = $script:report.summary_counts
    Write-Host "Summary: $($counts.total) total, $($counts.passed) PASS, $($counts.failed) FAIL, $($counts.skipped) SKIP, $($counts.not_run) NOT RUN"
    Write-Host "Overall: $($script:report.status)"
}

function Invoke-LiveCommand([string]$Command) {
    $remaining = [Math]::Min(30000, 90000 - $script:watch.ElapsedMilliseconds)
    if ($remaining -le 0) { throw 'The total validation deadline expired.' }
    # Core has validated these values; quote arguments without invoking another shell.
    $arguments = @('-c', 'from electronicload_tool_cli.cli import main; raise SystemExit(main())',
        $Command, '--resource', $Resource, '--backend', $Backend, '--model', $Target,
        '--json')
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $python
    $start.Arguments = ($arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $start.WorkingDirectory = $repo
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $started = $false
    $entry = $script:report.results | Where-Object { $_.command -eq $Command }
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
            $entry['session_closure'] = 'unconfirmed after deadline termination'
            throw 'CLI deadline expired; session closure is unconfirmed. No input change was requested.'
        }
        $output = $stdout.GetAwaiter().GetResult()
        $diagnostic = $stderr.GetAwaiter().GetResult()
        if ($DebugArtifacts) {
            $privateDir = Join-Path $script:reportDirectory 'private'
            [void][IO.Directory]::CreateDirectory($privateDir)
            [IO.File]::WriteAllText((Join-Path $privateDir "$Command.json"),
                $output, [Text.UTF8Encoding]::new($false))
            [IO.File]::WriteAllText((Join-Path $privateDir "$Command.stderr.txt"),
                $diagnostic, [Text.UTF8Encoding]::new($false))
        }
        $result = $output | ConvertFrom-Json
        $shareable = $output | ConvertFrom-Json
        if ($null -ne $shareable.result.visa) {
            $shareable.result.visa.library = [IO.Path]::GetFileName($shareable.result.visa.library)
        }
        $entry['exit_code'] = $process.ExitCode
        $entry.status = $(if ($process.ExitCode -eq 0 -and $result.ok) { 'PASS' } else { 'FAIL' })
        $entry['response'] = $shareable
        if ($entry.status -eq 'FAIL') {
            $code = [string]$result.error.code
            $entry['error_code'] = $(if ($code -cmatch '^[a-z_]+$') { $code } else { 'execution_failed' })
        }
        Save-Report
        if ($process.ExitCode -ne 0 -or $result.ok -ne $true) {
            throw "$Command failed; see the local report."
        }
        return $result
    } finally {
        if ($started -and -not $process.HasExited) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
        }
        $process.Dispose()
    }
}

try {
    if (-not $Target -or -not $Resource) {
        Write-Host 'BLOCKED: explicit -Target and -Resource are required.'
        exit 2
    }
    if (-not (Test-Path -LiteralPath $cli)) {
        throw 'Sync the repository .venv with uv before running validation.'
    }
    foreach ($command in @('identify', 'input-state', 'measure')) {
        $output = & $cli $command --model $Target --resource $Resource --backend $Backend --dry-run --json
        $code = $LASTEXITCODE
        $plan = ($output -join "`n") | ConvertFrom-Json
        if ($code -ne 0 -or $plan.ok -ne $true) {
            Write-Host "BLOCKED: $($plan.error.message)"
            exit 2
        }
        $report.plans += $plan
    }
    $report.prechecks.plan = 'PASS'
    $name = 'readonly-live-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff') + '-' +
        [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $reportDirectory = Join-Path $repo ".tmp_tests/$name"
    [void][IO.Directory]::CreateDirectory($reportDirectory)
    Write-Host 'Plan: identify, input-state, measure; USB System VISA; 5000 ms I/O timeout; LF.'
    Write-Host 'Queries only. No input changes, reset, error-queue reads, local switch, or retries.'
    Write-Host 'Closing a session does not turn input OFF.'
    Write-Host 'V/I use MEASure; power FETCh reads a latest available value, not guaranteed fresh.'
    Write-Host 'Nonzero-load updates, accuracy, and V/I/P synchronization are not guaranteed.'
    $measurementPlan = $report.plans | Where-Object { $_.command -eq 'measure' } | Select-Object -First 1
    Write-Host "Measurement queries: $($measurementPlan.result.queries -join ', ')"
    if ($PlanOnly) {
        $report.status = 'PLAN ONLY'
        Save-Report
        Show-Summary
        Write-Host "PASS: no hardware accessed. Report: $reportDirectory/shareable/report.json"
        exit 0
    }
    if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) {
        $report.status = 'BLOCKED'
        Save-Report
        Show-Summary
        Write-Host 'BLOCKED: real validation requires an interactive operator.'
        exit 2
    }
    & (Join-Path $PSScriptRoot 'preflight-cli.ps1')
    if ($LASTEXITCODE -ne 0) {
        $report.prechecks.preflight = 'FAIL'
        throw 'Preflight failed.'
    }
    $report.prechecks.preflight = 'PASS'
    Write-Host "Target: $Target; resource: $Resource; backend: $Backend"
    $confirmation = Read-Host 'With separate hardware authorization, press Enter to start (Ctrl+C to cancel)'
    if ($confirmation -cne '') {
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
    Write-Host 'Running precheck: Identify...'
    $identity = Invoke-LiveCommand 'identify'
    $report.prechecks.identify = 'PASS'
    if ($null -eq $identity.result.visa -or
        [string]::IsNullOrWhiteSpace([string]$identity.result.visa.library)) {
        throw 'VISA runtime library metadata is missing.'
    }
    Write-Host 'PASS precheck: Identify'
    Write-Host "VISA library: $($identity.result.visa.library)"
    Write-Host "VISA implementation manufacturer: $($identity.result.visa.manufacturer)"
    Write-Host 'Runtime metadata is diagnostic only; no Keysight operator verification was performed.'
    $report.prechecks.runtime = 'OBSERVED (not operator-verified)'
    $report.prechecks.runtime_manufacturer = $identity.result.visa.manufacturer
    $report.runtime_confirmation = 'not operator-verified'
    $phase = 'cases'
    foreach ($command in @('input-state', 'measure')) {
        $number = $(if ($command -eq 'input-state') { 1 } else { 2 })
        $label = $(if ($number -eq 1) { 'Input State' } else { 'Measurement (V/I/P)' })
        Write-Host "Running [$number/2] $label..."
        try {
            $result = Invoke-LiveCommand $command
            if ($result.result.visa.library -cne $identity.result.visa.library -or
                $result.result.visa.manufacturer -cne $identity.result.visa.manufacturer) {
                throw 'VISA runtime evidence changed between commands; validation cannot pass.'
            }
            if ($number -eq 1) {
                Write-Host "PASS [1/2] Input State: $($result.result.input_state)"
            } else {
                Write-Host "PASS [2/2] Measurement: $($result.result.voltage_v) V, $($result.result.current_a) A, $($result.result.power_w) W"
            }
        } catch {
            $entry = $report.results | Where-Object { $_.command -eq $command }
            if ($entry.status -eq 'NOT RUN') {
                Write-Host "NOT RUN [$number/2] $label (command did not start)"
            } else {
                $entry.status = 'FAIL'
                if (-not $entry.Contains('error_code')) { $entry['error_code'] = 'execution_failed' }
                Write-Host "FAIL [$number/2] $label ($($entry.error_code))"
            }
            throw
        }
    }
    $report.status = 'PASS'
    Save-Report
    Show-Summary
    Write-Host "PASS: bounded read-only validation of Product LIVE commands. Report: $reportDirectory/shareable/report.json"
    exit 0
} catch {
    if ($phase -eq 'identify') {
        $report.prechecks.identify = 'FAIL'
        $report.status = 'BLOCKED'
    } elseif ($phase -eq 'cases') {
        $report.status = 'FAIL'
    } else {
        $report.status = 'BLOCKED'
    }
    if ($DebugArtifacts -and $null -ne $reportDirectory) {
        $privateDir = Join-Path $reportDirectory 'private'
        [void][IO.Directory]::CreateDirectory($privateDir)
        [IO.File]::WriteAllText((Join-Path $privateDir 'failure.txt'),
            $_.Exception.ToString(), [Text.UTF8Encoding]::new($false))
    }
    Save-Report
    if ($null -ne $reportDirectory) {
        Show-Summary
        Write-Host "Report: $reportDirectory/shareable/report.json"
    }
    Write-Host 'Validation did not complete; check the report.'
    exit 3
}
