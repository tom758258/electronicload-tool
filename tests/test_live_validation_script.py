"""Run the real PowerShell script with physical VISA forbidden in child CLIs."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

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

    run.powershell = powershell
    run.env = env
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
        expected_names += ['set-cc-high', 'cc-handoff-from-cr', 'set-cc-auto', 'set-cc-low', 'input-on', 'measure-on',
                           'input-off', 'final-input-state']
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
        steps = ['setup', 'on', 'measure', 'off', 'final-state']
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
    (None, ('cc', 'cv', 'cr', 'cw', 'transient-cc', 'transient-cv', 'transient-cr', 'transient-cw')),
    ('cc', ('cc',)), ('cv', ('cv',)), ('cr', ('cr',)), ('cw', ('cw',)),
    ('transient-cc', ('transient-cc',)),
    ('transient-cv', ('transient-cv',)), ('transient-cr', ('transient-cr',)),
    ('transient-cw', ('transient-cw',)),
    ('transient-cw,transient-cv', ('transient-cv', 'transient-cw')),
    ('cc,cv', ('cc', 'cv')),
    ('cc,transient-cc', ('cc', 'transient-cc')),
    ('CV,cc,cv', ('cc', 'cv')),
])
def test_hardware_script_mode_plans(run_script, selection, modes):
    extra = ['-PlanOnly'] + (['-Modes', selection] if selection is not None else [])
    assert_report(run_script(extra), 'PLAN ONLY', modes)


def test_plan_only_console_summary_and_verbose_steps(run_script):
    normal = run_script(['-PlanOnly'])
    assert normal.returncode == 0, normal.stdout + normal.stderr
    assert 'PLAN ONLY COMPLETE' in normal.stdout
    assert 'Selected modes:' in normal.stdout
    assert 'External source (operator-managed): 5 V / 0.8 A current limit.' in normal.stdout
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


@pytest.mark.parametrize('selection', ['unknown', 'cc,unknown', 'cc,', '', 'cc-slew', 'von', 'short'])
def test_hardware_script_invalid_modes(run_script, selection):
    result = run_script(['-PlanOnly', '-Modes', selection])
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Modes must contain one or more of:' in result.stdout
    assert 'Report:' not in result.stdout  # Rejected before planning any cases.


@pytest.mark.parametrize('selection', [
    'cc', 'cv', 'transient-cc', 'transient-cv', 'transient-cr', 'transient-cw',
])
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


@pytest.mark.skipif(os.name == 'nt', reason='Requires a POSIX pseudo-terminal for fake operator input.')
@pytest.mark.parametrize('scope,off_failure', [('transient-cv', False), ('transient-cr', True),
                                              ('transient-cw', False),
                                              ('transient-cv,trace', False),
                                              ('input-timer', False), ('input-timer', True)])
def test_validation_script_failure_cleanup_uses_active_scope(run_script, tmp_path, scope, off_failure):
    # Run the actual script through its interactive gates, with every LIVE CLI
    # replaced by a stateful fake. The existing VISA blocker remains installed.
    import pty
    trace = tmp_path / 'fake-commands.jsonl'
    state = tmp_path / 'fake-state.json'
    mixed_trace = 'trace' in scope
    active_scope = scope.split(',')[0]
    customization = tmp_path / 'sitecustomize.py'
    with customization.open('a', encoding='utf-8') as output:
        output.write(
            f"\nTRACE = {str(trace)!r}\nSTATE = {str(state)!r}\n"
            f"OFF_FAILURE = {off_failure!r}\nTRACE_MIXED = {mixed_trace!r}\n"
        )
        output.write(FAKE_LIVE_CLI)
    # Keep this check scoped to cleanup: stage the unchanged script with a
    # successful preflight stub instead of rerunning unrelated CLI preflight.
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(ROOT / 'scripts/live-cli-check.ps1', scripts / 'live-cli-check.ps1')
    (scripts / 'preflight-cli.ps1').write_text('exit 0\n', encoding='utf-8')
    (tmp_path / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
    master, slave = pty.openpty()
    transcript = tmp_path / 'powershell-output.txt'
    try:
        with transcript.open('w', encoding='utf-8') as output:
            process = subprocess.Popen(
                [run_script.powershell, '-NoProfile', '-File', str(scripts / 'live-cli-check.ps1'),
                 '-Target', 'itech-it8812c', '-Resource', RESOURCE,
                 *(['-InputTimerOnly'] if scope == 'input-timer' else ['-Modes', scope]),
                 '-SourceCurrentLimitA', '0.5', '-SourceVoltageLimitV', '12'],
                cwd=ROOT, env=run_script.env,
                stdin=slave, stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 180
                confirmed = False
                while process.poll() is None and time.monotonic() < deadline:
                    text = transcript.read_text(encoding='utf-8')
                    if not confirmed and 'press Enter to run' in text:
                        os.write(master, b'\n')
                        confirmed = True
                    time.sleep(.05)
                assert process.poll() is not None, transcript.read_text(encoding='utf-8')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
    finally:
        os.close(master)
        os.close(slave)
    text = transcript.read_text(encoding='utf-8')
    assert process.returncode == 3 and confirmed, text
    assert not (tmp_path / 'visa-attempt').exists()
    commands = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
    names = [command[0] for command in commands]
    assert names.count('input-on') == names.count('input-off') == 1
    if off_failure:
        disable_command = 'input-timer-disable' if scope == 'input-timer' else 'transient-disable'
        assert disable_command not in names[names.index('input-on') + 1:]
        assert 'Immediate operator action is required' in text
    else:
        assert names[-3:] == ['input-off', 'input-state',
                              'input-timer-disable' if scope == 'input-timer' else 'transient-disable']
        disable = commands[-1]
        assert disable[disable.index('--hardware-validation') + 1] == active_scope
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', text)
    report = json.loads(Path(paths[-1]).read_text(encoding='utf-8-sig'))
    cases = {case['name']: case for case in report['cases']}
    assert report['overall_status'] == 'FAIL'
    if active_scope == 'input-timer':
        assert cases['timer-expiry']['status'] == 'FAIL'
        assert cases['timer-off']['status'] == 'NOT RUN'
    else:
        assert cases[f'{active_scope}-continuous-trigger-1']['status'] == 'FAIL'
        assert cases[f'{active_scope}-pulse-setup']['status'] == 'NOT RUN'
    if mixed_trace:
        # A query failure *before* any TRACE command must keep the original
        # explicit OFF / disable cleanup and not claim TRACE VISA is untrusted.
        assert 'trace-configure' not in names and 'trace-start' not in names
        assert report['trace_safe_shutdown_unconfirmed'] is False
    assert cases['cleanup-input-off']['status'] == ('FAIL' if off_failure else 'PASS')
    assert RESOURCE not in Path(paths[-1]).read_text(encoding='utf-8-sig')
    assert not (Path(paths[-1]).parents[1] / 'private').exists()


FAKE_LIVE_CLI = r'''
import json
from electronicload_tool_cli import cli
original_main = cli.main
def fake_main(argv=None):
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if '--dry-run' in args or '--simulate' in args or args[0] == 'manifest':
        return original_main(args)
    command = args[0]
    with open(TRACE, 'a', encoding='utf-8') as trace:
        trace.write(json.dumps(args) + '\n')
    state = json.loads(Path(STATE).read_text()) if Path(STATE).exists() else {'on': False}
    failed = (command == 'transient-trigger'
              or (command == 'input-timer-status' and state['on'])
              or (command == 'input-off' and OFF_FAILURE))
    if command == 'input-on':
        state['on'] = True
    if command == 'input-off' and not failed:
        state['on'] = False
    if command in ('transient-enable', 'input-timer-enable'):
        state['enabled'] = True
    if command in ('transient-disable', 'input-timer-disable'):
        state['enabled'] = False
    Path(STATE).write_text(json.dumps(state))
    if command.startswith('set-transient-'):
        selected = args[args.index('--range') + 1] if '--range' in args else None
        if selected == 'auto':
            selected = 'low' if command == 'set-transient-cv' else 'high'
        state['configuration'] = {'range': selected}
        Path(STATE).write_text(json.dumps(state))
    result = {'seconds': 5, 'configuration': state.get('configuration'),
              'input_state': 'ON' if state['on'] else 'OFF',
              'enabled': state.get('enabled', False),
              'visa': {'library': 'FAKE', 'manufacturer': 'FAKE'}}
    error_code = 'query_failed' if TRACE_MIXED else 'synthetic_failure'
    print(json.dumps({'ok': not failed, 'result': result,
                      'error': {'code': error_code} if failed else None}))
    return 3 if failed else 0
cli.main = fake_main
'''


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
    assert len(cases) == 17
    assert all(case['status'] == 'NOT RUN' for case in cases.values())
    assert sum(case['command'] == 'trace-configure' for case in cases.values()) == 5
    assert sum(case['command'] == 'trace-start' for case in cases.values()) == 5
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


def run_interactive_script(run_script, tmp_path, modes, *, extra=(), readiness=None, observation=None):
    # Preserve the actual interactive gate and stub only unrelated offline preflight.
    import pty

    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(ROOT / 'scripts/live-cli-check.ps1', scripts / 'live-cli-check.ps1')
    (scripts / 'preflight-cli.ps1').write_text('exit 0\n', encoding='utf-8')
    (tmp_path / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
    master, slave = pty.openpty()
    transcript = tmp_path / 'powershell-console.txt'
    try:
        with transcript.open('w', encoding='utf-8') as output:
            process = subprocess.Popen(
                [run_script.powershell, '-NoProfile', '-File', str(scripts / 'live-cli-check.ps1'),
                 '-Target', 'itech-it8812c', '-Resource', RESOURCE, '-Modes', modes,
                 '-SourceCurrentLimitA', '0.5', '-SourceVoltageLimitV', '12', *extra],
                cwd=ROOT, env=run_script.env,
                stdin=slave, stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 150
                confirmed = False
                ready_prompts = observed_prompts = 0
                while process.poll() is None and time.monotonic() < deadline:
                    log = transcript.read_text(encoding='utf-8')
                    if not confirmed and 'press Enter to run' in log:
                        os.write(master, b'\n')
                        confirmed = True
                    if readiness is not None and log.count('press Enter to start the bounded') > ready_prompts:
                        os.write(master, readiness + b'\n')
                        ready_prompts += 1
                    if observation is not None and log.count('Did you observe the expected') > observed_prompts:
                        os.write(master, observation + b'\n')
                        observed_prompts += 1
                    time.sleep(.05)
                assert process.poll() is not None, transcript.read_text(encoding='utf-8')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
    finally:
        os.close(master)
        os.close(slave)
    assert confirmed
    return process.returncode, transcript.read_text(encoding='utf-8')


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
    for source in ('manual', 'hold', 'timer', 'bus', 'external'):
        assert cases[f'trigger-{source}-configure']['command'] == 'trigger-configure'
        assert '--confirm' in cases[f'trigger-{source}-on']['arguments']
        assert cases[f'trigger-{source}-off']['command'] == 'input-off'
        assert cases[f'trigger-{source}-observed']['command'] == 'operator-observation'
        assert (f'trigger-{source}-dispatch' in cases) == (source in ('hold', 'bus'))
    assert cases['trigger-bus-setup']['command'] == 'set-list-cc'
    assert cases['trigger-hold-setup']['command'] == 'set-transient-cc'
    assert not any(name.startswith('transient-') or name.startswith('cc-') for name in cases)
    refused = run_script(['-Modes', 'trigger'])
    assert refused.returncode == 2 and 'requires an interactive console' in refused.stdout
    preflight = run_script(['-TriggerOnly'], preflight=True)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr


FAKE_TRIGGER_CLI = r'''
import json
import sys
import time
from electronicload_tool_cli import cli

original_main = cli.main
def fake_trigger_main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if '--dry-run' in args or '--simulate' in args or args[0] == 'manifest':
        return original_main(args)
    command = args[0]
    state_path = Path(TRIGGER_STATE)
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        'input_state': 'OFF', 'source': 'EXTERNAL', 'interval_s': '0.375'
    }
    with open(TRIGGER_LOG, 'a', encoding='utf-8') as log:
        log.write(json.dumps([command, state.copy(), args[1:]]) + '\n')
    error = None
    if command == 'trigger-configure':
        source = args[args.index('--source') + 1]
        if state['input_state'] != 'OFF':
            error = 'input_not_off'
        elif source == 'EXTERNAL' and '--interval-s' in args and SCENARIO == 'restore_failed':
            error = 'readback_mismatch'
        else:
            state['source'] = source
            if '--interval-s' in args:
                state['interval_s'] = args[args.index('--interval-s') + 1]
    elif command == 'input-on':
        state['input_state'] = 'ON'
    elif command == 'input-off' and SCENARIO != 'off_unconfirmed':
        state['input_state'] = 'OFF'
    state_path.write_text(json.dumps(state))
    if command == 'input-on' and SCENARIO == 'on_timeout':
        time.sleep(30)
    result = {**state, 'visa': {'library': 'FAKE', 'manufacturer': 'FAKE'},
              'voltage_v': 5, 'current_a': 0, 'power_w': 0}
    print(json.dumps({'ok': error is None, 'result': result if error is None else None,
                      'error': {'code': error} if error else None}))
    return 0 if error is None else 3

cli.main = fake_trigger_main
'''


@pytest.mark.skipif(os.name == 'nt', reason='Requires a POSIX pseudo-terminal for fake operator input.')
@pytest.mark.parametrize('scenario', ['observed', 'not_observed', 'invalid_answer', 'cancelled',
                                     'on_timeout', 'off_unconfirmed', 'restore_failed'])
def test_trigger_validation_interactive_cleanup_with_fake_cli(run_script, tmp_path, scenario):
    events = tmp_path / 'trigger-commands.jsonl'
    state = tmp_path / 'trigger-state.json'
    with (tmp_path / 'sitecustomize.py').open('a', encoding='utf-8') as output:
        output.write(
            f'\nTRIGGER_LOG = {str(events)!r}\nTRIGGER_STATE = {str(state)!r}\nSCENARIO = {scenario!r}\n'
        )
        output.write(FAKE_TRIGGER_CLI)
    code, console = run_interactive_script(
        run_script, tmp_path, 'trigger',
        extra=('-TriggerObservationSeconds', '1', '-TriggerMaxOnSeconds', '39',
               '-TriggerIntervalSeconds', '0.25', '-TriggerListWidthA', '0.1', '-TriggerListWidthB', '0.1',
               '-TriggerListRepeat', '1', '-LevelA', '0.1', '-LevelB', '0.2', '-RiseSlew', '0.1',
               '-FallSlew', '0.1', '-PulseWidth', '0.1', '-TriggerExternalSetup', 'PRIVATE EXTERNAL SETUP'),
        readiness=b'cancel' if scenario == 'cancelled' else b'',
        observation={'not_observed': b'No', 'invalid_answer': b'unknown'}.get(scenario, b'Yes'),
    )
    assert code == (0 if scenario == 'observed' else 3), console
    calls = [json.loads(line) for line in events.read_text().splitlines()]
    commands = [row[0] for row in calls]
    assert commands[:4] == ['identify', 'input-state', 'measure', 'trigger-status']
    expected_on = 5 if scenario in ('observed', 'restore_failed') else 0 if scenario == 'cancelled' else 1
    assert commands.count('input-on') == commands.count('input-off') == expected_on
    for command, before, _ in calls:
        if command == 'trigger-configure':
            assert before['input_state'] == 'OFF'
    if scenario in ('observed', 'restore_failed'):
        assert [row[1]['source'] for row in calls if row[0] in ('list-trigger', 'transient-trigger')] == ['HOLD', 'BUS']
    report_path = Path(re.findall(r'Report: (.+?/shareable/report\.json)', console)[-1])
    text = report_path.read_text(encoding='utf-8-sig')
    report = json.loads(text)
    assert RESOURCE not in text and 'PRIVATE EXTERNAL SETUP' not in text
    results = {entry['name']: entry for entry in report['cases']}
    assert results['trigger-snapshot']['trigger_interval_s'] == '0.375'
    restored = scenario not in ('on_timeout', 'off_unconfirmed', 'restore_failed')
    assert report['trigger_restore_status'] == ('PASS' if restored else 'FAIL')
    assert report['trigger_input_off_unconfirmed'] is (scenario == 'off_unconfirmed')
    if restored:
        assert json.loads(state.read_text()) == {'source': 'EXTERNAL', 'interval_s': '0.375', 'input_state': 'OFF'}
        assert results['trigger-restore']['status'] == 'PASS'
    elif scenario in ('on_timeout', 'off_unconfirmed'):
        assert commands[-1] == 'input-off'
        assert results['trigger-restore']['status'] == 'NOT RUN'
        assert 'Did you observe' not in console
    if scenario == 'not_observed':
        assert results['trigger-manual-observed']['operator_observed'] == 'No'
    if scenario == 'observed':
        assert all(results[f'trigger-{name}-observed']['operator_observed'] == 'Yes'
                   for name in ('manual', 'hold', 'timer', 'bus', 'external'))


@pytest.mark.skipif(os.name == 'nt', reason='Requires a POSIX pseudo-terminal for fake operator input.')
@pytest.mark.parametrize('failure', [
    'none', 'untrusted', 'feed_unconfirmed', 'off_failed',
    'off_unconfirmed', 'final_state_on',
])
def test_trace_validation_script_interactive_fake_cleanup_and_results(
    run_script, tmp_path, failure,
):
    command_log = tmp_path / 'trace-calls.jsonl'
    state_file = tmp_path / 'trace-state.json'
    customization = tmp_path / 'sitecustomize.py'
    with customization.open('a', encoding='utf-8') as output:
        output.write(
            f"\nTRACE_CALLS = {str(command_log)!r}\n"
            f"TRACE_STATE = {str(state_file)!r}\nTRACE_FAILURE = {failure!r}\n"
        )
        output.write(FAKE_TRACE_CLI)
    exit_code, console = run_interactive_script(run_script, tmp_path, 'trace')
    assert 'manually set to 12 V with a 0.5 A current limit' in console
    assert 'independent manual Input OFF procedure' in console
    assert exit_code == (0 if failure == 'none' else 3), console
    calls = [json.loads(line) for line in command_log.read_text().splitlines()]
    commands = [row[0] for row in calls]
    assert commands.count('trace-configure') == 5
    assert commands.count('trace-start') == 5
    assert commands.count('input-on') == 1
    expected_off_calls = {
        'none': 1, 'untrusted': 0, 'feed_unconfirmed': 1,
        'off_failed': 2, 'off_unconfirmed': 2, 'final_state_on': 1,
    }[failure]
    assert commands.count('input-off') == expected_off_calls
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', console)
    assert paths, console
    report = json.loads(Path(paths[-1]).read_text(encoding='utf-8-sig'))
    assert report['overall_status'] == ('PASS' if failure == 'none' else 'FAIL')
    assert report['trace_safe_shutdown_unconfirmed'] is (failure != 'none')
    cases = {case['name']: case for case in report['cases']}
    if failure == 'untrusted':
        assert cases['trace-powered-start']['status'] == 'FAIL'
        assert cases['trace-powered-off']['status'] == 'NOT RUN'
        assert cases['cleanup-input-off']['status'] == 'NOT RUN'
        assert 'independent operator' in console
    elif failure == 'feed_unconfirmed':
        # Confirming Input OFF must not clear a still-unconfirmed TRACE Feed.
        assert cases['trace-powered-start']['status'] == 'FAIL'
        assert cases['cleanup-input-off']['status'] == 'PASS'
        assert cases['trace-powered-off']['status'] == 'NOT RUN'
    elif failure in ('off_failed', 'off_unconfirmed'):
        assert cases['trace-powered-start']['status'] == 'PASS'
        assert cases['trace-powered-off']['status'] == 'FAIL'
        assert cases['cleanup-input-off']['status'] == 'FAIL'
        assert 'physical Input OFF was not confirmed' in console
    elif failure == 'final_state_on':
        assert cases['trace-powered-off']['status'] == 'PASS'
        assert cases['trace-powered-final-state']['status'] == 'FAIL'
    else:
        assert cases['trace-powered-start']['trace_csv_complete'] is True
        assert cases['trace-powered-off']['status'] == 'PASS'
        assert cases['trace-powered-final-state']['status'] == 'PASS'
    assert RESOURCE not in Path(paths[-1]).read_text(encoding='utf-8-sig')
    assert str(tmp_path) not in Path(paths[-1]).read_text(encoding='utf-8-sig')
    assert not (Path(paths[-1]).parents[1] / 'private').exists()


FAKE_TRACE_CLI = r'''
from electronicload_tool_cli import cli
from pathlib import Path
import csv
import json
from datetime import datetime, timedelta, timezone

_original_main = cli.main

def fake_trace_main(argv=None):
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if '--dry-run' in args or '--simulate' in args or args[0] == 'manifest':
        return _original_main(args)
    command = args[0]
    with open(TRACE_CALLS, 'a', encoding='utf-8') as log:
        log.write(json.dumps(args) + '\n')
    state = json.loads(Path(TRACE_STATE).read_text()) if Path(TRACE_STATE).exists() else {
        'on': False, 'config': {'source': 'TWO', 'points': 4, 'interval_s': '1'}
    }
    def option(flag, default=None):
        return args[args.index(flag) + 1] if flag in args else default
    failed = False
    error = None
    result = {'input_state': 'ON' if state['on'] else 'OFF',
              'visa': {'library': 'FAKE', 'manufacturer': 'FAKE'}}
    if command == 'trace-configure':
        source = option('--source')
        points = int(option('--points'))
        interval = option('--interval-s')
        state['config'] = {'source': source, 'points': points, 'interval_s': interval}
        result.update({**state['config'], 'sample_count': points // (2 if source == 'TWO' else 1),
                       'validation_only': True, 'validation_scope': 'trace'})
    elif command == 'trace-start':
        config = state['config']
        count = config['points'] // (2 if config['source'] == 'TWO' else 1)
        if TRACE_FAILURE == 'untrusted' and state['on']:
            failed = True
            error = {'code': 'trace_session_untrusted', 'details': {'feed': 'UNKNOWN'}}
        elif TRACE_FAILURE == 'feed_unconfirmed' and state['on']:
            failed = True
            error = {'code': 'trace_timeout',
                     'details': {'feed': 'NEXT', 'feed_stop_unconfirmed': True}}
        else:
            values = {'VOLT': {'voltage_v': 12.0}, 'CURR': {'current_a': 0.1},
                      'TWO': {'voltage_v': 12.0, 'current_a': 0.1}}
            samples = [{'sample_index': i + 1, **values[config['source']]} for i in range(count)]
            stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
            result.update({**config, 'sample_count': count,
                           'samples': samples, 'read_timestamp_utc_plus_8': stamp,
                           'validation_only': True, 'validation_scope': 'trace'})
            if '--csv' in args:
                path = Path(option('--csv'))
                path.parent.mkdir(parents=True, exist_ok=True)
                columns = ['read_timestamp_utc_plus_8','sample_index','voltage_v','current_a',
                           'source','points','interval_s','sample_count',
                           'estimated_elapsed_s','estimated_delta_t_s']
                with path.open('x', encoding='utf-8', newline='') as handle:
                    writer = csv.DictWriter(handle, columns)
                    writer.writeheader()
                    for sample in samples:
                        i = sample['sample_index']
                        writer.writerow({**sample, **config, 'sample_count': count,
                                         'read_timestamp_utc_plus_8': stamp,
                                         'estimated_elapsed_s': str(i - 1),
                                         'estimated_delta_t_s': '' if i == 1 else '1'})
    elif command == 'input-on':
        state['on'] = True
        result['input_state'] = 'ON'
    elif command == 'input-off':
        if TRACE_FAILURE == 'off_failed':
            failed = True
            error = {'code': 'synthetic_failure'}
        elif TRACE_FAILURE == 'off_unconfirmed':
            result['input_state'] = 'ON'
        else:
            state['on'] = False
            state['off_done'] = True
            result['input_state'] = 'OFF'
    elif command == 'input-state' and TRACE_FAILURE == 'final_state_on' and state.get('off_done'):
        result['input_state'] = 'ON'
    elif command == 'set-cc':
        result['range'] = option('--range','low')
    Path(TRACE_STATE).write_text(json.dumps(state))
    print(json.dumps({'ok': not failed, 'result': None if failed else result, 'error': error}))
    return 3 if failed else 0

cli.main = fake_trace_main
'''


@pytest.mark.skipif(os.name == 'nt', reason='Requires a POSIX pseudo-terminal for fake operator input.')
@pytest.mark.parametrize('scenario,modes', [
    ('cc-origin', 'cc'), ('cr-origin', 'cc'), ('led-unconfirmed', 'cc'),
    ('late-resistance', 'cc'), ('plain-cr-to-trace', 'cr,trace'),
    ('slow-normal', 'current-slow-rise'), ('slow-trusted-failure', 'current-slow-rise'),
    ('slow-input-on', 'current-slow-rise'), ('slow-untrusted', 'current-slow-rise'),
])
def test_validation_script_mode_handoff_and_slow_rise_cleanup(run_script, tmp_path, scenario, modes):
    events = tmp_path / 'visa-calls.jsonl'
    state = tmp_path / 'instrument.json'
    with (tmp_path / 'sitecustomize.py').open('a', encoding='utf-8') as output:
        output.write(f'\nEVENTS = {str(events)!r}\nSTATE = {str(state)!r}\nSCENARIO = {scenario!r}\n')
        output.write(RECORDING_LIVE_VISA)
    run_script.env['PYTHONPATH'] += os.pathsep + str(ROOT / 'tests')
    exit_code, console = run_interactive_script(run_script, tmp_path, modes)
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', console)
    assert paths, console
    report_text = Path(paths[-1]).read_text(encoding='utf-8-sig')
    report = json.loads(report_text)
    cases = {case['name']: case for case in report['cases']}
    calls = [(event['args'], event['calls']) for event in map(json.loads, events.read_text().splitlines())]
    writes = [call for _, batch in calls for call in batch if '?' not in call]
    success = scenario in {'cc-origin', 'cr-origin', 'plain-cr-to-trace', 'slow-normal'}
    assert exit_code == (0 if success else 3), console
    assert report['overall_status'] == ('PASS' if success else 'FAIL')
    assert RESOURCE not in report_text
    assert not (tmp_path / 'visa-attempt').exists()
    assert not (Path(paths[-1]).parents[1] / 'private').exists()

    if modes in {'cc', 'cr,trace'}:
        trace = modes == 'cr,trace'
        regular = 'trace-powered-setup-cc' if trace else 'set-cc-high'
        handoff = 'trace-powered-cc-handoff-from-cr' if trace else 'cc-handoff-from-cr'
        scoped_calls = [batch for args, batch in calls if args[0] == 'set-cc' and 'cr-led' in args]
        if scenario in {'cc-origin', 'late-resistance'}:
            assert not scoped_calls
            assert cases[regular]['status'] == ('PASS' if success else 'FAIL')
            assert cases[handoff]['status'] == ('SKIP' if success else 'NOT RUN')
        else:
            assert cases[regular]['status'] == 'SKIP' and cases[regular]['origin_function'] == 'RES'
            [scoped] = scoped_calls
            if success:
                report_handoff = cases[handoff]
                assert report_handoff['status'] == 'PASS'
                assert report_handoff['validation_scope'] == 'cr-led'
                assert report_handoff['function'] == 'CURR' and report_handoff['input_state'] == 'OFF'
                assert scoped.index('RES:LED:STAT?') < scoped.index('FUNC CURR')
                if trace:
                    assert cases['cr-high-final-state']['input_state'] == 'OFF'
                    assert cases['trace-powered-start']['trace_csv_complete'] is True
                    assert cases['trace-powered-final-state']['input_state'] == 'OFF'
                else:
                    assert scoped.count('RES:LED:STAT 0') == 1
                    assert cases['final-input-state']['input_state'] == 'OFF'
            else:
                assert cases[handoff]['status'] == 'FAIL'
                assert 'RES:LED:STAT 0' in scoped
        assert writes.count('INP ON') == (3 if trace else int(success))
        if not success:
            assert 'FUNC CURR' not in writes and 'CURR:RANG MAX' not in writes
        return

    assert 'INP ON' not in writes
    assert writes.count('CURR:SLEW:STAT 1') == 1
    assert writes.count('CURR:SLEW:STAT 0') == int(scenario in {'slow-normal', 'slow-trusted-failure'})
    state_case = cases['cleanup-slow-rise-input-state']
    disable_case = cases['cleanup-slow-rise-disable']
    status_case = cases['cleanup-slow-rise-status']
    needs_operator = scenario in {'slow-input-on', 'slow-untrusted'}
    assert report['slow_rise_cleanup_unconfirmed'] is needs_operator
    assert ('Independent operator action is required' in console) is needs_operator
    summary = Path(paths[-1]).with_name('summary.md').read_text(encoding='utf-8-sig')
    assert ('Independent operator action is required' in summary) is needs_operator
    if scenario == 'slow-normal':
        assert cases['slow-rise-status-disabled']['enabled'] is False
        assert all(item['status'] == 'SKIP' for item in (state_case, disable_case, status_case))
    elif scenario == 'slow-trusted-failure':
        assert cases['slow-rise-status-after-off']['status'] == 'FAIL'
        assert cases['slow-rise-disable']['status'] == 'NOT RUN'
        assert state_case['input_state'] == 'OFF'
        assert disable_case['status'] == 'PASS' and status_case['enabled'] is False
        assert [args[0] for args, _ in calls[-3:]] == [
            'input-state', 'current-slow-rise-disable', 'current-slow-rise-status',
        ]
    else:
        assert state_case['status'] == ('FAIL' if scenario == 'slow-input-on' else 'NOT RUN')
        assert disable_case['status'] == status_case['status'] == 'NOT RUN'
        if scenario == 'slow-untrusted':
            assert calls[-1][0][0] == 'current-slow-rise-enable'


RECORDING_LIVE_VISA = r'''
import json
import sys

from test_hardware_validation import ValidationFake
from test_trace import RecordingVISA

class Device(ValidationFake):
    open_status = pyvisa.constants.StatusCode.success
    fields = ('input', 'mode', 'function', 'range', 'current', 'transient', 'short', 'remote', 'values')

    def __init__(self):
        super().__init__()
        self.trace = RecordingVISA()
        self.args = sys.argv[1:]
        if Path(STATE).is_file():
            device, trace = json.loads(Path(STATE).read_text())
            self.__dict__.update(device)
            self.trace.__dict__.update(trace)
        elif SCENARIO in ('cr-origin', 'led-unconfirmed'):
            self.function = 'RES'
            self.values['RES:LED:STAT'] = '1'
        if SCENARIO == 'led-unconfirmed':
            self.ignore = 'RES:LED:STAT 0'

    def query(self, command):
        if command == 'CURR:SLEW:STAT?' and self.args[0] == 'current-slow-rise-status':
            if SCENARIO in ('slow-trusted-failure', 'slow-input-on') and self.values['CURR:SLEW:STAT'] == '1':
                self.calls.append(command)
                return '0'  # Valid but unexpected readback.
        if SCENARIO == 'slow-input-on' and self.args[0] == 'input-state' and self.values['CURR:SLEW:STAT'] == '1':
            self.input = True
        if command == 'INP:TIM?' or command.startswith(('TRAC:', 'STAT:QUES:', 'MEAS:', 'FETC:')):
            self.calls.append(command)
            self.trace.input_on = self.input
            self.trace.current_a = self.current
            return self.trace.query(command)
        return super().query(command)

    def write(self, command):
        if command == 'TRIG' or command.startswith('TRAC:'):
            self.calls.append(command)
            self.trace.write(command)
            return
        super().write(command)
        if SCENARIO == 'late-resistance' and command == 'SYST:REM':
            self.function = 'RES'
        if SCENARIO == 'slow-untrusted' and command == 'CURR:SLEW:STAT 1':
            raise TimeoutError('Enable may have reached the fake instrument.')

    def get_visa_attribute(self, _attr):
        return 'FAKE'

    def close(self):
        device_state = {key: getattr(self, key) for key in self.fields}
        trace_state = {key: value for key, value in vars(self.trace).items() if key.startswith('trace_')}
        Path(STATE).write_text(json.dumps([device_state, trace_state]))
        with Path(EVENTS).open('a', encoding='utf-8') as out:
            out.write(json.dumps({'args': self.args, 'calls': self.calls}) + '\n')

class Manager:
    visalib = type('FakeLibrary', (), {'library_path': 'FAKE'})

    def __init__(self, backend):
        assert backend == '@ivi'

    def open_resource(self, resource, **_kwargs):
        assert resource == 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR'
        return Device()

    def close(self):
        pass

pyvisa.ResourceManager = Manager
'''
