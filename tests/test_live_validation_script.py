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


@pytest.fixture
def run_script(tmp_path):
    powershell = shutil.which('pwsh') or shutil.which('powershell')
    if powershell is None:
        local = ROOT / '.tmp_tests/powershell/pwsh'
        if not local.is_file():
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
            harness=None, extra_env=None):
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
                "'Assert-ReadbackFields','Fail-Case','Invoke-TimerExpiry','Invoke-LiveCommand',"
                "'Get-TimerMonotonicNs','Get-TimerRemainingMs','Get-TimerObservationDeadlineNs',"
                "'Confirm-TimerExpiry','Assert-Runtime','Save-Report',"
                "'New-CliProcessInfo','New-LiveCliProcess')) {\n"
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
            [powershell, '-NoProfile', '-NonInteractive', *invocation], cwd=ROOT,
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


def test_fixed_auto_offline_preflight(run_script):
    result = run_script(['-FixedAutoOnly'], preflight=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_protection_preflight_and_explicit_mode_report_without_visa(run_script):
    preflight = run_script(['-ProtectionOnly'], preflight=True)
    assert preflight.returncode == 0 and 'PASS: Protection preflight' in preflight.stdout
    for args, expected in [(['-PlanOnly'], 'PLAN ONLY'), ([], 'BLOCKED')]:
        result = run_script(['-Modes', 'protection', *args])
        assert result.returncode == (0 if args else 2), result.stdout + result.stderr
        paths = re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)
        assert len(paths) == 1, result.stdout + result.stderr
        path = Path(paths[0])
        text = path.read_text(encoding='utf-8-sig')
        report = json.loads(text)
        assert report['overall_status'] == expected and report['prechecks']['plan'] == 'PASS'
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
            assert cases[f'{prefix}-trial']['arguments'] == [
                '--hardware-validation', 'protection', '--confirm', '--protection-trial', trial,
            ]
            assert cases[f'{prefix}-off']['command'] == 'input-off'
            assert '--events' in cases[f'{prefix}-events']['arguments']
            assert cases[f'{prefix}-final']['command'] == 'protection-status'
            if trial.startswith('software-'):
                assert cases[f'{prefix}-source-off']['command'] == 'operator-observation'
                assert list(cases).index(f'{prefix}-source-off') < list(cases).index(f'{prefix}-clear')
            else:
                assert f'{prefix}-clear' not in cases
                assert cases[f'{prefix}-baseline-on']['command'] == 'operator-observation'
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
    opt_in = ['-LiveExtensions'] if mode == 'workflow' else []
    result = run_script(['-Modes', mode, '-PlanOnly', *opt_in, *extra])
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
    assert 'PASS: CR-LED and Current Slow Rise preflight' in result.stdout


@pytest.mark.parametrize('modes', [('cr-led',), ('current-slow-rise',), ('cr-led', 'current-slow-rise')])
def test_cr_led_slow_rise_script_plans_and_refuses_unattended(run_script, modes):
    selection = ['-Modes', ','.join(modes)]
    for args, expected in [(selection + ['-PlanOnly'], 'PLAN ONLY'), (selection, 'BLOCKED')]:
        result = run_script(args)
        report = assert_report(result, expected, modes)
        assert all(case['command'] != 'input-on' for case in report['cases'])
    assert 'requires an interactive console' in result.stdout


