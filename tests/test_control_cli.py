"""The CLI must not bypass Core safety or change existing JSON error behavior."""
import json

import pytest

from electronicload_tool_cli.cli import main
from electronicload_tool_cli import cli


def test_new_simulation_and_json(capsys):
    assert main(['set-cc','--simulate','--current','1.5','--range','low','--json'])==0
    p=json.loads(capsys.readouterr().out)
    assert p['result']['range']=='low' and p['result']['current_a']==1.5
    assert main(['input-on','--simulate','--confirm','--json'])==0
    assert json.loads(capsys.readouterr().out)['result']['input_state']=='ON'
    assert main(['input-off','--simulate','--json'])==0
    assert json.loads(capsys.readouterr().out)['result']['input_state']=='OFF'


def test_refusal_and_no_hardware(capsys):
    assert main(['set-cc','--dry-run','--current','1.5','--range','low','--json'])==0
    assert json.loads(capsys.readouterr().out)['result']['hardware_access'] is False
    assert main(['input-on','--simulate','--json'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='confirmation_required'
    assert main(['set-cc','--current','1.5','--range','low','--json'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='missing_resource'
    assert main(['set-cc','--resource','USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                 '--current','1.5','--range','low',
                 '--safety-config','old.toml','--json'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='argument_error'


def test_manifest_static(capsys):
    assert main(['manifest','--json'])==0
    p=json.loads(capsys.readouterr().out)
    assert p['schema_version']==2
    assert p['worker_protocol']['schema_versions']==[3]
    assert p['execution']=={'model':'multi_job','context_binding':'per_command'}
    assert p['features']=={'common.async_jobs':{'contract_version':1}}
    assert p['commands']['set-cr'] == {'kind': 'async_job'}
    assert p['commands']['set-cw'] == {'kind': 'async_job'}


def test_normal_live_cli_control_dispatches_without_validation_override(monkeypatch, capsys):
    observed = {}

    def fake_control(command, **kwargs):
        observed.update(command=command, **kwargs)
        return {'input_state': 'OFF'}

    monkeypatch.setattr(cli, 'control_execute', fake_control)
    assert main(['input-off', '--resource',
                 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR', '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['ok'] is True and payload['result']['input_state'] == 'OFF'
    assert observed['command'] == 'input-off'
    assert observed['validation'] is False


def test_live_cli_forwards_to_shared_core(monkeypatch, capsys):
    observed = []

    def fake_control(command, **kwargs):
        observed.append((command, kwargs))
        return {'input_state': 'OFF' if command == 'set-cc' else 'ON'}

    monkeypatch.setattr(cli, 'control_execute', fake_control)
    resource = 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR'
    assert main(['set-cc', '--resource', resource, '--current', '0.1',
                 '--range', 'low', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['ok'] is True
    assert main(['input-on', '--resource', resource, '--confirm', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['ok'] is True
    assert [command for command, _ in observed] == ['set-cc', 'input-on']
    assert all('safety_config' not in kwargs for _, kwargs in observed)
    assert observed[1][1]['confirmed'] is True


def test_cv_cli_simulation_and_live_refusal(capsys):
    assert main(['set-cv', '--simulate', '--voltage', '5', '--range', 'low',
                 '--json']) == 0
    result = json.loads(capsys.readouterr().out)['result']
    assert result['input_state'] == 'OFF'
    assert result['voltage_v'] == 5
    assert result['range'] == 'low'

    assert main(['set-cv', '--dry-run', '--voltage', '50', '--range', 'high',
                 '--json']) == 0
    result = json.loads(capsys.readouterr().out)['result']
    assert result['hardware_access'] is False
    assert result['voltage_v'] == 50

    assert main(['set-cv', '--resource',
                 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                 '--voltage', '5', '--range', 'low', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'unsupported_live_scope'


@pytest.mark.parametrize('mode', ['simulate', 'dry-run'])
@pytest.mark.parametrize('range_name,resistance', [('low', '1.23456789'), ('high', '123.456789')])
def test_cr_cli_offline_json(capsys, mode, range_name, resistance):
    assert main(['set-cr', f'--{mode}', '--resistance', resistance,
                 '--range', range_name, '--json']) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert output.err == ''
    assert payload['ok'] is True and payload['command'] == 'set-cr'
    assert payload['mode'] == mode
    result = payload['result']
    assert result['resistance_ohm'] == float(resistance)
    assert result['range'] == range_name
    if mode == 'simulate':
        assert result['input_state'] == 'OFF'
    else:
        assert result['hardware_access'] is False


@pytest.mark.parametrize('arguments,code', [
    (['--resistance', '5'], 'argument_error'),
    (['--range', 'low'], 'argument_error'),
    (['--resistance', '5', '--range', 'auto'], 'argument_error'),
    (['--resistance', 'NaN', '--range', 'low'], 'invalid_resistance'),
    (['--resistance', 'text', '--range', 'low'], 'invalid_resistance'),
    (['--resistance', '10.000000000000000001', '--range', 'low'], 'resistance_out_of_range'),
])
def test_cr_cli_invalid_arguments_are_json_errors(capsys, arguments, code):
    assert main(['set-cr', '--dry-run', *arguments, '--json']) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)['error']['code'] == code
    assert output.err == ''


@pytest.mark.parametrize('override', [[], ['--validation-allow-pending-live-support']])
def test_cr_cli_live_refusal(capsys, override):
    assert main(['set-cr', '--resource', 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                 '--resistance', '5', '--range', 'low', *override, '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'unsupported_live_scope'


def test_cr_cli_text_and_help(capsys):
    with pytest.raises(SystemExit) as error:
        main(['set-cr', '--help'])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert '--resistance' in output and '--range' in output
    assert main(['set-cr', '--simulate', '--resistance', '5', '--range', 'low']) == 0
    output = capsys.readouterr()
    assert 'resistance_ohm: 5.0' in output.out
    assert output.err == ''


@pytest.mark.parametrize('mode', ['simulate', 'dry-run'])
def test_cw_cli_offline_json(capsys, mode):
    assert main(['set-cw', f'--{mode}', '--power', '12.34', '--json']) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert output.err == ''
    assert payload['ok'] is True and payload['command'] == 'set-cw'
    assert payload['mode'] == mode
    result = payload['result']
    assert result['power_w'] == 12.34 and 'range' not in result
    if mode == 'simulate':
        assert result['input_state'] == 'OFF' and result['function'] == 'POW'
    else:
        assert result['hardware_access'] is False


@pytest.mark.parametrize('arguments,code', [
    ([], 'argument_error'),
    (['--power', '5', '--range', 'low'], 'argument_error'),
    (['--power', 'NaN'], 'invalid_power'),
    (['--power', 'text'], 'invalid_power'),
    (['--power', '-0.01'], 'power_out_of_range'),
    (['--power', '250.000000000000000001'], 'power_out_of_range'),
    (['--power', '12.340000000000000001'], 'invalid_power_resolution'),
])
def test_cw_cli_invalid_arguments_are_json_errors(capsys, arguments, code):
    assert main(['set-cw', '--dry-run', *arguments, '--json']) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)['error']['code'] == code
    assert output.err == ''


@pytest.mark.parametrize('override', [[], ['--validation-allow-pending-live-support']])
def test_cw_cli_live_refusal(capsys, override):
    assert main(['set-cw', '--resource', 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                 '--power', '5', *override, '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'unsupported_live_scope'


def test_cw_cli_text_and_help(capsys):
    with pytest.raises(SystemExit) as error:
        main(['set-cw', '--help'])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert '--power' in output and '--range' not in output
    assert main(['set-cw', '--simulate', '--power', '5']) == 0
    output = capsys.readouterr()
    assert 'power_w: 5.0' in output.out
    assert output.err == ''


@pytest.mark.parametrize('power,code', [
    ('12.3400', None),
    ('12.340000000000000001', 'invalid_power_resolution'),
    ('250.000000000000000001', 'power_out_of_range'),
])
def test_cw_cli_send_command_preserves_power_validation(monkeypatch, capsys, power, code):
    sent = []

    def fake_request(url, path, body):
        sent.append(body)
        return 202, {'status': 'accepted'}

    monkeypatch.setattr(cli, 'worker_request', fake_request)
    exit_code = main(['send-command', '--url', 'http://127.0.0.1:12345/command',
                     '--worker-command', 'set-cw', '--arguments-json', '{"power":' + power + '}',
                     '--context-json', '{"mode":"simulate","planning_model_id":"itech-it8812c"}',
                     '--json'])
    payload = json.loads(capsys.readouterr().out)
    if code:
        assert exit_code == 2 and payload['error']['code'] == code
        assert sent == []
    else:
        assert exit_code == 0 and payload['status'] == 'accepted'
        assert sent[0]['arguments'] == {'power': 12.34}
