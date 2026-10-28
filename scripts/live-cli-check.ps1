[CmdletBinding()]
param(
    [string]$Target,
    [ValidateSet('usb')][string]$Connection = 'usb',
    [string]$Resource,
    [string]$Backend = 'system',
    [switch]$PlanOnly
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
    product_live = 'not open'
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
    $path = Join-Path $script:reportDirectory 'report.json'
    [IO.File]::WriteAllText($path, ($script:report | ConvertTo-Json -Depth 12),
        [Text.UTF8Encoding]::new($false))
}

function Invoke-LiveCommand([string]$Command) {
    $remaining = [Math]::Min(30000, 90000 - $script:watch.ElapsedMilliseconds)
    if ($remaining -le 0) { throw 'The total validation deadline expired.' }
    # Core has validated these values; quote arguments without invoking another shell.
    $arguments = @('-c', 'from electronicload_tool_cli.cli import main; raise SystemExit(main())',
        $Command, '--resource', $Resource, '--backend', $Backend, '--model', $Target,
        '--validation-allow-pending-live-support', '--json')
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
        [IO.File]::WriteAllText((Join-Path $script:reportDirectory "$Command.private.json"),
            $output, [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText((Join-Path $script:reportDirectory "$Command.private.stderr.txt"),
            $diagnostic, [Text.UTF8Encoding]::new($false))
        $result = $output | ConvertFrom-Json
        $shareable = $output | ConvertFrom-Json
        if ($null -ne $shareable.result.visa) {
            $shareable.result.visa.library = [IO.Path]::GetFileName($shareable.result.visa.library)
        }
        $entry['exit_code'] = $process.ExitCode
        $entry.status = $(if ($process.ExitCode -eq 0 -and $result.ok) { 'PASS' } else { 'FAIL' })
        $entry['response'] = $shareable
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
    $name = 'readonly-live-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff') + '-' +
        [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $reportDirectory = Join-Path $repo ".tmp_tests/$name"
    [void][IO.Directory]::CreateDirectory($reportDirectory)
    Write-Host 'Plan: identify, input-state, measure; USB System VISA; 5000 ms I/O timeout; LF.'
    Write-Host 'Queries only. No input changes, reset, error-queue reads, local switch, or retries.'
    Write-Host 'Closing a session does not turn input OFF. V/I/P are separate measurements.'
    if ($PlanOnly) {
        $report.status = 'PLAN ONLY'
        Save-Report
        Write-Host "PASS: no hardware accessed. Report: $reportDirectory/report.json"
        exit 0
    }
    if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) {
        $report.status = 'BLOCKED'
        Save-Report
        Write-Host 'BLOCKED: real validation requires an interactive operator.'
        exit 2
    }
    & (Join-Path $PSScriptRoot 'preflight-cli.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Preflight failed.' }
    Write-Host "Target: $Target; resource: $Resource; backend: $Backend"
    $confirmation = Read-Host 'With separate hardware authorization, type RUN to identify this device'
    if ($confirmation -cne 'RUN') {
        $report.status = 'BLOCKED'
        Save-Report
        Write-Host 'BLOCKED: operator did not confirm.'
        exit 2
    }
    [IO.File]::WriteAllText((Join-Path $reportDirectory 'resource.private.txt'),
        $Resource, [Text.UTF8Encoding]::new($false))
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $identity = Invoke-LiveCommand 'identify'
    Write-Host "VISA library: $($identity.result.visa.library)"
    Write-Host "VISA implementation manufacturer: $($identity.result.visa.manufacturer)"
    Write-Host 'Inspect the private identify JSON for the full loaded library path.'
    Write-Host 'IVI alone does not prove Keysight runtime. Verify the library and installed IO Libraries.'
    $confirmation = Read-Host 'Type KEYSIGHT only after verifying the runtime; otherwise press Enter'
    if ($confirmation -cne 'KEYSIGHT' -or -not $identity.result.visa.manufacturer) {
        $report.status = 'BLOCKED'
        Save-Report
        Write-Host 'BLOCKED: Keysight runtime is unverified; remaining queries were not run.'
        exit 2
    }
    $report.runtime_confirmation = 'operator confirmed Keysight library and installation'
    foreach ($command in @('input-state', 'measure')) {
        $result = Invoke-LiveCommand $command
        if ($result.result.visa.library -cne $identity.result.visa.library -or
            $result.result.visa.manufacturer -cne $identity.result.visa.manufacturer) {
            throw 'VISA runtime evidence changed between commands; validation cannot pass.'
        }
        Write-Host "PASS: $command"
    }
    $report.status = 'PASS'
    Save-Report
    Write-Host "PASS: bounded read-only validation. Product LIVE remains closed. Report: $reportDirectory/report.json"
    exit 0
} catch {
    $report.status = 'FAIL'
    $report['failure'] = 'Validation failed; inspect failure.private.txt locally.'
    if ($null -ne $reportDirectory) {
        [IO.File]::WriteAllText((Join-Path $reportDirectory 'failure.private.txt'),
            $_.Exception.ToString(), [Text.UTF8Encoding]::new($false))
    }
    Save-Report
    Write-Host "FAIL: $($_.Exception.Message)"
    exit 3
}
