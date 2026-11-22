"""Exercise a real local Worker subprocess, HTTP, JSONL, stop and status."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]


def http(url, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, body, headers={'Content-Type':'application/json'} if body is not None else {})
    try:
        with urlopen(request, timeout=3) as r:
            return r.status, json.load(r)
    except HTTPError as e:
        return e.code, json.load(e)


@pytest.fixture
def worker():
    env = os.environ.copy()
    env['PYTHONPATH']=str(ROOT/'src')+os.pathsep+str(ROOT)
    p = subprocess.Popen([sys.executable,'-u','-c',
            'from electronicload_tool_cli.cli import main; raise SystemExit(main())',
            'worker','--mode','simulate','--control-port','0'],
            cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,bufsize=1)
    out=queue.Queue()
    def reader():
        for line in p.stdout:
            out.put(json.loads(line))
    t=threading.Thread(target=reader,daemon=True)
    t.start()
    try:
        ready=out.get(timeout=5)
        assert ready['event']=='ready'
        yield p,out,ready
    finally:
        if p.poll() is None:
            try:
                http(ready['stop_url'],{'schema_version':3})
                p.wait(timeout=5)
            except Exception:
                p.kill()
                p.wait(timeout=5)
        t.join(timeout=5)


def wait_finished(out, worker_job_id):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        event=out.get(timeout=max(.01,deadline-time.monotonic()))
        if event['event']=='job_finished' and event['worker_job_id']==worker_job_id:
            return event
    pytest.fail('Missing job_finished')


def request(command,arguments=None):
    return {'schema_version':3,'command':command,'arguments':arguments or {},
            'context':{'mode':'simulate','planning_model_id':'itech-it8812c'}}


def test_protection_worker_validates_requests_and_persists_simulated_settings(worker, capsys):
    from electronicload_tool_cli.cli import main

    _, out, ready = worker
    url = ready['command_url']
    status_fields = http(ready['status_url'])[1].keys()
    for command, arguments in [
        ('protection-status', {'events': 1}),
        ('protection-status', {'hardware_validation': 'protection'}),
        ('protection-set', {'confirm': True, 'software_power_limit': '0.50'}),
        ('protection-set', {'confirm': True, 'restore_defaults': 'software-power',
                            'software_power_limit': 0.5}),
        ('protection-clear', {}),
    ]:
        code, rejected = http(url, request(command, arguments))
        assert code == (409 if command == 'protection-clear' else 400)
        assert rejected['worker_job_id'] is None

    def job(command, arguments=None, *, mode='simulate'):
        payload = request(command, arguments)
        payload['context']['mode'] = mode
        code, accepted = http(url, payload)
        assert code == 202 and accepted['kind'] == 'async_job'
        return wait_finished(out, accepted['worker_job_id'])

    value = '0.501234567890123456'
    assert main(['send-command', '--url', url, '--worker-command', 'protection-set',
                 '--arguments-json', '{"confirm":true,"software_power_limit":' + value + '}',
                 '--context-json', json.dumps(request('protection-set')['context']), '--json']) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert wait_finished(out, accepted['worker_job_id'])['result']['applied']['software_power_limit'] == value
    dry = job('protection-set', {'confirm': True, 'restore_defaults': 'software-power'}, mode='dry_run')
    assert dry['result']['hardware_access'] is False
    assert job('protection-status', {'events': True})['result']['settings']['software_power_limit'] == value
    assert job('input-on', {'confirm_input_on': True})['state'] == 'succeeded'
    assert job('protection-clear', {'confirm': True})['error']['code'] == 'input_not_off'
    assert job('input-state')['result']['input_state'] == 'ON'
    assert job('input-off')['state'] == 'succeeded'
    assert job('protection-clear', {'confirm': True})['result']['input_state'] == 'OFF'
    assert http(ready['status_url'])[1].keys() == status_fields
    assert main(['manifest']) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest['schema_version'] == 2 and manifest['worker_protocol']['schema_versions'] == [3]
    assert all(manifest['commands'][name] == {'kind': 'async_job'}
               for name in ('protection-status', 'protection-set', 'protection-clear'))


def test_trigger_worker_persists_exact_setup_without_extending_live(worker, monkeypatch, capsys):
    from electronicload_tool_cli.cli import main
    from electronicload_tool_cli.worker import Runtime
    from electronicload_tool_core import control

    _, out, ready = worker
    url = ready['command_url']
    baseline = http(ready['status_url'])[1].keys()
    for arguments in ({'source': 'TIMER'}, {'source': 'BUS', 'interval_s': 1},
                      {'source': 'TIMER', 'interval_s': '1'}, {'source': 'HOLD', 'hardware_validation': 'trigger'}):
        code, result = http(url, request('trigger-configure', arguments))
        assert code == 400 and result['worker_job_id'] is None
    for mode, resulting_source in [('dry_run', 'HOLD'), ('simulate', 'TIMER')]:
        context = dict(request('trigger-configure')['context'], mode=mode)
        assert main(['send-command', '--url', url, '--worker-command', 'trigger-configure',
                     '--arguments-json', '{"source":"TIMER","interval_s":0.250000000000000001}',
                     '--context-json', json.dumps(context), '--json']) == 0
        accepted = json.loads(capsys.readouterr().out)
        configured = wait_finished(out, accepted['worker_job_id'])['result']
        assert configured['interval_s'] == '0.250000000000000001'
        code, status = http(url, request('trigger-status'))
        assert code == 202
        result = wait_finished(out, status['worker_job_id'])
        assert result['result']['source'] == resulting_source
    assert http(ready['status_url'])[1].keys() == baseline
    assert main(['manifest']) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest['schema_version'] == 2
    assert all(manifest['commands'][name]['kind'] == 'async_job'
               for name in ('trigger-status', 'trigger-configure'))

    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    runtime = Runtime(mode='live', resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR')
    for command, arguments in [('trigger-status', {}), ('trigger-configure', {'source': 'BUS'})]:
        runtime.run_job({'worker_job_id': command, 'job_id': None, 'command': command},
                        arguments, {'mode': 'live'})
        assert json.loads(capsys.readouterr().out.splitlines()[-1])['error']['code'] == 'unsupported_live_scope'


def test_cr_led_and_slow_rise_worker_persist_settings_and_validate_decimal_input(worker, capsys):
    from electronicload_tool_cli.cli import main

    _, out, ready = worker
    command_url = ready['command_url']
    status_keys = http(ready['status_url'])[1].keys()
    for command, arguments in [
        ('set-cr-led', {'range': 'auto', 'resistance': 50}),
        ('set-cr-led', {'range': 'auto', 'resistance': 50, 'vdrop': '1'}),
        ('set-cr-led', {'range': 'auto', 'resistance': 50, 'vdrop': True}),
        ('current-slow-rise-status', {'vdrop': 1}),
        ('cr-led-status', {'hardware_validation': 'cr-led'}),
    ]:
        code, result = http(command_url, request(command, arguments))
        assert code == 400 and result['worker_job_id'] is None

    args = '{"resistance":50,"range":"auto","vdrop":1.000000000000000001}'
    context = json.dumps(request('set-cr-led')['context'])
    assert main(['send-command', '--url', command_url, '--worker-command', 'set-cr-led',
                 '--arguments-json', args, '--context-json', context, '--json']) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert wait_finished(out, accepted['worker_job_id'])['result']['vdrop_v'] == '1.000000000000000001'

    def run(command, arguments=None, *, dry=False, error=None):
        payload = request(command, arguments)
        if dry:
            payload['context']['mode'] = 'dry_run'
        code, accepted = http(command_url, payload)
        assert code == 202
        finished = wait_finished(out, accepted['worker_job_id'])
        assert finished['state'] == ('failed' if error else 'succeeded')
        if error:
            assert finished['error']['code'] == error
        return finished['result']

    assert run('set-cr', {'range': 'auto', 'resistance': 50}, dry=True)['hardware_access'] is False
    assert run('cr-led-status')['led_enabled']
    run('current-slow-rise-enable', error='unsafe_mode')
    run('set-cc', {'range': 'low', 'current': 0.1})
    assert not run('cr-led-status')['led_enabled']
    assert run('current-slow-rise-enable')['enabled']
    run('set-cr-led', {'range': 'auto', 'resistance': 50, 'vdrop': 1},
        error='current_slow_rise_enabled')
    assert run('current-slow-rise-status')['enabled']
    assert not run('current-slow-rise-disable')['enabled']
    assert http(ready['status_url'])[1].keys() == status_keys


def test_cr_led_and_slow_rise_worker_live_refuses_before_visa(monkeypatch, capsys):
    from electronicload_tool_cli.worker import Runtime
    from electronicload_tool_core import control

    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    runtime = Runtime(mode='live', resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR')
    for command in ('set-cr-led', 'cr-led-status', *control.SLOW_RISE_COMMANDS):
        arguments = {'resistance': 50, 'range': 'auto', 'vdrop': 1} if command == 'set-cr-led' else {}
        runtime.run_job({'worker_job_id': command, 'job_id': None, 'command': command},
                        arguments, {'mode': 'live'})
        result = json.loads(capsys.readouterr().out.splitlines()[-1])
        assert result['state'] == 'failed' and result['error']['code'] == 'unsupported_live_scope'


def test_measurement_worker_admission_results_and_average_state(worker, capsys):
    from electronicload_tool_cli.cli import main
    _, out, ready = worker
    initial = http(ready['status_url'])[1]
    invalid = [
        ('measure-min-max', {'metric': value}) for value in ('unknown', '', None, True, 1, [], {})
    ] + [
        ('measure-discharge', {'metric': 'voltage-min'}),
        ('measure-discharge', {'extra': True}),
        ('average-status', {'count': 2}),
        ('measure-min-max', {'validation_allow_pending_live_support': True}),
        ('set-average', {'count': 8, 'hardware_validation': 'input-timer'}),
        *[('set-average', {'count': value}) for value in (None, True, 1, 17, 2.0, '2')],
        ('set-average', {}), ('set-average', {'count': 8, 'extra': True}),
    ]
    for command, arguments in invalid:
        code, response = http(ready['command_url'], request(command, arguments))
        assert code == 400 and response['worker_job_id'] is None
    assert http(ready['status_url'])[1]['last_job'] is None

    def run(command, arguments=None, dry_run=False, error=None):
        payload = request(command, arguments)
        if dry_run:
            payload['context']['mode'] = 'dry_run'
        code, response = http(ready['command_url'], payload)
        assert code == 202 and response['kind'] == 'async_job'
        terminal = wait_finished(out, response['worker_job_id'])
        assert terminal['schema_version'] == 3
        assert terminal['state'] == ('failed' if error else 'succeeded')
        if error:
            assert terminal['result'] is None and terminal['error']['code'] == error
        status = http(ready['status_url'])[1]
        assert status.keys() == initial.keys()
        assert status['last_job']['result'] == terminal['result']
        return terminal['result']

    assert run('measure-min-max') == {
        'voltage_min_v': 11.9, 'voltage_max_v': 12.1, 'current_min_a': 0.4, 'current_max_a': 0.6}
    for metric, field, value in [('voltage-min', 'voltage_min_v', 11.9),
                                  ('voltage-max', 'voltage_max_v', 12.1),
                                  ('current-min', 'current_min_a', 0.4),
                                  ('current-max', 'current_max_a', 0.6)]:
        assert run('measure-min-max', {'metric': metric}) == {field: value}
    assert run('measure-discharge') == {'capacity_ah': 0.25, 'discharge_time_s': 1800.0}
    assert run('measure-discharge', {'metric': 'capacity'}) == {'capacity_ah': 0.25}
    assert run('measure-discharge', {'metric': 'time'}) == {'discharge_time_s': 1800.0}
    assert run('measure-discharge', {'metric': 'time'}, dry_run=True)['queries'] == [
        '*IDN?', 'MEAS:TIME?']
    assert run('measure-min-max', {'metric': 'current-max'}, dry_run=True)['queries'] == [
        '*IDN?', 'MEAS:CURR:MAX?']
    assert main(['send-command', '--url', ready['command_url'], '--worker-command', 'set-average',
                 '--arguments-json', '{"count":16}', '--context-json',
                 json.dumps(request('set-average')['context']), '--json']) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert wait_finished(out, accepted['worker_job_id'])['result'] == {'average_count': 16}
    run('set-average', {'count': 2}, dry_run=True)
    assert run('average-status') == {'average_count': 16}
    run('input-on', {'confirm_input_on': True})
    assert run('average-status') == {'average_count': 16}
    run('set-average', {'count': 8}, error='input_not_off')
    assert run('average-status') == {'average_count': 16}
    assert run('input-state') == {'input_state': 'ON'}
    run('input-off')
    assert run('set-average', {'count': 8}) == {'average_count': 8}


def sequence_request(*actions, **arguments):
    steps = [{"action": action, "arguments": params} for action, params in actions]
    return request("sequence", {"document": {"version": 1, "loop_count": 1, "steps": steps}, **arguments})


SEQUENCE_SET = ("set-cc", {"current": 0.25, "range": "auto"})
SEQUENCE_ON = ("input-on", {})
SEQUENCE_OFF = ("input-off", {})
SEQUENCE_READ = ("input-state", {})


@pytest.mark.parametrize('commands', [
    (
        ('measure-min-max', {}), ('measure-discharge', {}), ('set-average', {'count': 8}),
        ('average-status', {}), ('trace-start', {}),
        ('trace-configure', {'source': 'TWO', 'points': 4, 'interval_s': 0.001}),
        ('protection-status', {'events': True}),
        ('protection-set', {'confirm': True, 'hardware_power_limit': 0.5}),
        ('protection-clear', {'confirm': True}),
    ),
    (('scpi-error-next', {'all': True}), ('scpi-version', {}), ('display-mode', {})),
    (('sequence', sequence_request(
        SEQUENCE_SET, SEQUENCE_ON, SEQUENCE_OFF, confirm_input_on=True,
    )['arguments']),),
], ids=['measurement_trace_protection', 'system_query', 'sequence'])
def test_offline_live_jobs_fail_before_visa(fake_visa, commands):
    from http.server import ThreadingHTTPServer
    from electronicload_tool_cli import worker as worker_module
    runtime = worker_module.Runtime(mode='live',
                                    resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                                    allow_input_on=True)
    with ThreadingHTTPServer(('127.0.0.1', 0), worker_module._handler(runtime)) as server:
        runtime.server, runtime.port = server, server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for command, arguments in (*commands, ('measure', {})):
                payload = {'schema_version': 3, 'command': command, 'context': {'mode': 'live'},
                           'arguments': arguments}
                code, accepted = http(runtime.urls()['command_url'], payload)
                assert code == 202
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    terminal = http(runtime.urls()['status_url'])[1]['last_job']
                    if terminal and terminal['worker_job_id'] == accepted['worker_job_id']:
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail(f'{command} job did not finish')
                if command == 'measure':
                    assert terminal['state'] == 'succeeded'
                    assert terminal['result']['power_w'] == 6.0
                    queries = [call for call in fake_visa.calls
                               if isinstance(call, str) and call.endswith('?')]
                    assert queries == ['*IDN?', 'MEAS:VOLT?', 'MEAS:CURR?', 'FETC:POW?']
                else:
                    assert terminal['state'] == 'failed' and terminal['result'] is None
                    assert terminal['error']['code'] == 'unsupported_live_scope'
                    assert fake_visa.calls == []
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_system_query_worker_keeps_fifo_across_http_jobs(capsys):
    from http.server import ThreadingHTTPServer
    from electronicload_tool_cli import worker as worker_module

    commands = ('scpi-error-next', 'scpi-version', 'display-mode')
    manifest = worker_module.manifest('0.1.0')
    assert manifest['schema_version'] == 2 and manifest['worker_protocol']['schema_versions'] == [3]
    assert all(manifest['commands'][name] == {'kind': 'async_job'} for name in commands)

    runtime = worker_module.Runtime(mode='simulate')
    runtime.system_query_simulator.error_queue.extend([(-222, 'Data out of range'), (120, 'Overflow')])
    with ThreadingHTTPServer(('127.0.0.1', 0), worker_module._handler(runtime)) as server:
        runtime.server, runtime.port = server, server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = runtime.urls()['command_url']

        def job(command, arguments=None, *, mode='simulate'):
            payload = request(command, arguments)
            payload['context']['mode'] = mode
            code, accepted = http(url, payload)
            assert code == 202 and accepted['kind'] == 'async_job'
            runtime.job_thread.join(timeout=3)
            terminal = json.loads(capsys.readouterr().out.splitlines()[-1])
            assert terminal['event'] == 'job_finished' and terminal['ok'] is True, terminal['error']
            assert terminal['worker_job_id'] == accepted['worker_job_id']
            return terminal['result']

        try:
            for command, arguments in [
                ('scpi-error-next', {'all': 1}), ('scpi-error-next', {'all': None}),
                ('scpi-error-next', {'all': 'true'}), ('scpi-error-next', {'max_reads': 21}),
                ('scpi-version', {'all': False}), ('display-mode', {'hardware_validation': 'system-query'}),
            ]:
                code, refused = http(url, request(command, arguments))
                assert code == 400 and refused['worker_job_id'] is None
            plan = job('scpi-error-next', {'all': True}, mode='dry_run')
            assert plan['queries'] == ['*IDN?', 'SYST:ERR?'] and plan['max_reads'] == 20
            assert job('scpi-version') == {'scpi_version': '1999.0'}
            assert job('display-mode') == {'display_mode': 'NORMAL'}
            job('trace-configure', {'source': 'TWO', 'points': 4, 'interval_s': 0.001})
            assert job('scpi-error-next') == {'error_code': -222, 'message': 'Data out of range'}
            assert job('scpi-error-next', {'all': True}) == {
                'errors': [{'error_code': 120, 'message': 'Overflow'}], 'read_count': 2,
                'max_reads': 20, 'empty_confirmed': True, 'limit_reached': False,
            }
            assert job('scpi-error-next', {'all': False}) == {'error_code': 0, 'message': 'No error'}
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_measurement_worker_query_failure_and_discharge_modes(fake_visa, capsys):
    from electronicload_tool_cli.worker import Runtime
    runtime = Runtime(mode='simulate')
    for mode in ('FIX', 'TRAN', 'LIST'):
        runtime.simulator.mode = mode
        assert runtime._execute('measure-discharge', {}, {'mode': 'simulate'}) == {
            'capacity_ah': 0.25, 'discharge_time_s': 1800.0}
        assert runtime.simulator.mode == mode
    fake_visa.responses['MEAS:CAP?'] = '0.25'
    fake_visa.responses['MEAS:TIME?'] = TimeoutError()
    runtime.simulator = fake_visa.session
    runtime.run_job({'worker_job_id': 'measurement', 'job_id': None, 'command': 'measure-discharge'},
                    {}, {'mode': 'simulate'})
    terminal = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert terminal['event'] == 'job_finished' and terminal['state'] == 'failed'
    assert terminal['result'] is None and terminal['error']['code'] == 'query_failed'
    assert terminal['error']['details']['query'] == 'MEAS:TIME?'
    assert fake_visa.calls == ['*IDN?', 'MEAS:CAP?', 'MEAS:TIME?']


def test_fixed_auto_worker_resolved_state_and_dry_run(worker):
    _, out, ready = worker

    def execute(command, arguments=None, dry_run=False, error=None):
        payload = request(command, arguments)
        if dry_run:
            payload['context']['mode'] = 'dry_run'
        code, accepted = http(ready['command_url'], payload)
        assert code == 202
        terminal = wait_finished(out, accepted['worker_job_id'])
        snapshot = http(ready['status_url'])[1]['last_job']
        assert snapshot == {key: terminal[key] for key in snapshot}
        if error:
            assert terminal['error']['code'] == error
            return
        assert terminal['ok'] and terminal['state'] == 'succeeded'
        return terminal['result']

    for command, field, low, high in [
        ('set-cc', 'current', 2.5, 10), ('set-cv', 'voltage', 12, 24),
        ('set-cr', 'resistance', 10, 100),
    ]:
        for value, expected in [(low, 'low'), (high, 'high')]:
            result = execute(command, {field: value, 'range': 'auto'})
            assert result['range'] == expected and result['input_state'] == 'OFF'
        # Planning another range must not replace the active High setup.
        plan = execute(command, {field: low, 'range': 'auto'}, dry_run=True)
        assert plan['range'] == 'low' and plan['hardware_access'] is False
        on = execute('input-on', {'confirm_input_on': True})
        assert on['input_state'] == 'ON' and on['range'] == 'high'
        execute(command, {field: low, 'range': 'auto'}, error='input_not_off')
        assert execute('input-state')['input_state'] == 'ON'
        assert execute('input-off')['input_state'] == 'OFF'


@pytest.mark.parametrize('command,field,value', [
    ('set-cc', 'current', 2), ('set-cv', 'voltage', 12), ('set-cr', 'resistance', 10),
])
def test_fixed_auto_worker_argument_refusals_create_no_job(worker, command, field, value):
    _, _, ready = worker
    for arguments in ({field: value}, {'range': 'auto'},
                      {field: value, 'range': None}, {field: value, 'range': []},
                      {field: value, 'range': 'unknown'},
                      {field: True, 'range': 'auto'}, {field: str(value), 'range': 'auto'},
                      {field: None, 'range': 'auto'}, {field: value, 'range': 'auto', 'extra': 1}):
        code, rejected = http(ready['command_url'], request(command, arguments))
        assert code == 400 and rejected['worker_job_id'] is None
    assert http(ready['status_url'])[1]['last_job'] is None


@pytest.mark.parametrize('command,field,token', [
    ('set-cc', 'current', '1.00000000000000000000000000000001'),
    ('set-cv', 'voltage', '5.0000000000000000001'),
    ('set-cr', 'resistance', '7500.00000000000000001'),
])
def test_fixed_auto_worker_and_cli_preserve_decimal_refusal(worker, capsys, command, field, token):
    from electronicload_tool_cli.cli import main
    from electronicload_tool_cli.worker_client import request as client_request
    _, _, ready = worker
    arguments = '{"' + field + '":' + token + ',"range":"auto"}'
    # Use a complete request with one arguments field, as an external HTTP caller would.
    payload = request(command)
    del payload['arguments']
    body = (json.dumps(payload)[:-1] + ',"arguments":' + arguments + '}').encode()
    code, rejected = client_request(ready['command_url'], '/command', body)
    assert code == 400 and rejected['error']['code'] == 'no_matching_range'
    assert main(['send-command', '--url', ready['command_url'], '--worker-command', command,
                 '--arguments-json', arguments, '--context-json', json.dumps(payload['context']),
                 '--json']) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected['error']['code'] == 'no_matching_range'
    assert rejected['worker_job_id'] is None
    assert http(ready['status_url'])[1]['last_job'] is None


def test_worker_multi_job_terminal_without_files(worker,tmp_path):
    p,out,ready=worker
    status_url,command_url,stop_url=ready['status_url'],ready['command_url'],ready['stop_url']
    _,status=http(status_url)
    assert status['worker_state']=='running' and status['fatal_error'] is None
    assert status['last_job'] is None
    for cmd,args,expected in [('set-cc',{'current':1.5,'range':'low'},'OFF'),
                              ('input-on',{'confirm_input_on':True},'ON'),
                              ('input-off',{},'OFF')]:
        code,accepted=http(command_url,request(cmd,args))
        assert code==202 and accepted['kind']=='async_job'
        finished=wait_finished(out,accepted['worker_job_id'])
        assert finished['state']=='succeeded' and finished['result']['input_state']==expected
        _,status=http(status_url)
        assert status['last_job']['worker_job_id']==accepted['worker_job_id']
        assert status['last_job']['result']==finished['result']
    bad=request('input-off')
    bad['schema_version']=3.0
    assert http(command_url,bad)[0]==400
    code, stopped = http(stop_url,{'schema_version':3})
    assert code == 202
    assert stopped == {'schema_version':3, 'run_id':ready['run_id'],
                       'status':'accepted', 'error':None}
    assert p.wait(timeout=5)==0
    summary=[]
    while not out.empty():
        summary.append(out.get())
    assert any(e['event']=='summary' and e['ok'] for e in summary)
    assert not list(tmp_path.rglob('request.json')) and not list(tmp_path.rglob('result.json'))


def test_worker_invalid_request_does_not_create_job(worker):
    _,_,ready=worker
    command_url=ready['command_url']
    for payload in [request('unknown'),
                    request('set-cc',{'current':9,'range':'low'}),
                    {**request('input-off'),'context':{'mode':'live'}},
                    {**request('input-off'),'unknown':1}]:
        assert http(command_url,payload)[0]==400
    assert http(ready['status_url'])[1]['last_job'] is None


def test_stop_waits_for_http_reply_when_active_job_finishes(monkeypatch):
    """A completing job must not shut down HTTP before Stop's JSON is sent."""
    from http.server import ThreadingHTTPServer
    from electronicload_tool_cli import worker as worker_module

    job_started = threading.Event()
    finish_job = threading.Event()
    job_finished = threading.Event()
    reply_waiting = threading.Event()
    release_reply = threading.Event()
    shutdown_called = threading.Event()
    shutdown_calls = []
    runtime = worker_module.Runtime(mode='simulate')

    def delayed_execute(command, arguments, context):
        job_started.set()
        assert finish_job.wait(timeout=3)
        return {'input_state': 'OFF'}

    monkeypatch.setattr(runtime, '_execute', delayed_execute)
    original_emit = runtime.emit

    def track_events(event, **values):
        original_emit(event, **values)
        if event == 'job_finished':
            job_finished.set()

    monkeypatch.setattr(runtime, 'emit', track_events)
    handler = worker_module._handler(runtime)
    original_send = handler._send

    def delay_stop_reply(self, status, payload):
        if self.path == '/stop' and status == 202:
            reply_waiting.set()
            assert release_reply.wait(timeout=3)
        return original_send(self, status, payload)

    monkeypatch.setattr(handler, '_send', delay_stop_reply)
    with ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
        runtime.server = server
        runtime.port = server.server_port
        original_shutdown = server.shutdown

        def track_shutdown():
            shutdown_calls.append(1)
            shutdown_called.set()
            original_shutdown()

        monkeypatch.setattr(server, 'shutdown', track_shutdown)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        stopped = {}

        def request_stop():
            try:
                stopped['response'] = http(runtime.urls()['stop_url'], {'schema_version':3})
            except Exception as exc:
                stopped['error'] = exc

        stop_thread = threading.Thread(target=request_stop, daemon=True)
        try:
            code, accepted = http(runtime.urls()['command_url'], request('input-off'))
            assert code == 202 and accepted['worker_job_id']
            assert job_started.wait(timeout=3)
            stop_thread.start()
            assert reply_waiting.wait(timeout=3)
            finish_job.set()
            assert job_finished.wait(timeout=3)
            assert not shutdown_called.wait(timeout=0.2)
            release_reply.set()
            stop_thread.join(timeout=3)
            assert not stop_thread.is_alive()
            assert 'error' not in stopped, stopped
            assert stopped['response'] == (202, {
                'schema_version': 3, 'run_id': runtime.run_id,
                'status': 'accepted', 'error': None})
            assert shutdown_called.wait(timeout=3)
            serving.join(timeout=3)
            assert not serving.is_alive()
            runtime.job_thread.join(timeout=3)
            assert not runtime.job_thread.is_alive()
            assert len(shutdown_calls) == 1
        finally:
            finish_job.set()
            release_reply.set()
            original_shutdown()
            serving.join(timeout=3)
            if runtime.job_thread is not None:
                runtime.job_thread.join(timeout=3)

