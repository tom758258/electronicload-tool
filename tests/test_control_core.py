from contextlib import contextmanager
from threading import Event

import pytest

from electronicload_tool_core import control
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.safety import SafetyLimits

RESOURCE = 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR'


class Fake:
    def __init__(self):
        self.calls = []
        self.input = False
        self.mode = 'FIX'
        self.function = 'CURR'
        self.range = 60.0
        self.current = 0.0
        self.transient = False
        self.short = False
        self.response_errors = {}
        self.write_fail = None

    def query(self, command):
        self.calls.append(command)
        if command in self.response_errors:
            raise self.response_errors[command]
        result = {'*IDN?': 'ITECH Ltd,IT8812C,SYNTHETIC,1.0',
                  'INP:STAT?': str(int(self.input)), 'FUNC:MODE?': self.mode,
                  'FUNC?': self.function, 'CURR:RANG?': str(self.range),
                  'CURR?': str(self.current), 'TRAN?': str(int(self.transient)),
                  'INP:SHOR:STAT?': str(int(self.short))}
        return result[command]

    def write(self, command):
        self.calls.append(command)
        if command == self.write_fail:
            raise TimeoutError('PRIVATE-RESOURCE')
        if command == 'FUNC:MODE FIX':
            self.mode='FIX'
        if command == 'FUNC CURR':
            self.function='CURR'
        if command == 'CURR:RANG MIN':
            self.range=6.0
            self.current=min(self.current,6)
        if command == 'CURR:RANG MAX':
            self.range=60.0
        if command.startswith('CURR '):
            self.current=float(command.split(' ')[1])
        if command == 'INP ON':
            self.input=True
        if command == 'INP OFF':
            self.input=False


def session(fake, monkeypatch):
    @contextmanager
    def opener(_resource):
        yield fake, {'manufacturer': 'FAKE', 'library': 'FAKE'}
    monkeypatch.setattr(control, 'open_session', opener)


def live(command, **kw):
    return control.execute(command, resource=RESOURCE, validation=True, **kw)


def test_four_off_checks_and_four_readbacks(monkeypatch):
    fake=Fake()
    session(fake,monkeypatch)
    result=live('set-cc',limits=SafetyLimits(2),current=1.5,range_name='low')
    assert result['range']=='low' and result['current_a']==1.5
    assert fake.calls==['*IDN?','INP:STAT?','FUNC:MODE FIX',
                        'INP:STAT?','FUNC CURR','INP:STAT?','CURR:RANG MIN',
                        'INP:STAT?','CURR 1.5','FUNC:MODE?','FUNC?','CURR:RANG?','CURR?']
    assert not fake.input


def test_setting_failure_does_not_rollback_or_off(monkeypatch):
    fake=Fake()
    session(fake,monkeypatch)
    fake.write_fail='CURR:RANG MIN'
    with pytest.raises(LoadError) as err:
        live('set-cc',limits=SafetyLimits(2),current=1.5,range_name='low')
    assert err.value.code=='write_failed'
    assert err.value.details['completed_steps']==['fixed_mode','cc_mode']
    assert 'CURR 1.5' not in fake.calls and 'INP OFF' not in fake.calls


def test_cc_requires_off(monkeypatch):
    fake=Fake()
    fake.input=True
    session(fake,monkeypatch)
    with pytest.raises(LoadError,match='Input'):
        live('set-cc',limits=SafetyLimits(2),current=1.5,range_name='low')
    assert not any(x.startswith('FUNC ') for x in fake.calls)


@pytest.mark.parametrize('current,range_name,code',[
    (2.1,'low','safety_limit_exceeded'), (6.1,'low','current_out_of_range'),
    (1.2345,'high','invalid_current_resolution'), (float('nan'),'low','invalid_current'),
    (True,'low','invalid_current'), (1.0,'medium','invalid_range')])
