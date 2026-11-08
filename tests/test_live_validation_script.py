"""Run the real PowerShell planner/refusal gates with VISA forbidden in child CLIs."""
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
    def run(extra, *, source_limits=True, mode_array=None, preflight=False):
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
        expected_names += ['set-cc-high', 'set-cc-auto', 'set-cc-low', 'input-on', 'measure-on',
                           'input-off', 'final-input-state']
        assert cases['set-cc-high']['arguments'][-1] == 'high'
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


def test_hardware_script_powershell_mode_array(run_script):
    result = run_script(['-PlanOnly'], mode_array='cc,transient-cc')
    assert_report(result, 'PLAN ONLY', ('cc', 'transient-cc'))


@pytest.mark.parametrize('selection', ['unknown', 'cc,unknown', 'cc,', ''])
def test_hardware_script_invalid_modes(run_script, selection):
    result = run_script(['-PlanOnly', '-Modes', selection])
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Modes must contain one or more of:' in result.stdout
    assert 'Report:' not in result.stdout  # Rejected before planning any cases.


@pytest.mark.parametrize('selection,gates', [
    ('cc', []), ('cv', ['-ConfirmWrites']), ('transient-cc', ['-EnableInputOn']),
    ('transient-cc', ['-ConfirmWrites', '-EnableInputOn']),
    ('transient-cv', []), ('transient-cr', ['-ConfirmWrites']),
    ('transient-cw', ['-ConfirmWrites', '-EnableInputOn']),
])
def test_hardware_script_unattended_gates(run_script, selection, gates):
    result = run_script(['-Modes', selection, *gates])
    assert_report(result, 'BLOCKED', (selection,))
    if len(gates) == 2:
        assert 'requires an interactive console' in result.stdout
    else:
        assert 'requires -ConfirmWrites and -EnableInputOn' in result.stdout


def test_hardware_script_selected_parameter_checks(run_script):
    # Unselected Transient settings/source limits cannot block CC-only planning.
    assert_report(run_script(['-PlanOnly', '-Modes', 'cc', '-LevelA', '3'], source_limits=False),
                  'PLAN ONLY', ('cc',))
    # A CV-only plan does not validate CC/Transient-only settings.
    assert_report(run_script(['-PlanOnly', '-Modes', 'cv', '-Current', '3', '-LevelA', '3']),
                  'PLAN ONLY', ('cv',))
    result = run_script(['-PlanOnly', '-Modes', 'cv'], source_limits=False)
    assert result.returncode == 2
    assert 'requires declared source limits' in result.stdout


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
                                              ('transient-cw', False)])
def test_transient_script_failure_cleanup_uses_active_scope(run_script, tmp_path, scope, off_failure):
    # Run the actual script through its interactive gates, with every LIVE CLI
    # replaced by a stateful fake. The existing VISA blocker remains installed.
    import pty
    trace = tmp_path / 'fake-commands.jsonl'
    state = tmp_path / 'fake-state.json'
    customization = tmp_path / 'sitecustomize.py'
    with customization.open('a', encoding='utf-8') as output:
        output.write(f"\nTRACE = {str(trace)!r}\nSTATE = {str(state)!r}\nOFF_FAILURE = {off_failure!r}\n")
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
                 '-Target', 'itech-it8812c', '-Resource', RESOURCE, '-Modes', scope,
                 '-SourceCurrentLimitA', '0.5', '-SourceVoltageLimitV', '12',
                 '-ConfirmWrites', '-EnableInputOn'], cwd=ROOT, env=run_script.env,
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
        assert 'transient-disable' not in names
        assert 'Immediate operator action is required' in text
    else:
        assert names[-3:] == ['input-off', 'input-state', 'transient-disable']
        disable = commands[-1]
        assert disable[disable.index('--hardware-validation') + 1] == scope
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', text)
    report = json.loads(Path(paths[-1]).read_text(encoding='utf-8-sig'))
    cases = {case['name']: case for case in report['cases']}
    assert report['overall_status'] == 'FAIL'
    assert cases[f'{scope}-continuous-trigger-1']['status'] == 'FAIL'
    assert cases[f'{scope}-pulse-setup']['status'] == 'NOT RUN'
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
    failed = command == 'transient-trigger' or (command == 'input-off' and OFF_FAILURE)
    if command == 'input-on':
        state['on'] = True
    if command == 'input-off' and not failed:
        state['on'] = False
    if command == 'transient-enable':
        state['enabled'] = True
    if command == 'transient-disable':
        state['enabled'] = False
    Path(STATE).write_text(json.dumps(state))
    if command.startswith('set-transient-'):
        selected = args[args.index('--range') + 1] if '--range' in args else None
        if selected == 'auto':
            selected = 'low' if command == 'set-transient-cv' else 'high'
        state['configuration'] = {'range': selected}
        Path(STATE).write_text(json.dumps(state))
    result = {'configuration': state.get('configuration'),
              'input_state': 'ON' if state['on'] else 'OFF',
              'enabled': state.get('enabled', False),
              'visa': {'library': 'FAKE', 'manufacturer': 'FAKE'}}
    print(json.dumps({'ok': not failed, 'result': result,
                      'error': {'code': 'synthetic_failure'} if failed else None}))
    return 3 if failed else 0
cli.main = fake_main
'''