def test_local_lifecycle_client(worker):
    _,out,ready=worker
    from electronicload_tool_cli.worker_client import request as client_request, wait_ready
    from electronicload_tool_core.errors import LoadError
    status=wait_ready(ready['status_url'],timeout_ms=1500)
    assert status['run_id']==ready['run_id']
    code,response=client_request(ready['command_url'],'/command',request('input-off'))
    assert code==202
    finished=wait_finished(out,response['worker_job_id'])
    assert finished['ok'] is True
    try:
        client_request('http://example.com:80/status','/status')
    except LoadError as err:
        assert err.code=='invalid_url'
    else:
        assert False,'Nonlocal HTTP URL was accepted'


def test_live_worker_model_range_admission_and_authorization(monkeypatch):
    from http.server import ThreadingHTTPServer
    from electronicload_tool_cli import worker as worker_module

    calls = []

    def fake_control(command, **kwargs):
        calls.append((command, kwargs))
        return {'input_state': 'ON' if command == 'input-on' else 'OFF'}

    monkeypatch.setattr(worker_module, 'control_execute', fake_control)
    runtime = worker_module.Runtime(mode='live',
                                    resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                                    allow_input_on=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), worker_module._handler(runtime))
    runtime.server = server
    runtime.port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def live_request(command, arguments):
        return {'schema_version': 3, 'command': command, 'arguments': arguments,
                'context': {'mode': 'live'}}

    def finish(worker_job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            _, status = http(runtime.urls()['status_url'])
            job = status['last_job']
            if job is not None and job['worker_job_id'] == worker_job_id:
                return job
            time.sleep(0.01)
        pytest.fail('Live Worker fake job did not finish')

    try:
        command_url = runtime.urls()['command_url']
        assert http(command_url, live_request('set-cc',
                    {'current': 6.1, 'range': 'low'}))[0] == 400
        code, accepted = http(command_url, live_request('set-cc',
                               {'current': 0.1, 'range': 'low'}))
        assert code == 202 and accepted['status'] == 'accepted'
        assert finish(accepted['worker_job_id'])['state'] == 'succeeded'

        code, rejected = http(command_url, live_request('input-on', {}))
        assert code == 409 and rejected['error']['code'] == 'confirmation_required'
        runtime.allow_input_on = False
        assert http(command_url, live_request('input-on',
                    {'confirm_input_on': True}))[0] == 409
        runtime.allow_input_on = True
        code, accepted = http(command_url, live_request('input-on',
                               {'confirm_input_on': True}))
        assert code == 202 and accepted['status'] == 'accepted'
        assert finish(accepted['worker_job_id'])['state'] == 'succeeded'
        assert calls[-1][0] == 'input-on'
        assert calls[-1][1]['confirmed'] is True
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)


