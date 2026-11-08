"""Transient CLI parsing and exact JSON forwarding through the local client."""
import json

import pytest

from electronicload_tool_cli import cli


BASE = ['set-transient-cc', '--transient-mode', 'toggle', '--range', 'auto',
        '--level-a', '1', '--level-b', '2', '--rise-slew', '.1', '--fall-slew', '.1']


def base(function):
    args = BASE.copy()
    args[0] = 'set-transient-' + function.lower()
    if function != 'CC':
        args = args[:-4]
    if function == 'CW':
        del args[3:5]
    return args


@pytest.mark.parametrize('mode', ['simulate', 'dry-run'])
@pytest.mark.parametrize('transient_mode,widths', [
    ('continuous', ['--width-a', '.001', '--width-b', '.002']),
    ('pulse', ['--pulse-width', '.001']), ('toggle', []),
])
@pytest.mark.parametrize('function', ['CC', 'CV', 'CR', 'CW'])
def test_transient_cli_modes(capsys, mode, transient_mode, widths, function):
    args = base(function)
    args[2] = transient_mode
    assert cli.main([*args, *widths, f'--{mode}', '--json']) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert not output.err and payload['ok'] and payload['mode'] == mode
    result = payload['result']
    assert result['range'] == (None if function == 'CW' else 'low')
    assert result['function'] == function
    if mode == 'simulate':
        assert result['mode'] == transient_mode and not result['enabled']
    else:
        assert result['hardware_access'] is False


@pytest.mark.parametrize('extra,code', [
    (['--level-a', '1.0000000000000000001'], 'no_matching_range'),
    (['--pulse-width', '.001'], 'argument_error'),
    (['--transient-mode', 'pulse'], 'argument_error'),
    (['--transient-mode', 'continuous', '--width-a', '.001'], 'argument_error'),
    (['--range', 'low', '--rise-slew', '.251'], 'slew_out_of_range'),
])
def test_transient_cli_refusals(capsys, extra, code):
    assert cli.main([*BASE, *extra, '--dry-run', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == code


def test_transient_cli_stateless_controls_and_manifest(capsys):
    assert cli.main([*BASE, '--simulate', '--json']) == 0
    capsys.readouterr()
    assert cli.main(['transient-enable', '--simulate', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'transient_not_configured'
    assert cli.main(['transient-status', '--simulate', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['result']['configured'] is False
    assert cli.main(['manifest', '--json']) == 0
    manifest = json.loads(capsys.readouterr().out)
    for command in ('set-transient-cc', 'set-transient-cv', 'set-transient-cr', 'set-transient-cw', 'transient-enable', 'transient-trigger',
                    'transient-disable', 'transient-status'):
        assert manifest['commands'][command] == {'kind': 'async_job'}
    assert manifest['schema_version'] == 2 and manifest['worker_protocol']['schema_versions'] == [3]


@pytest.mark.parametrize('override', [[], ['--validation-allow-pending-live-support']])
@pytest.mark.parametrize('function', ['CC', 'CV', 'CR', 'CW'])
def test_transient_cli_live_refusal(capsys, override, function):
    assert cli.main([*base(function), '--resource', 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                     *override, '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'unsupported_live_scope'


def test_transient_cli_help(capsys):
    with pytest.raises(SystemExit) as result:
        cli.main(['set-transient-cc', '--help'])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    for flag in ('--transient-mode', '--rise-slew', '--fall-slew', '--pulse-width'):
        assert flag in help_text


@pytest.mark.parametrize('function', ['CC', 'CV', 'CR', 'CW'])
def test_transient_send_command_preserves_numeric_tokens(monkeypatch, capsys, function):
    raw = '{"transient_mode":"toggle","range":"auto","level_a":1.000000000000000001}'
    observed = []

    def request(url, path, body):
        observed.append(body)
        return 400, {'status': 'error'}

    monkeypatch.setattr(cli, 'worker_request', request)
    assert cli.main(['send-command', '--url', 'http://127.0.0.1:12345/command',
                     '--worker-command', 'set-transient-' + function.lower(), '--arguments-json', raw,
                     '--context-json', '{"mode":"simulate","planning_model_id":"itech-it8812c"}',
                     '--json']) == 2
    assert raw.encode() in observed[0]
    assert json.loads(observed[0])['schema_version'] == 3
    assert json.loads(capsys.readouterr().out)['status'] == 'error'


@pytest.mark.parametrize('function', ['CV', 'CR', 'CW'])
@pytest.mark.parametrize('extra', [['--rise-slew', '.1'], ['--fall-slew', '.1']])
def test_transient_new_cli_rejects_slew(capsys, function, extra):
    assert cli.main([*base(function), *extra, '--simulate', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'argument_error'


@pytest.mark.parametrize('function', ['CV', 'CR', 'CW'])
def test_transient_new_cli_help(capsys, function):
    with pytest.raises(SystemExit) as result:
        cli.main(['set-transient-' + function.lower(), '--help'])
    assert result.value.code == 0
    text = capsys.readouterr().out
    assert '--level-a' in text and '--pulse-width' in text
    assert '--rise-slew' not in text and '--fall-slew' not in text
    assert ('--range' in text) is (function != 'CW')
