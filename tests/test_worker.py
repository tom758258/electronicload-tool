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
    assert http(stop_url,{'schema_version':3})[0]==202
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


def test_live_worker_rejects_explicit_empty_safety_config():
    from electronicload_tool_cli.worker import run_worker
    from electronicload_tool_core.errors import LoadError

    with pytest.raises(LoadError) as error:
        run_worker(mode='live',
                   resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                   safety_config='')
    assert error.value.code == 'missing_safety_config'


def test_live_worker_optional_safety_config_admission_and_authorization(monkeypatch):
    from http.server import ThreadingHTTPServer
    from electronicload_tool_cli import worker as worker_module
    from electronicload_tool_core.safety import SafetyLimits

    calls = []

    def fake_control(command, **kwargs):
        calls.append((command, kwargs))
        return {'input_state': 'ON' if command == 'input-on' else 'OFF'}

    monkeypatch.setattr(worker_module, 'control_execute', fake_control)
    runtime = worker_module.Runtime(mode='live',
                                    resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                                    limits=None, allow_input_on=True)
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
        assert calls[-1][1]['limits'] is None

        runtime.limits = SafetyLimits(0.05)
        assert http(command_url, live_request('set-cc',
                    {'current': 0.1, 'range': 'low'}))[0] == 400
        runtime.limits = None
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
        assert calls[-1][0] == 'input-on' and calls[-1][1]['limits'] is None
        assert calls[-1][1]['confirmed'] is True
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