def test_cv_worker_simulated_job_and_invalid_arguments(worker):
    _, out, ready = worker
    command_url = ready['command_url']
    for args in ({'voltage': 18.001, 'range': 'low'},
                 {'voltage': 5, 'range': 'invalid'},
                 {'voltage': True, 'range': 'low'}):
        assert http(command_url, request('set-cv', args))[0] == 400

    code, accepted = http(command_url, request('set-cv', {'voltage': 5, 'range': 'low'}))
    assert code == 202 and accepted['kind'] == 'async_job'
    finished = wait_finished(out, accepted['worker_job_id'])
    assert finished['ok'] is True
    assert finished['result']['range'] == 'low'
    assert finished['result']['voltage_v'] == 5
    assert finished['result']['input_state'] == 'OFF'

    code, accepted = http(command_url, request('input-on',
                                               {'confirm_input_on': True}))
    assert code == 202
    assert wait_finished(out, accepted['worker_job_id'])['result']['input_state'] == 'ON'


def test_control_worker_dry_run_returns_plan_without_hardware(worker):
    """Common v3 dry_run must reach Core as dry-run for every control command."""
    _, out, ready = worker
    cases = [
        ('set-cc', {'current': 1.5, 'range': 'low'}),
        ('set-cv', {'voltage': 5, 'range': 'low'}),
        ('set-cr', {'resistance': 123.456789, 'range': 'high'}),
        ('set-cw', {'power': 12.34}),
        ('input-on', {}),
        ('input-off', {}),
    ]
    for command, arguments in cases:
        payload = request(command, arguments)
        payload['context']['mode'] = 'dry_run'
        code, accepted = http(ready['command_url'], payload)
        assert code == 202 and accepted['kind'] == 'async_job'
        finished = wait_finished(out, accepted['worker_job_id'])
        assert finished['state'] == 'succeeded' and finished['ok'] is True
        assert finished['result']['command'] == command
        assert finished['result']['hardware_access'] is False
        if command == 'set-cr':
            assert finished['result']['resistance_ohm'] == arguments['resistance']
            assert finished['result']['range'] == arguments['range']
        if command == 'set-cw':
            assert finished['result']['power_w'] == arguments['power']
            assert 'range' not in finished['result']


def test_cr_worker_validation_and_confirmed_input_cycle(worker):
    _, out, ready = worker
    for arguments in (
        {}, {'resistance': 5}, {'range': 'low'},
        {'resistance': '5', 'range': 'low'},
        {'resistance': True, 'range': 'low'},
        {'resistance': float('nan'), 'range': 'low'},
        {'resistance': 5, 'range': []},
        {'resistance': 5, 'range': 'unknown'},
        {'resistance': 9, 'range': 'high'},
        {'resistance': 5, 'range': 'low', 'extra': 1},
    ):
        code, rejected = http(ready['command_url'], request('set-cr', arguments))
        assert code == 400
        assert rejected['worker_job_id'] is None
        assert rejected['error']['category'] == 'validation'
    status = http(ready['status_url'])[1]
    assert status['active_job'] is None and status['last_job'] is None

    cases = [
        ('set-cr', {'resistance': 1.23456789, 'range': 'low'}, 'OFF'),
        ('set-cr', {'resistance': 123.456789, 'range': 'high'}, 'OFF'),
        ('input-on', {}, None),
        ('input-on', {'confirm_input_on': True}, 'ON'),
        ('input-off', {}, 'OFF'),
    ]
    for index, (command, arguments, state) in enumerate(cases):
        payload = {**request(command, arguments), 'job_id': f'cr-{index}'}
        code, accepted = http(ready['command_url'], payload)
        assert code == 202 and accepted['kind'] == 'async_job'
        terminal = wait_finished(out, accepted['worker_job_id'])
        assert terminal['job_id'] == payload['job_id']
        assert terminal['schema_version'] == 3 and terminal['run_id'] == ready['run_id']
        if state is None:
            assert terminal['state'] == 'failed'
            assert terminal['error']['code'] == 'confirmation_required'
        else:
            assert terminal['state'] == 'succeeded'
            assert terminal['result']['input_state'] == state
            if command == 'set-cr':
                assert terminal['result']['resistance_ohm'] == arguments['resistance']
                assert terminal['result']['range'] == arguments['range']
        status = http(ready['status_url'])[1]
        assert status['last_job']['worker_job_id'] == accepted['worker_job_id']
        assert status['last_job']['result'] == terminal['result']
        assert status['last_job']['error'] == terminal['error']


