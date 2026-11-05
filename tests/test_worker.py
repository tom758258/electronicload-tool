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
    for payload in [request('unknown'),request('set-cc',{'current':9,'range':'low'}),
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
