"""Run the real PowerShell script with physical VISA forbidden in child CLIs."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RESOURCE = 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR'
ASRL = 'ASRL999::INSTR'
FULL_MODES = [
    'cc', 'cv', 'cr', 'cw', 'transient-cc', 'transient-cv', 'transient-cr', 'transient-cw',
    'trace', 'cr-led', 'current-slow-rise', 'trigger', 'input-timer', 'protection',
    'system-query', 'readback', 'sequence', 'workflow', 'measurement-extensions',
    'auxiliary-controls', 'list-cc',
]


@pytest.fixture
def run_script(tmp_path):
    powershell = shutil.which('pwsh')
    if os.name == 'nt' and os.environ.get('GITHUB_ACTIONS') == 'true':
        if powershell is None:
            pytest.fail('PowerShell 7 (pwsh) is required in the Windows validation jobs.')
        if shutil.which('powershell.exe') is None:
            pytest.fail('Windows PowerShell (powershell.exe) is required in the Windows validation jobs.')
    powershell = powershell or shutil.which('powershell')
    if powershell is None:
        local = ROOT / '.tmp_tests/powershell/pwsh'
        if not local.is_file():
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                pytest.fail('PowerShell is required in the Windows validation jobs.')
            pytest.skip('PowerShell is required for the script check.')
        powershell = str(local)
    (tmp_path / 'sitecustomize.py').write_text(
        'import pyvisa\n'
        'from pathlib import Path\n'
        'def forbidden(*args, **kwargs):\n'
        f'    Path({str(tmp_path / "visa-attempt")!r}).touch()\n'
        '    raise AssertionError("Physical VISA forbidden in script tests")\n'
        'pyvisa.ResourceManager = forbidden\n'
        'pyvisa.highlevel.open_visa_library = forbidden\n', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    for name in ('XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        directory = tmp_path / name.lower()
        directory.mkdir()
        env[name] = str(directory)
    def run(extra, *, source_limits=False, mode_array=None, preflight=False, resource=RESOURCE,
            harness=None, extra_env=None, windows_powershell=False):
        engine = powershell
        policy = []
        if windows_powershell and os.name == 'nt':
            engine = shutil.which('powershell.exe')
            assert engine is not None, 'Windows PowerShell is required for default preflight.'
            policy = ['-ExecutionPolicy', 'Bypass']
        script = ROOT / 'scripts/live-cli-check.ps1'
        if harness is None:
            args = ([str(ROOT / 'scripts/preflight-cli.ps1')] if preflight else
                    [str(script), '-Target', 'itech-it8812c'])
            if resource is not None and not preflight:
                args += ['-Resource', resource]
            if source_limits and not preflight:
                args += ['-SourceCurrentLimitA', '0.5', '-SourceVoltageLimitV', '12']
        else:
            # Run the real function bodies against controlled responses, without an interactive
            # console or any of the script's hardware-dispatch code.
            harness_path = tmp_path / 'harness.ps1'
            harness_path.write_text(
                "param([string]$Scenario)\n$ErrorActionPreference = 'Stop'\n"
                f"$source = '{str(script).replace(chr(39), chr(39) * 2)}'\n"
                "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
                "$source, [ref]$null, [ref]$null)\n"
                "foreach ($node in $ast.EndBlock.Statements) {\n"
                "    if ($node.Name -in @('Invoke-ProtectionValidation','Invoke-ProtectionStep',"
                "'Get-ProtectionWriteProgress','Assert-InputOff',"
                "'Assert-ReadbackFields','Fail-Case','Invoke-TimerExpiry','Invoke-LiveCommand','Get-WorkflowFailureEvidence',"
                "'Get-SettingsFailureEvidence',"
                "'Get-TimerMonotonicNs','Get-TimerRemainingMs','Get-TimerObservationDeadlineNs',"
                "'Confirm-TimerExpiry','Confirm-WorkflowLoad','Assert-Runtime','Save-Report',"
                "'New-CliProcessInfo','New-LiveCliProcess','Invoke-SelectedPreflight',"
                "'Test-SourceConnectedTrials','Confirm-OperatorStart','Confirm-IsolatedBatch',"
                "'Confirm-ProtectionOperator','Invoke-PoweredProtectionTrial','Restore-Protection',"
                "'Get-ProtectionSetArgs','Invoke-TriggerTrial','Assert-TriggerOff')) {\n"
                "        Invoke-Expression $node.Extent.Text\n"
                "    }\n}\n" + harness, encoding='utf-8')
            args = [str(harness_path)]
        args += extra
        if mode_array is None:
            invocation = ['-File', *args]
        else:
            # Exercise native PowerShell array syntax as well as external -File comma lists.
            quoted = ["'" + arg.replace("'", "''") + "'" if not arg.startswith('-') else arg
                      for arg in args]
            invocation = ['-Command', '& ' + ' '.join(quoted) + ' -Modes ' + mode_array]
        result = subprocess.run(
            [engine, '-NoProfile', '-NonInteractive', *policy, *invocation], cwd=ROOT,
            env={**env, **(extra_env or {})},
            stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=180)
        assert not (tmp_path / 'visa-attempt').exists(), result.stdout + result.stderr
        return result

    return run


def assert_report(result, expected, modes):
    assert result.returncode == (0 if expected == 'PLAN ONLY' else 2), result.stdout + result.stderr
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)
    assert paths, result.stdout + result.stderr
    report_text = Path(paths[-1]).read_text(encoding='utf-8-sig')
    report = json.loads(report_text)
    assert report['overall_status'] == expected
    assert report['hardware_touched'] is False
    assert report['prechecks']['plan'] == 'PASS'
    assert report['summary_counts']['passed'] == report['summary_counts']['skipped'] == 0
    assert report['summary_counts']['failed'] == 0
    assert RESOURCE not in report_text
    assert not (Path(paths[-1]).parents[1] / 'private').exists()
    cases = {case['name']: case for case in report['cases']}
    assert all(case['status'] == 'NOT RUN' for case in cases.values())
    expected_names = ['input-state', 'measure-off']
    if 'cc' in modes:
        expected_names += ['set-cc-high', 'cc-handoff-from-cr', 'set-cc-high-readback',
                           'set-cc-auto', 'set-cc-low', 'set-cc-low-readback', 'input-on',
                           'measure-on', 'input-off', 'final-input-state']
        assert cases['set-cc-high']['arguments'][-1] == 'high'
        assert cases['cc-handoff-from-cr']['arguments'] == [
            *cases['set-cc-high']['arguments'], '--hardware-validation', 'cr-led',
        ]
        assert cases['cc-handoff-from-cr']['condition']
        assert cases['set-cc-low']['arguments'][-1] == 'low'
        assert cases['set-cc-auto']['arguments'][-1] == 'auto'
        assert cases['set-cc-auto']['expected_range'] == 'low'
    prefixes = {
        'cv': ('cv-low', 'cv-high'), 'cr': ('cr-low', 'cr-high'), 'cw': ('cw',),
        'transient-cc': ('transient-continuous-auto', 'transient-pulse-low',
                         'transient-toggle-high', 'transient-continuous-high',
                         'transient-pulse-auto-high'),
        **{f'transient-{f}': tuple(f'transient-{f}-{m}' for m in ('continuous', 'pulse', 'toggle'))
           for f in ('cv', 'cr', 'cw')},
    }
    for mode, trials in prefixes.items():
        if mode not in modes:
            continue
        steps = ['setup', 'readback', 'on', 'measure', 'off', 'final-state']
        if mode.startswith('transient-'):
            steps = ['setup', 'enable', 'on', 'trigger-1', 'trigger-2', 'status-on',
                     'measure', 'off', 'disable', 'status-off', 'final-state']
        for prefix in trials:
            if prefix in ('cv-low', 'cr-low', 'cr-high'):
                automatic = 'cv-auto' if mode == 'cv' else prefix.replace('cr-', 'cr-auto-')
                expected_names += [f'{automatic}-setup']
                setup = cases[f'{automatic}-setup']
                assert setup['command'] == f'set-{mode}'
                assert setup['arguments'][setup['arguments'].index('--range') + 1] == 'auto'
                expected_range = 'high' if prefix == 'cr-high' else 'low'
                assert setup['expected_range'] == expected_range
            expected_names += [f'{prefix}-{step}' for step in steps]
            assert cases[f'{prefix}-setup']['command'] == f'set-{mode}'
            assert cases[f'{prefix}-off']['command'] == 'input-off'
            assert cases[f'{prefix}-final-state']['command'] == 'input-state'
            if mode.startswith('transient-'):
                for step in ('trigger-1', 'trigger-2'):
                    assert cases[f'{prefix}-{step}']['command'] == 'transient-trigger'
                assert cases[f'{prefix}-disable']['command'] == 'transient-disable'
                assert cases[f'{prefix}-status-off']['arguments'] == ['--hardware-validation', 'readback']
            else:
                assert cases[f'{prefix}-readback']['command'] == 'fixed-status'
    if 'cr-led' in modes:
        expected_names += ['cr-led-set', 'cr-led-status-on', 'cr-led-plain-cr', 'cr-led-status-off',
                           'cr-led-set-again', 'cr-led-leave-for-cc', 'cr-led-status-after-cc',
                           'cr-led-final-input-state']
        assert cases['cr-led-set']['expected_range'] == cases['cr-led-plain-cr']['expected_range'] == 'high'
        assert cases['cr-led-leave-for-cc']['arguments'][-2:] == ['--hardware-validation', 'cr-led']
    if 'current-slow-rise' in modes:
        expected_names += ['slow-rise-set-cc', 'slow-rise-enable', 'slow-rise-explicit-input-off',
                           'slow-rise-status-after-off', 'slow-rise-repeat-cc', 'slow-rise-status-after-cc',
                           'slow-rise-block-cv', 'slow-rise-disable', 'slow-rise-status-disabled',
                           'slow-rise-final-input-state', 'cleanup-slow-rise-input-state',
                           'cleanup-slow-rise-disable', 'cleanup-slow-rise-status']
        assert cases['slow-rise-explicit-input-off']['command'] == 'input-off'
        assert cases['slow-rise-disable']['arguments'] == ['--hardware-validation', 'current-slow-rise']
        assert cases['cleanup-slow-rise-disable']['arguments'] == cases['slow-rise-disable']['arguments']
        assert all(cases[name]['condition'] for name in expected_names[-3:])
        refusal = cases['slow-rise-block-cv']
        assert refusal['command'] == 'set-cv'
        assert refusal['arguments'][-2:] == ['--hardware-validation', 'cv']
        assert refusal['expected_error'] == 'current_slow_rise_enabled'
    assert list(cases) == expected_names
    assert report['summary_counts']['total'] == report['summary_counts']['not_run'] == len(expected_names)
    assert len(report['cases']) == len(expected_names)  # No duplicate selected trials.
    for case in cases.values():
        if case['command'] == 'input-on':
            assert '--confirm' in case['arguments']
    has_validation_modes = any(mode != 'cc' for mode in modes)
    assert report['includes_validation_modes'] is has_validation_modes
    assert report['per_command_deadline_ms'] == 30000
    assert report['total_deadline_ms'] == (900000 if has_validation_modes else 240000)
    summary = Path(paths[-1]).with_name('summary.md').read_text(encoding='utf-8-sig')
    assert RESOURCE not in summary
    for name in expected_names:
        assert f'{name}: NOT RUN' in summary
    return report


def test_default_offline_preflight(run_script):
    # Keep the existing Windows PowerShell 5.1 boundary, with the child VISA blocker.
    result = run_script([], preflight=True, windows_powershell=True)
    assert result.returncode == 0, result.stdout + result.stderr
    for marker in (
        'PASS: Software Sequence offline preflight',
        'PASS: Readback preflight',
        'PASS: Input Timer preflight',
        'PASS: Fixed CC Slew, VON and Short preflight',
        'PASS: Fixed Auto required Range, manual High, LIVE refusal and Manifest',
        'PASS: Transient Auto High fallback and exact slew reporting',
        'PASS: offline control commands and Worker manifest',
    ):
        assert marker in result.stdout
    for mode in ('cv', 'cr', 'cw'):
        assert f'PASS: set-transient-{mode}' in result.stdout


def test_fixed_auto_offline_preflight(run_script):
    result = run_script(['-FixedAutoOnly'], preflight=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith('PASS: Fixed Auto required Range, manual High, LIVE refusal and Manifest')


def test_protection_offline_preflight(run_script):
    preflight = run_script(['-ProtectionOnly'], preflight=True)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    assert preflight.stdout.rstrip().endswith('PASS: Protection preflight')


def test_protection_explicit_mode_report_without_visa(run_script):
    result = run_script(['-Modes', 'protection'])
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'requires an interactive console' in result.stdout
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)
    assert len(paths) == 1, result.stdout + result.stderr
    path = Path(paths[0])
    text = path.read_text(encoding='utf-8-sig')
    report = json.loads(text)
    assert report['overall_status'] == 'BLOCKED' and report['prechecks']['plan'] == 'PASS'
    assert report['hardware_touched'] is False and RESOURCE not in text
    assert not (path.parents[1] / 'private').exists()
    assert report['declared_source_limits']['voltage_v'] == 5
    assert report['declared_source_limits']['current_a'] == 0.8
    assert report['protection_trials'] == {
        trial: 'NOT RUN' for trial in ('software-current', 'software-power', 'hardware-power')
    }
    cases = {case['name']: case for case in report['cases']}
    assert list(cases)[:2] == ['input-state', 'measure-off']
    assert list(cases)[2:6] == [
        'protection-start-state', 'protection-prepare-cc',
        'protection-prepare-cc-readback', 'protection-software-current-original',
    ]
    assert cases['protection-prepare-cc']['arguments'] == ['--current', '0.20', '--range', 'low']
    assert cases['protection-prepare-cc-readback']['arguments'] == ['--hardware-validation', 'readback']
    assert all(name.startswith('protection-') for name in list(cases)[2:])
    assert all(case['status'] == 'NOT RUN' for case in cases.values())
    assert report['summary_counts']['total'] == report['summary_counts']['not_run'] == len(cases)
    for trial in ('software-current', 'software-power', 'hardware-power'):
        prefix = f'protection-{trial}'
        assert cases[f'{prefix}-cc']['arguments'][:2] == ['--current', '0.20']
        assert cases[f'{prefix}-on']['command'] == 'operator-observation'
        assert cases[f'{prefix}-on']['condition']
        assert cases[f'{prefix}-trial']['arguments'] == [
            '--hardware-validation', 'protection', '--confirm', '--protection-trial', trial,
        ]
        assert cases[f'{prefix}-off']['command'] == 'input-off'
        assert '--events' in cases[f'{prefix}-events']['arguments']
        assert cases[f'{prefix}-final']['command'] == 'protection-status'
        if trial.startswith('software-'):
            assert cases[f'{prefix}-source-off']['command'] == 'operator-observation'
            assert (list(cases).index(f'{prefix}-source-off') < list(cases).index(f'{prefix}-restore-settings')
                    < list(cases).index(f'{prefix}-restore-cc') < list(cases).index(f'{prefix}-clear')
                    < list(cases).index(f'{prefix}-final'))
        else:
            assert f'{prefix}-clear' not in cases
            assert cases[f'{prefix}-baseline-on']['command'] == 'operator-observation'
            assert cases[f'{prefix}-baseline-on']['condition']
            assert cases[f'{prefix}-baseline-trial']['arguments'] == [
                '--hardware-validation', 'protection', '--confirm', '--protection-trial',
                'hardware-power-baseline',
            ]
            assert cases[f'{prefix}-baseline-off']['command'] == 'input-off'
            baseline_start = list(cases).index(f'{prefix}-cc') + 1
            assert list(cases)[baseline_start:baseline_start + 5] == [
                f'{prefix}-{step}' for step in ('baseline-on', 'baseline-trial', 'baseline-off', 'set', 'on')
            ]
    for group in ('software-power', 'hardware-power'):
        assert cases[f'protection-default-{group}-set']['arguments'][-2:] == ['--restore-defaults', group]


@pytest.mark.parametrize('extra', [['-SourceCurrentLimitA', '1'], ['-SourceVoltageLimitV', '6']])
@pytest.mark.parametrize('mode,expected', [
    ('protection', 'Sequence and Protection require a dedicated 5 V / 0.8 A source.'),
    ('workflow', 'Contributor Workflow load trials require a dedicated 5 V / 0.8 A source.'),
])
def test_source_precheck_rejects_overrides(run_script, mode, expected, extra):
    result = run_script(['-Modes', mode, '-PlanOnly', *extra])
    assert result.returncode == 2
    assert expected in result.stdout


@pytest.mark.parametrize('scenario,calls,code', [
    ('cw', ['start-state', 'prepare-cc', 'prepare-cc-readback', 'software-current-original'], None),
    ('readback-cc', ['software-current-original'], None),
    ('on', ['start-state'], 'input_not_off'),
    ('short-on', ['start-state'], 'unsafe_mode'),
    ('readback-short-unknown', [], 'unsafe_mode'),
    ('list', ['start-state'], 'unsafe_mode'),
    ('mismatch', ['start-state', 'prepare-cc', 'prepare-cc-readback'], 'readback_mismatch'),
])
def test_protection_gates_snapshot_on_fixed_cc_and_input_off(run_script, scenario, calls, code):
    result = run_script(['-Scenario', scenario], harness="""