def test_cr_live_worker_job_fails_without_visa(monkeypatch, capsys):
    from electronicload_tool_cli.worker import Runtime
    from electronicload_tool_core import control

    def forbidden_open(_resource):
        pytest.fail('CR Worker attempted to open VISA')

    monkeypatch.setattr(control, 'open_session', forbidden_open)
    runtime = Runtime(mode='live', resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR')
    job = {'worker_job_id': 'cr-live', 'job_id': 'request-cr', 'command': 'set-cr'}
    runtime.run_job(job, {'resistance': 5, 'range': 'low'}, {'mode': 'live'})
    terminal = runtime.snapshot()['last_job']
    assert terminal['state'] == 'failed' and terminal['ok'] is False
    assert terminal['error']['code'] == 'unsupported_live_scope'
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]['event'] == 'job_finished'
    assert events[-1]['error'] == terminal['error']


@pytest.mark.parametrize('power,code', [
    ('12.340000000000000001', 'invalid_power_resolution'),
    ('250.000000000000000001', 'power_out_of_range'),
])
def test_cw_worker_rejects_exact_json_number_without_rounding(worker, power, code):
    _, _, ready = worker
    body = ('{"schema_version":3,"command":"set-cw","arguments":{"power":' + power
            + '},"context":{"mode":"simulate","planning_model_id":"itech-it8812c"}}')
    req = Request(ready['command_url'], body.encode(), headers={'Content-Type': 'application/json'})
    with pytest.raises(HTTPError) as error:
        urlopen(req, timeout=3)
    with error.value as response:
        assert response.code == 400
        rejected = json.load(response)
    assert rejected['worker_job_id'] is None and rejected['error']['code'] == code
    assert rejected['command'] == 'set-cw' and rejected['kind'] == 'async_job'
    assert http(ready['status_url'])[1]['last_job'] is None


def test_cw_worker_validation_and_confirmed_input_cycle(worker):
    _, out, ready = worker
    for arguments in (
        {}, {'power': '5'}, {'power': True}, {'power': None}, {'power': []},
        {'power': float('nan')}, {'power': float('inf')},
        {'power': -1}, {'power': 250.01}, {'power': 1.001},
        {'power': 5, 'range': 'low'}, {'power': 5, 'extra': 1},
    ):
        code, rejected = http(ready['command_url'], request('set-cw', arguments))
        assert code == 400 and rejected['worker_job_id'] is None
        assert rejected['error']['category'] == 'validation'
    status = http(ready['status_url'])[1]
    assert status['active_job'] is None and status['last_job'] is None

    cases = [
        ('set-cw', {'power': 0}, 'simulate', None),
        ('set-cw', {'power': 12.34}, 'simulate', None),
        ('set-cw', {'power': 250}, 'dry_run', None),
        ('input-on', {}, 'simulate', 'confirmation_required'),
        ('input-on', {'confirm_input_on': True}, 'simulate', None),
        ('set-cw', {'power': 5}, 'simulate', 'input_not_off'),
        ('input-off', {}, 'simulate', None),
    ]
    for index, (command, arguments, mode, error_code) in enumerate(cases):
        payload = {**request(command, arguments), 'job_id': f'cw-{index}'}
        payload['context']['mode'] = mode
        code, accepted = http(ready['command_url'], payload)
        assert code == 202 and accepted['kind'] == 'async_job'
        terminal = wait_finished(out, accepted['worker_job_id'])
        assert terminal['job_id'] == payload['job_id']
        assert terminal['schema_version'] == 3 and terminal['run_id'] == ready['run_id']
        if error_code:
            assert terminal['state'] == 'failed' and terminal['ok'] is False
            assert terminal['error']['code'] == error_code
        else:
            assert terminal['state'] == 'succeeded' and terminal['ok'] is True
            result = terminal['result']
            if command == 'set-cw':
                assert result['power_w'] == arguments['power'] and 'range' not in result
            if mode == 'dry_run':
                assert result['hardware_access'] is False
            else:
                assert result['input_state'] == ('ON' if command == 'input-on' else 'OFF')
            if command == 'input-on':
                assert result['function'] == 'POW' and result['power_w'] == 12.34
        status = http(ready['status_url'])[1]
        assert status['last_job']['worker_job_id'] == accepted['worker_job_id']
        assert status['last_job']['result'] == terminal['result']
        assert status['last_job']['error'] == terminal['error']


def test_cw_live_worker_job_fails_without_visa(monkeypatch, capsys):
    from electronicload_tool_cli.worker import Runtime
    from electronicload_tool_core import control

    def forbidden_open(_resource):
        pytest.fail('CW Worker attempted to open VISA')

    monkeypatch.setattr(control, 'open_session', forbidden_open)
    runtime = Runtime(mode='live', resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR')
    job = {'worker_job_id': 'cw-live', 'job_id': 'request-cw', 'command': 'set-cw'}
    runtime.run_job(job, {'power': 5}, {'mode': 'live'})
    terminal = runtime.snapshot()['last_job']
    assert terminal['state'] == 'failed' and terminal['ok'] is False
    assert terminal['error']['code'] == 'unsupported_live_scope'
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]['event'] == 'job_finished'
    assert events[-1]['error'] == terminal['error']


def transient_arguments(mode='toggle', function='CC'):
    arguments = {'transient_mode': mode, 'range': 'auto', 'level_a': 1, 'level_b': 2,
                 'rise_slew': .1, 'fall_slew': .1}
    if mode == 'continuous':
        arguments.update(width_a=.001, width_b=.002)
    elif mode == 'pulse':
        arguments['pulse_width'] = .001
    if function != 'CC':
        del arguments['rise_slew'], arguments['fall_slew']
    if function == 'CW':
        del arguments['range']
    return arguments


@pytest.mark.parametrize('mode', ['continuous', 'pulse', 'toggle'])
@pytest.mark.parametrize('function', ['CC', 'CV', 'CR', 'CW'])
def test_transient_worker_control_flow_and_status(worker, mode, function):
    _, out, ready = worker
    status_keys = set(http(ready['status_url'])[1])

    def execute(command, arguments=None, dry_run=False):
        payload = request(command, arguments)
        if dry_run:
            payload['context']['mode'] = 'dry_run'
        code, accepted = http(ready['command_url'], payload)
        assert code == 202 and accepted['kind'] == 'async_job'
        terminal = wait_finished(out, accepted['worker_job_id'])
        assert terminal['ok'], terminal
        assert http(ready['status_url'])[1]['last_job']['result'] == terminal['result']
        return terminal['result']

    setup = execute('set-transient-' + function.lower(), transient_arguments(mode, function))
    assert setup['range'] == (None if function == 'CW' else 'low') and setup['mode'] == mode
    assert setup['function'] == function
    assert execute('input-state')['input_state'] == 'OFF'
    assert execute('transient-enable')['enabled']
    assert execute('input-on', {'confirm_input_on': True})['input_state'] == 'ON'
    for count in (1, 2):
        triggered = execute('transient-trigger')
        assert triggered['last_trigger']['sequence'] == count
        assert execute('transient-status') == triggered
        assert execute('input-state')['input_state'] == 'ON'
    # Every dry-run command is a plan and leaves ongoing control state alone.
    for command in ('set-transient-' + function.lower(), 'transient-enable', 'transient-trigger',
                    'transient-disable', 'transient-status'):
        arguments = transient_arguments('pulse', function) if command == 'set-transient-' + function.lower() else {}
        assert execute(command, arguments, dry_run=True)['hardware_access'] is False
    assert execute('transient-status') == triggered
    assert execute('input-off')['input_state'] == 'OFF'
    assert execute('transient-status')['enabled']
    assert not execute('transient-disable')['enabled']
    execute('set-cc', {'range': 'low', 'current': .1})
    assert not execute('transient-status')['configured']
    assert set(http(ready['status_url'])[1]) == status_keys


def test_transient_worker_invalid_arguments_do_not_create_job(worker):
    _, _, ready = worker
    for changes in ({'level_a': True}, {'level_b': '2'}, {'rise_slew': None},
                    {'transient_mode': []}, {'range': {}}, {'width_a': .001},
                    {'level_b': 61}, {'transient_mode': 'pulse'}):
        payload = request('set-transient-cc', {**transient_arguments(), **changes})
        code, rejected = http(ready['command_url'], payload)
        assert code == 400 and rejected['worker_job_id'] is None
    assert http(ready['command_url'], request('transient-trigger', {'extra': 1}))[0] == 400
    assert http(ready['status_url'])[1]['last_job'] is None


@pytest.mark.parametrize('field,token', [('level_a', '1.0000000000000000001'),
                                        ('fall_slew', '2.500000000000000001')])
def test_transient_worker_and_cli_forward_exact_number_refusal(worker, field, token, capsys):
    from electronicload_tool_cli.cli import main
    _, _, ready = worker
    arguments = json.dumps(transient_arguments()).replace(
        f'"{field}": {transient_arguments()[field]}', f'"{field}": {token}')
    assert main(['send-command', '--url', ready['command_url'],
                 '--worker-command', 'set-transient-cc', '--arguments-json', arguments,
                 '--context-json', json.dumps(request('input-state')['context']), '--json']) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected['error']['code'] == 'no_matching_range'
    assert rejected['worker_job_id'] is None
    assert http(ready['status_url'])[1]['last_job'] is None


def test_transient_worker_failed_trigger_terminal_and_stop_leave_input_on(worker):
    p, out, ready = worker
    code, accepted = http(ready['command_url'], request('transient-trigger'))
    assert code == 202
    failed = wait_finished(out, accepted['worker_job_id'])
    assert failed['state'] == 'failed' and failed['error']['code'] == 'transient_not_enabled'
    # Stop state itself must not alter simulator control or input state.
    from electronicload_tool_cli.worker import Runtime
    from electronicload_tool_core.errors import LoadError
    runtime = Runtime(mode='simulate')
    context = request('input-state')['context']
    runtime._execute('set-transient-cc', transient_arguments(), context)
    runtime._execute('transient-enable', {}, context)
    runtime._execute('input-on', {'confirm_input_on': True}, context)
    runtime.stop.set()
    assert runtime.simulator.input_on and runtime.simulator.transient_enabled
    with pytest.raises(LoadError) as error:
        runtime._execute('transient-trigger', {}, context)
    assert error.value.code == 'stopping'
    assert runtime.simulator.input_on and runtime.simulator.transient_enabled
    assert http(ready['stop_url'], {'schema_version': 3})[0] == 202
    assert p.wait(timeout=5) == 0


def test_transient_worker_preserves_accepted_slew_through_cli_and_jsonl(worker, capsys):
    from electronicload_tool_cli.cli import main
    _, out, ready = worker
    slew = '0.12345678901234567890123456789'
    arguments = json.dumps(transient_arguments()).replace('"rise_slew": 0.1',
                                                           f'"rise_slew": {slew}')
    assert main(['send-command', '--url', ready['command_url'],
                 '--worker-command', 'set-transient-cc', '--arguments-json', arguments,
                 '--context-json', json.dumps(request('input-state')['context']), '--json']) == 0
    accepted = json.loads(capsys.readouterr().out)
    terminal = wait_finished(out, accepted['worker_job_id'])
    assert terminal['ok']
    assert terminal['result']['configuration']['rise_slew_a_per_us'] == slew
    assert http(ready['status_url'])[1]['last_job']['result'] == terminal['result']


@pytest.mark.parametrize('command', ['set-transient-cc', 'set-transient-cv', 'set-transient-cr', 'set-transient-cw', 'transient-enable',
                                     'transient-trigger', 'transient-disable', 'transient-status'])
def test_transient_live_worker_terminal_refuses_before_visa(command, monkeypatch, capsys):
    from electronicload_tool_cli.worker import Runtime
    from electronicload_tool_core import control
    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    runtime = Runtime(mode='live', resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR')
    job = {'worker_job_id': 'synthetic', 'job_id': None, 'command': command}
    arguments = transient_arguments(function=command[-2:].upper()) if command.startswith('set-transient-') else {}
    runtime.run_job(job, arguments, {'mode': 'live'})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]['event'] == 'job_finished' and not events[-1]['ok']
    assert events[-1]['error']['code'] == 'unsupported_live_scope'