def test_invalid_cc_no_writes(monkeypatch,current,range_name,code):
    fake=Fake()
    session(fake,monkeypatch)
    with pytest.raises(LoadError) as err:
        live('set-cc',limits=SafetyLimits(2),current=current,range_name=range_name)
    assert err.value.code==code
    assert fake.calls==[]


def test_live_controls_still_pending_product_acceptance():
    with pytest.raises(LoadError) as err:
        control.execute('set-cc',resource=RESOURCE,limits=SafetyLimits(2),current=1,range_name='low')
    assert err.value.code=='unsupported_live_scope'


def test_input_on_requires_fresh_safe_state_and_explicit_confirmation(monkeypatch):
    fake=Fake()
    fake.current=1.5
    fake.range=6
    session(fake,monkeypatch)
    assert live('input-on',limits=SafetyLimits(2),confirmed=True)['input_state']=='ON'
    assert fake.calls[-2:]==['INP ON','INP:STAT?']
    with pytest.raises(LoadError) as err:
        live('input-on',limits=SafetyLimits(2),confirmed=True)
    assert err.value.code=='already_on'
    assert fake.calls.count('INP ON')==1


@pytest.mark.parametrize('attribute,value',[('transient',True),('short',True),('mode','LIST'),
                                             ('function','VOLT'),('current',3),('input',True)])
def test_unsafe_state_rejected(monkeypatch,attribute,value):
    fake=Fake()
    fake.current=1
    fake.range=6
    setattr(fake,attribute,value)
    session(fake,monkeypatch)
    with pytest.raises(LoadError):
        live('input-on',limits=SafetyLimits(2),confirmed=True)
    assert 'INP ON' not in fake.calls


def test_stop_prevents_unsent_on(monkeypatch):
    fake=Fake()
    fake.range=6
    fake.current=1
    session(fake,monkeypatch)
    stop=Event()
    stop.set()
    with pytest.raises(LoadError) as err:
        live('input-on',limits=SafetyLimits(2),confirmed=True,stop=stop)
    assert err.value.code=='stopping'
    assert 'INP ON' not in fake.calls


def test_input_on_readback_failure_reports_unknown_no_rescue(monkeypatch):
    fake=Fake()
    fake.range=6
    fake.current=1
    count=0
    original=fake.query
    def query(command):
        nonlocal count
        if command=='INP:STAT?':
            count+=1
            if count==2:
                raise TimeoutError('PRIVATE-RESOURCE')
        return original(command)
    fake.query=query
    session(fake,monkeypatch)
    with pytest.raises(LoadError) as err:
        live('input-on',limits=SafetyLimits(2),confirmed=True)
    assert err.value.details['input_state']=='UNKNOWN'
    assert err.value.details['input_on_attempted'] is True
    assert 'INP OFF' not in fake.calls


def test_input_off_no_safety_and_verified(monkeypatch):
    fake=Fake()
    fake.input=True
    session(fake,monkeypatch)
    assert live('input-off')['input_state']=='OFF'
    assert fake.calls==['*IDN?','INP OFF','INP:STAT?']


def test_input_on_write_timeout_is_unknown(monkeypatch):
    fake=Fake()
    fake.range=6
    fake.current=1
    fake.write_fail='INP ON'
    session(fake,monkeypatch)
    with pytest.raises(LoadError) as err:
        live('input-on',limits=SafetyLimits(2),confirmed=True)
    assert err.value.details['input_on_attempted'] is True
    assert err.value.details['input_state']=='UNKNOWN'
    assert fake.calls.count('INP ON') == 1 and 'INP OFF' not in fake.calls


def test_input_off_write_timeout_is_unknown(monkeypatch):
    fake=Fake()
    fake.input=True
    fake.write_fail='INP OFF'
    session(fake,monkeypatch)
    with pytest.raises(LoadError) as err:
        live('input-off')
    assert err.value.details['input_state']=='UNKNOWN'