$script:protectionTrials = @(@{name='software-current'})
$script:calls = @()
$names = @('start-state','prepare-cc','prepare-cc-readback','software-current-original')
$script:report = @{protection_trials=@{};results=@(
    $names | ForEach-Object { [ordered]@{name="protection-$_";status='NOT RUN'} }
)}
function Invoke-ProtectionStep([string]$Name) {
    $script:calls += $Name.Substring('protection-'.Length)
    switch ($Name) {
        'protection-start-state' {
            return @{result=@{
                active_mode=$(if ($Scenario -eq 'list') { 'list' } else { 'fixed' })
                function='CW';input_state=$(if ($Scenario -eq 'on') { 'ON' } else { 'OFF' })
                short_state=$(if ($Scenario -eq 'short-on') { 'ON' } else { 'OFF' })
            }}
        }
        'protection-prepare-cc-readback' {
            return @{result=@{active_mode='fixed';function='CC';range='low';input_state='OFF'
                state_source='instrument_readback'
                current_a=$(if ($Scenario -eq 'mismatch') { '0.3' } else { '0.20' })
            }}
        }
        'protection-software-current-original' { throw 'SNAPSHOT' }
    }
}
$initial = switch ($Scenario) {
    'readback-cc' { @{active_mode='fixed';function='CC';input_state='OFF';short_state='OFF'} }
    'readback-short-unknown' { @{active_mode='fixed';function='CV';input_state='OFF'} }
}
try { Invoke-ProtectionValidation $initial }
catch {
    $failure = $script:report.results | Where-Object { $_.status -eq 'FAIL' }
    if ($_.Exception.Message -ne 'SNAPSHOT' -and -not $failure) { throw }
}
@{calls=$script:calls;error_code=$failure.error_code} | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {'calls': calls, 'error_code': code}

