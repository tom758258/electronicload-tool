"""Run the real PowerShell script with physical VISA forbidden in child CLIs."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
RESOURCE = 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR'


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
        'pyvisa.ResourceManager = forbidden\n', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    for name in ('XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        directory = tmp_path / name.lower()
        directory.mkdir()
        env[name] = str(directory)
    def run(extra, *, source_limits=False, mode_array=None, preflight=False):
        args = ([str(ROOT / 'scripts/preflight-cli.ps1')] if preflight else
                [str(ROOT / 'scripts/live-cli-check.ps1'), '-Target', 'itech-it8812c',
                 '-Resource', RESOURCE])
        if source_limits and not preflight:
            args += ['-SourceCurrentLimitA', '0.5', '-SourceVoltageLimitV', '12']
        args += extra
        if mode_array is None:
            invocation = ['-File', *args]
        else:
            # Exercise native PowerShell array syntax as well as external -File comma lists.
            quoted = ["'" + arg.replace("'", "''") + "'" if not arg.startswith('-') else arg
                      for arg in args]
            invocation = ['-Command', '& ' + ' '.join(quoted) + ' -Modes ' + mode_array]
        result = subprocess.run(
            [powershell, '-NoProfile', '-NonInteractive', *invocation], cwd=ROOT, env=env,
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
def test_protection_precheck_rejects_overrides(run_script, extra):
    result = run_script(['-Modes', 'protection', '-PlanOnly', *extra])
    assert result.returncode == 2
    assert 'Select Protection alone with a dedicated 5 V / 0.8 A source.' in result.stdout


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


def test_hardware_script_default_runs_every_mode_in_plan_only(run_script):
    result = run_script(['-PlanOnly'])
    assert result.returncode == 0, result.stdout + result.stderr
    expected = ('cc', 'cv', 'cr', 'cw', 'transient-cc', 'transient-cv', 'transient-cr',
                'transient-cw', 'trace', 'cr-led', 'current-slow-rise', 'trigger', 'input-timer')
    assert f"Selected modes: {', '.join(expected)}." in result.stdout
    report_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)[-1])
    text = report_path.read_text(encoding='utf-8-sig')
    report = json.loads(text)
    names = [item['name'] for item in report['cases']]
    assert report['overall_status'] == 'PLAN ONLY' and report['hardware_touched'] is False
    assert len(names) == len(set(names))
    # Identify is a precheck, not a shareable result case.
    assert report['prechecks']['identify'] == 'NOT RUN'
    assert names[:4] == ['input-state', 'measure-off', 'trigger-snapshot', 'set-cc-high']
    assert names.index('trigger-snapshot') < names.index('set-cc-high')
    assert not any(name.startswith('trigger-manual-') for name in names)
    for name in ('timer-on', 'trace-powered-on', 'cr-led-set', 'slow-rise-enable',
                 'trigger-hold-on', 'trigger-external-on', 'trigger-restore'):
        assert name in names
    assert all(item['status'] == 'NOT RUN' for item in report['cases'])
    assert RESOURCE not in text


def test_plan_only_console_summary_and_verbose_steps(run_script):
    normal = run_script(['-PlanOnly'])
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


@pytest.mark.parametrize('selection', ['unknown', 'cc,unknown', ''])
def test_hardware_script_invalid_modes(run_script, selection):
    result = run_script(['-PlanOnly', '-Modes', selection])
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Modes must contain one or more of:' in result.stdout
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

    expected = ['input-state', 'measure-off', 'readback', 'fixed-status',
                'fixed-slew-status', 'von-status', 'short-status', 'average-status']
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
