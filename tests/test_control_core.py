from contextlib import contextmanager
from decimal import Decimal
from threading import Event

import pytest

from electronicload_tool_core import control
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.simulator import ControlSimulator

RESOURCE = 'USB0::0xFFFF::0x0001::SYNTHETIC::INSTR'


@pytest.mark.parametrize('mode', ['simulate', 'dry-run'])
@pytest.mark.parametrize('command,field,value,expected', [
    ('set-cc', 'current', '0', 'low'),
    ('set-cc', 'current', '0.0001', 'low'),
    ('set-cc', 'current', '2.5000', 'low'),
    ('set-cc', 'current', '6', 'low'),
    ('set-cc', 'current', '6.001', 'high'),
    ('set-cc', 'current', '60', 'high'),
    ('set-cv', 'voltage', '0.1', 'low'),
    ('set-cv', 'voltage', '12', 'low'),
    ('set-cv', 'voltage', '18', 'low'),
    ('set-cv', 'voltage', '18.01', 'high'),
    ('set-cv', 'voltage', '120', 'high'),
    ('set-cr', 'resistance', '0.05', 'low'),
    ('set-cr', 'resistance', '1.234567890123456789', 'low'),
    ('set-cr', 'resistance', '10', 'low'),
    ('set-cr', 'resistance', '10.000000000000000001', 'high'),
    ('set-cr', 'resistance', '7500', 'high'),
])
def test_fixed_auto_matches_explicit_range(mode, command, field, value, expected):
    automatic = control.execute(command, mode=mode, range_name='auto', **{field: value})
    manual = control.execute(command, mode=mode, range_name=expected, **{field: value})
    assert automatic == manual
    assert automatic['range'] == expected


@pytest.mark.parametrize('command,field,value,code', [
    ('set-cc', 'current', '-0.0001', 'no_matching_range'),
    ('set-cc', 'current', '6.0001', 'no_matching_range'),
    ('set-cc', 'current', '1.00000000000000000000000000000001', 'no_matching_range'),
    ('set-cc', 'current', '1e-1000000', 'no_matching_range'),
    ('set-cv', 'voltage', '0.099999999999999999', 'no_matching_range'),
    ('set-cv', 'voltage', '18.001', 'no_matching_range'),
    ('set-cv', 'voltage', '120.000000000000001', 'no_matching_range'),
    ('set-cr', 'resistance', '0.049999999999999999', 'no_matching_range'),
    ('set-cr', 'resistance', '7500.000000000000001', 'no_matching_range'),
    ('set-cc', 'current', True, 'invalid_current'),
    ('set-cv', 'voltage', float('inf'), 'invalid_voltage'),
    ('set-cr', 'resistance', float('nan'), 'invalid_resistance'),
    ('set-cr', 'resistance', [], 'invalid_resistance'),
])
def test_fixed_auto_refusal_preserves_state(command, field, value, code):
    sim = ControlSimulator()
    before = vars(sim).copy()
    with pytest.raises(LoadError) as error:
        control.execute(command, mode='simulate', simulator=sim,
                        range_name='auto', **{field: value})
    assert error.value.code == code
    if code == 'no_matching_range':
        assert set(error.value.details['ranges']) == {'low', 'high'}
    assert vars(sim) == before


@pytest.mark.parametrize('command,field,value', [
    ('set-cc', 'current', 2), ('set-cv', 'voltage', 12), ('set-cr', 'resistance', 10),
])
def test_fixed_auto_dry_run_and_on_setup_do_not_change_active_state(command, field, value):
    sim = ControlSimulator()
    control.execute(command, mode='simulate', simulator=sim, range_name='high', **{field: value})
    control.execute('input-on', mode='simulate', simulator=sim, confirmed=True)
    before = vars(sim).copy()
    plan = control.execute(command, mode='dry-run', simulator=sim,
                           range_name='auto', **{field: value})
    assert plan['range'] == 'low' and plan['hardware_access'] is False
    with pytest.raises(LoadError) as error:
        control.execute(command, mode='simulate', simulator=sim,
                        range_name='auto', **{field: value})
    assert error.value.code == 'input_not_off'
    assert vars(sim) == before


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
        self.input_transition_reads = 0
        self.pending_input = None

    def query(self, command):
        self.calls.append(command)
        if command in self.response_errors:
            raise self.response_errors[command]
        if command == 'INP:STAT?' and self.pending_input is not None:
            if self.input_transition_reads:
                self.input_transition_reads -= 1
            else:
                self.input = self.pending_input
                self.pending_input = None
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
        if command in ('INP ON', 'INP OFF'):
            value = command == 'INP ON'
            if self.input_transition_reads:
                self.pending_input = value
            else:
                self.input = value


