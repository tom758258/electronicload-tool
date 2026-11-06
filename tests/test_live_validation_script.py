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


@pytest.mark.parametrize('extra,expected', [
    (['-PlanOnly'], 'PLAN ONLY'),
    (['-PlanOnly', '-IncludeValidationModes'], 'PLAN ONLY'),
    (['-IncludeValidationModes'], 'BLOCKED'),
    (['-IncludeValidationModes', '-ConfirmWrites', '-EnableInputOn'], 'BLOCKED'),
])
def test_hardware_script_plan_and_unattended_gates(tmp_path, extra, expected):
    powershell = shutil.which('pwsh') or shutil.which('powershell')
    if powershell is None:
        local = ROOT / '.tmp_tests/powershell/pwsh'
        if not local.is_file():
            pytest.skip('PowerShell is required for the script check.')
        powershell = str(local)
    (tmp_path / 'sitecustomize.py').write_text(
        'import pyvisa\n'
        'def forbidden(*args, **kwargs):\n'
        '    raise AssertionError("Physical VISA forbidden in script tests")\n'
        'pyvisa.ResourceManager = forbidden\n', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    for name in ('XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        directory = tmp_path / name.lower()
        directory.mkdir()
        env[name] = str(directory)
    result = subprocess.run(
        [powershell, '-NoProfile', '-NonInteractive', '-File', str(ROOT / 'scripts/live-cli-check.ps1'),
         '-Target', 'itech-it8812c', '-Resource', RESOURCE, '-SourceCurrentLimitA', '0.5',
         '-SourceVoltageLimitV', '12', *extra], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
        text=True, capture_output=True, timeout=180)
    assert result.returncode == (0 if expected == 'PLAN ONLY' else 2), result.stdout + result.stderr
    paths = re.findall(r'Report: (.+?/shareable/report\.json)', result.stdout)
    assert paths, result.stdout + result.stderr
    report_text = Path(paths[-1]).read_text(encoding='utf-8-sig')
    report = json.loads(report_text)
    assert report['overall_status'] == expected
    assert report['hardware_touched'] is False
    assert report['prechecks']['plan'] == 'PASS'
    assert report['summary_counts']['passed'] == report['summary_counts']['skipped'] == 0
    assert RESOURCE not in report_text
    assert not (Path(paths[-1]).parents[1] / 'private').exists()
    cases = {case['name']: case for case in report['cases']}
    assert all(case['status'] == 'NOT RUN' for case in cases.values())
    if '-IncludeValidationModes' in extra:
        for prefix in ('cv-low', 'cv-high', 'cr-low', 'cr-high', 'cw',
                       'transient-continuous-auto', 'transient-pulse-low',
                       'transient-toggle-high', 'transient-continuous-high', 'transient-pulse-auto-high'):
            assert all(f'{prefix}-{name}' in cases for name in ('setup', 'on', 'off', 'final-state'))
        assert cases['transient-pulse-auto-high-trigger-2']['command'] == 'transient-trigger'
        assert cases['transient-pulse-auto-high-disable']['command'] == 'transient-disable'
    else:
        assert not any(name.startswith(('cv-', 'cr-', 'cw-', 'transient-')) for name in cases)
