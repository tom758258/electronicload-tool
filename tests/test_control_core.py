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
        self.remote = False
        self.scpi_error = '0,"No error"'

    def query(self, command):
        self.calls.append(command)
        if command in self.response_errors:
            raise self.response_errors[command]
        result = {'*IDN?': 'ITECH Ltd,IT8812C,SYNTHETIC,1.0',
                  'INP:STAT?': str(int(self.input)), 'FUNC:MODE?': self.mode,
                  'FUNC?': self.function, 'CURR:RANG?': str(self.range),
                  'CURR?': str(self.current), 'TRAN?': str(int(self.transient)),
                  'INP:SHOR:STAT?': str(int(self.short)),
                  'SYST:ERR?': self.scpi_error}
        value = result[command]
        if command == 'SYST:ERR?':
            self.scpi_error = '0,"No error"'
        return value

    def write(self, command):
        self.calls.append(command)
        if command == self.write_fail:
            raise TimeoutError('PRIVATE-RESOURCE')
        if command == 'SYST:REM':
            self.remote = True
            return
        if not self.remote:
            return
        if command == 'FUNC:MODE FIX':
            self.mode='FIX'
        if command == 'FUNC CURR':
            self.function='CURR'
        if command.startswith('CURR:RANG '):
            selector = command.split(' ', 1)[1]
            self.range = (0.0 if selector == 'MIN' else 60.0 if selector == 'MAX'
                          else float(selector))
            self.current = min(self.current, self.range)
        if command.startswith('CURR '):
            value = float(command.split(' ')[1])
            if value > self.range:
                self.scpi_error = '120,"Parameter overflowed"'
            else:
                self.current = value
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
    assert fake.calls==['*IDN?','INP:STAT?','SYST:REM',
                        'INP:STAT?','FUNC:MODE FIX',
                        'INP:STAT?','FUNC CURR','INP:STAT?','CURR:RANG 1.5',
                        'INP:STAT?','CURR 1.5','FUNC:MODE?','FUNC?','CURR:RANG?','CURR?',
                        'INP:STAT?']
    assert not fake.input


def test_setting_failure_does_not_rollback_or_off(monkeypatch):
    fake=Fake()
    session(fake,monkeypatch)
    fake.write_fail='CURR:RANG 1.5'
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
            if count==3:
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
    assert fake.calls==['*IDN?','SYST:REM','INP OFF','INP:STAT?']


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


def test_remote_required_for_local_hardware_cc(monkeypatch):
    fake = Fake()
    session(fake, monkeypatch)
    # The hardware mock ignores configuration writes until remote mode.
    fake.write('CURR 0.1')
    assert fake.current == 0
    fake.calls.clear()
    result = live('set-cc', limits=SafetyLimits(0.5), current=0.1, range_name='low')
    assert result['range'] == 'low'
    assert result['range_readback_a'] == 0.1
    assert result['current_a'] == 0.1
    assert fake.calls.count('SYST:REM') == 1
    assert fake.remote is True
    assert fake.calls[-1] == 'INP:STAT?'


def test_setting_not_applied_never_passes_even_with_zero_range_readback(monkeypatch):
    fake = Fake()
    fake.range = 0.0
    session(fake, monkeypatch)
    # The remote command has no effect on this mock (failed remote control).
    def ignored_remote(command):
        fake.calls.append(command)
    fake.write = ignored_remote
    with pytest.raises(LoadError) as error:
        live('set-cc', limits=SafetyLimits(0.5), current=0.1, range_name='low')
    assert error.value.code == 'readback_mismatch'
    assert error.value.details['observed']['current_a'] == 0.0
    assert error.value.details['completed_steps'] == ['fixed_mode', 'cc_mode', 'range', 'current']
    assert 'INP ON' not in fake.calls


def test_unknown_range_fails_closed_with_safe_numeric_diagnostics(monkeypatch):
    fake = Fake()
    fake.range = 61.0
    fake.current = 0.1
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        live('input-on', limits=SafetyLimits(0.5), confirmed=True)
    assert error.value.code == 'invalid_range_readback'
    assert error.value.details == {'range_readback_a': 61.0, 'current_readback_a': 0.1}
    assert 'SYST:REM' not in fake.calls
    assert 'INP ON' not in fake.calls