def session(fake, monkeypatch):
    @contextmanager
    def opener(_resource):
        yield fake, {'manufacturer': 'FAKE', 'library': 'FAKE'}
    monkeypatch.setattr(control, 'open_session', opener)


def live(command, **kw):
    return control.execute(command, resource=RESOURCE, validation=True, **kw)


@pytest.mark.parametrize('selection', ['low', 'auto'])
def test_four_off_checks_and_four_readbacks(monkeypatch, selection):
    fake=Fake()
    session(fake,monkeypatch)
    result=live('set-cc',current=1.5,range_name=selection)
    assert result['range']=='low' and result['current_a']==1.5
    assert fake.calls==['*IDN?','INP:STAT?','SYST:REM',
                        'INP:STAT?','FUNC:MODE FIX',
                        'INP:STAT?','FUNC CURR','INP:STAT?','CURR:RANG 1.5',
                        'INP:STAT?','CURR 1.5','FUNC:MODE?','FUNC?','CURR:RANG?','CURR?',
                        'INP:STAT?']
    assert not fake.input


@pytest.mark.parametrize('current,range_name', [(0.1, 'low'), (7.0, 'high')])
def test_live_cc_without_extra_config_respects_model_range(monkeypatch, current, range_name):
    fake = Fake()
    session(fake, monkeypatch)
    result = live('set-cc', current=current, range_name=range_name)
    assert result['current_a'] == current
    assert result['range'] == range_name
    assert result['input_state'] == 'OFF'
    assert 'INP ON' not in fake.calls


@pytest.mark.parametrize('current,range_name', [(6.1, 'low'), (60.1, 'high')])
def test_live_cc_without_extra_config_rejects_out_of_range(monkeypatch, current, range_name):
    fake = Fake()
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        live('set-cc', current=current, range_name=range_name)
    assert error.value.code == 'current_out_of_range'
    assert fake.calls == []


def test_live_input_on_without_extra_config_still_checks_physical_current(monkeypatch):
    fake = Fake()
    fake.current = 0.1
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        live('input-on')
    assert error.value.code == 'confirmation_required'
    assert 'INP ON' not in fake.calls
    assert live('input-on', confirmed=True)['input_state'] == 'ON'
    assert fake.calls.count('INP ON') == 1

    fake.input = False
    fake.current = 61.0
    fake.calls.clear()
    with pytest.raises(LoadError) as error:
        live('input-on', confirmed=True)
    assert error.value.code == 'safety_limit_exceeded'
    assert 'INP ON' not in fake.calls


@pytest.mark.parametrize('selection', ['low', 'auto'])
def test_setting_failure_does_not_rollback_or_off(monkeypatch, selection):
    fake=Fake()
    session(fake,monkeypatch)
    fake.write_fail='CURR:RANG 1.5'
    with pytest.raises(LoadError) as err:
        live('set-cc',current=1.5,range_name=selection)
    assert err.value.code=='write_failed'
    assert err.value.details['completed_steps']==['fixed_mode','cc_mode']
    assert 'CURR 1.5' not in fake.calls and 'INP OFF' not in fake.calls


@pytest.mark.parametrize('selection', ['low', 'auto'])
def test_cc_requires_off(monkeypatch, selection):
    fake=Fake()
    fake.input=True
    session(fake,monkeypatch)
    with pytest.raises(LoadError,match='Input'):
        live('set-cc',current=1.5,range_name=selection)
    assert not any(x.startswith('FUNC ') for x in fake.calls)
    assert 'SYST:REM' not in fake.calls


@pytest.mark.parametrize('current,range_name,code',[
    (6.1,'low','current_out_of_range'),
    (1.2345,'high','invalid_current_resolution'), (float('nan'),'low','invalid_current'),
    (True,'low','invalid_current'), (1.0,'medium','invalid_range'),
    ('6.0001', 'auto', 'no_matching_range'),
    (65, 'auto', 'no_matching_range'),
    (float('nan'), 'auto', 'invalid_current')])
def test_invalid_cc_no_writes(monkeypatch,current,range_name,code):
    fake=Fake()
    session(fake,monkeypatch)
    with pytest.raises(LoadError) as err:
        live('set-cc',current=current,range_name=range_name)
    assert err.value.code==code
    assert fake.calls==[]


