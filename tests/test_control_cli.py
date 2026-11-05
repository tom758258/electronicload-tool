"""The CLI must not bypass Core safety or change existing JSON error behavior."""
import json

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