def test_input_on_rechecks_changed_state_after_remote(monkeypatch):
    fake = Fake()
    fake.range = 0.0
    fake.current = 0.1
    original_write = fake.write

    def remote_changes_mode(command):
        original_write(command)
        if command == 'SYST:REM':
            fake.transient = True

    fake.write = remote_changes_mode
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        live('input-on', limits=SafetyLimits(0.5), confirmed=True)
    assert error.value.code == 'unsafe_mode'
    assert 'SYST:REM' in fake.calls
    assert 'INP ON' not in fake.calls


def test_worker_stop_after_remote_blocks_input_on(monkeypatch):
    fake = Fake()
    fake.range = 0.0
    fake.current = 0.1
    stop = Event()
    original_write = fake.write

    def remote_requests_stop(command):
        original_write(command)
        if command == 'SYST:REM':
            stop.set()

    fake.write = remote_requests_stop
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        live('input-on', limits=SafetyLimits(0.5), confirmed=True, stop=stop)
    assert error.value.code == 'stopping'
    assert 'INP ON' not in fake.calls


def test_remote_failure_does_not_attempt_cc_or_input_on(monkeypatch):
    fake = Fake()
    fake.write_fail = 'SYST:REM'
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        live('set-cc', limits=SafetyLimits(0.5), current=0.1, range_name='low')
    assert error.value.code == 'write_failed'
    assert error.value.details['completed_steps'] == []
    assert fake.calls == ['*IDN?', 'INP:STAT?', 'SYST:REM']
    assert not fake.input


def test_range_mapping_uses_programmed_range_value_not_only_ceiling(monkeypatch):
    for raw, name in ((0, 'low'), (6, 'low'), (6.0001, 'high'), (60, 'high')):
        fake = Fake()
        fake.current = 0.1
        fake.range = raw
        session(fake, monkeypatch)
        result = live('input-on', limits=SafetyLimits(0.5), confirmed=True)
        assert result['range'] == name
        assert result['range_readback_a'] == raw


def test_input_off_remote_transition_failure_reports_unknown(monkeypatch):
    fake = Fake()
    fake.input = True
    fake.write_fail = 'SYST:REM'
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        live('input-off')
    assert error.value.code == 'write_failed'
    assert error.value.details['input_state'] == 'UNKNOWN'
    assert fake.calls == ['*IDN?', 'SYST:REM']
    assert fake.input is True


def test_offline_control_never_requires_or_opens_remote_hardware(monkeypatch):
    def forbidden_open(_resource):
        raise AssertionError('Offline request accessed a live VISA session')

    monkeypatch.setattr(control, 'open_session', forbidden_open)
    assert control.execute('set-cc', mode='dry-run', current=0.1, range_name='low')[
        'hardware_access'
    ] is False
    assert control.execute('set-cc', mode='simulate', current=0.1,
                           range_name='low')['current_a'] == 0.1


def test_numeric_low_range_fix_reproduces_real_error_120_and_recovers(monkeypatch):
    fake = Fake()
    fake.write('SYST:REM')
    fake.write('CURR:RANG MIN')
    fake.write('CURR 0.1')
    assert fake.current == 0.0
    assert fake.query('SYST:ERR?') == '120,"Parameter overflowed"'

    # The bounded numeric range selector was separately verified on IT8812C.
    fake.calls.clear()
    session(fake, monkeypatch)
    result = live('set-cc', limits=SafetyLimits(0.5), current=0.1, range_name='low')
    assert result['input_state'] == 'OFF'
    assert result['range_readback_a'] == 0.1
    assert result['current_a'] == 0.1
    assert 'CURR:RANG 0.1' in fake.calls
    assert 'CURR:RANG MIN' not in fake.calls
    assert 'INP ON' not in fake.calls


def test_high_range_still_uses_existing_max_command(monkeypatch):
    fake = Fake()
    session(fake, monkeypatch)
    result = live('set-cc', limits=SafetyLimits(2), current=1.5, range_name='high')
    assert result['range'] == 'high'
    assert result['range_readback_a'] == 60.0
    assert result['current_a'] == 1.5
    assert 'CURR:RANG MAX' in fake.calls
