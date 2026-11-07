"""Run the real PowerShell planner/refusal gates with VISA forbidden in child CLIs."""
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
    }
    for mode, trials in prefixes.items():
        if mode not in modes:
            continue
        steps = ['setup', 'on', 'measure', 'off', 'final-state']
        if mode == 'transient-cc':
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
            if mode == 'transient-cc':
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
    (None, ('cc', 'cv', 'cr', 'cw', 'transient-cc')),
    ('cc', ('cc',)), ('cv', ('cv',)), ('cr', ('cr',)), ('cw', ('cw',)),
    ('transient-cc', ('transient-cc',)),
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