def list_arguments(repeat_count=1):
    return {'range': 'auto', 'repeat_count': repeat_count, 'steps': [
        {'current_a': .5, 'slew_a_per_us': .1, 'width_s': 1},
        {'current_a': 1, 'slew_a_per_us': .2, 'width_s': 2},
    ]}


@pytest.mark.parametrize('repeat_count', [1, 65536])
def test_list_worker_http_jsonl_cycle_and_unchanged_runtime_status(worker, repeat_count):
    p, out, ready = worker
    status_keys = set(http(ready['status_url'])[1])

    def execute(command, arguments=None, dry_run=False, error=None):
        payload = request(command, arguments)
        if dry_run:
            payload['context']['mode'] = 'dry_run'
        code, accepted = http(ready['command_url'], payload)
        assert code == 202 and accepted['kind'] == 'async_job'
        terminal = wait_finished(out, accepted['worker_job_id'])
        assert terminal['schema_version'] == 3 and terminal['run_id'] == ready['run_id']
        snapshot = http(ready['status_url'])[1]['last_job']
        assert snapshot == {key: value for key, value in terminal.items()
                            if key not in {'event', 'schema_version', 'run_id'}}
        if error:
            assert terminal['state'] == 'failed' and terminal['error']['code'] == error
        else:
            assert terminal['ok'], terminal
        return terminal['result']

    execute('list-trigger', error='list_not_selected')
    setup = execute('set-list-cc', list_arguments(repeat_count))
    assert setup['selected'] and setup['range'] == 'low'
    assert setup['configuration']['repeat_count'] == repeat_count
    assert execute('input-state')['input_state'] == 'OFF'
    assert execute('list-save', {'slot': 7})['slot'] == 7
    execute('list-exit')
    assert execute('list-recall', {'slot': 7})['selected']
    execute('input-on', {'confirm_input_on': True})
    triggered = execute('list-trigger')
    assert triggered['last_trigger']['sequence'] == 1 and triggered['selected']
    assert execute('input-state')['input_state'] == 'ON'  # Job success never implies OFF.
    assert execute('list-status') == triggered
    execute('list-exit', error='input_not_off')
    for command in ('set-list-cc', 'list-save', 'list-recall', 'list-trigger',
                    'list-status', 'list-exit'):
        arguments = (list_arguments(3) if command == 'set-list-cc' else
                     {'slot': 1} if command in {'list-save', 'list-recall'} else {})
        assert execute(command, arguments, dry_run=True)['hardware_access'] is False
    assert execute('list-status') == triggered
    execute('input-off')
    assert execute('list-status')['selected']  # OFF and exit remain separate.
    exited = execute('list-exit')
    assert not exited['selected'] and exited['configured']
    assert exited['configuration'] == setup['configuration']
    assert set(http(ready['status_url'])[1]) == status_keys
    assert http(ready['stop_url'], {'schema_version': 3})[0] == 202
    assert p.wait(timeout=5) == 0
    summary = out.get(timeout=5)
    assert summary['event'] == 'summary' and summary['ok']


def test_list_worker_invalid_arguments_are_rejected_before_job_creation(worker):
    _, _, ready = worker
    # Core covers field-by-field numeric and schema variants; Worker verifies
    # representative HTTP admission failures without creating a job.
    invalid = [
        request('set-list-cc', {**list_arguments(), 'repeat_count': 65537}),
        request('set-list-cc', {**list_arguments(), 'steps': []}),
        request('set-list-cc', {**list_arguments(), 'file': 'client.json'}),
        request('set-list-cc', {**list_arguments(),
                                'steps': [{'current_a': 1, 'width_s': 1}] * 2}),
        request('list-save', {'slot': 0}),
        request('list-recall', {'slot': '1'}),
        request('list-trigger', {'wait_complete': True}),
        request('list-status', {'file': 'client.json'}),
        request('set-list-cc'),
    ]
    for payload in invalid:
        code, rejected = http(ready['command_url'], payload)
        assert code == 400 and rejected['worker_job_id'] is None
    assert http(ready['status_url'])[1]['last_job'] is None


@pytest.mark.parametrize('token,accepted', [('0.12345678901234567890123456789', True),
                                          ('2.500000000000000001', False)])
def test_list_worker_preserves_nested_numbers_through_cli_http_jsonl(worker, capsys, token, accepted):
    from electronicload_tool_cli.cli import main
    _, out, ready = worker
    arguments = json.dumps(list_arguments()).replace('"slew_a_per_us": 0.1',
                                                     f'"slew_a_per_us": {token}')
    code = main(['send-command', '--url', ready['command_url'],
                 '--worker-command', 'set-list-cc', '--arguments-json', arguments,
                 '--context-json', json.dumps(request('input-state')['context']), '--json'])
    response = json.loads(capsys.readouterr().out)
    if accepted:
        assert code == 0
        terminal = wait_finished(out, response['worker_job_id'])
        assert terminal['ok']
        assert terminal['result']['configuration']['steps'][0]['slew_a_per_us'] == token
        assert http(ready['status_url'])[1]['last_job']['result'] == terminal['result']
    else:
        assert code == 2 and response['worker_job_id'] is None
        assert response['error']['code'] == 'no_matching_range'
        assert http(ready['status_url'])[1]['last_job'] is None


def test_list_worker_raw_json_rejects_duplicates_nonfinite_and_fractional_schema(worker):
    from electronicload_tool_cli.worker_client import request as send
    _, _, ready = worker
    raw = json.dumps(request('set-list-cc', list_arguments()))
    for body in (raw.replace('"range": "auto"', '"range":"low","range":"auto"'),
                 raw.replace('"width_s": 1', '"width_s":1,"width_s":2'),
                 raw.replace('"current_a": 0.5', '"current_a":NaN'),
                 raw.replace('"schema_version": 3', '"schema_version":3.0'),
                 raw.replace('"schema_version": 3', '"schema_version":3e0')):
        code, rejected = send(ready['command_url'], '/command', body.encode())
        assert code == 400 and rejected['worker_job_id'] is None
    assert http(ready['status_url'])[1]['last_job'] is None


def test_list_worker_stop_is_io_free_and_blocks_trigger_without_changing_input():
    from electronicload_tool_cli.worker import Runtime
    from electronicload_tool_core.errors import LoadError
    runtime = Runtime(mode='simulate')
    context = request('input-state')['context']
    runtime._execute('set-list-cc', list_arguments(65536), context)
    runtime._execute('input-on', {'confirm_input_on': True}, context)
    triggered = runtime._execute('list-trigger', {}, context)
    runtime.stop.set()
    with pytest.raises(LoadError) as error:
        runtime._execute('list-trigger', {}, context)
    assert error.value.code == 'stopping'
    assert runtime.simulator.input_on
    assert runtime._execute('list-status', {}, context) == triggered
    # The explicit safety action is still independent of Stop.
    assert runtime._execute('input-off', {}, context)['input_state'] == 'OFF'


@pytest.mark.parametrize('command', ['set-list-cc', 'list-save', 'list-recall',
                                     'list-trigger', 'list-status', 'list-exit'])