def test_protection_defaults_refusal_leaves_restore_not_run(run_script, tmp_path):
    fake_cli = tmp_path / 'fake_protection_cli.py'
    fake_cli.write_text(
        'import json, sys\n'
        'if sys.argv[1] == "protection-status":\n'
        '    result = {"visa": {"library": "fake", "manufacturer": "fake"}, "input_state": "OFF"}\n'
        '    print(json.dumps({"ok": True, "result": result}))\n'
        '    sys.exit()\n'
        'details = {"default_rejected_before_write": True, "input_state": "OFF", "confirmed": {},\n'
        '           "unconfirmed": [], "not_attempted": ["hardware_power_limit"]}\n'
        'print(json.dumps({"ok": False, "error": {"code": "invalid_response", "details": details}}))\n'
        'sys.exit(3)\n', encoding='utf-8')
    result = run_script([], extra_env={
        'FAKE_PROTECTION_PYTHON': sys.executable, 'FAKE_PROTECTION_CLI': str(fake_cli),
    }, harness="""
$script:protectionTrials = @()
$script:protectionTrusted = $true
$script:expectedLibrary = 'fake'
$script:expectedManufacturer = 'fake'
$script:watch = [Diagnostics.Stopwatch]::StartNew()
$script:totalDeadlineMs = 900000
$script:protectionPlans = @(foreach ($group in @('software-power','hardware-power')) {
    @{name="protection-default-$group-original";command='protection-status';args=@()}
    @{name="protection-default-$group-set";command='protection-set';args=@()}
})
$script:report = @{results=@(foreach ($name in @('start-state','prepare-cc','prepare-cc-readback')) {
    [ordered]@{name="protection-$name";status='NOT RUN'}
}) + @(foreach ($step in $script:protectionPlans) {
    [ordered]@{name=$step.name;status='NOT RUN'}
})}
function New-CliProcessInfo([string[]]$Arguments) {
    $start = [Diagnostics.ProcessStartInfo]::new($env:FAKE_PROTECTION_PYTHON)
    $start.Arguments = '"{0}" {1}' -f $env:FAKE_PROTECTION_CLI, $Arguments[2]
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    return $start
}
function Restore-Protection { throw 'Unexpected restore after a read-only refusal.' }
Invoke-ProtectionValidation @{active_mode='fixed';function='CC';input_state='OFF';short_state='OFF'}
$defaults = $script:report.results | Where-Object { $_.name -like 'protection-default-*' }
@{statuses=@($defaults | ForEach-Object { $_.status }); codes=@($defaults.error_code);
  off_unconfirmed=$script:report.protection_input_off_unconfirmed} | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence == {
        'statuses': ['PASS', 'NOT RUN', 'PASS', 'NOT RUN'],
        'codes': ['protection_default_out_of_bounds'] * 2,
        'off_unconfirmed': False,
    }


def test_cr_led_slow_rise_offline_preflight(run_script):
    result = run_script(['-CrLedSlowRiseOnly'], preflight=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith('PASS: CR-LED and Current Slow Rise preflight')


@pytest.mark.parametrize('modes', [('cr-led',), ('current-slow-rise',), ('cr-led', 'current-slow-rise')])
def test_cr_led_slow_rise_script_plans_and_refuses_unattended(run_script, modes):
    result = run_script(['-Modes', ','.join(modes)])
    report = assert_report(result, 'BLOCKED', modes)
    assert all(case['command'] != 'input-on' for case in report['cases'])
    assert 'requires an interactive console' in result.stdout


def test_fixed_slew_von_short_offline_preflight(run_script):
    result = run_script(['-FixedSlewVonShortOnly'], preflight=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith('PASS: Fixed CC Slew, VON and Short preflight')


@pytest.mark.parametrize('current', ['0', '3', '0.1000000000000000001'])
def test_fixed_auto_script_current_bounds_and_no_rounding(run_script, current):
    result = run_script(['-PlanOnly', '-Modes', 'cc', '-Current', current], source_limits=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Report:' not in result.stdout


@pytest.mark.parametrize('selection,modes', [
    ('cv', ('cv',)), ('cr', ('cr',)), ('cw', ('cw',)),
    ('transient-cv', ('transient-cv',)), ('transient-cr', ('transient-cr',)),
    ('transient-cw', ('transient-cw',)), ('CV,cc,cv', ('cc', 'cv')),
])
def test_hardware_script_mode_plans(run_script, selection, modes):
    token = '0.000020000000000000000001'
    extra = ['-PlanOnly', '-Modes', selection]
    if selection == 'cv':
        # CC and Transient CC settings must not affect a CV-only plan.
        extra += ['-Current', '3', '-LevelA', '3']
    resistance = '10.000000000000000001'
    if selection == 'cr':
        extra += ['-ResistanceHigh', resistance]
    if selection == 'CV,cc,cv':
        extra += ['-Voltage', '12.0000']
    if selection.startswith('transient-'):
        extra += ['-WidthA', token, '-PulseWidth', token]
    if selection == 'transient-cv':
        extra += ['-Verbose']
    result = run_script(extra)
    report = assert_report(result, 'PLAN ONLY', modes)
    cases = {case['name']: case for case in report['cases']}
    if selection == 'cv':
        assert report['declared_source_limits']['current_a'] == 0.8
        assert report['declared_source_limits']['voltage_v'] == 5
        for name in ('cv-low-setup', 'cv-high-setup'):
            args = cases[name]['arguments']
            assert args[args.index('--voltage') + 1] == '3'
    if selection == 'cr':
        args = cases['cr-auto-high-setup']['arguments']
        assert args[args.index('--resistance') + 1] == resistance
    if selection == 'CV,cc,cv':
        args = cases['cv-auto-setup']['arguments']
        assert args[args.index('--voltage') + 1] == '12.0000'
    if selection.startswith('transient-'):
        scope = selection.removeprefix('transient-')
        case = cases[f'transient-{scope}-continuous-setup']
        assert case['arguments'][case['arguments'].index('--width-a') + 1] == token
        if scope in ('cv', 'cr'):
            assert case['expected_range'] == ('low' if scope == 'cv' else 'high')
        for case in cases.values():
            assert '--rise-slew' not in case['arguments'] and '--fall-slew' not in case['arguments']
            if case['command'] == 'set-transient-cw':
                assert '--range' not in case['arguments']
    if selection == 'transient-cv':
        for name in ('transient-cv-continuous-setup', 'transient-cv-pulse-setup',
                     'transient-cv-toggle-setup'):
            args = cases[name]['arguments']
            assert args[args.index('--level-a') + 1] == '3'
            assert args[args.index('--level-b') + 1] == '4'
        assert 'transient-cv-continuous-setup:' in result.stdout
        assert '--validation-settings-json' in result.stdout
        assert result.stdout.count('Report: ') == 1


def test_default_usb_and_explicit_asrl_full_plan_share_the_matrix(run_script):
    usb = run_script(['-PlanOnly'])
    asrl = run_script(['-Connection', 'asrl', '-Modes', 'full', '-PlanOnly'], resource=ASRL)
    reports = []
    for result, connection, resource in ((usb, 'usb', RESOURCE), (asrl, 'asrl', ASRL)):
        assert result.returncode == 0, result.stdout + result.stderr
        path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
        report_text = path.read_text(encoding='utf-8-sig')
        assert resource not in report_text + result.stdout and str(ROOT) not in report_text
        report = json.loads(report_text)
        assert report['connection'] == connection and report['resource'] == '<redacted>'
        assert report['overall_status'] == 'PLAN ONLY' and report['hardware_touched'] is False
        assert report['prechecks']['identify'] == 'NOT RUN'
        assert report['prechecks']['plan'] == 'PASS'
        for case in report['cases']:
            arguments = case.get('arguments', [])
            for index, argument in enumerate(arguments):
                if argument in ('--validation-settings-json', '--arguments-json'):
                    arguments[index + 1] = json.loads(arguments[index + 1])
        reports.append(report)

    expected = FULL_MODES
    assert reports[0]['modes'] == reports[1]['modes'] == expected
    cases = reports[0]['cases']
    assert cases == reports[1]['cases']
    names = [case['name'] for case in cases]
    assert len(names) == len(set(names))
    assert reports[0]['summary_counts'] == reports[1]['summary_counts'] == {
        'total': len(names), 'passed': 0, 'failed': 0, 'skipped': 0, 'not_run': len(names),
    }
    assert all(case['status'] == 'NOT RUN' for case in cases)
    assert not any(name.startswith('trigger-manual-') for name in names)
    for name in ('timer-on', 'trace-powered-on', 'cr-led-set', 'slow-rise-enable',
                 'trigger-hold-on', 'trigger-external-on', 'trigger-restore'):
        assert name in names
    assert names.index('trigger-snapshot') < names.index('scpi-version') < names.index('readback')
    assert names.index('protection-start-state') < names.index('trigger-restore') < names.index('average-status')
    assert names.index('protection-prepare-cc-readback') < names.index('protection-software-current-original')
    assert names.index('protection-default-hardware-power-final') < names.index('sequence-cc')
    assert names.index('sequence-cw') < names.index('set-cc-high')
    assert all(case.get('execution') in (None, 'live') for case in cases)
    assert all(case['command'] != 'pytest' for case in cases)
    by_name = {case['name']: case for case in cases}
    for name in ('fixed-slew-status', 'von-status', 'short-status', 'average-status'):
        case = by_name[name]
        assert case['command'] == 'contributor-settings' and case['execution'] == 'live'
        assert '--readback-trial' in case['arguments'] and '--confirm-isolated' in case['arguments']


def test_hardware_script_powershell_mode_array(run_script):
    override = run_script([
        '-PlanOnly', '-Current', '1',
        '-LevelA', '0.9', '-LevelB', '1',
        '-SourceCurrentLimitA', '1.2', '-SourceVoltageLimitV', '12',
    ], mode_array='cc,transient-cc')
    report = assert_report(override, 'PLAN ONLY', ('cc', 'transient-cc'))
    assert report['declared_source_limits']['current_a'] == 1.2
    assert report['declared_source_limits']['voltage_v'] == 12
    assert 'Selected modes:' in override.stdout
    assert 'transient-continuous-auto-setup:' not in override.stdout
    assert '--validation-settings-json' not in override.stdout
    assert override.stdout.count('Report: ') == 1


@pytest.mark.parametrize('selection', ['unknown', 'cc,unknown', '', 'full,cc'])
def test_hardware_script_invalid_modes(run_script, selection):
    result = run_script(['-PlanOnly', '-Modes', selection])
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Select full or one or more of:' in result.stdout
    assert 'Report:' not in result.stdout  # Rejected before planning any cases.


@pytest.mark.parametrize('selection', ['cc', 'transient-cc'])
def test_hardware_script_unattended_refusal(run_script, selection):
    extra = ['-LevelA', '3'] if selection == 'cc' else []
    result = run_script(['-Modes', selection, *extra])
    assert_report(result, 'BLOCKED', (selection,))
    assert 'requires an interactive console' in result.stdout


@pytest.mark.parametrize('parameter,value', [
    ('-SourceCurrentLimitA', '0'),
    ('-SourceCurrentLimitA', '2.1'),
    ('-SourceVoltageLimitV', '0'),
    ('-SourceVoltageLimitV', '18.1'),
])
def test_hardware_script_rejects_invalid_source_limits(run_script, parameter, value):
    result = run_script(['-PlanOnly', '-Modes', 'cc', parameter, value])
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Declared source limits must be' in result.stdout
    assert 'Report:' not in result.stdout


@pytest.mark.parametrize('selection,extra', [
    ('cc', ['-Current', '0.9']),
    ('trace', ['-Current', '0.9']),
    ('transient-cc', ['-LevelA', '0.9']),
    ('transient-cc', ['-LevelB', '0.9']),
])
def test_hardware_script_current_respects_source_limit(run_script, selection, extra):
    result = run_script(['-PlanOnly', '-Modes', selection, *extra])
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'must not exceed the declared source current limit' in result.stdout
    assert 'Report:' not in result.stdout


def test_transient_offline_preflight(run_script):
    result = run_script(['-TransientOnly'], preflight=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS: Software Sequence offline preflight' not in result.stdout
    assert 'PASS: identify simulate JSON' not in result.stdout
    for mode in ('cv', 'cr', 'cw'):
        assert f'PASS: set-transient-{mode}' in result.stdout


@pytest.mark.parametrize('scope,args', [
    ('transient-cv', ['-VoltageLevelA', '18.001']),
    ('transient-cr', ['-ResistanceLevelA', '5']),
    ('transient-cw', ['-PowerLevelA', '1.000000000000000001']),
    ('transient-cw', ['-WidthA', '0.000019']),
])
def test_new_transient_script_refuses_invalid_setup(run_script, scope, args):
    result = run_script(['-PlanOnly', '-Modes', scope, *args])
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Report:' not in result.stdout


def test_input_timer_offline_preflight(run_script):
    result = run_script(['-InputTimerOnly'], preflight=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith('PASS: Input Timer preflight')


@pytest.mark.parametrize('mode_args,planned,windows_powershell', [
    (['-Mode', 'input-timer'], True, True),
    (['-InputTimerOnly'], True, False),
    (['-InputTimerOnly'], False, False),
])
def test_input_timer_script_plan_and_live_refusal(run_script, mode_args, planned, windows_powershell):
    # Keep the legacy shortcut and exercise the real abbreviated entry on Windows PowerShell 5.1.
    result = run_script([*mode_args, *(['-PlanOnly'] if planned else [])],
                        windows_powershell=windows_powershell)
    assert result.returncode == (0 if planned else 2), result.stdout + result.stderr
    if not planned:
        assert 'requires an interactive console' in result.stdout
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)
    assert paths
    text = Path(paths[-1]).read_text(encoding='utf-8-sig')
    report = json.loads(text)
    assert report['overall_status'] == ('PLAN ONLY' if planned else 'BLOCKED')
    assert report['prechecks']['plan'] == 'PASS'
    assert report['prechecks']['preflight'] == 'NOT RUN'
    assert report['modes'] == ['input-timer']
    assert report['hardware_touched'] is False
    assert report['summary_counts']['passed'] == report['summary_counts']['failed'] == 0
    assert all(c['status'] == 'NOT RUN' for c in report['cases'])
    assert RESOURCE not in text
    assert not (Path(paths[-1]).parents[1] / 'private').exists()
    commands = [c['command'] for c in report['cases']]
    assert commands.count('input-on') == commands.count('input-off') == 1
    assert 'set-input-timer' in commands and commands.count('input-timer-status') == 3
    assert 'transient-enable' not in commands and 'set-cv' not in commands
    on = next(c for c in report['cases'] if c['command'] == 'input-on')
    assert '--confirm' in on['arguments'] and 'input-timer' in on['arguments']
    assert report['declared_source_limits']['current_a'] == 0.8
    assert report['declared_source_limits']['voltage_v'] == 5
    cases = {case['name']: case for case in report['cases']}
    args = cases['timer-setup']['arguments']
    assert args[args.index('--seconds') + 1] == '5'
    assert '10 s cutoff' in cases['timer-on']['condition']
    assert 'SCPI ON write start' in cases['timer-expiry']['condition']


@pytest.mark.parametrize('extra', [['-Modes', 'cc'], ['-TimerSeconds', '0'],
                                   ['-TimerSeconds', '11'], ['-TimerSeconds', '1.5'],
                                   ['-Current', '0.9'], ['-Current', '3']])
def test_input_timer_script_invalid_settings_and_selection(run_script, extra):
    result = run_script(['-InputTimerOnly', *extra])
    assert result.returncode != 0, result.stdout + result.stderr
    if extra == ['-Current', '0.9']:
        assert 'CC current must not exceed the declared source current limit.' in result.stdout
        assert 'Report:' not in result.stdout


def test_input_timer_is_also_selectable_with_modes(run_script):
    result = run_script(['-PlanOnly', '-Modes', 'input-timer', '-TimerSeconds', '3'])
    assert result.returncode == 0, result.stdout + result.stderr
    report_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    names = [case['name'] for case in report['cases']]
    assert 'timer-on' in names and 'timer-disable' in names
    assert 'trigger-snapshot' not in names and 'set-cc-high' not in names
    assert report['overall_status'] == 'PLAN ONLY' and report['prechecks']['plan'] == 'PASS'
    assert report['hardware_touched'] is False
    assert all(case['status'] == 'NOT RUN' for case in report['cases'])
    cases = {case['name']: case for case in report['cases']}
    assert '10 s cutoff' in cases['timer-on']['condition']
    assert 'SCPI ON write start' in cases['timer-expiry']['condition']
    args = cases['timer-setup']['arguments']
    assert args[args.index('--seconds') + 1] == '3'

    refused = run_script(['-Modes', 'input-timer', '-TimerSeconds', '3'])
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert 'cutoff applies only to the 5 s Timer trial' in refused.stdout
    refused_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', refused.stdout)[-1])
    refused_report = json.loads(refused_path.read_text(encoding='utf-8-sig'))
    assert refused_report['overall_status'] == 'BLOCKED'
    assert refused_report['prechecks']['plan'] == 'PASS'
    assert refused_report['modes'] == ['input-timer']
    assert refused_report['hardware_touched'] is False
    assert all(case['status'] == 'NOT RUN' for case in refused_report['cases'])


@pytest.mark.parametrize('scenario,launches,status,code', [
    ('normal-expiry-query-delay', 1, 'PASS', None),
    ('early-expiry-within-resolution', 1, 'PASS', None),
    ('late-on-observation', 1, 'PASS', None),
    ('crossing-off', 1, 'FAIL', 'timer_automatic_off_unconfirmed'),
    ('unbracketed-off', 1, 'FAIL', 'timer_automatic_off_unconfirmed'),
    ('stays-on', 1, 'FAIL', 'timer_expiry_unconfirmed'),
    ('invalid', 1, 'FAIL', 'readback_mismatch'),
    ('late-off', 1, 'FAIL', 'timer_expiry_unconfirmed'),
    ('late-on-confirmation', 0, 'FAIL', 'timer_expiry_unconfirmed'),
    ('early-off', 1, 'FAIL', 'timer_expiry_early'),
    ('unconfirmed-timing', 1, 'FAIL', 'timer_timing_unconfirmed'),
])
def test_timer_expiry_requires_automatic_off_within_scpi_window(run_script, scenario, launches, status, code):
    result = run_script(['-Scenario', scenario], harness="""
$script:watch = @{ElapsedMilliseconds=1000}
$script:timerOnStartMs = 0
$script:timerOnConfirmedMs = 500
$script:timerOnTiming = @{on_write_started_ns=10000000000L;on_confirmed_ns=10500000000L}
$script:launches = 0
$script:perCommandDeadlineMs = 30000
$TimerSeconds = '5'
$script:report = @{results=@([ordered]@{name='timer-expiry';status='NOT RUN'})}
function Get-TimerMonotonicNs {
    if ($Scenario -eq 'late-on-confirmation') { return 16001000000L }
    return 11000000000L
}
function Assert-Runtime($Result, [string]$Name) {}
function Read-ValidationAnswer { throw 'Timer result must not ask the operator.' }
function New-Observation([long]$Start, [long]$End, [string]$State) {
    @{seconds=5;enabled=($State -eq 'ON');input_state=$State;
      timer_timing=@{query_started_ns=$Start;query_completed_ns=$End}}
}
function Invoke-LiveCommand([string]$Command, [string]$CaseName, [string[]]$ExtraArgs,
                            [int]$DeadlineMs, [long]$TimerObservationDeadlineNs) {
    $script:launches++
    if ($Command -cne 'input-timer-status' -or $TimerObservationDeadlineNs -ne 16000000000L) {
        throw 'Unexpected observation command or deadline.'
    }
    $settings = $ExtraArgs[-1] | ConvertFrom-Json
    if ($settings.seconds -ne 5 -or $settings.on_write_started_ns -ne 10000000000L -or
        $settings.on_confirmed_ns -ne 10500000000L) { throw 'Missing SCPI timing context.' }
    # Both physical events in the approved resolution give identical evidence.
    $on = New-Observation 14450000000L 14600000000L 'ON'
    $off = New-Observation 15200000000L 15350000000L 'OFF'
    switch ($Scenario) {
        'late-on-observation' { $on = New-Observation 15010000000L 15160000000L 'ON' }
        'crossing-off' { $on = New-Observation 13900000000L 14050000000L 'ON' }
        'unbracketed-off' { $on = $null }
        'stays-on' { $off = New-Observation 15200000000L 15350000000L 'ON' }
        'invalid' { $on.input_state = 'UNKNOWN' }
        'late-off' { $off = New-Observation 15900000000L 16050000000L 'OFF' }
        'early-off' { $off = New-Observation 14700000000L 14850000000L 'OFF' }
        'unconfirmed-timing' { $on.timer_timing = $null }
    }
    $script:report.results[0]['observed_at_ns'] = $off.timer_timing.query_completed_ns
    $script:report.results[0]['observed_at_ms'] = 5500
    @{result=@{timer_observations=@($on,$off | Where-Object { $null -ne $_ })}}
}
try {
    Invoke-TimerExpiry @{name='timer-expiry';command='input-timer-status';args=@()}
    if ($script:report.results[0].status -ne 'NOT RUN') { throw 'Expiry was marked before cleanup.' }
    Confirm-TimerExpiry
}
catch { if ($script:report.results[0].status -ne 'FAIL') { throw } }
$entry = $script:report.results[0]
@{launches=$script:launches;status=$entry.status;error_code=$entry.error_code;
  bracket=$entry.timer_expiry_bracket} | ConvertTo-Json -Depth 4 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    bracket = evidence.pop('bracket')
    assert evidence == {'launches': launches, 'status': status, 'error_code': code}
    if status == 'PASS':
        assert 0 <= bracket['uncertainty_ns'] <= bracket['resolution_ns'] == 10**9
        assert bracket['first_off_completed_ns'] >= bracket['expected_expiry_ns']
        assert bracket['uncertainty_ns'] == bracket['first_off_completed_ns'] - bracket['last_on_started_ns']

@pytest.mark.parametrize('scenario,code', [
    ('late-readback', 'timer_expiry_unconfirmed'),
    ('command-timeout', 'deadline_expired'),
    ('observation-timeout', 'timer_expiry_unconfirmed'),
])
def test_timer_expiry_preserves_cleanup_when_communication_is_trusted(run_script, tmp_path, scenario, code):
    fake_cli = tmp_path / 'fake_timer_cli.py'
    fake_cli.write_text(
        'import json\n'
        'import sys\n'
        'import time\n'
        'command = sys.argv[1]\n'
        'result = {"visa": {"library": "fake", "manufacturer": "fake"}, "input_state": "OFF"}\n'
        'if command == "input-timer-status":\n'
        '    if sys.argv[2] != "late-readback":\n'
        '        time.sleep(1.2)\n'
        '    result.update(seconds=5, enabled=True, input_state="ON")\n'
        'print(json.dumps({"ok": True, "result": result}))\n', encoding='utf-8')
    result = run_script(['-Scenario', scenario], extra_env={
        'FAKE_TIMER_PYTHON': sys.executable, 'FAKE_TIMER_CLI': str(fake_cli),
    }, harness="""
$script:calls = @()
$script:report = @{status='NOT RUN';results=@([ordered]@{name='timer-expiry';status='NOT RUN'})}
$script:perCommandDeadlineMs = 30000
$script:totalDeadlineMs = if ($Scenario -eq 'command-timeout') { 600 } else { 900000 }
$script:expectedLibrary = 'fake'
$script:expectedManufacturer = 'fake'
$script:triggerSelected = $true
$script:triggerTrusted = $true
$inputOnAttempted = $true
$timerEnableAttempted = $true
$TimerSeconds = '5'
$script:timerOnStartMs = -1000
$script:timerOnConfirmedMs = -750
$script:timerOnTiming = @{on_write_started_ns=1000000000L;on_confirmed_ns=1100000000L}
$script:clockReads = 0
function Get-TimerMonotonicNs {
    $script:clockReads++
    if ($Scenario -eq 'late-readback' -and $script:clockReads -ge 5) { return 7001000000L }
    if ($Scenario -eq 'observation-timeout') { return 6600000000L }
    return 5500000000L
}
function New-CliProcessInfo([string[]]$Arguments) {
    $command = $Arguments[2]
    $script:calls += $command
    $start = [Diagnostics.ProcessStartInfo]::new($env:FAKE_TIMER_PYTHON)
    $start.Arguments = '\"{0}\" {1} {2}' -f $env:FAKE_TIMER_CLI, $command, $Scenario
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    return $start
}
# Run the outer script's cleanup with the flags left by a dispatched Timer trial.
$main = $ast.EndBlock.Statements | Where-Object { $null -ne $_.Finally } | Select-Object -Last 1
$cleanup = $main.Finally.Extent.Text
$script:watch = [Diagnostics.Stopwatch]::StartNew()
try { Invoke-TimerExpiry @{name='timer-expiry';command='input-timer-status';args=@()} }
catch {
    if ($script:report.results[0].status -ne 'FAIL') { throw }
    $script:report.status = 'FAIL'
} finally {
    . ([scriptblock]::Create($cleanup.Substring(1, $cleanup.Length - 2)))
}
@{calls=$script:calls;error_code=$script:report.results[0].error_code;
  statuses=@($script:report.results | ForEach-Object { $_.status })} | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence['error_code'] == code and evidence['statuses'][0] == 'FAIL'
    if scenario == 'late-readback':
        assert evidence['calls'] == ['input-timer-status', 'input-off', 'input-state', 'input-timer-disable']
        assert evidence['statuses'][1:] == ['PASS'] * 3
    else:
        assert evidence['calls'] == ['input-timer-status']
        assert evidence['statuses'][1:] == ['NOT RUN'] * 3
        assert 'independent operator action required' in result.stdout


@pytest.mark.parametrize('scenario', ['timely', 'late-on', 'late-on-with-trigger'])
def test_input_timer_on_subprocess_respects_scpi_deadline(run_script, tmp_path, scenario):
    write_stamp = tmp_path / 'on-write-ns'
    with (tmp_path / 'sitecustomize.py').open('a', encoding='utf-8') as preload:
        preload.write("""
import os
import sys
from contextlib import contextmanager
from time import monotonic_ns, sleep

sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_hardware_validation import ValidationFake
from electronicload_tool_core import control, readonly

class TimerFake(ValidationFake):
    def write(self, command):
        if command == 'INP ON':
            Path(os.environ['FAKE_TIMER_WRITE_STAMP']).write_text(str(monotonic_ns()))
        super().write(command)

    def query(self, command):
        if self.input and command == 'INP:STAT?' and os.environ['FAKE_TIMER_SCENARIO'] != 'timely':
            sleep(12)
        return super().query(command)

@contextmanager
def fake_session(_resource):
    fake = TimerFake()
    fake.current = 1
    fake.values.update({'INP:TIM:DEL': '5', 'INP:TIM': '1'})
    if 'input-on' in sys.argv:
        sleep(0.8)
    yield fake, {'library': 'fake', 'manufacturer': 'fake'}

control.open_session = readonly.open_session = fake_session
""")
    result = run_script(['-Scenario', scenario], extra_env={
        'FAKE_TIMER_PYTHON': sys.executable, 'FAKE_TIMER_RESOURCE': RESOURCE,
        'FAKE_TIMER_WRITE_STAMP': str(write_stamp), 'FAKE_TIMER_SCENARIO': scenario,
    }, harness="""
$python = $env:FAKE_TIMER_PYTHON
$repo = (Get-Location).Path
$script:connectionArgs = @('--resource', $env:FAKE_TIMER_RESOURCE)
$script:report = @{status='NOT RUN';results=@([ordered]@{name='timer-on';status='NOT RUN'})}
$script:perCommandDeadlineMs = 30000
$script:totalDeadlineMs = 900000
$script:expectedLibrary = 'fake'
$script:expectedManufacturer = 'fake'
$script:triggerSelected = $Scenario -eq 'late-on-with-trigger'
$script:triggerTrusted = $true
$script:timerPowered = $true
$inputOnAttempted = $true
$timerEnableAttempted = $true
$TimerSeconds = '5'
$main = $ast.EndBlock.Statements | Where-Object { $null -ne $_.Finally } | Select-Object -Last 1
$cleanup = $main.Finally.Extent.Text
$watch = [Diagnostics.Stopwatch]::StartNew()
$realProcessInfo = ${function:New-CliProcessInfo}
function New-CliProcessInfo([string[]]$Arguments) {
    if ($Arguments[2] -ceq 'input-off') { $script:cleanupDispatchedNs = Get-TimerMonotonicNs }
    & $realProcessInfo $Arguments
}
try {
    $on = Invoke-LiveCommand -Command 'input-on' -CaseName 'timer-on' -ExtraArgs @(
        '--hardware-validation', 'input-timer', '--confirm', '--validation-settings-json',
        '{"seconds":5,"range":"high","current":1}')
} catch {
    $failureNs = Get-TimerMonotonicNs
    if ($script:report.results[0].status -ne 'FAIL') { throw }
    $script:report.status = 'FAIL'
} finally {
    . ([scriptblock]::Create($cleanup.Substring(1, $cleanup.Length - 2)))
}
@{failure_ns=$failureNs;cleanup_dispatched_ns=$script:cleanupDispatchedNs;
  timing=$on.result.timer_timing;error_code=$script:report.results[0].error_code;
  statuses=@($script:report.results | ForEach-Object { $_.status })} | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert write_stamp.exists(), result.stdout + result.stderr
    write_ns = int(write_stamp.read_text())
    if scenario == 'timely':
        assert evidence['statuses'] == ['PASS'] * 4
        assert evidence['timing']['on_write_started_ns'] <= write_ns <= evidence['timing']['on_confirmed_ns']
    else:
        assert evidence['error_code'] == 'timer_expiry_unconfirmed'
        if scenario == 'late-on-with-trigger':
            assert evidence['statuses'] == ['FAIL', 'NOT RUN', 'NOT RUN', 'NOT RUN']
            assert evidence['cleanup_dispatched_ns'] is None
            assert 'independent operator action required' in result.stdout
            handled_ns = evidence['failure_ns']
        else:
            assert evidence['statuses'] == ['FAIL', 'PASS', 'PASS', 'PASS']
            handled_ns = evidence['cleanup_dispatched_ns']
        # Allow process scheduling overhead while distinguishing a launch-relative timeout
        # and the late reply from the approved six seconds after the write starts.
        assert 5.75 <= (handled_ns - write_ns) / 1e9 <= 7.5


@pytest.mark.parametrize('scenario,command,deadline,code', [
    ('Timer', 'input-on', 7_000_000_000, 'timer_expiry_unconfirmed'),
    ('Workflow', 'cc-timed-load', 11_000_000_000, 'workflow_cleanup_unconfirmed'),
])
def test_powered_deadline_notifies_operator_before_waiting_for_stuck_process(run_script, scenario, command, deadline,
                                                                           code):
    result = run_script(['-Scenario', scenario], harness="""
$script:nowNs = 1000000000L
$case = if ($Scenario -ceq 'Timer') { 'timer-on' } else { 'workflow-cc-timed-load' }
$command = if ($Scenario -ceq 'Timer') { 'input-on' } else { 'cc-timed-load' }
$script:report = @{status='NOT RUN';results=@([ordered]@{name=$case;status='NOT RUN'})}
$script:perCommandDeadlineMs = 30000
$script:totalDeadlineMs = 900000
$script:watch = [Diagnostics.Stopwatch]::StartNew()
$script:timerPowered = $Scenario -ceq 'Timer'
$inputOnAttempted = $script:timerPowered
$timerEnableAttempted = $script:timerPowered
$TimerSeconds = '5'
$script:calls = @()
function Get-TimerMonotonicNs { $script:nowNs }
function New-LiveCliProcess([string[]]$Arguments) {
    $script:calls += $Arguments[2]
    $signal = [Text.Encoding]::UTF8.GetBytes("$Scenario INP ON write started: 1000000000`n")
    $process = [pscustomobject]@{
        HasExited = $false
        StandardOutput = [IO.StreamReader]::new([IO.MemoryStream]::new())
        StandardError = [IO.StreamReader]::new([IO.MemoryStream]::new($signal))
    }
    $process | Add-Member ScriptMethod Start { $true }
    $process | Add-Member ScriptMethod Kill { $script:killNs = Get-TimerMonotonicNs }
    $process | Add-Member ScriptMethod WaitForExit {
        param([int]$Milliseconds)
        $script:nowNs += [long]$Milliseconds * 1000000L
        $false
    }
    $process | Add-Member ScriptMethod Dispose {
        $this.StandardOutput.Dispose()
        $this.StandardError.Dispose()
    }
    return $process
}
function Write-Host([string]$Message) {
    if ($Message -like '*independent*' -and -not $script:notice) {
        $script:notice = @{at_ns=(Get-TimerMonotonicNs);status=$script:report.status;
                           error_code=$script:report.results[0].error_code}
    }
    Microsoft.PowerShell.Utility\\Write-Host $Message
}
$main = $ast.EndBlock.Statements | Where-Object { $null -ne $_.Finally } | Select-Object -Last 1
$cleanup = $main.Finally.Extent.Text
try { Invoke-LiveCommand -Command $command -CaseName $case }
catch {
    if (-not $script:report.results[0].error_code) { throw }
    $script:report.status = 'FAIL'
} finally {
    . ([scriptblock]::Create($cleanup.Substring(1, $cleanup.Length - 2)))
}
@{notice=$script:notice;kill_ns=$script:killNs;completed_ns=(Get-TimerMonotonicNs);
  calls=@($script:calls);statuses=@($script:report.results | ForEach-Object { $_.status })} |
    ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence['notice'] == {
        'at_ns': deadline, 'status': 'FAIL', 'error_code': code,
    }
    assert evidence['kill_ns'] == deadline
    assert evidence['completed_ns'] == deadline + 5_000_000_000
    assert evidence['calls'] == [command]
    assert evidence['statuses'] == ['FAIL'] + (['NOT RUN'] * 3 if scenario == 'Timer' else [])


def test_timer_expiry_script_observes_with_one_real_cli_process(run_script, tmp_path):
    # Exercise the actual process/CLI/session boundary that caused the
    # former polling interval to exceed the approved one-second bracket.
    with (tmp_path / 'sitecustomize.py').open('a', encoding='utf-8') as preload:
        preload.write("""
import os
import sys
from contextlib import contextmanager
from time import monotonic_ns, sleep

sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_hardware_validation import ValidationFake
from electronicload_tool_core import control

@contextmanager
def fake_session(_resource):
    class TimerObserver(ValidationFake):
        def query(self, command):
            if command == 'INP:STAT?':
                self.input = monotonic_ns() < int(os.environ['FAKE_TIMER_ON_NS']) + 5_010_000_000
            response = super().query(command)
            sleep(0.15)
            return response

        def write(self, command):
            raise AssertionError('Timer observation must be read-only')

    with Path(os.environ['FAKE_TIMER_SESSIONS']).open('a') as log:
        log.write('session\\n')
    fake = TimerObserver()
    fake.query_delay = 0.15
    fake.values.update({'INP:TIM:DEL': '5', 'INP:TIM': '1'})
    yield fake, {'library': 'fake', 'manufacturer': 'fake'}

control.open_session = fake_session
""")
    sessions = tmp_path / 'sessions.txt'
    result = run_script([], extra_env={'FAKE_TIMER_PYTHON': sys.executable,
                                      'FAKE_TIMER_RESOURCE': RESOURCE,
                                      'FAKE_TIMER_SESSIONS': str(sessions)}, harness="""
$python = $env:FAKE_TIMER_PYTHON
$repo = (Get-Location).Path
$script:connectionArgs = @('--resource', $env:FAKE_TIMER_RESOURCE)
$script:report = @{status='NOT RUN';results=@([ordered]@{name='timer-expiry';status='NOT RUN'})}
$script:perCommandDeadlineMs = 30000
$script:totalDeadlineMs = 900000
$script:expectedLibrary = 'fake'
$script:expectedManufacturer = 'fake'
$watch = [Diagnostics.Stopwatch]::StartNew()
$script:timerPowered = $true
$TimerSeconds = '5'
$now = Get-TimerMonotonicNs
$script:timerOnTiming = @{on_write_started_ns=($now - 1000000000L);on_confirmed_ns=$now}
$script:timerOnStartMs = -1000
$script:timerOnConfirmedMs = 0
$env:FAKE_TIMER_ON_NS = [string]$script:timerOnTiming.on_write_started_ns
function Read-ValidationAnswer { throw 'Timer result must not ask the operator.' }
Invoke-TimerExpiry @{name='timer-expiry';command='input-timer-status';args=@()}
$entry = $script:report.results[0]
@{status=$entry.status;bracket=$entry.timer_expiry_bracket} | ConvertTo-Json -Depth 4 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence['status'] == 'NOT RUN'  # Real cleanup remains a separate requirement.
    assert 0 <= evidence['bracket']['uncertainty_ns'] <= 10**9
    assert evidence['bracket']['first_off_completed_ns'] >= evidence['bracket']['expected_expiry_ns']
    assert sessions.read_text().splitlines() == ['session']


@pytest.mark.parametrize('scenario', ['timed', 'early-drop', 'voltage', 'late-off-reply'])
def test_contributor_workflow_script_accepts_bounded_duration_evidence_after_confirmed_off(run_script, tmp_path, scenario):
    with (tmp_path / 'sitecustomize.py').open('a', encoding='utf-8') as preload:
        preload.write("""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_hardware_validation import ValidationFake
from electronicload_tool_core import workflows
from time import sleep

@contextmanager
def fake_session(_resource):
    class DelayedLoad(ValidationFake):
        def write(self, command):
            super().write(command)
            if command == 'INP OFF':
                self.off_written = True

        def query(self, command):
            response = super().query(command)
            if (command == 'INP:STAT?' and getattr(self, 'off_written', False)
                    and os.environ.get('FAKE_WORKFLOW_SCENARIO') == 'late-off-reply'):
                sleep(2.2)
                return response
            if self.input:
                sleep(self.query_delay)
                if command == 'MEAS:CURR?' and os.environ.get('FAKE_WORKFLOW_SCENARIO') == 'early-drop':
                    return '0'
                if command == 'MEAS:VOLT?' and os.environ.get('FAKE_WORKFLOW_SCENARIO') == 'voltage':
                    return '4.4'
            return response

    fake = DelayedLoad()
    fake.range = 6
    fake.query_delay = 0.15
    fake.values.update({'INP:TIM': '0', 'MEAS:CURR': '0.1', 'MEAS:VOLT': '5'})
    yield fake, {'library': 'fake', 'manufacturer': 'fake'}

workflows.open_session = fake_session
""")
    result = run_script([], extra_env={'FAKE_WORKFLOW_PYTHON': sys.executable, 'FAKE_WORKFLOW_RESOURCE': RESOURCE,
                                       'FAKE_WORKFLOW_SCENARIO': scenario,
                                       'FAKE_WORKFLOW_REPORT': str(tmp_path / 'workflow-report')}, harness="""
$python = $env:FAKE_WORKFLOW_PYTHON
$repo = (Get-Location).Path
$script:connectionArgs = @('--resource', $env:FAKE_WORKFLOW_RESOURCE)
$command = if ($env:FAKE_WORKFLOW_SCENARIO -ceq 'voltage') { 'cc-discharge' } else { 'cc-timed-load' }
$caseName = "workflow-$command"
$script:reportDirectory = $env:FAKE_WORKFLOW_REPORT
$script:report = @{status='NOT RUN';prechecks=@{};artifact_mode='minimal';
    results=@([ordered]@{name=$caseName;command=$command;status='NOT RUN'})}
$script:perCommandDeadlineMs = 30000
$script:totalDeadlineMs = 900000
$watch = [Diagnostics.Stopwatch]::StartNew()
$entry = $script:report.results[0]
function Read-ValidationAnswer { throw 'Workflow result must not ask the operator.' }
try {
    $extra = @('--hardware-validation', 'workflow', '--current', '0.1', '--range', 'low', '--confirm')
    if ($command -ceq 'cc-discharge') {
        $extra += @('--cutoff-voltage','4.5','--cutoff-capacity-ah','0.00010','--interval-s','0.5','--timeout-s','6')
    } else { $extra += @('--duration-s','5') }
    $null = Invoke-LiveCommand -Command $command -CaseName $caseName -ExtraArgs $extra
    Confirm-WorkflowLoad $caseName
} catch {
    if ($entry.error_code -cnotin @('workflow_load_unconfirmed','capacity_cutoff_unconfirmed','workflow_cleanup_unconfirmed')) { throw }
}
Save-Report
@{result=$entry.response.result;final_status=$entry.status;error_code=$entry.error_code;
  load_confirmed=$entry.load_duration_confirmed;diagnostics=$entry.error_diagnostics} | ConvertTo-Json -Depth 8 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    public = (tmp_path / 'workflow-report/shareable/report.json').read_text(encoding='utf-8-sig')
    assert RESOURCE not in public
    case = json.loads(public)['cases'][0]
    if scenario == 'late-off-reply':
        assert case['status'] == 'FAIL' and case['error_code'] == 'workflow_cleanup_unconfirmed'
        assert case['manual_action_required']
        diagnostic = case['error_diagnostics']
        assert diagnostic['input_state'] == 'UNKNOWN' and not diagnostic['cleanup']['off_confirmed']
        assert diagnostic['off_reply_received_ns'] - diagnostic['off_write_started_ns'] > 2 * 10**9
        assert 'off_confirmed_ns' not in diagnostic
        return
    if scenario == 'voltage':
        assert evidence['final_status'] == 'FAIL' and evidence['error_code'] == 'capacity_cutoff_unconfirmed'
        diagnostic = case['error_diagnostics']
        assert diagnostic['stop_reason'] == 'voltage_cutoff'
        assert diagnostic['last_measurement']['voltage_v'] == 4.4
        assert diagnostic['elapsed_s'] >= 0 and diagnostic['cleanup']['off_confirmed']
        return
    if scenario == 'early-drop':
        assert evidence['final_status'] == 'FAIL' and evidence['error_code'] == 'workflow_load_unconfirmed'
        diagnostic = case['error_diagnostics']
        assert diagnostic['input_state'] == 'OFF' and diagnostic['cleanup']['off_confirmed']
        assert diagnostic['current_samples'][0]['current_a'] == 0
        assert diagnostic['off_confirmed_ns'] - diagnostic['on_write_started_ns'] <= 10**10
        return
    assert evidence['final_status'] == 'PASS'
    assert evidence['error_code'] is None
    workflow = evidence['result']
    assert workflow['stop_reason'] == 'duration_elapsed' and workflow['input_state'] == 'OFF'
    assert workflow['cleanup']['off_confirmed'] and workflow['load_duration_confirmed'] is True
    assert 5 <= (workflow['off_write_started_ns'] - workflow['on_write_started_ns']) / 1e9 <= 10
    assert (workflow['off_confirmed_ns'] - workflow['on_write_started_ns']) / 1e9 <= 10
    assert evidence['load_confirmed'] is True
    assert 0 <= workflow['load_evidence_tail_ns'] <= workflow['load_evidence_resolution_ns'] == 10**9
    assert workflow['load_evidence_source'] == 'instrument_current_samples'


def test_contributor_settings_displays_private_baseline_before_write_and_keeps_it_out_of_report(run_script, tmp_path):
    with (tmp_path / 'sitecustomize.py').open('a', encoding='utf-8') as preload:
        preload.write("""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_hardware_validation import ValidationFake
from electronicload_tool_core import control
from test_trigger import TriggerFake

@contextmanager
def fake_session(_resource):
    class SettingsFake(TriggerFake):
        def write(self, command):
            assert 'slew' not in sys.argv, 'Out-of-bounds baseline must be refused before writes.'
            assert (Path(os.environ['FAKE_SETTINGS_REPORT']) / 'baseline-visible').is_file()
            super().write(command)
            if ('list-cc' in sys.argv and sys.argv[sys.argv.index('--slot') + 1] == '1'
                    and command.startswith('LIST:WID 1,')):
                self.list_steps['LIST:SLEW? 2'] = '0.5'

        def query(self, command):
            if command == 'VOLT:ON?' and self.values['VOLT:LATC'] == '0':
                raise TimeoutError()
            return super().query(command)

    fake = SettingsFake()
    fake.range = 6
    fake.values.update({'INP:TIM': '0', 'VOLT:ON': '0.536', 'VOLT:LATC': '1', 'CURR:SLEW:POS': '0.5'})
    fake.list_steps = {f'LIST:{key}? {index}': value for index in (1, 2)
                       for key, value in (('LEV', '.2'), ('SLEW', '.1'), ('WID', '.3'))}
    if 'list-status' in sys.argv:
        fake.values['LIST:RANG'] = '61'
    if 'trigger-observe' in sys.argv:
        fake.mode = 'LIST'
        fake.input = True
        fake.values['TRIG:SOUR'] = 'BUS'
        fake.values['LIST:RANG'] = '61'
    yield fake, {'library': 'fake', 'manufacturer': 'fake'}

control.open_session = fake_session
""")
    report_dir = tmp_path / 'report'
    report_dir.mkdir()
    result = run_script([], extra_env={'FAKE_SETTINGS_PYTHON': sys.executable, 'FAKE_SETTINGS_RESOURCE': RESOURCE,
                                       'FAKE_SETTINGS_REPORT': str(report_dir)}, harness="""
$python = $env:FAKE_SETTINGS_PYTHON
$repo = (Get-Location).Path
$script:reportDirectory = $env:FAKE_SETTINGS_REPORT
$script:connectionArgs = @('--resource', $env:FAKE_SETTINGS_RESOURCE)
$script:report = @{status='FAIL';prechecks=@{};artifact_mode='minimal';
    results=@([ordered]@{name='auxiliary-controls-von';command='contributor-settings';status='NOT RUN'})}
$script:perCommandDeadlineMs = 30000
$script:totalDeadlineMs = 900000
$watch = [Diagnostics.Stopwatch]::StartNew()
function Write-Host($Text) {
    if ($Text.StartsWith('Settings recovery baseline (private): ')) {
        New-Item -Path (Join-Path $script:reportDirectory 'baseline-visible') -ItemType File -Force | Out-Null
    }
    Microsoft.PowerShell.Utility\\Write-Host $Text
}
try {
    Invoke-LiveCommand -Command 'contributor-settings' -CaseName 'auxiliary-controls-von' -ExtraArgs @(
        '--trial', 'von', '--confirm-isolated')
} catch { Write-Host $_.Exception.Message }
$script:report.results += [ordered]@{name='auxiliary-controls-slew';command='contributor-settings';status='NOT RUN'}
try {
    $null = Invoke-LiveCommand -Command 'contributor-settings' -CaseName 'auxiliary-controls-slew' -ExtraArgs @(
        '--trial', 'slew', '--confirm-isolated')
} catch { Write-Host $_.Exception.Message }
$script:report.results += [ordered]@{name='trigger-bus-readback';command='list-status';status='NOT RUN'}
try {
    $null = Invoke-LiveCommand -Command 'list-status' -CaseName 'trigger-bus-readback' -ExtraArgs @('--hardware-validation','readback')
} catch { Write-Host $_.Exception.Message }
$script:report.results += [ordered]@{name='list-cc-settings';command='contributor-settings';status='NOT RUN'}
try {
    $null = Invoke-LiveCommand -Command 'contributor-settings' -CaseName 'list-cc-settings' -DeadlineMs 300000 -ExtraArgs @(
        '--trial','list-cc','--slot','1','--confirm-isolated')
} catch { Write-Host $_.Exception.Message }
$script:report.results += [ordered]@{name='list-cc-recall';command='contributor-settings';status='NOT RUN'}
try {
    $null = Invoke-LiveCommand -Command 'contributor-settings' -CaseName 'list-cc-recall' -DeadlineMs 300000 -ExtraArgs @(
        '--trial','list-cc','--slot','2','--confirm-isolated')
} catch { Write-Host $_.Exception.Message }
$script:report.results += [ordered]@{name='trigger-bus-dispatch';command='trigger-observe';status='NOT RUN'}
$settings = '{"function":"LIST","trigger":{"source":"BUS"},"configuration":{"range":"low","repeat_count":1,"steps":[{"current_a":0.1,"slew_a_per_us":0.1,"width_s":2},{"current_a":0.2,"slew_a_per_us":0.1,"width_s":2}]}}'
try {
    $deadline = (Get-TimerMonotonicNs) + 14000000000L
    $null = Invoke-LiveCommand -Command 'trigger-observe' -CaseName 'trigger-bus-dispatch' -ExtraArgs @(
        '--hardware-validation','trigger','--validation-settings-json',$settings,'--observation-seconds','15',
        '--deadline-ns',[string]$deadline,'--confirm')
} catch { Write-Host $_.Exception.Message }
# Success responses also contain private untouched baseline values.
$script:report.results += [ordered]@{name='list-status';command='contributor-settings';execution='live';status='PASS';
    response=@{result=@{restored=$true;input_state='OFF';expected_width_s='1';
        observed=@{configuration=@{steps=@(@{width_s='1'},@{width_s='17.123456789'})}}}}}
$script:report.results += [ordered]@{name='transient-status';command='contributor-settings';execution='live';status='PASS';
    response=@{result=@{restored=$true;input_state='OFF';expected_level_a='0.2';
        observed=@{configuration=@{level_b_a='0.876543219'}}}}}
Save-Report
""")
    assert result.returncode == 0, result.stdout + result.stderr
    baseline = next(line for line in result.stdout.splitlines() if line.startswith('Settings recovery baseline'))
    assert json.loads(baseline.split(': ', 1)[1])['threshold_v'] == '0.536'
    assert 'keep the terminals isolated' in result.stdout, result.stdout + result.stderr
    assert result.stdout.index(baseline) < result.stdout.index('keep the terminals isolated')
    report_text = (report_dir / 'shareable/report.json').read_text(encoding='utf-8-sig')
    case, slew_case, bus_case, nested_list, recall_case, capture_case, list_case, transient_case = json.loads(report_text)['cases']
    assert recall_case['error_code'] == 'readback_mismatch'
    recall = recall_case['error_diagnostics']
    assert recall['list_phase'] == 'Baseline Recall' and recall['query'] == 'LIST:WID? 1'
    assert recall['step'] == 1 and float(recall['expected']) != float(recall['observed'])
    assert capture_case['error_code'] == 'readback_mismatch'
    capture = capture_case['error_diagnostics']
    assert capture['query'] == 'LIST:RANG?' and float(capture['observed']) == 61
    assert capture['software_trigger_write_attempted'] is False
    assert bus_case['error_code'] == 'invalid_range_readback'
    assert bus_case['error_diagnostics']['query'] == 'LIST:RANG?'
    assert float(bus_case['error_diagnostics']['observed']) == 61
    assert nested_list['error_code'] == 'settings_restore_unconfirmed'
    assert nested_list['manual_action_required']
    nested = nested_list['error_diagnostics']['original_error']
    assert nested['error_code'] == 'invalid_response' and nested['query'] == 'LIST:SLEW? 2'
    assert float(nested['observed']) == 0.5 and float(nested['maximum']) == 0.25
    assert RESOURCE not in report_text
    assert slew_case['status'] == 'FAIL' and slew_case['error_code'] == 'invalid_response'
    diagnostic = slew_case['error_diagnostics']
    assert diagnostic['query'] == 'CURR:SLEW:POS?' and diagnostic['range'] == 'low'
    assert float(diagnostic['observed']) == 0.5 and float(diagnostic['maximum']) == 0.25
    assert diagnostic['no_settings_written'] is True
    assert list_case['restored'] and list_case['expected_width_s'] == '1'
    assert transient_case['restored'] and transient_case['expected_level_a'] == '0.2'
    assert 'observed' not in list_case and 'observed' not in transient_case
    assert '17.123456789' not in report_text and '0.876543219' not in report_text
    assert case['status'] == 'FAIL' and case['error_code'] == 'settings_restore_unconfirmed'
    assert case['manual_action_required']
    assert '0.536' not in report_text and not (report_dir / 'private').exists()


def test_selected_modes_report_actual_checks(run_script, tmp_path):
    # Exercise the real preflight dispatch with a harmless child script, not an interactive VISA run.
    stub = tmp_path / 'check.ps1'
    stub.write_text(
        "param([switch]$BaseOnly, [switch]$ReadbackOnly, [switch]$CrLedSlowRiseOnly)\n"
        "$check = @($PSBoundParameters.Keys)[0]\n"
        "Add-Content -LiteralPath $env:CHECK_LOG -Value $check\n"
        "if ($check -ceq $env:FAIL_CHECK) { exit 1 }\n"
        "exit 0\n", encoding='utf-8')
    result = run_script([], extra_env={'CHECK_SCRIPT': str(stub), 'CHECK_LOG': str(tmp_path / 'calls.txt')},
                        harness="""
# The shared harness has a typed [string]$Scenario parameter (case-insensitive).
$results = @(foreach ($checkCase in @(
    @{modes=@('workflow'); fail=''},
    @{modes=@('measurement-extensions'); fail=''},
    @{modes=@('auxiliary-controls'); fail=''},
    @{modes=@('list-cc'); fail=''},
    @{modes=@('workflow','measurement-extensions','auxiliary-controls','list-cc'); fail=''},
    @{modes=@('cc','workflow'); fail=''},
    @{modes=@('readback','list-cc'); fail=''},
    @{modes=@('cc','readback'); fail=''},
    @{modes=@('cc','readback'); fail='BaseOnly'},
    @{modes=@('cc','readback'); fail='ReadbackOnly'},
    @{modes=@('cr-led','current-slow-rise'); fail=''}
)) {
    $script:report = @{prechecks=@{preflight='NOT RUN'}}
    [IO.File]::WriteAllText($env:CHECK_LOG, '')
    $env:FAIL_CHECK = $checkCase.fail
    $failed = $false
    try {
        Invoke-SelectedPreflight -Modes $checkCase.modes -PreflightScript $env:CHECK_SCRIPT
    } catch {
        $failed = $true
    }
    [ordered]@{status=$script:report.prechecks.preflight; failed=$failed;
        calls=@(Get-Content -LiteralPath $env:CHECK_LOG)}
})
ConvertTo-Json -InputObject $results -Depth 5 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    results = json.loads(result.stdout)
    assert results == [
        *[{'status': 'NOT RUN', 'failed': False, 'calls': []} for _ in range(5)],
        {'status': 'PASS', 'failed': False, 'calls': ['BaseOnly']},
        {'status': 'PASS', 'failed': False, 'calls': ['ReadbackOnly']},
        {'status': 'PASS', 'failed': False, 'calls': ['BaseOnly', 'ReadbackOnly']},
        {'status': 'FAIL', 'failed': True, 'calls': ['BaseOnly']},
        {'status': 'FAIL', 'failed': True, 'calls': ['BaseOnly', 'ReadbackOnly']},
        {'status': 'PASS', 'failed': False, 'calls': ['CrLedSlowRiseOnly']},
    ]


@pytest.mark.parametrize('windows_powershell', [False, True] if os.name == 'nt' else [False])
def test_selected_modes_confirm_preparation_before_start(run_script, windows_powershell):
    # PlanOnly and redirected-input refusal exit before the interactive
    # boundary. Exercise the real start/transition functions without VISA.
    result = run_script([], windows_powershell=windows_powershell, extra_env={
        'FULL_MODES': ','.join(FULL_MODES),
    }, harness="""
$Target = 'itech-it8812c'
$Resource = 'synthetic'
$Backend = 'system'
$SourceVoltageLimitV = 5
$SourceCurrentLimitA = 0.8
$TriggerExternalSetup = 'reviewed fixture'
$ListScratchSlot = 3
$extensionModes = @('measurement-extensions','auxiliary-controls','list-cc')
$featureModes = @('cr-led','current-slow-rise')
function Write-Host([string]$Text) { $script:notices += $Text }
function Read-Host([string]$Prompt) {
    $script:answers += @{name='identify';prompt=$Prompt;notices=@($script:notices)}
    return $script:reply
}
function Read-ValidationAnswer([string]$Prompt, [string]$CaseName, [switch]$DiscardPendingKeys) {
    $script:answers += @{name=$CaseName;prompt=$Prompt;notices=@($script:notices)}
    if ($CaseName -ceq 'identify') { return $script:reply }
    return $script:transitionReply
}
$all = @($env:FULL_MODES -split ',')
$selections = @(foreach ($mode in $all) { @{modes=@($mode);legacy=$false} })
$selections += @(
    @{modes=$all;legacy=$false},
    @{modes=@('readback','auxiliary-controls','list-cc');legacy=$false},
    @{modes=@('cc','readback');legacy=$false},
    @{modes=@('readback');legacy=$true},
    @{modes=@('cc','readback');legacy=$true},
    @{modes=@('readback','auxiliary-controls');legacy=$true},
    @{modes=@('input-timer','workflow');legacy=$false},
    @{modes=@('system-query','cr-led','current-slow-rise');legacy=$false}
)
$results = @(foreach ($selectionCase in $selections) {
    $modes = $selectionCase.modes
    $isolated = @(if ('readback' -in $modes -and -not $selectionCase.legacy) { @{name='fixed-status-cc'} }
                  if ('auxiliary-controls' -in $modes) { @{name='auxiliary-controls-von'} }
                  if ('list-cc' -in $modes) { @{name='list-cc-settings'} })
    $script:notices = @()
    $script:answers = @()
    $script:reply = ''
    $script:transitionReply = ''
    $connected = Test-SourceConnectedTrials $modes
    $ack = Confirm-OperatorStart $modes $isolated $connected
    if ($null -eq $ack -or $ack -cne '') { throw 'Initial Enter did not authorize the run.' }
    $confirmed = $false
    foreach ($item in $isolated) {
        $confirmed = Confirm-IsolatedBatch $confirmed $connected $item.name
        if (-not $confirmed) { throw 'Isolation authorization missing.' }
    }
    @{modes=$modes;legacy=$selectionCase.legacy;answers=@($script:answers);connected=$connected}
})
# Cancellation must fail at the physical transition and not authorize settings.
$script:transitionReply = 'cancel'
$script:report = @{results=@([ordered]@{name='fixed-status-cc';status='NOT RUN'})}
try {
    $null = Confirm-IsolatedBatch $false $true 'fixed-status-cc'
    throw 'Cancelled transition accepted.'
} catch {
    if ($script:report.results[0].error_code -cne 'operator_confirmation_required') { throw }
}
ConvertTo-Json -InputObject $results -Depth 8 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    selections = json.loads(result.stdout)
    assert len(selections) == len(FULL_MODES) + 8
    assert [item['modes'] for item in selections[:len(FULL_MODES)]] == [[mode] for mode in FULL_MODES]
    for item in selections:
        modes = set(item['modes'])
        answers = item['answers']
        connected = bool(modes & (set(FULL_MODES[:9]) | {
            'trigger', 'input-timer', 'protection', 'sequence', 'workflow',
        }))
        isolated = bool(modes & {'auxiliary-controls', 'list-cc'} or
                        'readback' in modes and not item['legacy'])
        assert item['connected'] is connected
        assert len(answers) == 1 + int(connected and isolated)
        assert answers[0]['name'] == 'identify'
        notices = '\n'.join(answers[0]['notices'])
        assert ('Confirm the external source is manually set' in notices) is connected
        assert ('Input Timer:' in notices) == ('input-timer' in modes)
        assert ('Workflow:' in notices) == ('workflow' in modes)
        assert ('10 s hardware cutoff' in notices) == bool(modes & {'input-timer', 'workflow'})
        assert ('Sequence:' in notices) == ('sequence' in modes)
        assert ('EXTERNAL Trigger equipment' in notices) == ('trigger' in modes)
        assert ('Protection:' in notices) == ('protection' in modes)
        assert ('Average count' in notices) == ('measurement-extensions' in modes)
        assert ('Scratch Slot 3' in notices) == ('list-cc' in modes)
        assert ('Automatic Readback temporarily' in notices) == (
            'readback' in modes and not item['legacy']
        )
        assert ('Physically disconnect the source and DUT before pressing Enter' in notices) == (
            isolated and not connected
        )
        if connected and isolated:
            assert 'Physically disconnect the source and DUT now' in answers[1]['prompt']
            assert 'OVERWRITE' not in answers[1]['prompt'] and 'ISOLATED' not in answers[1]['prompt']


def test_protection_prompts_only_for_physical_source_transitions(run_script):
    # Use the real Protection orchestrator and powered-trial function with
    # fake command results. No VISA, console or elapsed loading is involved.
    result = run_script([], harness="""
$script:protectionTrials = @(
    @{name='software-current';settings=@{current=1};flags=@('OC','PS')},
    @{name='software-power';settings=@{power=1};flags=@('OP','PS')},
    @{name='hardware-power';settings=@{limit=1};flags=@()}
)
$names = @('start-state','prepare-cc','prepare-cc-readback',
    'software-current-source-off','software-power-source-off',
    'hardware-power-baseline-on','hardware-power-baseline-trial','hardware-power-baseline-off')
foreach ($trial in $script:protectionTrials) {
    foreach ($suffix in @('cc','restore-cc','on','trial','off','restore-guard','restore-settings','clear','final')) {
        $names += "$($trial.name)-$suffix"
    }
}
$script:protectionScope = @('--hardware-validation','protection')
function Read-ValidationAnswer([string]$Prompt, [string]$CaseName, [switch]$DiscardPendingKeys) {
    $script:calls += "prompt:$CaseName"
    if ($CaseName -ceq $script:cancelAt) { return 'cancel' }
    return ''
}
function Invoke-ProtectionStep([string]$Name, [string[]]$ArgsOverride, [switch]$Cleanup, [switch]$AllowDefaultRefusal) {
    $script:calls += $Name
    $settings = [pscustomobject]@{current='0.30';power='2';limit='3'}
    $fixedCc = @{range='low';current_a='0.20'}
    if ($Name -like '*-original' -or $Name -like '*-final') {
        return @{ok=$true;result=@{fixed_cc=$fixedCc;settings=$settings;input_state='OFF';condition=@{raw=0;flags=@()}}}
    }
    if ($Name -like 'protection-default-*-set') { return @{ok=$false} }
    if ($Name -ceq 'protection-software-current-trial') {
        return @{result=@{input_state='OFF';condition=@{flags=@('OC','PS')}}}
    }
    if ($Name -ceq 'protection-software-power-trial') {
        return @{result=@{input_state='OFF';condition=@{flags=@('OP','PS')}}}
    }
    if ($Name -ceq 'protection-hardware-power-baseline-trial') {
        return @{result=@{input_state='ON';condition=@{flags=@()};voltage_v=5;current_a=0.20;calculated_power_w=1}}
    }
    if ($Name -ceq 'protection-hardware-power-trial') {
        return @{result=@{input_state='ON';condition=@{flags=@()};voltage_v=5;calculated_power_w=0.50}}
    }
    return @{ok=$true;result=@{input_state='OFF'}}
}
$results = @(foreach ($cancelCase in @('', 'protection-software-current-source-off', 'protection-software-power-on')) {
    $script:cancelAt = $cancelCase
    $script:report = @{protection_trials=@{};protection_input_off_unconfirmed=$false;
        results=@(foreach ($name in $names) { [ordered]@{name="protection-$name";status='NOT RUN'} })}
    $script:protectionTrusted = $true
    $script:calls = @()
    try {
        Invoke-ProtectionValidation @{active_mode='fixed';function='CC';input_state='OFF';short_state='OFF'}
        if ($cancelCase) { throw 'Cancelled source transition accepted.' }
    } catch {
        $failed = $script:report.results | Where-Object { $_.name -ceq $cancelCase }
        if (-not $cancelCase -or $failed.error_code -cne 'confirmation_required') { throw }
    }
    @{cancel=$cancelCase;calls=$script:calls;trials=$script:report.protection_trials;
      observations=@($script:report.results | Where-Object { $_.name -like '*-on' -or $_.name -like '*-source-off' })}
})
ConvertTo-Json -InputObject $results -Depth 6 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence, cancelled_off, cancelled_on = json.loads(result.stdout.splitlines()[-1])
    assert 'protection-software-current-clear' not in cancelled_off['calls']
    assert 'protection-software-power-trial' not in cancelled_off['calls']
    assert 'protection-software-power-trial' not in cancelled_on['calls']
    assert 'protection-hardware-power-baseline-trial' not in cancelled_on['calls']
    calls = evidence['calls']
    assert [call for call in calls if call.startswith('prompt:')] == [
        'prompt:protection-software-current-source-off',
        'prompt:protection-software-power-on',
        'prompt:protection-software-power-source-off',
        'prompt:protection-hardware-power-baseline-on',
    ]
    for trial in ('software-current', 'software-power'):
        prefix = f'protection-{trial}'
        assert calls.index(f'{prefix}-trial') < calls.index(f'prompt:{prefix}-source-off')
        assert (calls.index(f'prompt:{prefix}-source-off') < calls.index(f'{prefix}-restore-settings')
                < calls.index(f'{prefix}-clear') < calls.index(f'{prefix}-final'))
    assert calls.index('prompt:protection-software-power-on') < calls.index('protection-software-power-trial')
    assert calls.index('prompt:protection-hardware-power-baseline-on') < calls.index(
        'protection-hardware-power-baseline-trial')
    assert calls.index('protection-hardware-power-baseline-off') < calls.index('protection-hardware-power-trial')
    assert evidence['trials'] == dict.fromkeys(('software-current', 'software-power', 'hardware-power'), 'PASS')
    assert {entry['name']: entry['status'] for entry in evidence['observations']} == {
        'protection-software-current-on': 'SKIP', 'protection-software-current-source-off': 'PASS',
        'protection-software-power-on': 'PASS', 'protection-software-power-source-off': 'PASS',
        'protection-hardware-power-baseline-on': 'PASS', 'protection-hardware-power-on': 'SKIP',
    }


@pytest.mark.parametrize('scenario', ['cancel', 'success', 'persistent', 'late-clean', 'untrusted', 'unexpected-on', 'unexpected-on-off', 'final-fault'])
def test_protection_cancel_restores_original_settings(run_script, scenario):
    # Real finally/restore path; only command exchanges and operator input are fake.
    result = run_script([scenario], harness="""
$script:protectionTrials = @(
    @{name='software-current';settings=[ordered]@{current=1};flags=@('OC','PS')}
)
$script:protectionScope = @('--hardware-validation','protection')
$script:protectionTrusted = $true
$script:report = @{
    protection_trials=@{}
    protection_input_off_unconfirmed=$false
    protection_restore_unconfirmed=$false
    results=@(foreach ($name in @(
        'start-state','prepare-cc','prepare-cc-readback',
        'software-current-original','software-current-cc','software-current-on',
        'software-current-set','software-current-trial','software-current-off','software-current-events',
        'software-current-source-off','software-current-clear',
        'software-current-restore-guard','software-current-restore-settings',
        'software-current-restore-cc','software-current-final'
    )) { [ordered]@{name="protection-$name";status='NOT RUN'} })
}
$script:calls = @()
function Read-ValidationAnswer([string]$Prompt, [string]$CaseName, [switch]$DiscardPendingKeys) {
    $script:calls += "prompt:$CaseName"
    if ($Scenario -ceq 'cancel') { return 'cancel' }
    return ''
}
function Invoke-ProtectionStep([string]$Name, [string[]]$ArgsOverride,
                               [switch]$Cleanup, [switch]$AllowDefaultRefusal) {
    $script:calls += $Name
    if ($PSBoundParameters.ContainsKey('ArgsOverride')) {
        $script:restoreArguments = @($ArgsOverride)
    }
    $entry = $script:report.results | Where-Object { $_.name -ceq $Name }
    $settings = [pscustomobject]@{current='0.30'}
    $fixedCc = [pscustomobject]@{range='low';current_a=$(if ($Scenario -ceq 'cancel') {'0.20'} else {'0.10'})}
    if ($Name -like 'protection-default-*-original') {
        return @{ok=$true;result=@{settings=$settings;fixed_cc=$fixedCc}}
    }
    if ($Name -like 'protection-default-*-set') { return @{ok=$false} }
    if ($Name -ceq 'protection-software-current-clear' -and $Scenario -cin @('persistent','late-clean','untrusted','unexpected-on','unexpected-on-off')) {
        if ($Scenario -ceq 'untrusted') { $script:protectionTrusted = $false }
        if ($Scenario -ceq 'unexpected-on') { $script:report.protection_input_off_unconfirmed = $true }
        $code = if ($Scenario -ceq 'untrusted') { 'query_failed' }
            elseif ($Scenario -cin @('unexpected-on','unexpected-on-off')) { 'protection_clear_unexpected_on' }
            else { 'protection_still_active' }
        Fail-Case $Name $code 'Synthetic Clear failure.'
    }
    $payload = switch ($Name) {
        'protection-software-current-original' {
            @{input_state='OFF';fixed_cc=$fixedCc;settings=$settings}
        }
        'protection-software-current-set' {
            @{input_state='OFF';settings=[pscustomobject]@{current='1'}}
        }
        'protection-software-current-trial' {
            @{input_state='OFF';current_a=0.20;condition=@{flags=@('OC','PS')}}
        }
        'protection-software-current-events' {
            @{input_state='OFF';events=@()}
        }
        'protection-software-current-restore-guard' {
            @{input_state='OFF'}
        }
        'protection-software-current-restore-settings' {
            @{input_state='OFF';settings=$settings}
        }
        'protection-software-current-cc' { @{input_state='OFF'} }
        'protection-software-current-restore-cc' { @{input_state='OFF'} }
        'protection-software-current-clear' { @{input_state='OFF';condition=@{raw=0;flags=@()}} }
        'protection-software-current-final' {
            $flags = if ($Scenario -cin @('persistent','final-fault')) { @('OC','PS') } else { @() }
            @{input_state='OFF';fixed_cc=$fixedCc;settings=$settings;condition=@{raw=$(if ($flags) {8194} else {0});flags=@($flags)}}
        }
        default { throw "Unexpected Protection exchange: $Name" }
    }
    $entry.status = 'PASS'
    $entry.protection_state = $payload.input_state
    $script:report.protection_input_off_unconfirmed = $payload.input_state -cne 'OFF'
    return @{ok=$true;result=$payload}
}
try {
    Invoke-ProtectionValidation @{
        active_mode='fixed';function='CC';input_state='OFF';short_state='OFF'
    }
    if ($Scenario -cne 'success') { throw 'Expected the Protection trial to stop.' }
} catch {
    $script:caught = $_.Exception.Message
}
$sourceOff = $script:report.results | Where-Object { $_.name -ceq 'protection-software-current-source-off' }
$clear = $script:report.results | Where-Object { $_.name -ceq 'protection-software-current-clear' }
$restoreNames = @('protection-software-current-restore-guard',
                  'protection-software-current-restore-settings',
                  'protection-software-current-final')
$restoreStatuses = @{}
foreach ($name in $restoreNames) {
    $entry = $script:report.results | Where-Object { $_.name -ceq $name }
    $restoreStatuses[$name] = $entry.status
}
@{
    caught=$script:caught
    source_off_status=$sourceOff.status
    source_off_error=$sourceOff.error_code
    clear_status=$clear.status
    restore_unconfirmed=$script:report.protection_restore_unconfirmed
    restore_input_off_unconfirmed=$script:report.protection_input_off_unconfirmed
    restore_statuses=$restoreStatuses
    restore_arguments=$script:restoreArguments
    calls=$script:calls
    trial_status=$script:report.protection_trials['software-current']
} | ConvertTo-Json -Depth 8 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    if scenario != 'cancel':
        prefix = 'protection-software-current'
        calls = evidence['calls']
        assert calls.count(f'{prefix}-clear') == 1
        assert (calls.index(f'prompt:{prefix}-source-off') < calls.index(f'{prefix}-restore-guard')
                < calls.index(f'{prefix}-restore-settings') < calls.index(f'{prefix}-restore-cc')
                < calls.index(f'{prefix}-clear'))
        if scenario in {'untrusted', 'unexpected-on'}:
            assert f'{prefix}-final' not in calls
            assert evidence['restore_unconfirmed'] is True
        else:
            assert calls.index(f'{prefix}-clear') < calls.index(f'{prefix}-final')
            assert evidence['restore_unconfirmed'] is False
            expected_final = 'FAIL' if scenario in {'persistent', 'final-fault'} else 'PASS'
            assert evidence['restore_statuses'][f'{prefix}-final'] == expected_final
        assert evidence['trial_status'] == ('PASS' if scenario == 'success' else 'FAIL')
        assert evidence['clear_status'] == ('PASS' if scenario in {'success', 'final-fault'} else 'FAIL')
        if scenario in {'late-clean', 'unexpected-on-off'}:
            assert evidence['caught'] == 'Synthetic Clear failure.'
        return
    assert evidence['caught'] == 'The operator did not confirm the Protection step.'
    assert evidence['source_off_status'] == 'FAIL'
    assert evidence['source_off_error'] == 'confirmation_required'
    assert evidence['clear_status'] == 'NOT RUN'
    assert evidence['restore_unconfirmed'] is False
    assert evidence['restore_input_off_unconfirmed'] is False
    assert evidence['restore_statuses'] == {
        'protection-software-current-restore-guard': 'PASS',
        'protection-software-current-restore-settings': 'PASS',
        'protection-software-current-final': 'PASS',
    }
    assert evidence['restore_arguments'] == [
        '--hardware-validation', 'protection', '--confirm', '--current', '0.30',
    ]
    assert evidence['calls'][-3:] == [
        'protection-software-current-restore-guard',
        'protection-software-current-restore-settings',
        'protection-software-current-final',
    ]


def test_selected_execution_loop_has_no_repeated_operator_prompts(run_script):
    # Execute the real selected-mode loop with controlled CLI responses.
    # Timer, both Workflow loads and Sequence must proceed after the initial
    # authorization without another Read-Host/Read-ValidationAnswer call.
    result = run_script([], harness="""
$source = (Join-Path (Get-Location) 'scripts/live-cli-check.ps1')
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $source, [ref]$null, [ref]$null)
$loop = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.ForStatementAst] -and
        $node.Extent.Text.StartsWith('for ($i = 1; $i -le $planned.Count; $i++)')
}, $true)
if ($null -eq $loop) { throw 'Selected-mode execution loop was not found.' }

$planned = @(
    @{name='identify';command='identify';args=@()},
    @{name='input-state';command='input-state';args=@()},
    @{name='measure-off';command='measure';args=@()},
    @{name='timer-setup';command='set-input-timer';args=@()},
    @{name='timer-enable';command='input-timer-enable';args=@()},
    @{name='timer-status-off';command='input-timer-status';args=@()},
    @{name='timer-on';command='input-on';args=@()},
    @{name='timer-expiry';command='input-timer-status';args=@()},
    @{name='timer-off';command='input-off';args=@()},
    @{name='timer-disable';command='input-timer-disable';args=@()},
    @{name='timer-status-disabled';command='input-timer-status';args=@()},
    @{name='timer-final-state';command='input-state';args=@()},
    @{name='workflow-cc-timed-load';command='cc-timed-load';args=@();workflow_load=$true},
    @{name='workflow-cc-discharge';command='cc-discharge';args=@();workflow_load=$true},
    @{name='sequence';command='sequence';args=@()}
)
$script:report = @{
    results=@(foreach ($step in $planned[1..($planned.Count - 1)]) {
        [ordered]@{name=$step.name;status='NOT RUN'}
    })
    prechecks=@{input_off='NOT RUN'}
    sequence_input_off_unconfirmed=$false
}
$script:calls = @()
$script:answers = @()
$timerSelected = $true
$workflowSelected = $true
$sequenceSelected = $true
$traceSelected = $false
$triggerSelected = $false
$protectionSelected = $false
$readbackSelected = $false
$watch = [Diagnostics.Stopwatch]::StartNew()
$TimerSeconds = '5'
$perCommandDeadlineMs = 30000
$traceStartDeadlineMs = 120000
$readbackSelection = $null
$isolatedBatchConfirmed = $false
$triggerCompleted = $false
$inputOnAttempted = $false
$timerEnableAttempted = $false
$timerPowered = $false
$transientEnableAttempted = $false
$slowRiseEnableAttempted = $false
$activeTransientScope = $null
$slowRiseCleanup = @()
function Write-Host([string]$Text) {}
function Read-Host([string]$Prompt) { throw "Unexpected extra Read-Host prompt: $Prompt" }
function Read-ValidationAnswer([string]$Prompt, [string]$CaseName, [switch]$DiscardPendingKeys) {
    throw "Unexpected extra operator confirmation: $CaseName"
}
function Assert-Runtime($Response, [string]$Name) {
    if ($Response.ok -ne $true) { throw "Unexpected command failure: $Name" }
}
function Assert-InputOff($Response, [string]$Name) {
    if ($Response.result.input_state -cne 'OFF') { throw "Input was not OFF: $Name" }
}
function Assert-ReadbackFields($Response, $Expected, [string]$Name) {}
function Invoke-TimerExpiry($Step) {
    $script:calls += 'timer-expiry'
    ($script:report.results | Where-Object { $_.name -ceq $Step.name }).status = 'PASS'
}
function Confirm-TimerExpiry { $script:calls += 'timer-final-confirmed' }
function Confirm-WorkflowLoad([string]$CaseName) { $script:calls += "workflow:$CaseName" }
function Save-SequenceEvidence($Entry, $Result) {
    $script:calls += 'sequence-evidence'
    $script:report.sequence_input_off_unconfirmed = -not (
        $Result.input_state -ceq 'OFF' -and $Result.cleanup.off_confirmed -eq $true
    )
}
function Invoke-LiveCommand([string]$Command, [string]$CaseName, [string[]]$ExtraArgs,
                            [int]$DeadlineMs, [string]$ExpectedError, [switch]$AllowCrHandoff) {
    $script:calls += "command:$CaseName"
    $payload = @{input_state='OFF'}
    switch ($CaseName) {
        'timer-status-off' {
            $payload.seconds = 5
            $payload.enabled = $true
        }
        'timer-on' {
            $payload.input_state = 'ON'
            $payload.timer_timing = @{on_write_started_ns=1000000000L;on_confirmed_ns=1100000000L}
        }
        'timer-status-disabled' {
            $payload.seconds = 5
            $payload.enabled = $false
        }
        'sequence' {
            $payload = @{
                input_state='OFF'
                completed_steps=7
                measurements=@(@{voltage_v=5;current_a=0.1;power_w=0.5})
                input_states=@{input_state=@('ON','OFF')}
                cleanup=@{off_confirmed=$true;attempted=$true}
                failed_step=$null
                original_error=@{code=$null}
            }
        }
    }
    return @{ok=$true;result=$payload}
}
Invoke-Expression $loop.Extent.Text
@{
    calls=$script:calls
    answers=$script:answers
    timer_powered=$timerPowered
    timer_enabled=$timerEnableAttempted
    input_on_attempted=$inputOnAttempted
    sequence_off_unconfirmed=$script:report.sequence_input_off_unconfirmed
} | ConvertTo-Json -Depth 8 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence['answers'] == []
    assert evidence['timer_powered'] is False
    assert evidence['timer_enabled'] is False
    assert evidence['input_on_attempted'] is False
    assert evidence['sequence_off_unconfirmed'] is False
    calls = evidence['calls']
    assert calls.count('timer-expiry') == 1
    assert calls.count('timer-final-confirmed') == 1
    assert calls.count('workflow:workflow-cc-timed-load') == 1
    assert calls.count('workflow:workflow-cc-discharge') == 1
    assert calls.count('sequence-evidence') == 1
    assert 'command:timer-on' in calls and 'command:timer-off' in calls
    assert 'command:sequence' in calls


def test_trace_validation_preflight_only(run_script):
    checked = run_script(['-TraceOnly'], preflight=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert checked.stdout.rstrip().endswith('PASS: TRACE-only offline preflight; no VISA opened.')


def test_trace_validation_script_plan_only_and_noninteractive_refusal(run_script):
    planned = run_script(['-Modes', 'trace'])
    assert planned.returncode == 2, planned.stdout + planned.stderr
    assert 'requires an interactive console' in planned.stdout
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', planned.stdout)
    assert paths, planned.stdout
    report = json.loads(Path(paths[-1]).read_text(encoding='utf-8-sig'))
    assert report['overall_status'] == 'BLOCKED' and report['prechecks']['plan'] == 'PASS'
    assert report['hardware_touched'] is False
    assert report['trace_start_deadline_ms'] == 120000
    cases = {case['name']: case for case in report['cases']}
    assert len(cases) == 22
    assert all(case['status'] == 'NOT RUN' for case in cases.values())
    assert sum(case['command'] == 'trace-configure' for case in cases.values()) == 5
    assert sum(case['command'] == 'trace-start' for case in cases.values()) == 5
    assert sum(case['command'] == 'trace-status' for case in cases.values()) == 5
    ordered = list(cases)
    assert ordered.index('trace-powered-configure') < ordered.index('trace-powered-status') < ordered.index('trace-powered-on')
    assert 'trace-powered-on' in cases and 'trace-powered-off' in cases
    assert cases['trace-powered-cc-handoff-from-cr']['arguments'] == [
        *cases['trace-powered-setup-cc']['arguments'], '--hardware-validation', 'cr-led',
    ]
    assert cases['trace-powered-cc-handoff-from-cr']['condition']
    assert not any('transient-' in name or name.startswith('cv-') for name in cases)
    assert cases['trace-powered-start']['arguments'][-1] == 'elapsed,delta'
    csv_args = cases['trace-powered-start']['arguments']
    assert csv_args[csv_args.index('--csv') + 1] == '<redacted>'
    assert RESOURCE not in Path(paths[-1]).read_text(encoding='utf-8-sig')
    assert not (Path(paths[-1]).parents[1] / 'trace-powered.csv').exists()


def test_trigger_validation_script_plan_and_noninteractive_refusal(run_script):
    plan = run_script(['-Modes', 'trigger'])
    assert plan.returncode == 2, plan.stdout + plan.stderr
    assert 'requires an interactive console' in plan.stdout
    report_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', plan.stdout)[-1])
    text = report_path.read_text(encoding='utf-8-sig')
    report = json.loads(text)
    assert report['overall_status'] == 'BLOCKED' and report['hardware_touched'] is False
    assert report['prechecks']['plan'] == 'PASS'
    assert report['trigger_restore_status'] == 'NOT RUN'
    assert RESOURCE not in text and not (report_path.parents[1] / 'private').exists()
    cases = {entry['name']: entry for entry in report['cases']}
    assert all(case['status'] == 'NOT RUN' for case in cases.values())
    assert report['trigger_observation_seconds'] == 15
    assert report['trigger_max_on_seconds'] == 60
    assert not any(name.startswith('trigger-manual-') for name in cases)
    for source in ('hold', 'timer', 'bus', 'external'):
        assert cases[f'trigger-{source}-configure']['command'] == 'trigger-configure'
        assert cases[f'trigger-{source}-source-status']['command'] == 'trigger-status'
        assert '--confirm' in cases[f'trigger-{source}-on']['arguments']
        assert cases[f'trigger-{source}-off']['command'] == 'input-off'
        software = source in ('hold', 'bus')
        assert cases[f'trigger-{source}-observed']['command'] == ('automatic-observation' if software else 'trigger-observe')
        assert (f'trigger-{source}-dispatch' in cases) == software
        capture = cases[f'trigger-{source}-dispatch' if software else f'trigger-{source}-observed']
        assert capture['command'] == 'trigger-observe'
        names = list(cases)
        capture_name = f'trigger-{source}-dispatch' if software else f'trigger-{source}-observed'
        assert names.index(f'trigger-{source}-on') < names.index(capture_name) < names.index(f'trigger-{source}-off')
        assert 'MEAS:CURR?' in capture['queries']
        assert ('STAT:QUES:COND?' in capture['queries']) == (source in ('bus', 'external'))
        assert capture['planned_writes'] == (
            ['SYST:REM', 'TRIG:IMM' if source == 'hold' else '*TRG'] if software else [])
    assert cases['trigger-bus-setup']['command'] == 'set-list-cc'
    for source in ('bus', 'external'):
        assert cases[f'trigger-{source}-readback']['command'] == 'list-status'
        assert list(cases).index(f'trigger-{source}-readback') < list(cases).index(f'trigger-{source}-on')
    assert cases['trigger-hold-setup']['command'] == 'set-transient-cc'
    pulse_args = cases['trigger-hold-setup']['arguments']
    assert pulse_args[pulse_args.index('--pulse-width') + 1] == '2'
    timer_args = cases['trigger-timer-configure']['arguments']
    assert timer_args[timer_args.index('--interval-s') + 1] == '5'
    list_args = cases['trigger-bus-setup']['arguments']
    list_plan = json.loads(list_args[list_args.index('--arguments-json') + 1])
    assert list_plan['repeat_count'] == 1
    assert [step['width_s'] for step in list_plan['steps']] == [2, 2]
    assert not any(name.startswith('transient-') or name.startswith('cc-') for name in cases)
    for flags in (['-PulseWidth', '0.001'],
                  ['-TriggerObservationSeconds', '40', '-TriggerListWidthA', '20', '-TriggerListWidthB', '20']):
        refused_plan = run_script(['-Modes', 'trigger', '-PlanOnly', *flags])
        assert refused_plan.returncode == 2, refused_plan.stdout + refused_plan.stderr
    mixed = run_script(['-Modes', 'transient-cc,trigger', '-PlanOnly'])
    assert mixed.returncode == 0, mixed.stdout + mixed.stderr
    mixed_report_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', mixed.stdout)[-1])
    mixed_names = [case['name'] for case in json.loads(
        mixed_report_path.read_text(encoding='utf-8-sig'))['cases']]
    assert mixed_names.index('trigger-snapshot') < mixed_names.index('transient-continuous-auto-setup')


@pytest.mark.parametrize('ready', [True, False])
def test_external_trigger_confirms_equipment_before_input_on(run_script, ready):
    # Exercise the actual trial with scripted instrument replies and clock.
    result = run_script(['ready' if ready else 'cancel'], harness="""
$script:watch = @{ElapsedMilliseconds=0}
$script:totalDeadlineMs = 900000
$script:perCommandDeadlineMs = 30000
$TriggerMaxOnSeconds = 60
$TriggerObservationSeconds = 15
$TriggerListRepeat = 1
$LevelA = '0.1'; $LevelB = '0.2'
$RiseSlew = '0.1'; $FallSlew = '0.1'
$TriggerListWidthA = '2'; $TriggerListWidthB = '2'
$TriggerExternalSetup = 'reviewed fixture'
$script:triggerTrusted = $true
$script:triggerProcessStillRunning = $false
$script:triggerArtifacts = @()
$script:report = @{results=@(foreach ($suffix in @('setup','observed','off','release')) {
    [ordered]@{name="trigger-external-$suffix";status='NOT RUN'}
})}
$script:calls = @()
function Read-ValidationAnswer([string]$Prompt, [string]$CaseName, [switch]$DiscardPendingKeys) {
    if (-not $DiscardPendingKeys -or $CaseName -cne 'trigger-external-observed' -or
        -not $script:triggerOffConfirmed) { throw 'Unsafe EXTERNAL readiness confirmation.' }
    $script:calls += 'equipment-confirmation'
    if ($Scenario -ceq 'ready') { return '' }
    return 'cancel'
}
function Read-Host { throw 'EXTERNAL must use the bounded readiness confirmation.' }
function Start-Sleep([int]$Milliseconds) { $script:watch.ElapsedMilliseconds += $Milliseconds }
function Save-Report {}
function Get-TimerMonotonicNs { return [long]$script:watch.ElapsedMilliseconds * 1000000L }
function Invoke-TriggerStep([string]$Name, [int]$DeadlineMs, [int]$TerminationMs,
                            [long]$ObservationDeadlineNs, [switch]$Cleanup) {
    $script:calls += $Name
    switch ($Name) {
        'trigger-external-source-status' { return @{result=@{input_state='OFF';source='EXTERNAL'}} }
        'trigger-external-readback' {
            return @{result=@{state_source='instrument_readback';selected=$true;input_state='OFF';
                configuration=@{range='low';repeat_count=1;step_count=2;steps=@(
                    @{current_a=0.1;slew_a_per_us=0.1;width_s=2},
                    @{current_a=0.2;slew_a_per_us=0.1;width_s=2}
                )}}}
        }
        'trigger-external-on' { return @{result=@{input_state='ON'}} }
        'trigger-external-observed' {
            Write-Host 'External capture ready (synthetic evidence)'
            return @{result=@{validation_only=$true;validation_scope='trigger';trigger_source='EXTERNAL';
                waveform_completion_known=$true;observation=@{source='instrument_current_samples';
                    sample_count=10;events_confirmed=1;list_run_completed=$true}}}
        }
        default { return @{result=@{input_state='OFF'}} }
    }
}
try {
    Invoke-TriggerTrial @{prefix='trigger-external';source='EXTERNAL';isList=$true}
} catch {
    $entry = $script:report.results | Where-Object { $_.name -ceq 'trigger-external-observed' }
    if ($Scenario -cne 'cancel' -or $entry.error_code -cne 'operator_cancelled') { throw }
}
$entry = $script:report.results | Where-Object { $_.name -ceq 'trigger-external-observed' }
@{calls=$script:calls;status=$entry.status;error_code=$entry.error_code;
    off_confirmed=$script:triggerOffConfirmed} | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence['off_confirmed']
    expected = [f'trigger-external-{suffix}' for suffix in ('configure', 'source-status', 'setup', 'readback')]
    expected.append('equipment-confirmation')
    if ready:
        assert evidence['status'] == 'PASS'
        expected.extend(f'trigger-external-{suffix}' for suffix in ('on', 'observed', 'off'))
    else:
        assert evidence['status'] == 'FAIL' and evidence['error_code'] == 'operator_cancelled'
    expected.append('trigger-external-release')
    assert evidence['calls'] == expected
    assert 'capture-ready message' in result.stdout


@pytest.mark.parametrize('source', ['HOLD', 'BUS'])
@pytest.mark.parametrize('scenario', ['baseline', 'uncertain-write', 'waveform', 'identity', 'unknown'])
def test_software_trigger_failure_attribution(run_script, source, scenario):
    # Real trial and cleanup; only CLI exchanges and clock are fake.
    result = run_script([f'{source}:{scenario}'], harness="""
$source, $fault = $Scenario.Split(':')
$prefix = "trigger-$($source.ToLowerInvariant())"
$script:watch = @{ElapsedMilliseconds=0}
$script:totalDeadlineMs = 900000
$script:perCommandDeadlineMs = 30000
$TriggerMaxOnSeconds = 60
$TriggerObservationSeconds = 15
$TriggerListRepeat = 1
$LevelA = '0.1'; $LevelB = '0.2'
$RiseSlew = '0.1'; $FallSlew = '0.1'
$TriggerListWidthA = '2'; $TriggerListWidthB = '2'
$script:triggerTrusted = $true
$script:triggerProcessStillRunning = $false
$script:triggerArtifacts = @()
$script:report = @{results=@(foreach ($suffix in @('setup','dispatch','observed','off','release')) {
    [ordered]@{name="$prefix-$suffix";status='NOT RUN'}
})}
$script:calls = @()
function Start-Sleep([int]$Milliseconds) { $script:watch.ElapsedMilliseconds += $Milliseconds }
function Save-Report {}
function Get-TimerMonotonicNs { return [long]$script:watch.ElapsedMilliseconds * 1000000L }
function Invoke-TriggerStep([string]$Name, [int]$DeadlineMs, [int]$TerminationMs,
                            [long]$ObservationDeadlineNs, [switch]$Cleanup) {
    $script:calls += $Name
    switch ($Name) {
        "$prefix-source-status" { return @{result=@{input_state='OFF';source=$source}} }
        "$prefix-readback" {
            return @{result=@{state_source='instrument_readback';selected=$true;input_state='OFF';
                configuration=@{range='low';repeat_count=1;step_count=2;steps=@(
                    @{current_a=0.1;slew_a_per_us=0.1;width_s=2},
                    @{current_a=0.2;slew_a_per_us=0.1;width_s=2}
                )}}}
        }
        "$prefix-on" { return @{result=@{input_state='ON'}} }
        "$prefix-dispatch" {
            $dispatch = $script:report.results | Where-Object { $_.name -ceq $Name }
            if ($fault -cne 'unknown') {
                $dispatch['error_diagnostics'] = [ordered]@{
                    software_trigger_write_attempted=($fault -cnotin @('baseline','identity'))
                    software_trigger_write_confirmed=($fault -ceq 'waveform')
                }
            }
            $code = 'trigger_observation_unconfirmed'
            if ($fault -ceq 'uncertain-write') { $script:triggerTrusted = $false; $code = 'write_failed' }
            if ($fault -ceq 'identity') { $script:triggerTrusted = $false; $code = 'identity_mismatch' }
            if ($fault -ceq 'unknown') { $script:triggerTrusted = $false; $code = 'deadline_expired' }
            Fail-Case $Name $code 'Synthetic capture failure.'
        }
        default { return @{result=@{input_state='OFF'}} }
    }
}
try {
    Invoke-TriggerTrial @{prefix=$prefix;source=$source;isList=($source -ceq 'BUS')}
    throw 'Expected capture failure.'
} catch {
    if ($_.Exception.Message -cne 'Synthetic capture failure.') { throw }
}
@{cases=$script:report.results;calls=$script:calls;off_confirmed=$script:triggerOffConfirmed} | ConvertTo-Json -Depth 8 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    prefix = f'trigger-{source.lower()}'
    cases = {case['name']: case for case in evidence['cases']}
    dispatch, observed = cases[f'{prefix}-dispatch'], cases[f'{prefix}-observed']
    if scenario in {'waveform', 'baseline', 'identity'}:
        assert dispatch['status'] == ('PASS' if scenario == 'waveform' else 'NOT RUN')
        assert dispatch['trigger_dispatch_exchange'] == ('CONFIRMED' if scenario == 'waveform' else 'NOT ATTEMPTED')
        assert 'error_code' not in dispatch
        assert observed['status'] == 'FAIL'
        assert observed['error_code'] == ('identity_mismatch' if scenario == 'identity' else 'trigger_observation_unconfirmed')
        assert observed['error_diagnostics']['software_trigger_write_confirmed'] is (scenario == 'waveform')
    else:
        assert dispatch['status'] == 'FAIL'
        assert dispatch['trigger_dispatch_exchange'] == ('UNKNOWN' if scenario == 'unknown' else 'UNCONFIRMED')
        assert observed['status'] == 'NOT RUN' and observed['error_code'] == 'observation_blocked_by_dispatch'
    if scenario in {'uncertain-write', 'identity', 'unknown'}:
        assert not evidence['off_confirmed']
        assert f'{prefix}-off' not in evidence['calls'] and f'{prefix}-release' not in evidence['calls']
    else:
        assert evidence['off_confirmed']
        assert evidence['calls'][-2:] == [f'{prefix}-off', f'{prefix}-release']


@pytest.mark.parametrize('windows_powershell', [False, True] if os.name == 'nt' else [False])
def test_trigger_offline_preflight(run_script, windows_powershell):
    preflight = run_script(['-TriggerOnly'], preflight=True, windows_powershell=windows_powershell)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    assert preflight.stdout.rstrip().endswith('PASS: Trigger Setup preflight')


def test_readback_offline_preflight(run_script):
    preflight = run_script(['-ReadbackOnly'], preflight=True)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    assert preflight.stdout.rstrip().endswith('PASS: Readback preflight')


def test_configuration_readback_script_plan_and_noninteractive_refusal(run_script):
    expected = ['input-state', 'measure-off', 'readback',
                *(f'fixed-status-{mode}' for mode in ('cc', 'cv', 'cr', 'cw')),
                'transient-status', 'list-status', 'fixed-slew-status', 'von-status',
                'short-status', 'average-status']
    baseline = ['-ExpectedRiseSlew', '0.1', '-ExpectedFallSlew', '0.2',
                '-ExpectedVonThreshold', '0', '-ExpectedVonLatch', 'OFF',
                '-ExpectedShort', 'on', '-ExpectedAverageCount', '8']
    settings = {'fixed-slew-status': {'rise_slew_a_per_us': 0.1, 'fall_slew_a_per_us': 0.2},
                'von-status': {'threshold_v': 0, 'latch': 'off'}, 'short-status': {'short_state': 'ON'},
                'average-status': {'average_count': 8}}
    auto = run_script(['-Modes', 'readback', '-PlanOnly'])
    assert auto.returncode == 0, auto.stdout + auto.stderr
    auto_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', auto.stdout)[-1])
    auto_text = auto_path.read_text(encoding='utf-8-sig')
    auto_report = json.loads(auto_text)
    assert auto_report['overall_status'] == 'PLAN ONLY'
    assert auto_report['prechecks']['plan'] == 'PASS' and auto_report['modes'] == ['readback']
    assert RESOURCE not in auto_text and not (auto_path.parents[1] / 'private').exists()
    assert [case['name'] for case in auto_report['cases']] == expected
    assert all(case['command'] == 'contributor-settings' and '--confirm-isolated' in case['arguments']
               and '--readback-trial' in case['arguments'] for case in auto_report['cases'][3:])
    assert all(case['status'] == 'NOT RUN' for case in auto_report['cases'])
    assert not auto_report['hardware_touched']
    assert auto_report['per_command_deadline_ms'] == 30000
    assert all(case.get('deadline_ms', 30000) == 30000 for case in auto_report['cases'])
    for case in auto_report['cases'][3:]:
        assert {'INP ON', '*TRG'}.isdisjoint(case['planned_writes'])
        assert all({'INP ON', '*TRG'}.isdisjoint(branch['writes'])
                   for branch in case.get('conditional_writes', []))
    for options, status in [([], 'BLOCKED'), (['-PlanOnly', *baseline], 'PLAN ONLY'),
                            (['-ExpectedRiseSlew', '0.1'], 'BLOCKED')]:
        result = run_script(['-Modes', 'readback', *options])
        assert result.returncode == (0 if status == 'PLAN ONLY' else 2), result.stdout + result.stderr
        path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
        report = json.loads(path.read_text(encoding='utf-8-sig'))
        assert report['overall_status'] == status and report['hardware_touched'] is False
        assert report['prechecks']['plan'] == 'PASS'
        assert [case['command'] for case in report['cases'][:1]] == ['input-state']
        assert [case['name'] for case in report['cases']] == expected
        if not options:
            assert report['validation_mode'] == 'live'
            assert report['cases'] == auto_report['cases']
            assert 'requires an interactive console' in result.stdout
            continue
        assert [case['command'] for case in report['cases'][3:7]] == ['fixed-status'] * 4
        assert all(case['arguments'] == ['--hardware-validation', 'readback']
                   for case in report['cases'][2:])
        assert all(case['status'] == 'NOT RUN' for case in report['cases'])
        for case in report['cases'][-4:]:
            assert case['readback_comparison'] == 'NOT RUN'
            if options == ['-PlanOnly', *baseline]:
                assert case['expected_readback'] == settings[case['name']]
            else:
                assert 'expected_readback' not in case and case['condition']
        if status == 'BLOCKED':
            assert 'requires an interactive console' in result.stdout

    mixed = run_script(['-Modes', 'readback,auxiliary-controls', '-ExpectedRiseSlew', '0.1'])
    assert mixed.returncode == 2 and 'requires an interactive console' in mixed.stdout
    mixed_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', mixed.stdout)[-1])
    mixed_report = json.loads(mixed_path.read_text(encoding='utf-8-sig'))
    assert mixed_report['overall_status'] == 'BLOCKED' and not mixed_report['hardware_touched']
    assert mixed_report['prechecks']['plan'] == 'PASS'
    assert mixed_report['prechecks']['preflight'] == 'NOT RUN'
    mixed_cases = {case['name']: case for case in mixed_report['cases']}
    assert list(mixed_cases) == [*expected, 'auxiliary-controls-von', 'auxiliary-controls-short',
                                 'auxiliary-controls-slew']
    assert all(case['status'] == 'NOT RUN' for case in mixed_cases.values())
    assert mixed_cases['fixed-slew-status']['command'] == 'fixed-slew-status'
    for name in ('auxiliary-controls-von', 'auxiliary-controls-short', 'auxiliary-controls-slew'):
        case = mixed_cases[name]
        assert case['command'] == 'contributor-settings' and case['execution'] == 'live'
        assert 'reuse_from' not in case and '--readback-trial' not in case['arguments']


def test_sequence_plan_or_noninteractive_refusal(run_script):
    result = run_script(["-Modes", "sequence"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "requires an interactive console" in result.stdout
    paths = re.findall(r"Report: (.+?/shareable/report\.json)", result.stdout)
    assert paths, result.stdout + result.stderr
    path = Path(paths[-1])
    report_text = path.read_text(encoding="utf-8-sig")
    report = json.loads(report_text)
    assert report["overall_status"] == "BLOCKED" and report["prechecks"]["plan"] == "PASS"
    assert not report["hardware_touched"]
    assert RESOURCE not in report_text and not (path.parents[1] / "private").exists()
    assert all(case["status"] == "NOT RUN" for case in report["cases"])
    cases = report["cases"][2:]
    assert [case["name"] for case in cases] == ["sequence-cc", "sequence-cv", "sequence-cr", "sequence-cw"]
    assert report["per_command_deadline_ms"] == 30000 and report["total_deadline_ms"] == 900000
    for case in cases:
        assert "<redacted>" in case["arguments"] and "--confirm" in case["arguments"]
        actions = [step["action"] for step in case["sequence_plan"]]
        assert actions == [
            case["name"].replace("sequence-", "set-"), "input-on", "input-state",
            "wait", "measure", "input-off", "input-state",
        ]
        assert case["sequence_plan"][3]["arguments"] == {"seconds": "0.25"}


def test_sequence_focused_preflight(run_script):
    checked = run_script(["-SequenceOnly"], preflight=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert checked.stdout.rstrip().endswith('PASS: Software Sequence offline preflight')


@pytest.mark.parametrize('plan_only,serial', [
    (True, ['-SerialBaudRate', '19200', '-SerialDataBits', '7', '-SerialParity', 'even',
            '-SerialStopBits', '2', '-SerialFlowControl', 'none', '-SerialReadTermination', 'CRLF',
            '-SerialWriteTermination', 'LF']),
    (False, ['-SerialWriteTermination', 'CR']),
])
def test_asrl_mixed_mode_plan_and_noninteractive_refusal(run_script, plan_only, serial):
    result = run_script(['-Connection', 'asrl', '-Modes', 'cc,system-query,readback,sequence',
                         *serial, *(['-PlanOnly'] if plan_only else [])], resource=ASRL)
    assert result.returncode == (0 if plan_only else 2), result.stdout + result.stderr
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report_text = path.read_text(encoding='utf-8-sig')
    report = json.loads(report_text)
    assert report['overall_status'] == ('PLAN ONLY' if plan_only else 'BLOCKED')
    assert report['connection'] == 'asrl' and report['modes'] == [
        'cc', 'system-query', 'readback', 'sequence',
    ]
    assert report['serial_overrides'] == (
        {'baud_rate': '19200', 'data_bits': '7', 'parity': 'even', 'stop_bits': '2',
         'flow_control': 'none', 'read_termination': 'CRLF', 'write_termination': 'LF'}
        if plan_only else {'write_termination': 'CR'}
    )
    assert report['prechecks']['plan'] == 'PASS' and not report['hardware_touched']
    cases = {case['name']: case for case in report['cases']}
    assert {'set-cc-high', 'fixed-status-cv', 'sequence-cv', 'scpi-error-next'} <= cases.keys()
    assert all(case['status'] == 'NOT RUN' for case in cases.values())
    assert report['summary_counts']['total'] == report['summary_counts']['not_run'] == len(cases)
    assert report['resource'] == '<redacted>'
    summary = path.with_name('summary.md').read_text(encoding='utf-8-sig')
    assert ASRL not in report_text + summary + result.stdout and str(ROOT) not in report_text
    assert not (path.parents[1] / 'private').exists()
    if not plan_only:
        assert 'requires an interactive console' in result.stdout


def test_system_query_offline_preflight(run_script):
    preflight = run_script(['-SystemQueryOnly'], preflight=True)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    assert preflight.stdout.rstrip().endswith('PASS: System Query and ASRL offline preflight')


def test_system_query_focused_plan(run_script):
    result = run_script(['-Modes', 'system-query', '-PlanOnly'])
    assert result.returncode == 0, result.stdout + result.stderr
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    cases = report['cases'][2:]
    assert [case['command'] for case in cases] == ['scpi-version', 'display-mode', 'scpi-error-next']
    assert all(case['arguments'] == ['--hardware-validation', 'system-query'] for case in cases)


@pytest.mark.parametrize('mode,connection,resource,plan_only', [
    ('workflow', 'usb', RESOURCE, True),
    ('workflow', 'asrl', ASRL, False),
    ('measurement-extensions', 'usb', RESOURCE, True),
    ('auxiliary-controls', 'usb', RESOURCE, True),
    ('list-cc', 'usb', RESOURCE, False),
    ('list-cc,protection', 'usb', RESOURCE, True),
    ('list-cc,input-timer', 'usb', RESOURCE, True),
    ('sequence,auxiliary-controls,list-cc', 'usb', RESOURCE, True),
    ('cv,list-cc', 'asrl', ASRL, True),
    ('cr,auxiliary-controls', 'usb', RESOURCE, True),
    ('transient-cw,readback,list-cc', 'usb', RESOURCE, True),
    ('list-cc,auxiliary-controls', 'asrl', ASRL, True),
    ('workflow,auxiliary-controls,list-cc,measurement-extensions', 'asrl', ASRL, False),
    ('full', 'usb', RESOURCE, True),
])
def test_selected_modes_never_use_simulation(run_script, mode, connection, resource, plan_only):
    flags = ['-Modes', mode, '-Connection', connection]
    slot = 2 if mode == 'list-cc,auxiliary-controls' else 1
    if slot != 1:
        flags += ['-ListScratchSlot', str(slot)]
    result = run_script([*flags, *(['-PlanOnly'] if plan_only else [])], resource=resource)
    assert result.returncode == (0 if plan_only else 2), result.stdout + result.stderr
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report_text = path.read_text(encoding='utf-8-sig')
    report = json.loads(report_text)
    summary = path.with_name('summary.md').read_text(encoding='utf-8-sig')
    assert resource not in report_text + summary + result.stdout and str(ROOT) not in report_text
    assert not (path.parents[1] / 'private').exists()
    assert report['overall_status'] == ('PLAN ONLY' if plan_only else 'BLOCKED')
    assert report['validation_mode'] == ('plan-only' if plan_only else 'live')
    assert not report['hardware_touched'] and report['resource'] == '<redacted>'
    assert report['prechecks']['plan'] == 'PASS'
    assert report['prechecks']['preflight'] == 'NOT RUN'
    assert report['connection'] == connection
    cases = {case['name']: case for case in report['cases']}
    selected = FULL_MODES if mode == 'full' else [name for name in FULL_MODES if name in mode.split(',')]
    assert report['modes'] == selected
    assert len(cases) == len(report['cases'])
    assert report['summary_counts'] == {
        'total': len(cases), 'passed': 0, 'failed': 0, 'skipped': 0, 'not_run': len(cases),
    }
    expected_extensions = {
        'workflow': ('workflow-measure-log', 'workflow-measure-until',
                     'workflow-cc-timed-load', 'workflow-cc-discharge'),
        'measurement-extensions': ('measurement-extensions-min-max', 'measurement-extensions-discharge',
                                   'measurement-extensions-average'),
        'auxiliary-controls': ('auxiliary-controls-von', 'auxiliary-controls-short', 'auxiliary-controls-slew'),
        'list-cc': ('list-cc-settings',),
        'readback': ('fixed-status-cc', 'fixed-status-cv', 'fixed-status-cr', 'fixed-status-cw',
                     'transient-status', 'list-status', 'fixed-slew-status', 'von-status',
                     'short-status', 'average-status'),
    }
    for feature, names in expected_extensions.items():
        if feature in selected:
            assert all(cases[name]['execution'] == 'live' for name in names)
        else:
            assert set(names).isdisjoint(cases)
    assert ('readback' in cases) == ('readback' in selected)
    for feature in ('readback', 'auxiliary-controls', 'list-cc'):
        if feature in selected:
            for name in expected_extensions[feature]:
                case = cases[name]
                assert case['command'] == 'contributor-settings'
                assert {'INP ON', '*TRG'}.isdisjoint(case['planned_writes'])
                assert all({'INP ON', '*TRG'}.isdisjoint(branch['writes'])
                           for branch in case.get('conditional_writes', []))
    if not plan_only:
        assert 'requires an interactive console' in result.stdout
    assert all(case['status'] == 'NOT RUN' for case in cases.values())
    assert all(case.get('execution') in (None, 'live') for case in cases.values())
    assert all(case['command'] != 'pytest' for case in cases.values())
    if 'measurement-extensions' in selected:
        assert 'MEAS:VOLT:MIN?' in cases['measurement-extensions-min-max']['queries']
        assert 'SENS:AVER:COUN 8' in cases['measurement-extensions-average']['planned_writes']
    if 'list-cc' in selected:
        trial = cases['list-cc-settings']
        assert trial['deadline_ms'] == 300000
        assert all(not any(write.startswith('FUNC <original>') for write in branch['writes'])
                   for branch in trial['conditional_writes'])
        assert 'LIST:WID? {step}' in trial['queries']
        assert trial['planned_writes'].count(f'LIST:SAV {slot}') == 2
        assert trial['planned_writes'].count(f'LIST:RCL {slot}') == 2
        assert 'CURR:SLEW:STAT?' in trial['queries']
        assert ['LIST:WID 1,<original>'] in [branch['writes'] for branch in trial['conditional_writes']]
        restoration = next(branch['writes'] for branch in trial['conditional_writes']
                           if branch['writes'][0].startswith('LIST:RANG '))
        assert {write.split()[0] for write in restoration} == {
            'LIST:RANG', 'LIST:COUN', 'LIST:STEP', 'LIST:LEV', 'LIST:SLEW', 'LIST:WID',
        }
        assert 'error_code' not in trial
        if mode == 'full':
            assert list(cases).index('trigger-restore') < list(cases).index('list-cc-settings')
            assert list(cases).index('workflow-cc-discharge') < list(cases).index('protection-start-state')
            assert list(cases).index('trigger-restore') < list(cases).index('average-status')
    if 'auxiliary-controls' in selected:
        if mode == 'full':
            for name, prior in (('von', 'von-status'), ('short', 'short-status'), ('slew', 'fixed-slew-status')):
                alias = cases[f'auxiliary-controls-{name}']
                assert alias['reuse_from'] == prior
                assert alias['planned_writes'] == alias['queries'] == alias['conditional_writes'] == []
        else:
            assert {'VOLT:ON?', 'VOLT:LATC?'} <= set(cases['auxiliary-controls-von']['queries'])
            short = cases['auxiliary-controls-short']
            assert short['planned_writes'] == ['SYST:REM', 'INP:SHOR 1', 'INP:SHOR 0']
            assert short['conditional_writes'][0]['writes'] == ['SYST:REM', 'INP OFF']
            assert not any(write.startswith('CURR:RANG ') for write in cases['auxiliary-controls-slew']['planned_writes'])
    needs_handoff = mode in ('sequence,auxiliary-controls,list-cc', 'cv,list-cc',
                             'cr,auxiliary-controls', 'transient-cw,readback,list-cc')
    assert ('isolated-fixed-cc-handoff' in cases) == needs_handoff
    if needs_handoff:
        handoff = cases['isolated-fixed-cc-handoff']
        names = list(cases)
        assert handoff['command'] == 'contributor-settings'
        expected_origin = ('CW' if 'sequence' in mode or 'transient-cw' in mode else
                           'CV' if mode.startswith('cv,') else 'CR')
        assert handoff['arguments'][handoff['arguments'].index('--origin-function') + 1] == expected_origin
        assert handoff['conditional_writes'][0]['writes'] == ['SYST:REM', 'FUNC CURR']
        assert handoff['planned_writes'] == []
        first = 'auxiliary-controls-von' if 'auxiliary-controls' in mode else 'list-cc-settings'
        assert names.index('isolated-fixed-cc-handoff') + 1 == names.index(first)
        if 'sequence' in mode:
            assert names.index('sequence-cw') < names.index('isolated-fixed-cc-handoff')
        if 'readback' in mode:
            assert names.index('average-status') < names.index('isolated-fixed-cc-handoff')
    if 'workflow' in selected:
        assert cases['workflow-measure-log']['queries'][:2] == ['*IDN?', 'INP:STAT?']
        assert cases['workflow-measure-until']['queries'] == ['*IDN?', 'INP:STAT?', 'MEAS:VOLT?']
        for name in ('workflow-cc-timed-load', 'workflow-cc-discharge'):
            assert {'INP ON', 'INP OFF'} <= set(cases[name]['planned_writes'])
            assert {'*IDN?', 'INP:STAT?', 'FUNC:MODE?', 'FUNC?', 'CURR:RANG?', 'CURR?', 'TRAN?',
                    'INP:SHOR:STAT?', 'INP:TIM?'} <= set(cases[name]['queries'])
            assert 'error_code' not in cases[name]


@pytest.mark.parametrize('mode,plan_only', [
    ('workflow', False), ('workflow', True), ('readback', False),
    ('measurement-extensions', False), ('auxiliary-controls', False), ('list-cc', False),
])
def test_hardware_modes_require_resource(run_script, mode, plan_only):
    result = run_script(['-Modes', mode, *(['-PlanOnly'] if plan_only else [])], resource=None)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'explicit -Target and -Resource are required' in result.stdout
    assert 'Report:' not in result.stdout


def test_new_modes_deduplicate_and_leave_physical_cases_not_run(run_script):
    result = run_script(['-Modes', 'list-cc,readback,measurement-extensions,list-cc'])
    assert result.returncode == 2 and 'requires an interactive console' in result.stdout
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    assert report['overall_status'] == 'BLOCKED' and report['hardware_touched'] is False
    assert report['modes'] == ['readback', 'measurement-extensions', 'list-cc']
    assert len({case['name'] for case in report['cases']}) == len(report['cases'])
    assert report['validation_mode'] == 'live'
    assert all(case.get('execution') in (None, 'live') for case in report['cases'])
    assert all(case['status'] == 'NOT RUN' and case['command'] != 'pytest' for case in report['cases'])


@pytest.mark.parametrize('extra,resource', [
    (['-Connection', 'asrl', '-Modes', 'cc'], None),
    (['-Connection', 'asrl', '-Modes', 'readback'], RESOURCE),
    (['-Modes', 'cc'], ASRL),
    (['-Modes', 'asrl-readonly'], RESOURCE),
    (['-Modes', 'cc', '-SerialBaudRate', '19200'], RESOURCE),
    (['-Connection', 'asrl', '-Modes', 'system-query', '-SerialStopBits', '3'], ASRL),
])
def test_asrl_script_rejects_unsafe_or_invalid_selection(run_script, extra, resource):
    result = run_script(['-PlanOnly', *extra], resource=resource)
    assert result.returncode == 2 and 'Report:' not in result.stdout, result.stdout + result.stderr
