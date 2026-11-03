"""The CLI must not bypass Core safety or change existing JSON error behavior."""
import json

from electronicload_tool_cli.cli import main


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
                 '--current','1.5','--range','low','--json'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='unsupported_live_scope'


def test_manifest_static(capsys):
    assert main(['manifest','--json'])==0
    p=json.loads(capsys.readouterr().out)
    assert p['schema_version']==2
    assert p['worker_protocol']['schema_versions']==[3]
    assert p['execution']=={'model':'multi_job','context_binding':'per_command'}
    assert p['features']=={'common.async_jobs':{'contract_version':1}}
