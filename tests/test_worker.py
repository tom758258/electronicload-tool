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
    invalid = [request('set-list-cc', {**list_arguments(), **changes}) for changes in (
        {'repeat_count': True}, {'repeat_count': '65536'}, {'repeat_count': 1.0},
        {'repeat_count': 65537}, {'range': []}, {'steps': []}, {'file': 'client.json'},
        {'steps': [{'current_a': '1', 'slew_a_per_us': .1, 'width_s': 1}] * 2},
        {'steps': [{'current_a': 1, 'width_s': 1}] * 2},
        {'steps': [{'current_a': 1, 'slew_a_per_us': .1, 'width_s': 1, 'extra': 2}] * 2},
    )]
    invalid += [request(command, {'slot': slot}) for command in ('list-save', 'list-recall')
                for slot in (None, 0, 8, True, '1', 1.0)]
    invalid += [request('list-trigger', {'wait_complete': True}),
                request('list-status', {'file': 'client.json'}), request('set-list-cc')]
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
def test_transient_new_worker_admission_and_precision(worker, function, capsys):
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