def test_list_live_worker_terminal_refuses_before_visa(command, monkeypatch, capsys):
    from electronicload_tool_cli.worker import Runtime
    from electronicload_tool_core import control
    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    runtime = Runtime(mode='live', resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR')
    job = {'worker_job_id': 'list-live', 'job_id': None, 'command': command}
    arguments = (list_arguments() if command == 'set-list-cc' else
                 {'slot': 1} if command in {'list-save', 'list-recall'} else {})
    runtime.run_job(job, arguments, {'mode': 'live'})
    terminal = runtime.snapshot()['last_job']
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert terminal['state'] == 'failed' and not terminal['ok']
    assert terminal['error']['code'] == 'unsupported_live_scope'
    assert events[-1]['event'] == 'job_finished' and events[-1]['error'] == terminal['error']


@pytest.mark.parametrize('function', ['CV', 'CR', 'CW'])
def test_transient_worker_new_functions_admission_and_precision(worker, function, capsys):
    from electronicload_tool_cli.cli import main
    _, out, ready = worker
    command = 'set-transient-' + function.lower()
    arguments = transient_arguments('pulse', function)
    for changes in ({'rise_slew': .1}, {'level_a': '1'}, {'level_b': True},
                    {'pulse_width': None}, {'hardware_validation': 'transient-' + function.lower()},
                    {'range': 'auto'} if function == 'CW' else {'range': []}):
        code, rejected = http(ready['command_url'], request(command, {**arguments, **changes}))
        assert code == 400 and rejected['worker_job_id'] is None
    assert http(ready['status_url'])[1]['last_job'] is None
    raw = json.dumps(arguments).replace('"pulse_width": 0.001',
                                        '"pulse_width": 0.000020000000000000000001')
    if function in ('CV', 'CW'):
        invalid = raw.replace('"level_a": 1', '"level_a": 1.000000000000000000000001')
        assert main(['send-command', '--url', ready['command_url'], '--worker-command', command,
                     '--arguments-json', invalid, '--context-json',
                     json.dumps(request('input-state')['context']), '--json']) == 2
        rejected = json.loads(capsys.readouterr().out)
        assert rejected['worker_job_id'] is None
        assert http(ready['status_url'])[1]['last_job'] is None
    else:
        raw = raw.replace('"level_a": 1', '"level_a": 1.000000000000000000000001')
    assert main(['send-command', '--url', ready['command_url'], '--worker-command', command,
                 '--arguments-json', raw, '--context-json',
                 json.dumps(request('input-state')['context']), '--json']) == 0
    accepted = json.loads(capsys.readouterr().out)
    result = wait_finished(out, accepted['worker_job_id'])['result']
    assert result['configuration']['pulse_width_s'] == '0.000020000000000000000001'
    if function == 'CR':
        assert result['configuration']['level_a_ohm'] == '1.000000000000000000000001'


@pytest.mark.parametrize(
    'function,changes,error_code',
    [
        ('CV', {'level_a': 1, 'level_b': 18.001}, 'no_matching_range'),
        ('CR', {'level_a': 5, 'level_b': 100}, 'no_matching_range'),
        ('CW', {'range': 'auto'}, 'argument_error'),
    ],
    ids=['CV', 'CR', 'CW'],
)
def test_transient_new_worker_rejects_invalid_setup_before_job(worker, function, changes, error_code):
    _, out, ready = worker
    code, accepted = http(ready['command_url'], request('input-state'))
    assert code == 202
    assert wait_finished(out, accepted['worker_job_id'])['state'] == 'succeeded'
    before = http(ready['status_url'])[1]

    arguments = {**transient_arguments(function=function), **changes}
    code, rejected = http(ready['command_url'], request('set-transient-' + function.lower(), arguments))
    assert code == 400
    assert rejected['worker_job_id'] is None
    assert rejected['error']['code'] == error_code
    after = http(ready['status_url'])[1]
    assert after['active_job'] is None
    assert after['last_job'] == before['last_job']

    # Shutdown flushes JSONL so an unexpected job cannot be missed.
    assert http(ready['stop_url'], {'schema_version': 3})[0] == 202
    assert out.get(timeout=5)['event'] == 'summary'


def test_transient_new_worker_preserves_cr_pulse_width_through_cli_and_jsonl(worker, capsys, monkeypatch):
    from electronicload_tool_cli import cli
    from electronicload_tool_cli.worker_client import request as worker_request

    _, out, ready = worker
    command = 'set-transient-cr'
    width = '0.000020000000000000000001'
    arguments = f'{{"transient_mode":"pulse","range":"auto","level_a":10,"level_b":1000,"pulse_width":{width}}}'

    def check_http_status(url, path, payload):
        code, response = worker_request(url, path, payload)
        assert code == 202
        return code, response

    monkeypatch.setattr(cli, 'worker_request', check_http_status)
    assert cli.main(['send-command', '--url', ready['command_url'],
                     '--worker-command', command, '--arguments-json', arguments,
                     '--context-json', json.dumps(request(command)['context']), '--json']) == 0
    accepted = json.loads(capsys.readouterr().out)
    terminal = wait_finished(out, accepted['worker_job_id'])
    assert terminal['state'] == 'succeeded'
    result = terminal['result']
    assert result['function'] == 'CR'
    assert result['mode'] == 'pulse'
    assert result['range'] == 'high'
    assert result['configuration']['pulse_width_s'] == width
    assert http(ready['status_url'])[1]['last_job']['result'] == result


def test_fixed_slew_von_short_worker_jobs_and_decimal_forwarding(worker, capsys):
    from electronicload_tool_cli.cli import main

    _, out, ready = worker
    fixed = {'current': 1, 'range': 'auto'}
    for command, arguments, expected in [
        ('set-cc', {**fixed, 'rise_slew': 0.1}, 'argument_error'),
        ('set-cc', {**fixed, 'rise_slew': '0.1', 'fall_slew': 0.2}, 'argument_error'),
        ('set-cc', {**fixed, 'rise_slew': None, 'fall_slew': None}, 'argument_error'),
        ('set-cc', {**fixed, 'rise_slew': 0.251, 'fall_slew': 0.2}, 'slew_out_of_range'),
        ('set-von', {'voltage': 10}, 'argument_error'),
        ('set-von', {'voltage': True, 'latch': 'on'}, 'argument_error'),
        ('set-von', {'voltage': 10, 'latch': True}, 'argument_error'),
        ('short-status', {'extra': True}, 'argument_error'),
    ]:
        code, rejected = http(ready['command_url'], request(command, arguments))
        assert code == 400 and rejected['worker_job_id'] is None
        assert rejected['error']['code'] == expected
    initial = http(ready['status_url'])[1]
    assert initial['active_job'] is None and initial['last_job'] is None

    def terminal(accepted, error=None):
        event = wait_finished(out, accepted['worker_job_id'])
        snapshot = http(ready['status_url'])[1]
        assert snapshot.keys() == initial.keys()
        assert snapshot['last_job'] == {key: event[key] for key in snapshot['last_job']}
        if error:
            assert event['state'] == 'failed' and event['error']['code'] == error
        else:
            assert event['state'] == 'succeeded'
        return event['result']

    def execute(command, arguments=None, *, dry_run=False, error=None):
        payload = request(command, arguments)
        if dry_run:
            payload['context']['mode'] = 'dry_run'
        code, accepted = http(ready['command_url'], payload)
        assert code == 202
        return terminal(accepted, error)

    precision = '0.100000000000000000000001'
    for command, raw, key in [
        ('set-cc', f'{{"current":1,"range":"auto","rise_slew":{precision},"fall_slew":0.2}}',
         'rise_slew_a_per_us'),
        ('set-von', f'{{"voltage":{precision},"latch":"off"}}', 'threshold_v'),
    ]:
        assert main(['send-command', '--url', ready['command_url'], '--worker-command', command,
                     '--arguments-json', raw, '--context-json',
                     json.dumps(request(command)['context']), '--json']) == 0
        assert terminal(json.loads(capsys.readouterr().out))[key] == precision

    assert execute('set-von', {'voltage': 2, 'latch': 'on'}, dry_run=True)['hardware_access'] is False
    assert execute('von-status') == {'threshold_v': precision, 'latch': 'off'}
    execute('short-on', error='input_not_on')
    execute('input-on', {'confirm_input_on': True})
    assert execute('short-on', dry_run=True)['hardware_access'] is False
    assert execute('short-status') == {'short_state': 'OFF'}
    assert execute('short-on') == {'short_state': 'ON'}
    assert execute('short-off') == {'short_state': 'OFF'}
    assert execute('input-state') == {'input_state': 'ON'}
    assert execute('input-off') == {'input_state': 'OFF'}


@pytest.mark.parametrize('command,arguments', [
    ('set-cc', {'current': 1, 'range': 'low', 'rise_slew': 0.1, 'fall_slew': 0.2}),
    ('set-von', {'voltage': 10, 'latch': 'on'}),
    ('short-on', {}),
])
def test_fixed_slew_von_short_live_worker_refuses_before_visa(command, arguments, monkeypatch, capsys):
    from electronicload_tool_cli.worker import Runtime
    from electronicload_tool_core import control

    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    runtime = Runtime(mode='live', resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR')
    job = {'worker_job_id': 'offline-control', 'job_id': None, 'command': command}
    runtime.run_job(job, arguments, {'mode': 'live'})
    terminal = runtime.snapshot()['last_job']
    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert terminal['state'] == 'failed' and terminal['error']['code'] == 'unsupported_live_scope'
    assert event['event'] == 'job_finished' and event['error'] == terminal['error']


def test_input_timer_worker_admission_jobs_state_and_cli_forwarding(worker, capsys):
    from electronicload_tool_cli.cli import main
    _, out, ready = worker
    initial = http(ready['status_url'])[1]
    for arguments in ({}, {'seconds': True}, {'seconds': 1.0}, {'seconds': '1'},
                      {'seconds': None}, {'seconds': 0}, {'seconds': 60001},
                      {'seconds': 1, 'extra': 1}):
        code, result = http(ready['command_url'], request('set-input-timer', arguments))
        assert code == 400 and result['worker_job_id'] is None
    code, result = http(ready['command_url'], request('input-timer-enable', {'seconds': 1}))
    assert code == 400 and result['worker_job_id'] is None
    assert http(ready['status_url'])[1]['last_job'] is None

    def run(command, arguments=None, dry_run=False, error=None):
        payload = request(command, arguments)
        if dry_run:
            payload['context']['mode'] = 'dry_run'
        code, result = http(ready['command_url'], payload)
        assert code == 202
        event = wait_finished(out, result['worker_job_id'])
        assert event['state'] == ('failed' if error else 'succeeded')
        if error:
            assert event['error']['code'] == error
        assert http(ready['status_url'])[1].keys() == initial.keys()
        return event['result']

    assert main(['send-command', '--url', ready['command_url'], '--worker-command', 'set-input-timer',
                 '--arguments-json', '{"seconds":60000}', '--context-json',
                 json.dumps(request('set-input-timer')['context']), '--json']) == 0
    accepted = json.loads(capsys.readouterr().out)
    event = wait_finished(out, accepted['worker_job_id'])
    assert event['result'] == {'seconds': 60000, 'enabled': False}
    run('set-input-timer', {'seconds': 1}, dry_run=True)
    assert run('input-timer-enable') == {'seconds': 60000, 'enabled': True}
    run('input-on', {'confirm_input_on': True})
    run('input-timer-disable', error='input_not_off')
    run('short-on', error='input_timer_enabled')
    assert run('input-timer-status') == {'seconds': 60000, 'enabled': True}
    run('input-off')
    assert run('input-timer-status')['enabled'] is True
    assert run('input-timer-disable')['enabled'] is False
    # Stop stays I/O-free; no Timer reset or Input mutation is hidden in Stop.
    from electronicload_tool_cli.worker import Runtime
    runtime = Runtime(mode='simulate')
    runtime._execute('input-timer-enable', {}, {'mode': 'simulate'})
    runtime._execute('input-on', {'confirm_input_on': True}, {'mode': 'simulate'})
    runtime.stop.set()
    assert runtime.simulator.input_on and runtime.simulator.input_timer_enabled
    assert runtime._execute('input-timer-status', {}, {'mode': 'simulate'})['enabled'] is True


@pytest.mark.parametrize('command', ['set-input-timer', 'input-timer-enable',
                                    'input-timer-disable', 'input-timer-status'])
def test_input_timer_live_worker_terminal_refuses_before_visa(command, monkeypatch, capsys):
    from electronicload_tool_cli.worker import Runtime, _arguments
    from electronicload_tool_core import control
    from electronicload_tool_core.errors import LoadError
    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    runtime = Runtime(mode='live', resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR')
    arguments = {'seconds': 5} if command == 'set-input-timer' else {}
    with pytest.raises(LoadError):
        _arguments(command, {**arguments, 'hardware_validation': 'input-timer'}, runtime)
    job = {'worker_job_id': 'timer', 'job_id': None, 'command': command}
    runtime.run_job(job, arguments, {'mode': 'live'})
    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert event['event'] == 'job_finished' and event['state'] == 'failed'
    assert event['error']['code'] == 'unsupported_live_scope'


def test_trace_worker_simulation_returns_full_batch_and_preserves_decimal_tokens(worker, capsys, tmp_path):
    from electronicload_tool_cli.cli import main

    _, out, ready = worker
    _, status = http(ready['status_url'])
    assert status['cancel_url'] == ready['cancel_url']
    assert status['features'] == ready['features']
    assert ready['features']['common.job_cancel'] == {
        'contract_version': 1, 'commands': ['trace-start'],
    }
    assert status['trace_safety'] is None

    interval = '0.0010000000000000000000000000000001'
    assert main(['send-command', '--url', ready['command_url'],
                 '--worker-command', 'trace-configure',
                 '--arguments-json', '{"source":"CURR","points":3,"interval_s":' + interval + '}',
                 '--context-json', json.dumps(request('trace-configure')['context'])]) == 0
    accepted = json.loads(capsys.readouterr().out)
    finished = wait_finished(out, accepted['worker_job_id'])
    assert finished['result']['interval_s'] == interval
    for invalid in ({'source': 'TWO', 'points': 3, 'interval_s': 1},
                    {'source': 'CURR', 'points': 2.0, 'interval_s': 1},
                    {'source': 'CURR', 'points': 2, 'interval_s': '1'}):
        assert http(ready['command_url'], request('trace-configure', invalid))[0] == 400
    assert http(ready['command_url'], request('trace-start', {'csv_format': 'full'}))[0] == 400
    path = tmp_path / 'new' / 'capture.csv'
    arguments = {'csv': str(path), 'csv_format': 'full', 'csv_time_columns': 'delta'}
    preview = request('trace-start', arguments)
    preview['context']['mode'] = 'dry_run'
    code, accepted = http(ready['command_url'], preview)
    assert code == 202
    assert wait_finished(out, accepted['worker_job_id'])['result']['hardware_access'] is False
    assert not path.parent.exists()

    code, accepted = http(ready['command_url'], request('trace-start', arguments))
    assert code == 202
    terminal = wait_finished(out, accepted['worker_job_id'])
    assert terminal['ok'] and terminal['result']['sample_count'] == 3
    assert terminal['result']['samples'] == [
        {'sample_index': index, 'current_a': 0.0} for index in (1, 2, 3)
    ]
    assert terminal['result']['interval_s'] == interval
    assert len(path.read_text().splitlines()) == 4
    assert http(ready['cancel_url'], {
        'schema_version': 3, 'worker_job_id': accepted['worker_job_id'],
    })[0] == 409
    assert http(ready['status_url'])[1]['last_job']['result'] == terminal['result']


@pytest.mark.parametrize("scenario", [
    "cancel", "stop", "feed_still_next", "visa_timeout", "out_of_sync", "deadline", "data_timeout",
])
def test_trace_worker_cancel_stop_and_recovery(scenario, tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer

    from pyvisa.constants import StatusCode
    from pyvisa.errors import VisaIOError

    from electronicload_tool_cli import worker as worker_module
    from electronicload_tool_cli.worker_client import request as client_request
    from electronicload_tool_core import trace
    from electronicload_tool_core.simulator import ControlSimulator

    armed = threading.Event()
    operations = []
    outcome = queue.Queue()
    worker_threads = set()
    now = 0
    if scenario == "deadline":
        monkeypatch.setattr(trace, "monotonic", lambda: now)

    class HoldingVISA(ControlSimulator):
        def write(self, command):
            nonlocal now
            operations.append(command)
            worker_threads.add(threading.get_ident())
            if command == "TRAC:FEED:CONT NEVER" and self.trace_feed == "NEXT":
                if scenario in {"feed_still_next", "deadline"}:
                    return
                if scenario == "visa_timeout":
                    raise VisaIOError(StatusCode.error_timeout)
                if scenario == "out_of_sync":
                    self.trace_feed = "wrong response"
                    return
            super().write(command)
            if command == "TRIG":
                if scenario != "data_timeout":
                    self.trace_capturing = False
                if scenario == "deadline":
                    now = 103680
                armed.set()

        def query(self, command):
            operations.append(command)
            worker_threads.add(threading.get_ident())
            if command == "TRAC:DATA?" and scenario == "data_timeout":
                raise VisaIOError(StatusCode.error_timeout)
            return super().query(command)

    runtime = worker_module.Runtime(mode="simulate")
    runtime.simulator = HoldingVISA()
    if scenario != "data_timeout":
        runtime.simulator.trace_points = 50
        runtime.simulator.trace_interval = "3600"
    runtime.simulator.input_on = True

    def emit(event, **values):
        if event == "job_finished":
            outcome.put(values)

    runtime.emit = emit
    with ThreadingHTTPServer((runtime.host, 0), worker_module._handler(runtime)) as server:
        runtime.server = server
        runtime.port = server.server_port
        urls = runtime.urls()
        serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        serving.start()

        def run(command, arguments=None):
            code, accepted = http(urls['command_url'], request(command, arguments))
            assert code == 202, accepted
            result = outcome.get(timeout=3)
            assert result['worker_job_id'] == accepted['worker_job_id']
            return result

        try:
            path = tmp_path / 'cancelled.csv'
            code, accepted = http(urls['command_url'], request('trace-start', {'csv': str(path)}))
            assert code == 202
            assert armed.wait(3)
            worker_job_id = accepted['worker_job_id']
            cancel = {'schema_version': 3, 'worker_job_id': worker_job_id, 'reason': 'test'}
            if scenario not in {'deadline', 'data_timeout'}:
                assert http(urls['command_url'], request('input-off'))[1]['error']['code'] == 'busy'
                assert http(urls['cancel_url'], {**cancel, 'reason': 3})[0] == 400
                assert http(urls['cancel_url'], {**cancel, 'worker_job_id': 'wrong'})[0] == 409
                assert client_request(urls['cancel_url'], '/cancel', (
                    '{"schema_version":3,"worker_job_id":"wrong","worker_job_id":"'
                    + worker_job_id + '"}').encode())[0] == 400
                assert outcome.empty()
                if scenario == 'stop':
                    assert http(urls['stop_url'], {'schema_version': 3})[0] == 202
                else:
                    assert http(urls['cancel_url'], cancel) == (202, {
                        'schema_version': 3, 'run_id': runtime.run_id, 'status': 'accepted',
                        'worker_job_id': worker_job_id, 'error': None,
                    })
            terminal = outcome.get(timeout=3)
            assert terminal['worker_job_id'] == worker_job_id and not terminal['ok']
            assert terminal['error']['details']['csv_incomplete'] is True
            assert len(path.read_text().splitlines()) == 1
            assert runtime.simulator.input_on
            assert operations.count('TRIG') == 1
            assert operations.count('TRAC:FEED:CONT NEVER') == int(scenario != 'data_timeout')
            assert operations.count('TRAC:DATA?') == int(scenario == 'data_timeout')
            assert 'TRAC:CLE' not in operations
            assert 'INP OFF' not in operations and 'INP ON' not in operations
            assert len(worker_threads) == 1  # Device I/O stays on the job thread.
            if scenario == 'stop':
                assert terminal['state'] == 'failed' and terminal['error']['code'] == 'trace_stopped'
                serving.join(timeout=3)
                assert not serving.is_alive()
                return

            observed_operations = operations.copy()
            _, status = http(urls['status_url'])
            assert status['active_job'] is None and status['last_job'] == terminal
            assert http(urls['cancel_url'], cancel)[0] == 409
            assert operations == observed_operations
            if scenario in {'visa_timeout', 'out_of_sync', 'data_timeout'}:
                assert terminal['state'] == 'failed'
                assert terminal['error']['code'] == 'trace_session_untrusted'
                if scenario == 'data_timeout':
                    assert operations[-1] == 'TRAC:DATA?'
                assert status['worker_state'] == 'failed' and status['fatal_error']
                assert status['trace_safety']['input_state'] == status['trace_safety']['feed'] == 'UNKNOWN'
                for command in ('input-off', 'input-state', 'measure', 'trace-start'):
                    code, rejection = http(urls['command_url'], request(command))
                    assert code == 409 and rejection['error']['code'] == 'trace_session_untrusted'
                assert http(urls['stop_url'], {'schema_version': 3})[0] == 202
                serving.join(timeout=3)
                assert not serving.is_alive() and operations == observed_operations
                assert runtime.simulator.input_on
                return

            configuration = {'source': 'CURR', 'points': 2, 'interval_s': 0.001}
            assert status['worker_state'] == 'running'
            if scenario in {'feed_still_next', 'deadline'}:
                assert terminal['state'] == 'failed'
                expected_error = ('trace_timeout' if scenario == 'deadline'
                                  else 'trace_cancel_feed_unconfirmed')
                assert terminal['error']['code'] == expected_error
                assert terminal['error']['details']['feed_stop_unconfirmed'] is True
                assert status['trace_safety']['state'] == 'restricted_recovery'
                for command, arguments in [('trace-start', {}), ('input-on', {'confirm_input_on': True}),
                                           ('measure', {}), ('trace-configure', configuration)]:
                    code, rejection = http(urls['command_url'], request(command, arguments))
                    assert code == 409 and rejection['error']['code'] == 'trace_recovery_required'
                assert operations == observed_operations
                assert run('input-state')['result']['input_state'] == 'ON'
                assert run('input-off')['result']['input_state'] == 'OFF'
                assert http(urls['command_url'], request('measure'))[0] == 409
                assert run('trace-configure', configuration)['state'] == 'failed'
                assert http(urls['status_url'])[1]['trace_safety']['input_state'] == 'OFF'
                runtime.simulator.trace_feed = 'NEVER'
                assert run('trace-configure', configuration)['ok']
                assert run('measure')['ok']
                assert http(urls['status_url'])[1]['trace_safety'] is None
            else:
                assert terminal['state'] == 'cancelled' and status['trace_safety'] is None
                assert run('input-off')['result']['input_state'] == 'OFF'
        finally:
            server.shutdown()
            serving.join(timeout=3)
            if runtime.job_thread is not None:
                runtime.job_thread.join(timeout=3)


@pytest.fixture
def trace_worker_with_session(monkeypatch):
    from electronicload_tool_cli import worker as worker_module

    events = queue.Queue()
    thread = None
    ready = None

    def launch(session):
        nonlocal thread, ready
        monkeypatch.setattr(worker_module, "ControlSimulator", lambda: session)

        def emit(runtime, event, **values):
            events.put({"event": event, "run_id": runtime.run_id, **values})

        monkeypatch.setattr(worker_module.Runtime, "emit", emit)
        thread = threading.Thread(target=worker_module.run_worker, daemon=True)
        thread.start()
        ready = events.get(timeout=5)
        assert ready["event"] == "ready"
        return ready, events

    yield launch
    if thread is not None and thread.is_alive():
        if ready is not None:
            try:
                http(ready["stop_url"], {"schema_version": 3})
            except OSError:
                pass  # The Worker may already be shutting down.
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("action", ["cancel", "stop"])
def test_trace_worker_observes_signal_before_next_precheck_io(action, trace_worker_with_session):
    from electronicload_tool_core.simulator import ControlSimulator

    in_identity = threading.Event()
    release = threading.Event()
    operations = []

    class BlockingIdentityVISA(ControlSimulator):
        def query(self, command):
            operations.append(command)
            if command == "*IDN?":
                in_identity.set()
                assert release.wait(timeout=5)
            return super().query(command)

        def write(self, command):
            operations.append(command)
            super().write(command)

    ready, events = trace_worker_with_session(BlockingIdentityVISA())
    code, accepted = http(ready["command_url"], request("trace-start"))
    assert code == 202
    try:
        assert in_identity.wait(timeout=3)
        signal = {"schema_version": 3}
        if action == "cancel":
            signal["worker_job_id"] = accepted["worker_job_id"]
        assert http(ready[f"{action}_url"], signal)[0] == 202
    finally:
        release.set()
    terminal = wait_finished(events, accepted["worker_job_id"])
    assert terminal["state"] == ("cancelled" if action == "cancel" else "failed")
    assert operations == ["*IDN?"]


def test_trace_worker_failed_configure_feed_stop_limits_admission_and_reports_shutdown(
        trace_worker_with_session):
    from electronicload_tool_core.simulator import ControlSimulator

    operations = []

    class StuckFeedVISA(ControlSimulator):
        def write(self, command):
            operations.append(command)
            if command != "TRAC:FEED:CONT NEVER":
                super().write(command)

        def query(self, command):
            operations.append(command)
            return super().query(command)

    session = StuckFeedVISA()
    session.trace_feed = "NEXT"
    ready, events = trace_worker_with_session(session)
    config = {"source": "TWO", "points": 4, "interval_s": 1}
    code, accepted = http(ready["command_url"], request("trace-configure", config))
    assert code == 202
    terminal = wait_finished(events, accepted["worker_job_id"])
    assert terminal["state"] == "failed"
    assert terminal["error"]["code"] == "trace_feed_active"
    assert terminal["error"]["details"]["feed_stop_unconfirmed"] is True
    assert operations[-2:] == ["TRAC:FEED:CONT NEVER", "TRAC:FEED:CONT?"]
    prior_io = operations.copy()
    _, status = http(ready["status_url"])
    assert status["worker_state"] == "running"
    assert status["trace_safety"]["input_state"] == "UNKNOWN"
    assert status["trace_safety"]["feed"] == "NEXT"
    assert http(ready["command_url"], request("measure"))[1]["error"]["code"] == "trace_recovery_required"
    assert http(ready["command_url"], sequence_request(SEQUENCE_READ))[1]["error"]["code"] == (
        "trace_recovery_required")
    assert http(ready["stop_url"], {"schema_version": 3})[0] == 202
    summary = events.get(timeout=3)
    assert summary["event"] == "summary" and summary["ok"] and summary["exit_code"] == 0
    assert summary["trace_safety"] == {**status["trace_safety"], "safe_shutdown_unconfirmed": True}
    assert operations == prior_io


def test_configuration_readback_worker_uses_session_and_async_contract(worker, capsys):
    from electronicload_tool_cli.cli import main
    from electronicload_tool_cli.worker import Runtime

    _, out, ready = worker
    url = ready["command_url"]
    status_fields = http(ready["status_url"])[1].keys()

    def job(command, arguments=None, *, mode="simulate"):
        payload = request(command, arguments)
        payload["context"]["mode"] = mode
        status, accepted = http(url, payload)
        assert status == 202 and accepted["kind"] == "async_job"
        return wait_finished(out, accepted["worker_job_id"])

    status, refused = http(url, request("fixed-status", {"hardware_validation": "readback"}))
    assert status == 400 and refused["worker_job_id"] is None

    job("set-cc", {"current": 0.25, "range": "low", "rise_slew": 0.1, "fall_slew": 0.2})
    assert job("readback")["result"]["configuration"]["current_a"] == 0.25
    assert job("fixed-slew-status")["result"]["fall_slew_a_per_us"] == "0.2"
    assert job("fixed-status", mode="dry_run")["result"]["planned_writes"] == []
    job("trace-configure", {"source": "VOLT", "points": 8, "interval_s": 0.125})
    assert job("trace-status")["result"]["points"] == 8
    job("set-transient-cc", {"transient_mode": "pulse", "range": "low", "level_a": 0.25,
                             "level_b": 0.5, "rise_slew": 0.1, "fall_slew": 0.2, "pulse_width": 0.001})
    fixed = job("fixed-status")["result"]
    assert fixed["function_mode"] == "FIX" and fixed["active_mode"] == "fixed"
    assert fixed["current_a"] == 0.25 and fixed["transient_enabled"] is False
    assert job("transient-status")["result"]["configured"] is True
    job("transient-enable")
    assert job("fixed-status")["error"]["code"] == "mode_not_fixed"
    summary = job("readback")["result"]
    assert summary["active_mode"] == "transient"
    assert summary["configuration"] == {"transient_mode": "pulse", "range": "low",
                                        "level_a_a": 0.25, "level_b_a": 0.5}
    job("transient-disable")
    assert job("fixed-slew-status")["result"]["active_mode"] == "fixed"
    job("set-list-cc", {"range": "low", "repeat_count": 1, "steps": [
        {"current_a": 0.25, "slew_a_per_us": 0.1, "width_s": 0.001},
        {"current_a": 0.5, "slew_a_per_us": 0.1, "width_s": 0.001},
    ]})
    assert job("readback")["result"]["configuration"]["step_count"] == 2
    assert job("fixed-status")["error"]["code"] == "mode_not_fixed"
    assert http(ready["status_url"])[1].keys() == status_fields

    assert main(["manifest", "--json"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    runtime = Runtime(mode="live", resource="USB0::0xFFFF::0x0001::SYNTHETIC::INSTR")
    for command in ("readback", "fixed-status", "fixed-slew-status", "trace-status"):
        assert manifest["commands"][command] == {"kind": "async_job"}
        runtime.run_job({"worker_job_id": command, "job_id": None, "command": command}, {}, {"mode": "live"})
        result = json.loads(capsys.readouterr().out.splitlines()[-1])
        assert result["error"]["code"] == "unsupported_live_scope"


def test_sequence_worker_admission_dry_run_and_simulator_state(worker):
    _, events, ready = worker
    endpoint = ready["command_url"]
    for payload, status, expected in [
        (sequence_request(SEQUENCE_ON, SEQUENCE_OFF), 400, "sequence_precheck_failed"),
        (sequence_request(SEQUENCE_SET, SEQUENCE_ON, SEQUENCE_OFF), 409, "confirmation_required"),
        (sequence_request(SEQUENCE_READ, confirm_input_on=1), 400, "argument_error"),
        (sequence_request(SEQUENCE_READ, file="sequence.json"), 400, "argument_error"),
    ]:
        code, rejected = http(endpoint, payload)
        assert code == status and rejected["error"]["code"] == expected
        assert rejected["worker_job_id"] is None
        if expected == "sequence_precheck_failed":
            assert rejected["error"]["details"]["step_index"] == 1
    assert http(ready["status_url"])[1]["last_job"] is None

    payload = sequence_request(SEQUENCE_SET, SEQUENCE_ON, SEQUENCE_OFF)
    preview = {**payload, "context": {"mode": "dry_run", "planning_model_id": "itech-it8812c"}}
    code, job = http(endpoint, preview)
    assert code == 202
    terminal = wait_finished(events, job["worker_job_id"])
    assert terminal["result"]["input_on_requires_confirmation"]

    code, job = http(endpoint, sequence_request(
        SEQUENCE_SET, SEQUENCE_ON, ("measure", {}), SEQUENCE_OFF, SEQUENCE_READ, confirm_input_on=True))
    assert code == 202
    terminal = wait_finished(events, job["worker_job_id"])
    assert terminal["state"] == "succeeded" and terminal["result"]["completed_steps"] == 5
    assert terminal["result"]["measurements"][0]["current_a"] == 0.25
    assert terminal["result"]["input_state"] == "OFF"
    assert http(ready["status_url"])[1]["last_job"]["result"] == terminal["result"]
    code, job = http(endpoint, request("fixed-status"))
    assert code == 202
    assert wait_finished(events, job["worker_job_id"])["result"]["current_a"] == 0.25


def test_sequence_worker_cli_forwarding_preserves_nested_decimal(worker, capsys):
    from electronicload_tool_cli.cli import main

    _, _, ready = worker
    payload = sequence_request(("set-cw", {"power": 250}))
    arguments = json.dumps(payload["arguments"]).replace("250", "250.0000000000000001")
    assert main(["send-command", "--url", ready["command_url"], "--worker-command", "sequence",
                 "--arguments-json", arguments, "--context-json", json.dumps(payload["context"])]) == 2
    response = json.loads(capsys.readouterr().out)
    assert response["schema_version"] == 3 and response["worker_job_id"] is None
    assert response["error"]["details"]["reason"] == "power_out_of_range"


def test_sequence_worker_stop_fails_and_reports_partial_results(trace_worker_with_session):
    from electronicload_tool_core.simulator import ControlSimulator

    measured = threading.Event()

    class MeasuredSimulator(ControlSimulator):
        def query(self, command):
            value = super().query(command)
            if command == "FETC:POW?":
                measured.set()
            return value

    simulator = MeasuredSimulator()
    ready, events = trace_worker_with_session(simulator)
    code, accepted = http(ready["command_url"], sequence_request(
        SEQUENCE_SET, SEQUENCE_ON, ("measure", {}), ("wait", {"seconds": 3600}),
        SEQUENCE_OFF, confirm_input_on=True))
    assert code == 202 and measured.wait(3)
    status = http(ready["status_url"])[1]
    assert status["active_job"]["worker_job_id"] == accepted["worker_job_id"]
    assert http(ready["command_url"], sequence_request(SEQUENCE_READ))[0] == 409
    assert http(ready["cancel_url"], {"schema_version": 3,
                                    "worker_job_id": accepted["worker_job_id"]})[0] == 409
    assert http(ready["stop_url"], {"schema_version": 3})[0] == 202
    terminal = wait_finished(events, accepted["worker_job_id"])
    assert terminal["state"] == "failed" and terminal["result"] is None
    assert terminal["error"]["code"] == "stopping"
    details = terminal["error"]["details"]
    assert details["cleanup"] == {"required": True, "attempted": True, "off_confirmed": True}
    assert details["measurements"][0]["step_index"] == 3
    assert details["failed_step"]["action"] == "wait" and not simulator.input_on


def test_workflow_worker_common_jobs_validation_and_simulator_state(worker, capsys, tmp_path):
    from electronicload_tool_cli.cli import main

    _, events, ready = worker
    arguments = {
        "measure-log": {"interval_s": 0.01, "count": 2},
        "measure-until": {"interval_s": 0.01, "metric": "power", "operator": "ge",
                          "threshold": 0, "timeout_s": 1},
        "cc-timed-load": {"current": 0.5, "duration_s": 0.001, "confirm_input_on": True},
        "cc-discharge": {"current": 0.5, "cutoff_voltage": 12, "timeout_s": 1,
                         "confirm_input_on": True},
    }
    assert main(["manifest", "--json"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert all(manifest["commands"][command] == {"kind": "async_job"} for command in arguments)
    assert manifest["features"]["common.job_cancel"]["commands"] == ["trace-start"]

    def call(command, values, mode="simulate"):
        payload = request(command, values)
        payload["context"]["mode"] = mode
        payload["job_id"] = "workflow-check"
        code, response = http(ready["command_url"], payload)
        assert code == 202 and response["kind"] == "async_job"
        terminal = wait_finished(events, response["worker_job_id"])
        assert terminal["job_id"] == "workflow-check" and terminal["state"] == "succeeded"
        assert http(ready["status_url"])[1]["last_job"]["result"] == terminal["result"]
        return terminal["result"]

    code, rejection = http(ready["command_url"], request(
        "cc-timed-load", {"current": 0.5, "duration_s": 0.01}))
    assert code == 409 and rejection["error"]["code"] == "confirmation_required"
    assert rejection["worker_job_id"] is None

    context = request("cc-timed-load")["context"]
    assert main(["send-command", "--url", ready["command_url"], "--worker-command", "cc-timed-load",
                 "--arguments-json",
                 '{"current":0.5000000000000000001,"range":"low","duration_s":1,"confirm_input_on":true}',
                 "--context-json", json.dumps(context)]) == 2
    rejection = json.loads(capsys.readouterr().out)
    assert rejection["error"]["code"] == "invalid_current_resolution"

    path = tmp_path / "samples.csv"
    preview_args = {**arguments["cc-discharge"], "csv": str(path)}
    preview_args.pop("confirm_input_on")
    preview = call("cc-discharge", preview_args, mode="dry_run")
    assert preview["csv_requested"] and not path.exists()
    assert call("input-state", {})["input_state"] == "OFF"

    assert call("measure-log", arguments["measure-log"])["sample_count"] == 2
    assert call("measure-until", arguments["measure-until"])["stop_reason"] == "condition_met"
    assert call("cc-timed-load", arguments["cc-timed-load"])["stop_reason"] == "duration_elapsed"
    assert call("cc-discharge", {**arguments["cc-discharge"], "csv": str(path)})["csv_written"]
    assert path.is_file()
    assert call("input-state", {})["input_state"] == "OFF"


def test_workflow_worker_stop_waits_for_off_confirmation(trace_worker_with_session):
    from electronicload_tool_core.simulator import ControlSimulator

    on_confirmed = threading.Event()

    class ActiveLoad(ControlSimulator):
        def query(self, command):
            reply = super().query(command)
            if command == "INP:STAT?" and reply == "1":
                on_confirmed.set()
            return reply

    load = ActiveLoad()
    ready, events = trace_worker_with_session(load)
    code, accepted = http(ready["command_url"], request(
        "cc-timed-load", {"current": 0.5, "duration_s": 300, "confirm_input_on": True}))
    assert code == 202 and on_confirmed.wait(3)
    assert http(ready["command_url"], request("measure"))[0] == 409
    assert http(ready["cancel_url"], {"schema_version": 3,
                                    "worker_job_id": accepted["worker_job_id"]})[0] == 409
    assert http(ready["stop_url"], {"schema_version": 3})[0] == 202
    terminal = wait_finished(events, accepted["worker_job_id"])
    assert terminal["state"] == "failed" and terminal["result"] is None
    assert terminal["error"]["code"] == "stopping"
    details = terminal["error"]["details"]
    assert details["stop_reason"] == "stopped" and details["input_state"] == "OFF"
    assert details["cleanup"] == {"required": True, "attempted": True, "off_confirmed": True}
    assert not load.input_on
    summary = events.get(timeout=5)
    assert summary["event"] == "summary" and summary["last_job"]["error"] == terminal["error"]


def test_workflow_worker_valid_live_jobs_fail_before_fake_visa(capsys, fake_visa):
    from electronicload_tool_cli.worker import Runtime

    runtime = Runtime(mode="live", resource="USB0::0xFFFF::0x0001::SYNTHETIC::INSTR",
                      allow_input_on=True)
    for command, arguments in (
        ("measure-log", {"interval_s": 1, "count": 1}),
        ("cc-discharge", {"current": 0.5, "cutoff_voltage": 3, "timeout_s": 5,
                          "confirm_input_on": True}),
    ):
        job = {"command": command, "worker_job_id": command, "job_id": None}
        runtime.run_job(job, arguments, {"mode": "live"})
        terminal = runtime.snapshot()["last_job"]
        assert terminal["state"] == "failed" and terminal["error"]["code"] == "unsupported_live_scope"
    terminal_events = [event for line in capsys.readouterr().out.splitlines()
                       if (event := json.loads(line))["event"] == "job_finished"]
    assert len(terminal_events) == 2 and not fake_visa.calls