def test_fixed_slew_von_short_offline_preflight(run_script):
    result = run_script(['-FixedSlewVonShortOnly'], preflight=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS: Fixed CC Slew, VON and Short preflight' in result.stdout


def test_fixed_auto_script_plan_uses_core_resolution_and_original_values(run_script):
    resistance = '10.000000000000000001'
    result = run_script(['-PlanOnly', '-Modes', 'cv,cr', '-Voltage', '12.0000',
                         '-ResistanceHigh', resistance])
    report = assert_report(result, 'PLAN ONLY', ('cv', 'cr'))
    setup = next(case for case in report['cases'] if case['name'] == 'cr-auto-high-setup')
    assert setup['arguments'][setup['arguments'].index('--resistance') + 1] == resistance


@pytest.mark.parametrize('current', ['0', '3', '0.1000000000000000001'])
def test_fixed_auto_script_current_bounds_and_no_rounding(run_script, current):
    result = run_script(['-PlanOnly', '-Modes', 'cc', '-Current', current], source_limits=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Report:' not in result.stdout


@pytest.mark.parametrize('selection,modes', [
    ('cc', ('cc',)), ('cv', ('cv',)), ('cr', ('cr',)), ('cw', ('cw',)),
    ('transient-cc', ('transient-cc',)),
    ('transient-cv', ('transient-cv',)), ('transient-cr', ('transient-cr',)),
    ('transient-cw', ('transient-cw',)),
    ('cc,transient-cc', ('cc', 'transient-cc')),
    ('CV,cc,cv', ('cc', 'cv')),
])
def test_hardware_script_mode_plans(run_script, selection, modes):
    extra = ['-PlanOnly'] + (['-Modes', selection] if selection is not None else [])
    assert_report(run_script(extra), 'PLAN ONLY', modes)


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
        for case in report['cases']:
            arguments = case.get('arguments', [])
            for index, argument in enumerate(arguments):
                if argument in ('--validation-settings-json', '--arguments-json'):
                    arguments[index + 1] = json.loads(arguments[index + 1])
        reports.append(report)

    expected = ['cc', 'cv', 'cr', 'cw', 'transient-cc', 'transient-cv', 'transient-cr',
                'transient-cw', 'trace', 'cr-led', 'current-slow-rise', 'trigger', 'input-timer',
                'protection', 'system-query', 'readback', 'sequence', 'workflow',
                'measurement-extensions', 'auxiliary-controls', 'list-cc']
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
    assert names.index('average-status') < names.index('protection-start-state')
    assert names.index('protection-prepare-cc-readback') < names.index('protection-software-current-original')
    assert names.index('protection-default-hardware-power-final') < names.index('sequence-cc')
    assert names.index('sequence-cw') < names.index('set-cc-high')
    for mode in expected[-3:]:
        assert names.count(f'{mode}-simulation') == 1
        assert names.index(f'{mode}-simulation') < names.index('input-state')


def test_plan_only_console_summary_and_verbose_steps(run_script):
    normal = run_script(['-PlanOnly', '-Modes', 'cc,transient-cc'])
    assert normal.returncode == 0, normal.stdout + normal.stderr
    assert 'Selected modes:' in normal.stdout
    assert 'transient-continuous-auto-setup:' not in normal.stdout
    assert '--validation-settings-json' not in normal.stdout
    assert normal.stdout.count('Report: ') == 1

    verbose = run_script(['-PlanOnly', '-Verbose', '-Modes', 'transient-cv'])
    assert verbose.returncode == 0, verbose.stdout + verbose.stderr
    assert 'transient-cv-continuous-setup:' in verbose.stdout
    assert '--validation-settings-json' in verbose.stdout
    assert verbose.stdout.count('Report: ') == 1


def test_hardware_script_powershell_mode_array(run_script):
    result = run_script(['-PlanOnly'], mode_array='cc,transient-cc')
    assert_report(result, 'PLAN ONLY', ('cc', 'transient-cc'))


@pytest.mark.parametrize('selection', ['unknown', 'cc,unknown', '', 'full,cc'])
def test_hardware_script_invalid_modes(run_script, selection):
    result = run_script(['-PlanOnly', '-Modes', selection])
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Select full or one or more of:' in result.stdout
    assert 'Report:' not in result.stdout  # Rejected before planning any cases.


@pytest.mark.parametrize('selection', ['cc', 'transient-cc'])
def test_hardware_script_unattended_refusal(run_script, selection):
    result = run_script(['-Modes', selection])
    assert_report(result, 'BLOCKED', (selection,))
    assert 'requires an interactive console' in result.stdout


def test_hardware_script_selected_parameter_checks(run_script):
    # Unselected Transient settings do not affect CC-only planning.
    assert_report(run_script(['-PlanOnly', '-Modes', 'cc', '-LevelA', '3'], source_limits=False),
                  'PLAN ONLY', ('cc',))
    # A CV-only plan does not validate CC/Transient-only settings.
    assert_report(run_script(['-PlanOnly', '-Modes', 'cv', '-Current', '3', '-LevelA', '3']),
                  'PLAN ONLY', ('cv',))
    report = assert_report(run_script(['-PlanOnly', '-Modes', 'cv'], source_limits=False),
                           'PLAN ONLY', ('cv',))
    assert report['declared_source_limits']['current_a'] == 0.8
    assert report['declared_source_limits']['voltage_v'] == 5


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


def test_hardware_script_source_override_and_cv_defaults(run_script):
    override = run_script([
        '-PlanOnly', '-Modes', 'cc,transient-cc', '-Current', '1',
        '-LevelA', '0.9', '-LevelB', '1',
        '-SourceCurrentLimitA', '1.2', '-SourceVoltageLimitV', '12',
    ])
    report = assert_report(override, 'PLAN ONLY', ('cc', 'transient-cc'))
    assert report['declared_source_limits']['current_a'] == 1.2
    assert report['declared_source_limits']['voltage_v'] == 12

    cv = assert_report(run_script(['-PlanOnly', '-Modes', 'cv,transient-cv']),
                       'PLAN ONLY', ('cv', 'transient-cv'))
    cases = {case['name']: case for case in cv['cases']}
    for name in ('cv-low-setup', 'cv-high-setup'):
        args = cases[name]['arguments']
        assert args[args.index('--voltage') + 1] == '3'
    for name in ('transient-cv-continuous-setup', 'transient-cv-pulse-setup',
                 'transient-cv-toggle-setup'):
        args = cases[name]['arguments']
        assert args[args.index('--level-a') + 1] == '3'
        assert args[args.index('--level-b') + 1] == '4'


def test_transient_offline_preflight(run_script):
    result = run_script(['-TransientOnly'], preflight=True)
    assert result.returncode == 0, result.stdout + result.stderr
    for mode in ('cv', 'cr', 'cw'):
        assert f'PASS: set-transient-{mode}' in result.stdout


def test_new_transient_script_precision_and_core_auto_selection(run_script):
    token = '0.000020000000000000000001'
    result = run_script(['-PlanOnly', '-Modes', 'transient-cv,transient-cr,transient-cw',
                         '-WidthA', token, '-PulseWidth', token])
    report = assert_report(result, 'PLAN ONLY', ('transient-cv', 'transient-cr', 'transient-cw'))
    cases = {case['name']: case for case in report['cases']}
    for scope, resolved in (('cv', 'low'), ('cr', 'high')):
        case = cases[f'transient-{scope}-continuous-setup']
        assert case['expected_range'] == resolved
        assert case['arguments'][case['arguments'].index('--width-a') + 1] == token
    for case in cases.values():
        assert '--rise-slew' not in case['arguments'] and '--fall-slew' not in case['arguments']
        if case['command'] == 'set-transient-cw':
            assert '--range' not in case['arguments']


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
    assert 'PASS: Input Timer preflight' in result.stdout


@pytest.mark.parametrize('planned', [True, False])
def test_input_timer_script_plan_and_live_refusal(run_script, planned):
    result = run_script(['-InputTimerOnly', *(['-PlanOnly'] if planned else [])])
    assert result.returncode == (0 if planned else 2), result.stdout + result.stderr
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)
    assert paths
    text = Path(paths[-1]).read_text(encoding='utf-8-sig')
    report = json.loads(text)
    assert report['overall_status'] == ('PLAN ONLY' if planned else 'BLOCKED')
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
    result = run_script(['-PlanOnly', '-Modes', 'input-timer'])
    assert result.returncode == 0, result.stdout + result.stderr
    report_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    names = [case['name'] for case in report['cases']]
    assert 'timer-on' in names and 'timer-disable' in names
    assert 'trigger-snapshot' not in names and 'set-cc-high' not in names
    assert report['hardware_touched'] is False
    assert all(case['status'] == 'NOT RUN' for case in report['cases'])
    cases = {case['name']: case for case in report['cases']}
    assert '10 s cutoff' in cases['timer-on']['condition']
    assert 'SCPI ON write start' in cases['timer-expiry']['condition']

    preview = run_script(['-PlanOnly', '-Modes', 'input-timer', '-TimerSeconds', '3'])
    assert preview.returncode == 0, preview.stdout + preview.stderr
    refused = run_script(['-Modes', 'input-timer', '-TimerSeconds', '3'])
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert 'cutoff applies only to the 5 s Timer trial' in refused.stdout


@pytest.mark.parametrize('scenario,checks,status,code', [
    ('off-after-late-write', 3, 'PASS', None),
    ('external-off', 3, 'FAIL', 'timer_automatic_off_unconfirmed'),
    ('stays-on', 4, 'FAIL', 'timer_expiry_unconfirmed'),
    ('invalid', 1, 'FAIL', 'readback_mismatch'),
    ('late-off', 1, 'FAIL', 'timer_expiry_unconfirmed'),
    ('late-on-confirmation', 0, 'FAIL', 'timer_expiry_unconfirmed'),
    ('early-off', 1, 'FAIL', 'timer_expiry_early'),
    ('unconfirmed-timing', 1, 'FAIL', 'timer_timing_unconfirmed'),
])
def test_timer_expiry_requires_automatic_off_within_scpi_window(run_script, scenario, checks, status, code):
    result = run_script(['-Scenario', scenario], harness="""
$script:watch = @{ElapsedMilliseconds=$(if ($Scenario -eq 'late-on-confirmation') { 9801 } else { 4000 })}
$script:timerOnStartMs = 0
$script:timerOnConfirmedMs = $script:watch.ElapsedMilliseconds
$script:timerOnTiming = @{on_write_started_ns=4800000000L;on_confirmed_ns=$(
    if ($Scenario -eq 'late-on-confirmation') { 10801000000L } else { 5000000000L }
)}
$script:checks = 0
$script:perCommandDeadlineMs = 30000
$TimerSeconds = '5'
$script:report = @{results=@([ordered]@{name='timer-expiry';status='NOT RUN'})}
function Get-TimerMonotonicNs { 1000000000L + $script:watch.ElapsedMilliseconds * 1000000L }
function Start-Sleep([int]$Milliseconds) { $script:watch.ElapsedMilliseconds += $Milliseconds }
function Assert-Runtime($Result, [string]$Name) {}
function Read-ValidationAnswer {
    if ($Scenario -eq 'external-off') { 'EXTERNAL' } else { 'AUTO' }
}
function Invoke-LiveCommand([string]$Command, [string]$CaseName, [string[]]$ExtraArgs,
                            [int]$DeadlineMs, [long]$TimerObservationDeadlineNs) {
    if ($TimerObservationDeadlineNs -ne 10800000000L) { throw 'Unexpected observation deadline.' }
    $script:checks++
    $queryStarted = Get-TimerMonotonicNs
    $script:watch.ElapsedMilliseconds += $(if ($Scenario -eq 'late-off') { 2001 } else { 350 })
    $script:report.results[0]['observed_at_ms'] = $script:watch.ElapsedMilliseconds
    $script:report.results[0]['observed_at_ns'] = Get-TimerMonotonicNs
    $off = $Scenario -in @('late-off','early-off') -or
        ($Scenario -in @('off-after-late-write','external-off') -and $script:checks -eq 3)
    $timing = if ($Scenario -ne 'unconfirmed-timing') {
        @{query_started_ns=$queryStarted;query_completed_ns=($queryStarted + 10000000L)}
    }
    return @{result=@{seconds=5;enabled=(-not $off);timer_timing=$timing;input_state=$(
        if ($off) { 'OFF' } elseif ($Scenario -eq 'invalid') { 'UNKNOWN' } else { 'ON' }
    )}}
}
try {
    Invoke-TimerExpiry @{name='timer-expiry';command='input-timer-status';args=@()}
    if ($script:report.results[0].status -ne 'NOT RUN') { throw 'Expiry was marked before operator confirmation.' }
    Confirm-TimerExpiry
}
catch { if ($script:report.results[0].status -ne 'FAIL') { throw } }
$entry = $script:report.results[0]
@{checks=$script:checks;status=$entry.status;error_code=$entry.error_code} | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        'checks': checks, 'status': status, 'error_code': code,
    }


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


def test_contributor_workflow_script_accepts_confirmed_off_within_write_start_deadline(run_script, tmp_path):
    with (tmp_path / 'sitecustomize.py').open('a', encoding='utf-8') as preload:
        preload.write("""
import sys
from contextlib import contextmanager

sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_hardware_validation import ValidationFake
from electronicload_tool_core import workflows

@contextmanager
def fake_session(_resource):
    fake = ValidationFake()
    fake.range = 6
    fake.query_delay = 0.15
    fake.values.update({'INP:TIM': '0', 'MEAS:CURR': '0.1'})
    yield fake, {'library': 'fake', 'manufacturer': 'fake'}

workflows.open_session = fake_session
""")
    result = run_script([], extra_env={'FAKE_WORKFLOW_PYTHON': sys.executable, 'FAKE_WORKFLOW_RESOURCE': RESOURCE},
                        harness="""
$python = $env:FAKE_WORKFLOW_PYTHON
$repo = (Get-Location).Path
$script:connectionArgs = @('--resource', $env:FAKE_WORKFLOW_RESOURCE)
$script:report = @{status='NOT RUN';results=@([ordered]@{name='workflow-cc-timed-load';status='NOT RUN'})}
$script:perCommandDeadlineMs = 30000
$script:totalDeadlineMs = 900000
$watch = [Diagnostics.Stopwatch]::StartNew()
$response = Invoke-LiveCommand -Command 'cc-timed-load' -CaseName 'workflow-cc-timed-load' -ExtraArgs @(
    '--hardware-validation', 'workflow', '--current', '0.1', '--range', 'low', '--duration-s', '5', '--confirm')
@{result=$response.result;status=$script:report.results[0].status} | ConvertTo-Json -Depth 6 -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence['status'] == 'NOT RUN'  # The live script still requires the operator's observation.
    workflow = evidence['result']
    assert workflow['stop_reason'] == 'duration_elapsed' and workflow['input_state'] == 'OFF'
    assert workflow['cleanup']['off_confirmed']
    assert 5 <= (workflow['off_confirmed_ns'] - workflow['on_write_started_ns']) / 1e9 <= 10


def test_input_timer_script_uses_default_source_limits(run_script):
    result = run_script(['-InputTimerOnly', '-PlanOnly'], source_limits=False)
    assert result.returncode == 0, result.stdout + result.stderr
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)
    report = json.loads(Path(paths[-1]).read_text(encoding='utf-8-sig'))
    assert report['declared_source_limits']['current_a'] == 0.8
    assert report['declared_source_limits']['voltage_v'] == 5


def test_trace_validation_preflight_only(run_script):
    checked = run_script(['-TraceOnly'], preflight=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert 'PASS: TRACE-only offline preflight' in checked.stdout


def test_trace_validation_script_plan_only_and_noninteractive_refusal(run_script):
    planned = run_script(['-PlanOnly', '-Modes', 'trace'])
    assert planned.returncode == 0, planned.stdout + planned.stderr
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', planned.stdout)
    assert paths, planned.stdout
    report = json.loads(Path(paths[-1]).read_text(encoding='utf-8-sig'))
    assert report['overall_status'] == 'PLAN ONLY'
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

    refused = run_script(['-Modes', 'trace'])
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert 'requires an interactive console' in refused.stdout


def test_trigger_validation_script_plan_preflight_and_noninteractive_refusal(run_script):
    plan = run_script(['-Modes', 'trigger', '-PlanOnly'])
    assert plan.returncode == 0, plan.stdout + plan.stderr
    report_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', plan.stdout)[-1])
    text = report_path.read_text(encoding='utf-8-sig')
    report = json.loads(text)
    assert report['overall_status'] == 'PLAN ONLY' and report['hardware_touched'] is False
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
        assert cases[f'trigger-{source}-observed']['command'] == 'operator-observation'
        assert (f'trigger-{source}-dispatch' in cases) == (source in ('hold', 'bus'))
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
    refused = run_script(['-Modes', 'trigger'])
    assert refused.returncode == 2 and 'requires an interactive console' in refused.stdout
    mixed = run_script(['-Modes', 'transient-cc,trigger', '-PlanOnly'])
    assert mixed.returncode == 0, mixed.stdout + mixed.stderr
    mixed_report_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', mixed.stdout)[-1])
    mixed_names = [case['name'] for case in json.loads(
        mixed_report_path.read_text(encoding='utf-8-sig'))['cases']]
    assert mixed_names.index('trigger-snapshot') < mixed_names.index('transient-continuous-auto-setup')
    preflight = run_script(['-TriggerOnly'], preflight=True)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr


def test_configuration_readback_script_plan_preflight_and_noninteractive_refusal(run_script):
    preflight = run_script(['-ReadbackOnly'], preflight=True)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    assert 'PASS: Readback preflight' in preflight.stdout

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
    for options, status in [(['-PlanOnly', *baseline], 'PLAN ONLY'),
                            (['-ExpectedRiseSlew', '0.1'], 'BLOCKED')]:
        result = run_script(['-Modes', 'readback', *options])
        assert result.returncode == (0 if status == 'PLAN ONLY' else 2), result.stdout + result.stderr
        path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
        report = json.loads(path.read_text(encoding='utf-8-sig'))
        assert report['overall_status'] == status and report['hardware_touched'] is False
        assert [case['command'] for case in report['cases'][:1]] == ['input-state']
        assert [case['name'] for case in report['cases']] == expected
        assert [case['command'] for case in report['cases'][3:7]] == ['fixed-status'] * 4
        assert all(case['arguments'] == ['--hardware-validation', 'readback']
                   for case in report['cases'][2:])
        assert all(case['status'] == 'NOT RUN' for case in report['cases'])
        for case in report['cases'][-4:]:
            assert case['readback_comparison'] == 'NOT RUN'
            if status == 'PLAN ONLY':
                assert case['expected_readback'] == settings[case['name']]
            else:
                assert 'expected_readback' not in case and case['condition']
        if status == 'BLOCKED':
            assert 'requires an interactive console' in result.stdout


@pytest.mark.parametrize("plan_only", [True, False])
def test_sequence_plan_or_noninteractive_refusal(run_script, plan_only):
    result = run_script(["-Modes", "sequence", *(["-PlanOnly"] if plan_only else [])])
    assert result.returncode == (0 if plan_only else 2), result.stdout + result.stderr
    paths = re.findall(r"Report: (.+?/shareable/report\.json)", result.stdout)
    assert paths, result.stdout + result.stderr
    path = Path(paths[-1])
    report_text = path.read_text(encoding="utf-8-sig")
    report = json.loads(report_text)
    assert report["overall_status"] == ("PLAN ONLY" if plan_only else "BLOCKED")
    assert not report["hardware_touched"]
    assert RESOURCE not in report_text and not (path.parents[1] / "private").exists()
    assert all(case["status"] == "NOT RUN" for case in report["cases"])
    if plan_only:
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


def test_system_query_focused_plan_and_preflight(run_script):
    preflight = run_script(['-SystemQueryOnly'], preflight=True)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    result = run_script(['-Modes', 'system-query', '-PlanOnly'])
    assert result.returncode == 0, result.stdout + result.stderr
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    cases = report['cases'][2:]
    assert [case['command'] for case in cases] == ['scpi-version', 'display-mode', 'scpi-error-next']
    assert all(case['arguments'] == ['--hardware-validation', 'system-query'] for case in cases)


@pytest.mark.parametrize('plan_only', [True, False])
@pytest.mark.parametrize('connection,resource', [('usb', None), ('usb', RESOURCE), ('asrl', ASRL)])
def test_workflow_only_stays_offline_with_optional_connection(run_script, plan_only, connection, resource):
    result = run_script(['-Connection', connection, '-Modes', 'workflow',
                         *(['-PlanOnly'] if plan_only else [])], resource=resource)
    assert result.returncode == 0, result.stdout + result.stderr
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    assert report['overall_status'] == ('PLAN ONLY' if plan_only else 'PASS')
    assert report['validation_mode'] == ('plan-only' if plan_only else 'simulate')
    assert report['hardware_touched'] is False
    cases = report['cases']
    assert [case['command'] for case in cases] == [
        'measure-log', 'measure-until', 'cc-timed-load', 'cc-discharge',
    ]
    assert all(case['execution'] == 'simulate' for case in cases)
    assert all(case['status'] == ('NOT RUN' if plan_only else 'PASS') for case in cases)
    if not plan_only:
        assert [case['stop_reason'] for case in cases] == [
            'completed', 'condition_met', 'duration_elapsed', 'capacity_cutoff',
        ]
        assert all(case['input_state'] == 'OFF' for case in cases[2:])


@pytest.mark.parametrize('mode,connection,resource,plan_only', [
    ('measurement-extensions', 'usb', RESOURCE, True),
    ('auxiliary-controls', 'usb', RESOURCE, True),
    ('list-cc', 'usb', RESOURCE, False),
    ('list-cc', 'usb', RESOURCE, True),
    ('list-cc,protection', 'usb', RESOURCE, True),
    ('list-cc,input-timer', 'usb', RESOURCE, True),
    ('list-cc,auxiliary-controls', 'asrl', ASRL, True),
    ('workflow,auxiliary-controls,list-cc,measurement-extensions', 'asrl', ASRL, False),
    ('full', 'usb', RESOURCE, True),
    ('full', 'asrl', ASRL, True),
])
def test_explicit_live_extensions_never_use_simulation(run_script, mode, connection, resource, plan_only):
    flags = ['-Modes', mode, '-LiveExtensions', '-Connection', connection]
    slot = 2 if mode == 'list-cc,auxiliary-controls' else 1
    if slot != 1:
        flags += ['-ListScratchSlot', str(slot)]
    result = run_script([*flags, *(['-PlanOnly'] if plan_only else [])], resource=resource)
    assert result.returncode == (0 if plan_only else 2), result.stdout + result.stderr
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    assert report['overall_status'] == ('PLAN ONLY' if plan_only else 'BLOCKED')
    assert not report['hardware_touched'] and report['resource'] == '<redacted>'
    cases = {case['name']: case for case in report['cases']}
    if mode == 'full':
        assert len(report['modes']) == 21
    assert all(case['status'] == 'NOT RUN' for case in cases.values())
    assert all(case.get('execution') in (None, 'live') for case in cases.values())
    if mode in ('full', 'measurement-extensions'):
        assert 'MEAS:VOLT:MIN?' in cases['measurement-extensions-min-max']['queries']
        assert 'SENS:AVER:COUN 8' in cases['measurement-extensions-average']['planned_writes']
    if 'list-cc-settings' in cases:
        trial = cases['list-cc-settings']
        assert 'LIST:WID? {step}' in trial['queries']
        assert f'LIST:SAV {slot}' in trial['planned_writes'] and f'LIST:RCL {slot}' in trial['planned_writes']
        assert 'INP ON' not in trial['planned_writes'] and '*TRG' not in trial['planned_writes']
        if mode not in ('list-cc', 'list-cc,auxiliary-controls'):
            assert trial['error_code'] == 'settings_require_isolated_run'
        else:
            assert 'error_code' not in trial
    if 'auxiliary-controls-von' in cases:
        assert {'VOLT:ON?', 'VOLT:LATC?'} <= set(cases['auxiliary-controls-von']['queries'])
        assert cases['auxiliary-controls-short']['planned_writes'] == ['SYST:REM', 'INP:SHOR 1', 'INP:SHOR 0']
        assert not any(write.startswith('CURR:RANG ') for write in cases['auxiliary-controls-slew']['planned_writes'])
    if 'workflow-measure-log' in cases:
        assert cases['workflow-measure-log']['queries'][:2] == ['*IDN?', 'INP:STAT?']
        assert cases['workflow-measure-until']['queries'] == ['*IDN?', 'INP:STAT?', 'MEAS:VOLT?']
        for name in ('workflow-cc-timed-load', 'workflow-cc-discharge'):
            assert {'INP ON', 'INP OFF'} <= set(cases[name]['planned_writes'])
            assert 'error_code' not in cases[name]


@pytest.mark.parametrize('mode,commands', [
    ('measurement-extensions', ['measure-min-max', 'measure-discharge', 'set-average']),
    ('auxiliary-controls', ['set-von', 'short-on', 'short-off', 'set-cc']),
    ('list-cc', ['set-list-cc', 'list-save', 'list-recall', 'list-exit']),
])
def test_new_modes_run_independently_without_a_resource(run_script, mode, commands):
    result = run_script(['-Modes', mode], resource=None)
    assert result.returncode == 0, result.stdout + result.stderr
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    assert report['overall_status'] == 'PASS'
    assert report['validation_mode'] == 'simulate' and report['hardware_touched'] is False
    assert report['modes'] == [mode]
    assert [case['command'] for case in report['cases']] == [*commands, 'pytest']
    assert [case['execution'] for case in report['cases']] == ['dry-run'] * len(commands) + ['simulate']
    assert all(case['status'] == 'PASS' for case in report['cases'])
    assert not (path.parents[1] / 'private').exists()
    assert not list(path.parents[1].rglob('results.xml'))


@pytest.mark.parametrize('scenario,status,diagnostic', [
    ('collect-only', 'NOT RUN', 'tests collected'),
    ('partly-deselected', 'NOT RUN', 'excluded from the offline test selection'),
    ('one-skipped', 'SKIP', 'offline test skip probe'),
])
def test_new_modes_report_incomplete_simulator_checks(run_script, tmp_path, scenario, status, diagnostic):
    if scenario == 'collect-only':
        overrides = {'PYTEST_ADDOPTS': '--collect-only'}
    elif scenario == 'partly-deselected':
        overrides = {'PYTEST_ADDOPTS': (
            '--deselect=tests/test_control_core.py::test_average_native_write_readback_and_query_while_on'
        )}
    else:
        (tmp_path / 'skip_one.py').write_text(
            'import pytest\n'
            'def pytest_collection_modifyitems(items):\n'
            '    items[0].add_marker(pytest.mark.skip(reason="offline test skip probe"))\n',
            encoding='utf-8')
        overrides = {'PYTEST_PLUGINS': 'skip_one'}
    result = run_script(['-Modes', 'measurement-extensions', '-DebugArtifacts'], resource=None,
                        extra_env=overrides)
    assert result.returncode == 3, result.stdout + result.stderr
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    assert report['overall_status'] == 'FAIL' and report['hardware_touched'] is False
    case = report['cases'][-1]
    assert case['command'] == 'pytest' and case['status'] == status
    log = (path.parents[1] / 'private' / f"{case['name']}.txt").read_text(encoding='utf-8-sig')
    assert diagnostic in log
    assert not list(path.parents[1].rglob('results.xml'))


def test_new_modes_deduplicate_and_leave_physical_cases_not_run(run_script):
    result = run_script(['-Modes', 'list-cc,readback,measurement-extensions,list-cc'])
    assert result.returncode == 2 and 'requires an interactive console' in result.stdout
    path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    assert report['overall_status'] == 'BLOCKED' and report['hardware_touched'] is False
    assert report['modes'] == ['readback', 'measurement-extensions', 'list-cc']
    assert len({case['name'] for case in report['cases']}) == len(report['cases'])
    assert {case['status'] for case in report['cases'] if 'execution' in case} == {'PASS'}
    assert {case['status'] for case in report['cases'] if 'execution' not in case} == {'NOT RUN'}


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