def test_live_control_product_scope_preserves_safety_and_input_confirmation(monkeypatch):
    fake = Fake()
    session(fake, monkeypatch)
    assert control.PRODUCT_LIVE_COMMANDS == frozenset({'set-cc', 'input-on', 'input-off'})

    result = control.execute('set-cc', resource=RESOURCE, current=0.1, range_name='low')
    assert result['input_state'] == 'OFF'
    assert 'INP ON' not in fake.calls

    with pytest.raises(LoadError) as err:
        control.execute('input-on', resource=RESOURCE)
    assert err.value.code == 'confirmation_required'
    assert 'INP ON' not in fake.calls

    assert control.execute('input-on', resource=RESOURCE, confirmed=True)['input_state'] == 'ON'
    assert control.execute('input-off', resource=RESOURCE)['input_state'] == 'OFF'
    assert fake.calls.count('INP ON') == 1
    assert fake.calls.count('INP OFF') == 1


@pytest.mark.parametrize('kwargs,code', [
    ({'model_id': 'unregistered'}, 'unsupported_model'),
    ({'backend': 'unsupported'}, 'unsupported_backend'),
    ({'resource': None}, 'missing_resource'),
])
def test_product_live_rejects_unregistered_connection_scopes(monkeypatch, kwargs, code):
    fake = Fake()
    session(fake, monkeypatch)
    options = {'resource': RESOURCE,
               'current': 0.1, 'range_name': 'low', **kwargs}
    with pytest.raises(LoadError) as err:
        control.execute('set-cc', **options)
    assert err.value.code == code
    assert fake.calls == []


def test_input_on_requires_fresh_safe_state_and_explicit_confirmation(monkeypatch):
    fake=Fake()
    fake.current=1.5
    fake.range=6
    session(fake,monkeypatch)
    assert live('input-on',confirmed=True)['input_state']=='ON'
    assert fake.calls[-2:]==['INP ON','INP:STAT?']
    with pytest.raises(LoadError) as err:
        live('input-on',confirmed=True)
    assert err.value.code=='already_on'
    assert fake.calls.count('INP ON')==1


@pytest.mark.parametrize('attribute,value',[('transient',True),('short',True),('mode','LIST'),
                                             ('function','VOLT'),('current',7),('input',True)])
def test_unsafe_state_rejected(monkeypatch,attribute,value):
    fake=Fake()
    fake.current=1
    fake.range=6
    setattr(fake,attribute,value)
    session(fake,monkeypatch)
    with pytest.raises(LoadError):
        live('input-on',confirmed=True)
    assert 'INP ON' not in fake.calls


def test_stop_prevents_unsent_on(monkeypatch):
    fake=Fake()
    fake.range=6
    fake.current=1
    session(fake,monkeypatch)
    stop=Event()
    stop.set()
    with pytest.raises(LoadError) as err:
        live('input-on',confirmed=True,stop=stop)
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
        live('input-on',confirmed=True)
    assert err.value.details['input_state']=='UNKNOWN'
    assert err.value.details['input_on_attempted'] is True
    assert 'INP OFF' not in fake.calls


def test_input_off_no_safety_and_verified(monkeypatch):
    fake=Fake()
    fake.input=True
    session(fake,monkeypatch)
    assert live('input-off')['input_state']=='OFF'
    assert fake.calls==['*IDN?','SYST:REM','INP OFF','INP:STAT?']


def fake_time(monkeypatch):
    elapsed = [0.0]
    sleeps = []
    monkeypatch.setattr(control, 'monotonic', lambda: elapsed[0])

    def advance(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds

    monkeypatch.setattr(control, 'sleep', advance)
    return elapsed, sleeps


@pytest.mark.parametrize('command,initial,expected', [
    ('input-on', False, 'ON'), ('input-off', True, 'OFF'),
])
def test_delayed_input_confirmation_does_not_repeat_write(monkeypatch, command, initial, expected):
    fake = Fake()
    fake.input = initial
    fake.range = 6
    fake.current = 0.1
    fake.input_transition_reads = 3
    clock, sleeps = fake_time(monkeypatch)
    session(fake, monkeypatch)

    kw = {'confirmed': True} if command == 'input-on' else {}
    assert live(command, **kw)['input_state'] == expected
    assert fake.calls.count('INP ON' if command == 'input-on' else 'INP OFF') == 1
    assert fake.pending_input is None
    assert len(sleeps) == 3
    assert clock[0] == pytest.approx(0.3)


@pytest.mark.parametrize('command,initial,error_code', [
    ('input-on', False, 'input_on_not_confirmed'),
    ('input-off', True, 'input_off_not_confirmed'),
])
def test_input_confirmation_timeout_fails_closed(monkeypatch, command, initial, error_code):
    fake = Fake()
    fake.input = initial
    fake.range = 6
    fake.current = 0.1
    fake.input_transition_reads = 1000
    clock, sleeps = fake_time(monkeypatch)
    session(fake, monkeypatch)

    kw = {'confirmed': True} if command == 'input-on' else {}
    with pytest.raises(LoadError) as err:
        live(command, **kw)
    assert err.value.code == error_code
    assert err.value.details['input_state'] == 'UNKNOWN'
    if command == 'input-on':
        assert err.value.details['input_on_attempted'] is True
    assert fake.calls.count('INP ON' if command == 'input-on' else 'INP OFF') == 1
    assert fake.calls.count('INP OFF') == (1 if command == 'input-off' else 0)
    assert clock[0] == pytest.approx(control.INPUT_STATE_CONFIRM_TIMEOUT_S)
    assert sleeps and max(sleeps) <= control.INPUT_STATE_POLL_INTERVAL_S


def test_input_off_query_failure_does_not_assume_off(monkeypatch):
    fake = Fake()
    fake.input = True
    fake.response_errors['INP:STAT?'] = TimeoutError('PRIVATE-RESOURCE')
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as err:
        live('input-off')
    assert err.value.code == 'query_failed'
    assert err.value.details['input_state'] == 'UNKNOWN'
    assert fake.calls.count('INP OFF') == 1


def test_input_on_write_timeout_is_unknown(monkeypatch):
    fake=Fake()
    fake.range=6
    fake.current=1
    fake.write_fail='INP ON'
    session(fake,monkeypatch)
    with pytest.raises(LoadError) as err:
        live('input-on',confirmed=True)
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
    result = live('set-cc', current=0.1, range_name='low')
    assert result['range'] == 'low'
    assert result['range_readback_a'] == 0.1
    assert result['current_a'] == 0.1
    assert fake.calls.count('SYST:REM') == 1
    assert fake.remote is True
    assert fake.calls[-1] == 'INP:STAT?'


@pytest.mark.parametrize('selection', ['low', 'auto'])
def test_setting_not_applied_never_passes_even_with_zero_range_readback(monkeypatch, selection):
    fake = Fake()
    fake.range = 0.0
    session(fake, monkeypatch)
    # The remote command has no effect on this mock (failed remote control).
    def ignored_remote(command):
        fake.calls.append(command)
    fake.write = ignored_remote
    with pytest.raises(LoadError) as error:
        live('set-cc', current=0.1, range_name=selection)
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
        live('input-on', confirmed=True)
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
        live('input-on', confirmed=True)
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
        live('input-on', confirmed=True, stop=stop)
    assert error.value.code == 'stopping'
    assert 'INP ON' not in fake.calls


def test_remote_failure_does_not_attempt_cc_or_input_on(monkeypatch):
    fake = Fake()
    fake.write_fail = 'SYST:REM'
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        live('set-cc', current=0.1, range_name='low')
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
        result = live('input-on', confirmed=True)
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
    result = live('set-cc', current=0.1, range_name='low')
    assert result['input_state'] == 'OFF'
    assert result['range_readback_a'] == 0.1
    assert result['current_a'] == 0.1
    assert 'CURR:RANG 0.1' in fake.calls
    assert 'CURR:RANG MIN' not in fake.calls
    assert 'INP ON' not in fake.calls


@pytest.mark.parametrize('current,selection', [(1.5, 'high'), (10, 'auto')])
def test_high_range_still_uses_existing_max_command(monkeypatch, current, selection):
    fake = Fake()
    session(fake, monkeypatch)
    result = live('set-cc', current=current, range_name=selection)
    assert result['range'] == 'high'
    assert result['range_readback_a'] == 60.0
    assert result['current_a'] == current
    assert 'CURR:RANG MAX' in fake.calls


@pytest.mark.parametrize('range_name,voltage', [('low', 5), ('high', 50)])
def test_fixed_cv_offline_setup_and_confirmed_input(range_name, voltage):
    from electronicload_tool_core.simulator import ControlSimulator

    sim = ControlSimulator()
    result = control.execute('set-cv', mode='simulate', simulator=sim,
                             range_name=range_name, voltage=voltage)
    assert result['input_state'] == 'OFF'
    assert result['range'] == range_name
    assert result['voltage_v'] == voltage
    assert control.execute('input-on', mode='simulate', simulator=sim,
                           confirmed=True)['input_state'] == 'ON'
    assert control.execute('input-off', mode='simulate',
                           simulator=sim)['input_state'] == 'OFF'


@pytest.mark.parametrize('voltage,range_name,code', [
    (0, 'low', 'voltage_out_of_range'),
    (18.001, 'low', 'voltage_out_of_range'),
    (120.01, 'high', 'voltage_out_of_range'),
    (1.2345, 'low', 'invalid_voltage_resolution'),
    (1.234, 'high', 'invalid_voltage_resolution'),
    (float('nan'), 'low', 'invalid_voltage'),
    (True, 'low', 'invalid_voltage'),
    (1, 'medium', 'invalid_range'),
])
def test_cv_invalid_settings_fail_without_hardware(voltage, range_name, code):
    with pytest.raises(LoadError) as err:
        control.execute('set-cv', mode='dry-run', voltage=voltage,
                        range_name=range_name)
    assert err.value.code == code


@pytest.mark.parametrize('selection', ['low', 'auto'])
def test_cv_live_stays_blocked_without_range_hardware_evidence(monkeypatch, selection):
    def forbidden_open(_resource):
        raise AssertionError('CV attempted real VISA')

    monkeypatch.setattr(control, 'open_session', forbidden_open)
    with pytest.raises(LoadError) as err:
        control.execute('set-cv', resource=RESOURCE, voltage=5, range_name=selection)
    assert err.value.code == 'unsupported_live_scope'

    fake = Fake()
    fake.function = 'VOLT'
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as err:
        control.execute('input-on', resource=RESOURCE, confirmed=True)
    assert err.value.code == 'unsupported_live_scope'
    assert 'SYST:REM' not in fake.calls and 'INP ON' not in fake.calls


@pytest.mark.parametrize('resistance,range_name', [
    ('0.05', 'low'), ('10', 'low'), ('10', 'high'), ('7500', 'high'),
    ('1.234567890123456789', 'low'), ('123.4567890123456789', 'high'),
])
def test_cr_offline_bounds_and_no_quantization(resistance, range_name):
    sim = ControlSimulator()
    requested = Decimal(resistance)
    assert control.validate_resistance(resistance, range_name) == requested
    plan = control.execute('set-cr', mode='dry-run', resistance=resistance,
                           range_name=range_name, simulator=sim)
    assert plan['hardware_access'] is False
    assert plan['resistance_ohm'] == float(requested)
    assert plan['range'] == range_name
    assert sim.function == 'CURR' and not sim.input_on

    result = control.execute('set-cr', mode='simulate', resistance=resistance,
                             range_name=range_name, simulator=sim)
    assert result['function_mode'] == 'FIX' and result['function'] == 'RES'
    assert result['input_state'] == 'OFF'
    assert result['range'] == range_name
    assert result['resistance_ohm'] == float(requested)
    assert result['range_readback_ohm'] == (10 if range_name == 'low' else 7500)
    assert Decimal(sim.query('RES?')) == requested


@pytest.mark.parametrize('resistance,range_name,code', [
    (0, 'low', 'resistance_out_of_range'),
    ('0.049999999999999999', 'low', 'resistance_out_of_range'),
    ('10.000000000000000001', 'low', 'resistance_out_of_range'),
    ('9.999999999999999999', 'high', 'resistance_out_of_range'),
    ('7500.000000000000001', 'high', 'resistance_out_of_range'),
    (-1, 'low', 'resistance_out_of_range'),
    (float('nan'), 'low', 'invalid_resistance'),
    (float('inf'), 'low', 'invalid_resistance'),
    (float('-inf'), 'high', 'invalid_resistance'),
    ('text', 'low', 'invalid_resistance'),
    (True, 'low', 'invalid_resistance'),
    ([], 'low', 'invalid_resistance'),
    (1, 'other', 'invalid_range'),
    (1, None, 'invalid_range'),
    (1, [], 'invalid_range'),
    (None, 'low', 'argument_error'),
])
def test_cr_invalid_settings_preserve_simulator(resistance, range_name, code):
    sim = ControlSimulator()
    before = vars(sim).copy()
    with pytest.raises(LoadError) as error:
        control.execute('set-cr', mode='simulate', resistance=resistance,
                        range_name=range_name, simulator=sim)
    assert error.value.code == code
    assert vars(sim) == before


@pytest.mark.parametrize('range_name,resistance', [('low', 5), ('high', 100)])
def test_cr_simulated_input_requires_confirmation_and_off_before_setup(range_name, resistance):
    sim = ControlSimulator()
    control.execute('set-cr', mode='simulate', resistance=resistance,
                    range_name=range_name, simulator=sim)
    with pytest.raises(LoadError) as error:
        control.execute('input-on', mode='simulate', simulator=sim)
    assert error.value.code == 'confirmation_required'
    assert sim.input_on is False
    assert control.execute('input-on', mode='simulate', simulator=sim,
                           confirmed=True)['input_state'] == 'ON'
    before = vars(sim).copy()
    with pytest.raises(LoadError) as error:
        control.execute('set-cr', mode='simulate', resistance=10,
                        range_name='low', simulator=sim)
    assert error.value.code == 'input_not_off'
    assert vars(sim) == before
    assert control.execute('input-off', mode='simulate', simulator=sim)['input_state'] == 'OFF'


def test_cr_readback_mismatch_fails_without_input_on():
    class MismatchedSimulator(ControlSimulator):
        def configure_cr(self, resistance, range_name):
            super().configure_cr(resistance + Decimal('0.000000000000000001'), range_name)

    sim = MismatchedSimulator()
    with pytest.raises(LoadError) as error:
        control.execute('set-cr', mode='simulate', resistance=5, range_name='low', simulator=sim)
    assert error.value.code == 'readback_mismatch'
    assert sim.input_on is False


@pytest.mark.parametrize('validation', [False, True])
@pytest.mark.parametrize('selection', ['low', 'auto'])
def test_cr_live_refuses_before_open_even_with_override(monkeypatch, validation, selection):
    def forbidden_open(_resource):
        pytest.fail('CR attempted to open VISA')

    monkeypatch.setattr(control, 'open_session', forbidden_open)
    with pytest.raises(LoadError) as error:
        control.execute('set-cr', resource=RESOURCE, resistance=5,
                        range_name=selection, validation=validation)
    assert error.value.code == 'unsupported_live_scope'


@pytest.mark.parametrize('function', ['RES', 'RESISTANCE'])
@pytest.mark.parametrize('validation', [False, True])
def test_cr_physical_input_on_refuses_without_writes(monkeypatch, function, validation):
    fake = Fake()
    fake.function = function
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        control.execute('input-on', resource=RESOURCE, confirmed=True, validation=validation)
    assert error.value.code == 'unsupported_live_scope'
    assert fake.calls == ['*IDN?', 'INP:STAT?', 'FUNC:MODE?', 'FUNC?']


@pytest.mark.parametrize('power', ['0', '0.01', '12.34', '249.99', '250', '12.3400', '1e-2'])
def test_cw_offline_bounds_resolution_and_readback(power):
    sim = ControlSimulator()
    requested = Decimal(power)
    before = vars(sim).copy()
    plan = control.execute('set-cw', mode='dry-run', power=power, simulator=sim)
    assert plan['hardware_access'] is False
    assert plan['power_w'] == float(requested)
    assert 'range' not in plan
    assert vars(sim) == before

    result = control.execute('set-cw', mode='simulate', power=power, simulator=sim)
    assert result == {'function_mode': 'FIX', 'function': 'POW',
                      'power_w': float(requested), 'input_state': 'OFF'}
    assert Decimal(sim.query('POW?')) == requested


@pytest.mark.parametrize('power,code', [
    (-0.01, 'power_out_of_range'), (250.01, 'power_out_of_range'),
    ('250.000000000000000001', 'power_out_of_range'),
    ('0.001', 'invalid_power_resolution'), ('12.345', 'invalid_power_resolution'),
    ('1.000000000000000000000000000001', 'invalid_power_resolution'),
    ('1e-1000', 'invalid_power_resolution'),
    (float('nan'), 'invalid_power'), (float('inf'), 'invalid_power'),
    (float('-inf'), 'invalid_power'), ('text', 'invalid_power'),
    (True, 'invalid_power'), (False, 'invalid_power'), ([], 'invalid_power'),
    ({}, 'invalid_power'), (None, 'argument_error'),
])
@pytest.mark.parametrize('mode', ['simulate', 'dry-run'])
def test_cw_invalid_settings_preserve_simulator(power, code, mode):
    sim = ControlSimulator()
    before = vars(sim).copy()
    with pytest.raises(LoadError) as error:
        control.execute('set-cw', mode=mode, power=power, simulator=sim)
    assert error.value.code == code
    assert vars(sim) == before


def test_cw_rejects_range():
    with pytest.raises(LoadError) as error:
        control.execute('set-cw', mode='dry-run', power=1, range_name='low')
    assert error.value.code == 'argument_error'


@pytest.mark.parametrize('power', [0, 12.34])
def test_cw_simulated_input_requires_confirmation_and_off_before_setup(power):
    sim = ControlSimulator()
    control.execute('set-cw', mode='simulate', power=power, simulator=sim)
    with pytest.raises(LoadError) as error:
        control.execute('input-on', mode='simulate', simulator=sim)
    assert error.value.code == 'confirmation_required'
    assert not sim.input_on
    result = control.execute('input-on', mode='simulate', simulator=sim, confirmed=True)
    assert result['input_state'] == 'ON' and result['power_w'] == power
    before = vars(sim).copy()
    with pytest.raises(LoadError) as error:
        control.execute('set-cw', mode='simulate', power=250, simulator=sim)
    assert error.value.code == 'input_not_off'
    plan = control.execute('set-cw', mode='dry-run', power=250, simulator=sim)
    assert plan['power_w'] == 250 and plan['hardware_access'] is False
    assert vars(sim) == before
    assert control.execute('input-off', mode='simulate', simulator=sim)['input_state'] == 'OFF'


def test_cw_readback_mismatch_fails_without_input_on():
    class MismatchedSimulator(ControlSimulator):
        def configure_cw(self, power):
            super().configure_cw(power + Decimal('0.01'))

    sim = MismatchedSimulator()
    with pytest.raises(LoadError) as error:
        control.execute('set-cw', mode='simulate', power=5, simulator=sim)
    assert error.value.code == 'readback_mismatch'
    assert error.value.exit_code == 3
    assert not sim.input_on


@pytest.mark.parametrize('validation', [False, True])
def test_cw_live_refuses_before_open_even_with_override(monkeypatch, validation):
    def forbidden_open(_resource):
        pytest.fail('CW attempted to open VISA')

    monkeypatch.setattr(control, 'open_session', forbidden_open)
    with pytest.raises(LoadError) as error:
        control.execute('set-cw', resource=RESOURCE, power=5, validation=validation)
    assert error.value.code == 'unsupported_live_scope'


@pytest.mark.parametrize('function', ['POW', 'POWER'])
@pytest.mark.parametrize('validation', [False, True])
def test_cw_physical_input_on_refuses_without_writes(monkeypatch, function, validation):
    fake = Fake()
    fake.function = function
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        control.execute('input-on', resource=RESOURCE, confirmed=True, validation=validation)
    assert error.value.code == 'unsupported_live_scope'
    assert fake.calls == ['*IDN?', 'INP:STAT?', 'FUNC:MODE?', 'FUNC?']


@pytest.mark.parametrize('current,selection,rise,fall,expected', [
    ('1', 'auto', '0.0001', '0.25', 'low'),
    ('6.001', 'auto', '0.001', '2.5', 'high'),
    ('1', 'low', '0.123456789012345678901', '0.2', 'low'),
])
def test_fixed_cc_slew_setup_and_preview(current, selection, rise, fall, expected):
    sim = ControlSimulator()
    kwargs = dict(current=current, range_name=selection, rise_slew=rise, fall_slew=fall)
    before = vars(sim).copy()
    preview = control.execute('set-cc', mode='dry-run', simulator=sim, **kwargs)
    assert preview['hardware_access'] is False
    assert vars(sim) == before
    result = control.execute('set-cc', mode='simulate', simulator=sim, **kwargs)
    assert result['range'] == preview['range'] == expected
    assert result['input_state'] == 'OFF'
    for response in (result, preview):
        assert response['rise_slew_a_per_us'] == rise
        assert response['fall_slew_a_per_us'] == fall


@pytest.mark.parametrize('rise,fall,code', [
    ('0.1', None, 'argument_error'), (None, '0.1', 'argument_error'),
    ('NaN', '0.1', 'invalid_slew'), ('0.1', True, 'invalid_slew'),
    ('0.25000000000000000001', '0.1', 'slew_out_of_range'),
    ('0.1', '0.00009999999999999999999', 'slew_out_of_range'),
])
def test_fixed_cc_slew_invalid_pair_cannot_change_state_or_select_high(rise, fall, code):
    sim = ControlSimulator()
    before = vars(sim).copy()
    with pytest.raises(LoadError) as error:
        control.execute('set-cc', mode='simulate', simulator=sim, current='1',
                        range_name='auto', rise_slew=rise, fall_slew=fall)
    assert error.value.code == code
    assert vars(sim) == before


@pytest.mark.parametrize('voltage,latch', [('0', 'off'), ('120', 'on'),
                                          ('0.0001234567890123456789', 'off')])
def test_von_requires_input_off_and_is_retained_across_fixed_modes(voltage, latch):
    sim = ControlSimulator()

    def execute(command, **kwargs):
        return control.execute(command, mode='simulate', simulator=sim, **kwargs)

    initial = execute('von-status')
    plan = control.execute('set-von', mode='dry-run', simulator=sim, voltage=voltage, latch=latch)
    assert plan['hardware_access'] is False
    assert execute('von-status') == initial
    expected = {'threshold_v': voltage, 'latch': latch}
    assert execute('set-von', voltage=voltage, latch=latch) == expected
    execute('set-cv', voltage='12', range_name='low')
    assert execute('input-on', confirmed=True)['input_state'] == 'ON'
    assert execute('von-status') == expected
    with pytest.raises(LoadError) as error:
        execute('set-von', voltage='1', latch='on')
    assert error.value.code == 'input_not_off'
    assert execute('von-status') == expected
    assert sim.input_on


@pytest.mark.parametrize('voltage,latch,code', [
    (None, 'on', 'argument_error'), ('1', None, 'argument_error'),
    ('1', True, 'argument_error'), ('Infinity', 'off', 'invalid_voltage'),
    ('-0.01', 'on', 'voltage_out_of_range'),
    ('120.0000000000000000001', 'off', 'voltage_out_of_range'),
])
def test_von_invalid_configuration_does_not_mutate_simulator(voltage, latch, code):
    sim = ControlSimulator()
    before = vars(sim).copy()
    with pytest.raises(LoadError) as error:
        control.execute('set-von', mode='simulate', simulator=sim, voltage=voltage, latch=latch)
    assert error.value.code == code
    assert vars(sim) == before


@pytest.mark.parametrize('setup,kwargs', [
    ('set-cc', {'current': '1', 'range_name': 'low'}),
    ('set-cv', {'voltage': '2', 'range_name': 'low'}),
    ('set-cr', {'resistance': '2', 'range_name': 'low'}),
    ('set-cw', {'power': '2'}),
])
def test_short_fixed_modes_keep_input_and_settings_independent(setup, kwargs):
    sim = ControlSimulator()

    def execute(command, **arguments):
        return control.execute(command, mode='simulate', simulator=sim, **arguments)

    execute(setup, **kwargs)
    with pytest.raises(LoadError) as error:
        execute('short-on')
    assert error.value.code == 'input_not_on'
    assert execute('short-off') == {'short_state': 'OFF'}
    before = execute('input-on', confirmed=True)
    assert execute('short-on') == execute('short-status') == {'short_state': 'ON'}
    with pytest.raises(LoadError) as error:
        execute('short-on')
    assert error.value.code == 'already_short_on'
    assert execute('short-off') == {'short_state': 'OFF'}
    assert sim.input_on
    execute('short-on')
    assert execute('input-off') == {'input_state': 'OFF'}
    assert execute('short-status') == {'short_state': 'ON'}
    with pytest.raises(LoadError) as error:
        execute('input-on', confirmed=True)
    assert error.value.code == 'unsafe_mode'
    assert execute('short-off') == {'short_state': 'OFF'}
    assert execute('input-on', confirmed=True) == before


@pytest.mark.parametrize('mode,function,transient', [
    ('TRAN', 'CURR', True), ('LIST', 'CURR', False),
    ('FIX', 'UNKNOWN', False), ('FIX', 'CURR', True),
])
def test_short_on_rejects_non_fixed_or_unknown_modes(mode, function, transient):
    sim = ControlSimulator()
    sim.mode, sim.function, sim.transient_enabled = mode, function, transient
    sim.input_on = True
    with pytest.raises(LoadError) as error:
        control.execute('short-on', mode='simulate', simulator=sim)
    assert error.value.code == 'unsafe_mode'
    assert control.execute('short-status', mode='simulate', simulator=sim) == {'short_state': 'OFF'}
    assert sim.input_on


def test_short_on_stop_and_unknown_state_leave_explicit_off_available():
    class UnknownShort(ControlSimulator):
        def query(self, command):
            return 'UNKNOWN' if command == 'INP:SHOR:STAT?' else super().query(command)

    sim = ControlSimulator()
    sim.input_on = True
    stop = Event()
    stop.set()
    with pytest.raises(LoadError) as error:
        control.execute('short-on', mode='simulate', simulator=sim, stop=stop)
    assert error.value.code == 'stopping'
    assert sim.input_on
    assert not sim.short_on
    stop.clear()
    sim = UnknownShort()
    sim.input_on = True
    with pytest.raises(LoadError) as error:
        control.execute('short-on', mode='simulate', simulator=sim, stop=stop)
    assert error.value.code == 'invalid_response'
    assert not sim.short_on
    with pytest.raises(LoadError) as error:
        control.execute('short-off', mode='simulate', simulator=sim, stop=stop)
    assert error.value.code == 'invalid_response'
    assert control.execute('input-off', mode='simulate', simulator=sim, stop=stop) == {
        'input_state': 'OFF'}


@pytest.mark.parametrize('override', [{}, {'validation': True}, {'hardware_validation': 'cv'}])
@pytest.mark.parametrize('command,arguments', [
    ('set-cc', {'current': '1', 'range_name': 'low', 'rise_slew': '0.1', 'fall_slew': '0.1'}),
    ('set-von', {'voltage': '10', 'latch': 'on'}),
    ('von-status', {}), ('short-on', {}), ('short-off', {}), ('short-status', {}),
])
def test_fixed_slew_von_short_live_refused_before_visa(monkeypatch, override, command, arguments):
    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    with pytest.raises(LoadError) as error:
        control.execute(command, resource=RESOURCE, **arguments, **override)
    assert error.value.code == 'unsupported_live_scope'
