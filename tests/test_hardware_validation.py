"""Validation-only hardware paths use recording VISA fakes, never an instrument."""
import json
from decimal import Decimal
from threading import Event

import pytest

from electronicload_tool_cli import cli
from electronicload_tool_cli.worker import _arguments
from electronicload_tool_core import control, readonly
from electronicload_tool_core.errors import LoadError
from test_control_core import Fake, RESOURCE, session


class ValidationFake(Fake):
    def __init__(self):
        super().__init__()
        self.values = {'VOLT:RANG': '18', 'VOLT:RANG:AUTO': '0', 'VOLT': '5',
                       'RES:RANG': '10', 'RES': '10', 'POW': '1',
                       'RES:LED:STAT': '0', 'RES:VDR': '0', 'CURR:SLEW:STAT': '0',
                       'CURR:TRAN:MODE': 'CONT', 'CURR:TRAN:ALEV': '.1',
                       'CURR:TRAN:BLEV': '.2', 'CURR:SLEW:POS': '.1', 'CURR:SLEW:NEG': '.1',
                       'CURR:TRAN:AWID': '.001', 'CURR:TRAN:BWID': '.002', 'TRIG:SOUR': 'HOLD'}
        for prefix in ('VOLT', 'RES', 'POW'):
            self.values.update({f'{prefix}:TRAN:MODE': 'CONT', f'{prefix}:TRAN:ALEV': '1',
                                f'{prefix}:TRAN:BLEV': '2', f'{prefix}:TRAN:AWID': '.001',
                                f'{prefix}:TRAN:BWID': '.002'})
        self.after_remote = None
        self.ignore = None
        self.after_write = None

    def query(self, command):
        if command == 'RES:VDR? MAX':
            self.calls.append(command)
            return '12'  # Test value, not a model limit.
        if command[:-1] in self.values:
            self.calls.append(command)
            return self.values[command[:-1]]
        return super().query(command)

    def write(self, command):
        super().write(command)
        if command == 'SYST:REM' and self.after_remote:
            self.after_remote(self)
        if not self.remote or command == self.ignore:
            return
        key, _, value = command.partition(' ')
        if key in self.values:
            self.values[key] = '0' if value == 'OFF' else value
        if key == 'FUNC':
            self.function = value
        if key == 'TRAN':
            self.transient = value == 'ON'
        if self.after_write:
            self.after_write(self, command)


def transient(mode='continuous', range_name='auto', function='CC'):
    settings = dict(transient_mode=mode, range=range_name, level_a='.1', level_b='.2',
                    rise_slew='.1', fall_slew='.1')
    if mode == 'continuous':
        settings.update(width_a='.001', width_b='.002')
    if mode == 'pulse':
        settings.update(pulse_width='.001')
    if function != 'CC':
        del settings['rise_slew'], settings['fall_slew']
        settings.update(level_a='10', level_b='10')
    if function == 'CW':
        del settings['range']
    return settings


def setup(scope, settings, **kwargs):
    arguments = ({'transient': settings} if scope.startswith('transient-') else
                 {'range_name' if key == 'range' else key: value for key, value in settings.items()})
    return control.execute('set-' + scope, resource=RESOURCE, hardware_validation=scope,
                           **arguments, **kwargs)


def run(command, scope='transient-cc', **kwargs):
    return control.execute(command, resource=RESOURCE, hardware_validation=scope, **kwargs)


def test_validation_cr_led_native_settings_and_confirmed_exit(monkeypatch):
    fake = ValidationFake()
    session(fake, monkeypatch)
    result = run('set-cr-led', 'cr-led', resistance='50', range_name='auto', vdrop='1.00000000000001')
    assert result['fixed_cr'] and result['led_enabled']
    assert result['vdrop_v'] == '1.00000000000001'
    assert result['vdrop_max_v'] == '12'
    assert result['range'] == 'high'
    assert fake.calls.index('RES:VDR? MAX') < fake.calls.index('SYST:REM')
    assert [call for call in fake.calls if '?' not in call] == [
        'SYST:REM', 'FUNC:MODE FIX', 'FUNC RES', 'RES:RANG 7500', 'RES 50',
        'RES:VDR 1.00000000000001', 'RES:LED:STAT 1']

    fake.calls.clear()
    assert not run('set-cr', 'cr-led', resistance='50', range_name='auto')['led_enabled']
    assert fake.calls.index('RES:LED:STAT 0') < fake.calls.index('RES:LED:STAT?')
    assert fake.calls.index('RES:LED:STAT?') < fake.calls.index('FUNC:MODE FIX')
    run('set-cr-led', 'cr-led', resistance='50', range_name='auto', vdrop='1')
    fake.calls.clear()
    run('set-cc', 'cr-led', current='0.1', range_name='low')
    led_off = fake.calls.index('RES:LED:STAT 0')
    assert led_off < fake.calls.index('RES:LED:STAT?', led_off) < fake.calls.index('FUNC CURR')
    fake.calls.clear()
    status = run('cr-led-status', 'cr-led')
    assert not status['fixed_cr'] and not status['led_enabled']
    assert all(call.endswith('?') for call in fake.calls)


@pytest.mark.parametrize('failure,code', [
    ('ignored', 'readback_mismatch'), ('unreadable', 'query_failed'), ('write', 'write_failed'),
])
def test_validation_cr_led_exit_stops_on_unconfirmed_off(monkeypatch, failure, code):
    fake = ValidationFake()
    fake.function = 'RES'
    fake.values['RES:LED:STAT'] = '1'
    if failure == 'ignored':
        fake.ignore = 'RES:LED:STAT 0'
    elif failure == 'unreadable':
        def corrupt_readback(device, command):
            if command == 'RES:LED:STAT 0':
                del device.values['RES:LED:STAT']

        fake.after_write = corrupt_readback
    else:
        fake.write_fail = 'RES:LED:STAT 0'
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        run('set-cc', 'cr-led', current='0.1', range_name='low')
    assert error.value.code == code
    assert error.value.details['completed_steps'] == ['remote']
    assert error.value.details['led_enabled'] is (True if failure == 'ignored' else None)
    assert [call for call in fake.calls if '?' not in call] == ['SYST:REM', 'RES:LED:STAT 0']
    assert fake.function == 'RES' and not fake.input


@pytest.mark.parametrize('ignore_led_off', [False, True])
def test_validation_cv_requires_confirmed_cr_led_off_before_mode_write(monkeypatch, ignore_led_off):
    fake = ValidationFake()
    fake.function = 'RES'
    fake.values['RES:LED:STAT'] = '1'
    session(fake, monkeypatch)
    settings = {'voltage': '0.1', 'range': 'low'}
    if ignore_led_off:
        fake.ignore = 'RES:LED:STAT 0'
        with pytest.raises(LoadError) as error:
            setup('cv', settings)
        assert error.value.code == 'readback_mismatch'
        assert error.value.details['completed_steps'] == ['SYST:REM']
        assert [call for call in fake.calls if '?' not in call] == ['SYST:REM', 'RES:LED:STAT 0']
        return
    setup('cv', settings)
    disabled = fake.calls.index('RES:LED:STAT 0')
    confirmed = fake.calls.index('RES:LED:STAT?', disabled)
    assert disabled < confirmed < fake.calls.index('FUNC:MODE FIX')


def test_transient_cr_enable_reports_remote_when_led_off_is_not_confirmed(monkeypatch):
    fake = ValidationFake()
    session(fake, monkeypatch)
    settings = transient(function='CR')
    setup('transient-cr', settings)
    fake.values['RES:LED:STAT'] = '1'
    fake.ignore = 'RES:LED:STAT 0'
    fake.calls.clear()

    with pytest.raises(LoadError) as error:
        run('transient-enable', 'transient-cr', validation_settings=settings)
    assert error.value.code == 'readback_mismatch'
    assert error.value.details['completed_steps'] == ['SYST:REM']
    assert error.value.details['led_enabled'] is True
    assert [call for call in fake.calls if '?' not in call] == ['SYST:REM', 'RES:LED:STAT 0']
    assert not fake.transient and not fake.input


@pytest.mark.parametrize('after_remote', [False, True])
def test_slow_rise_cc_validation_refuses_cr_without_led_access(monkeypatch, after_remote):
    fake = ValidationFake()
    fake.values['RES:LED:STAT'] = '1'
    if after_remote:
        fake.after_remote = lambda device: setattr(device, 'function', 'RES')
    else:
        fake.function = 'RES'
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        run('set-cc', 'current-slow-rise', current='0.1', range_name='low')
    assert error.value.code == 'unsupported_live_scope'
    assert error.value.details['completed_steps'] == (['remote'] if after_remote else [])
    assert [call for call in fake.calls if '?' not in call] == (['SYST:REM'] if after_remote else [])
    assert not any(call.startswith('RES:LED:') for call in fake.calls)


@pytest.mark.parametrize('field,value,code', [
    ('vdrop', '13', 'vdrop_out_of_range'),
    ('resistance', '0', 'no_matching_range'),
])
def test_validation_cr_led_checks_limits_before_any_write(monkeypatch, field, value, code):
    fake = ValidationFake()
    session(fake, monkeypatch)
    args = dict(resistance='50', range_name='auto', vdrop='1')
    args[field] = value
    with pytest.raises(LoadError) as error:
        run('set-cr-led', 'cr-led', **args)
    assert error.value.code == code
    assert all('?' in call for call in fake.calls)


def test_validation_slow_rise_native_reads_guards_and_preservation(monkeypatch):
    fake = ValidationFake()
    session(fake, monkeypatch)
    scope = 'current-slow-rise'
    assert run('current-slow-rise-enable', scope)['enabled']
    assert 'CURR:SLEW:STAT 1' in fake.calls
    control.execute('input-off', resource=RESOURCE)
    run('set-cc', scope, current='0.2', range_name='low')
    assert run('current-slow-rise-status', scope)['enabled']
    fake.calls.clear()
    with pytest.raises(LoadError) as error:
        setup('cv', {'voltage': '0.1', 'range': 'low'})
    assert error.value.code == 'current_slow_rise_enabled'
    assert error.value.details['completed_steps'] == []
    assert all(call.endswith('?') for call in fake.calls)
    fake.function = 'VOLT'  # Emulate a front-panel selection.
    assert not run('current-slow-rise-disable', scope)['enabled']
    assert 'CURR:SLEW:STAT 0' in fake.calls
    assert not any(call.startswith(('CURR:SLEW:POS ', 'CURR:SLEW:NEG ')) for call in fake.calls)
    fake.calls.clear()
    with pytest.raises(LoadError, match='Fixed CC'):
        run('current-slow-rise-enable', scope)
    assert all(call.endswith('?') for call in fake.calls)


def test_validation_slow_rise_checks_again_after_remote(monkeypatch):
    fake = ValidationFake()
    fake.after_remote = lambda device: setattr(device, 'input', True)
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        run('current-slow-rise-enable', 'current-slow-rise')
    assert error.value.code == 'input_not_off'
    assert [call for call in fake.calls if '?' not in call] == ['SYST:REM']


TRIALS = [('cv', {'voltage': '5', 'range': r}) for r in ('auto', 'low', 'high')] + [
    ('cr', {'resistance': '1000' if r == 'high' else '10', 'range': r})
    for r in ('auto', 'low', 'high')
] + [('cw', {'power': '1'})] + [
    ('transient-' + f.lower(), transient(m, r, f)) for f in ('CC', 'CV', 'CR', 'CW')
    for m in ('continuous', 'pulse', 'toggle')
    for r in (('auto',) if f == 'CW' else ('auto', 'low', 'high'))
]


@pytest.mark.parametrize('scope,settings', TRIALS)
def test_validation_complete_cycles_preserve_explicit_off(monkeypatch, scope, settings):
    fake = ValidationFake()
    session(fake, monkeypatch)
    result = setup(scope, settings)
    assert result['validation_only'] and result['input_state'] == 'OFF'
    assert not fake.input and not fake.transient
    assert 'FUNC:MODE TRAN' not in fake.calls
    if scope.startswith('transient-'):
        run('transient-enable', scope, validation_settings=settings)
        assert fake.transient and not fake.input
    run('input-on', scope, confirmed=True, validation_settings=settings)
    assert fake.input
    if scope.startswith('transient-'):
        for _ in range(2):
            result = run('transient-trigger', scope, validation_settings=settings)
            assert result['waveform_completion_known'] is False
        assert fake.calls.count('TRIG:IMM') == 2
        before = len(fake.calls)
        assert run('transient-status', scope)['enabled']
        assert all(command.endswith('?') for command in fake.calls[before:])
        with pytest.raises(LoadError):
            run('transient-disable', scope)
        assert fake.input and fake.transient and 'INP OFF' not in fake.calls
    run('input-off', scope)
    assert not fake.input
    if scope.startswith('transient-'):
        assert fake.transient  # OFF must not disable Transient.
        run('transient-disable', scope)
        assert not fake.transient
    assert fake.calls.count('INP ON') == fake.calls.count('INP OFF') == 1
    assert not any(command in fake.calls for command in ('*RST', 'SYST:ERR?', 'SYST:LOC'))


@pytest.mark.parametrize('scope,field,value,range_name', [
    ('cv', 'voltage', '12', 'low'), ('cv', 'voltage', '24', 'high'),
    ('cr', 'resistance', '10', 'low'), ('cr', 'resistance', '100', 'high'),
])
def test_fixed_auto_validation_plan_matches_manual(scope, field, value, range_name):
    automatic = setup(scope, {field: value, 'range': 'auto'}, mode='dry-run')
    manual = setup(scope, {field: value, 'range': range_name}, mode='dry-run')
    assert automatic == manual
    assert automatic['expected_configuration']['range'] == range_name
    assert automatic['hardware_access'] is False
    if scope == 'cv':
        assert 'VOLT:RANG:AUTO OFF' in automatic['planned_writes']


@pytest.mark.parametrize('scope,field,value', [('cv', 'voltage', '18.001'),
                                             ('cr', 'resistance', '8000')])
def test_fixed_auto_validation_refuses_before_visa(monkeypatch, scope, field, value):
    fake = ValidationFake()
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        setup(scope, {field: value, 'range': 'auto'})
    assert error.value.code == 'no_matching_range'
    assert fake.calls == []


def test_validation_auto_high_and_exact_numeric_tokens(monkeypatch):
    fake = ValidationFake()
    session(fake, monkeypatch)
    settings = transient('pulse')
    settings.update(rise_slew='.50000000000000000000001', fall_slew='.5')
    result = setup('transient-cc', settings)
    assert result['configuration']['range'] == 'high'
    assert 'CURR:SLEW:POS 0.50000000000000000000001' in fake.calls
    assert 'CURR:TRAN:AWID 0.001' in fake.calls and not any('BWID ' in c for c in fake.calls)


@pytest.mark.parametrize('scope,settings,ignored', [
    ('cv', {'voltage': '6', 'range': 'low'}, 'VOLT 6'),
    ('cr', {'resistance': '9', 'range': 'low'}, 'RES 9'),
    ('cw', {'power': '2'}, 'POW 2'),
    ('transient-cc', {**transient(), 'level_a': '.3'}, 'CURR:TRAN:ALEV 0.3'),
])
def test_setup_readback_mismatch_reports_failure_without_loading(monkeypatch, scope, settings, ignored):
    fake = ValidationFake()
    fake.ignore = ignored
    session(fake, monkeypatch)
    with pytest.raises(LoadError, match='Settings differ') as error:
        setup(scope, settings)
    assert error.value.code == 'readback_mismatch'
    assert error.value.details['completed_steps']
    assert not fake.input and 'INP ON' not in fake.calls and 'INP OFF' not in fake.calls


@pytest.mark.parametrize('scope,query,reply', [('cv', 'VOLT:RANG', '17'), ('cr', 'RES:RANG', '9000')])
def test_unrecognized_physical_range_blocks_on(monkeypatch, scope, query, reply):
    fake = ValidationFake()
    session(fake, monkeypatch)
    settings = dict(TRIALS)[scope]
    setup(scope, settings)
    fake.values[query] = reply
    fake.calls.clear()
    with pytest.raises(LoadError) as error:
        run('input-on', scope, confirmed=True, validation_settings=settings)
    assert error.value.code == 'invalid_range_readback'
    assert all(call.endswith('?') for call in fake.calls)


@pytest.mark.parametrize('led_readback,code', [('1', 'unsafe_mode'), ('UNKNOWN', 'invalid_response')])
@pytest.mark.parametrize('after_remote', [False, True])
def test_plain_cr_input_on_requires_confirmed_led_off(monkeypatch, led_readback, code, after_remote):
    fake = ValidationFake()
    session(fake, monkeypatch)
    settings = {'resistance': '50', 'range': 'high'}
    setup('cr', settings)
    if after_remote:
        fake.after_remote = lambda device: device.values.update({'RES:LED:STAT': led_readback})
    else:
        run('set-cr-led', 'cr-led', resistance='50', range_name='high', vdrop='1')
        fake.values['RES:LED:STAT'] = led_readback
    fake.calls.clear()

    with pytest.raises(LoadError) as error:
        run('input-on', 'cr', confirmed=True, validation_settings=settings)
    assert error.value.code == code
    assert [call for call in fake.calls if '?' not in call] == (['SYST:REM'] if after_remote else [])
    assert not fake.input


@pytest.mark.parametrize('function', ['CC', 'CV', 'CR', 'CW'])
@pytest.mark.parametrize('change', ['input', 'short', 'function', 'enabled', 'level', 'source', 'stop'])
@pytest.mark.parametrize('after_remote', [False, True])
def test_input_on_checks_every_condition_before_and_after_remote(monkeypatch, change, after_remote, function):
    fake = ValidationFake()
    session(fake, monkeypatch)
    scope = 'transient-' + function.lower()
    prefix = {'CC': 'CURR', 'CV': 'VOLT', 'CR': 'RES', 'CW': 'POW'}[function]
    settings = transient(function=function)
    setup(scope, settings)
    run('transient-enable', scope, validation_settings=settings)
    stop = Event()

    def unsafe(f):
        if change == 'input':
            f.input = True
        elif change == 'short':
            f.short = True
        elif change == 'function':
            f.function = 'CURR' if function != 'CC' else 'POW'
        elif change == 'enabled':
            f.transient = False
        elif change == 'level':
            f.values[f'{prefix}:TRAN:ALEV'] = '2'
        elif change == 'source':
            f.values['TRIG:SOUR'] = 'BUS'
        else:
            stop.set()

    if after_remote:
        fake.after_remote = unsafe
    else:
        unsafe(fake)
    fake.calls.clear()
    with pytest.raises(LoadError):
        run('input-on', scope, confirmed=True, validation_settings=settings, stop=stop)
    assert 'INP ON' not in fake.calls and 'INP OFF' not in fake.calls
    if not after_remote:
        assert 'SYST:REM' not in fake.calls


@pytest.mark.parametrize('scope,settings', TRIALS[:5])
def test_fixed_on_cannot_use_changed_setup(monkeypatch, scope, settings):
    fake = ValidationFake()
    session(fake, monkeypatch)
    setup(scope, settings)
    field = {'cv': 'VOLT', 'cr': 'RES', 'cw': 'POW'}[scope]
    fake.after_remote = lambda f: f.values.update({field: str(Decimal(settings[
        {'cv': 'voltage', 'cr': 'resistance', 'cw': 'power'}[scope]]) + 1)})
    with pytest.raises(LoadError):
        run('input-on', scope, confirmed=True, validation_settings=settings)
    assert 'INP ON' not in fake.calls


@pytest.mark.parametrize('selection', ['low', 'auto'])
def test_setup_stops_if_input_changes_between_writes(monkeypatch, selection):
    fake = ValidationFake()
    session(fake, monkeypatch)
    fake.after_write = lambda f, cmd: setattr(f, 'input', True) if cmd == 'FUNC VOLT' else None
    with pytest.raises(LoadError):
        setup('cv', {'voltage': '5', 'range': selection})
    assert 'VOLT:RANG:AUTO OFF' not in fake.calls and 'INP OFF' not in fake.calls


@pytest.mark.parametrize('failed_command', ['INP ON', 'TRAN ON', 'TRIG:IMM'])
def test_uncertain_writes_never_retry_or_implicitly_cleanup(monkeypatch, failed_command):
    fake = ValidationFake()
    session(fake, monkeypatch)
    settings = transient()
    setup('transient-cc', settings)
    if failed_command != 'TRAN ON':
        run('transient-enable', validation_settings=settings)
    if failed_command == 'TRIG:IMM':
        run('input-on', confirmed=True, validation_settings=settings)
    fake.calls.clear()
    fake.write_fail = failed_command
    command = {'INP ON': 'input-on', 'TRAN ON': 'transient-enable', 'TRIG:IMM': 'transient-trigger'}[failed_command]
    with pytest.raises(LoadError) as error:
        run(command, confirmed=True, validation_settings=settings)
    assert fake.calls.count(failed_command) == 1
    assert not any(c in fake.calls for c in ('INP OFF', 'TRAN OFF', 'SYST:LOC'))
    if failed_command == 'INP ON':
        assert error.value.details['input_state'] == 'UNKNOWN'
        assert error.value.details['input_on_attempted']


@pytest.mark.parametrize('command,scope,extra', [
    ('set-cv', 'cr', {'voltage': '5', 'range_name': 'low'}),
    ('input-on', 'cw', {'confirmed': True}),
    ('input-on', 'cw', {'validation_settings': {'power': 1}}),
    ('input-on', 'cw', {'confirmed': True, 'validation_settings': {'power': 1, 'range': 'low'}}),
    ('transient-trigger', 'transient-cc', {'validation_settings': {'transient_mode': 'pulse'}}),
    ('set-cw', 'cw', {'power': '1.001'}),
    ('set-cv', 'cv', {'voltage': '5.0000000000000000001', 'range_name': 'low'}),
])
def test_bad_validation_requests_fail_before_visa(monkeypatch, command, scope, extra):
    fake = ValidationFake()
    session(fake, monkeypatch)
    with pytest.raises(LoadError):
        run(command, scope, **extra)
    assert fake.calls == []


def test_validation_scope_keeps_identity_transport_and_model_guards(monkeypatch):
    fake = ValidationFake()
    session(fake, monkeypatch)
    for extra in ({'backend': 'py'}, {'model_id': 'unknown'}, {'mode': 'simulate'}):
        with pytest.raises(LoadError):
            setup('cw', {'power': '1'}, **extra)
    assert not fake.calls
    fake.response_errors['*IDN?'] = ValueError('wrong identity')
    with pytest.raises(LoadError):
        setup('cw', {'power': '1'})
    assert fake.calls == ['*IDN?']


@pytest.mark.parametrize('scope,settings', TRIALS[:5])
def test_normal_live_and_legacy_override_still_refuse(monkeypatch, scope, settings):
    fake = ValidationFake()
    session(fake, monkeypatch)
    setup(scope, settings)
    fake.calls.clear()
    with pytest.raises(LoadError) as error:
        control.execute('input-on', resource=RESOURCE, confirmed=True, validation=True)
    assert error.value.code == 'unsupported_live_scope'
    assert all(c.endswith('?') for c in fake.calls)


def test_cli_validation_json_and_precise_voltage(capsys):
    assert cli.main(['input-on', '--hardware-validation', 'cw', '--confirm',
                     '--validation-settings-json', '{"power":1.00}', '--dry-run', '--json']) == 0
    result = json.loads(capsys.readouterr().out)['result']
    assert result['validation_only'] and result['hardware_access'] is False
    assert result['expected_configuration']['power'] == '1.00'
    for settings in ('null', '[]', '{', '{"power":1.0000000000000000001}'):
        assert cli.main(['input-on', '--hardware-validation', 'cw', '--confirm',
                         '--validation-settings-json', settings, '--dry-run', '--json']) == 2
        assert json.loads(capsys.readouterr().out)['ok'] is False
    assert cli.main(['set-cv', '--hardware-validation', 'cv', '--voltage',
                     '5.0000000000000000001', '--range', 'low', '--dry-run', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'invalid_voltage_resolution'


def test_worker_cannot_select_hardware_validation():
    for field in ('hardware_validation', 'validation_settings'):
        with pytest.raises(LoadError):
            _arguments('set-cw', {'power': 1, field: 'cw'}, None)


@pytest.mark.parametrize('scope,prefix', [('transient-cv', 'VOLT'), ('transient-cr', 'RES'),
                                         ('transient-cw', 'POW')])
def test_new_transient_validation_scpi_plan_and_mismatch(monkeypatch, scope, prefix):
    settings = transient('pulse', function=scope[-2:].upper())
    settings['pulse_width'] = '0.00002000000000000000001'
    plan = setup(scope, settings, mode='dry-run')
    writes = plan['planned_writes']
    assert writes[:3] == ['SYST:REM', 'FUNC:MODE FIX', f'FUNC {prefix}']
    assert f'{prefix}:TRAN:MODE PULS' in writes
    assert f'{prefix}:TRAN:ALEV 10' in writes and f'{prefix}:TRAN:BLEV 10' in writes
    assert f'{prefix}:TRAN:AWID {settings["pulse_width"]}' in writes
    assert writes[-1] == 'TRIG:SOUR HOLD'
    assert not any('SLEW' in write or 'BWID' in write or write in ('INP ON', 'TRAN ON')
                   for write in writes)
    if scope == 'transient-cw':
        assert not any('RANG' in write for write in writes)
    fake = ValidationFake()
    session(fake, monkeypatch)
    fake.ignore = f'{prefix}:TRAN:BLEV 10'
    with pytest.raises(LoadError) as error:
        setup(scope, settings)
    assert error.value.code == 'readback_mismatch'
    assert 'INP ON' not in fake.calls and 'TRAN ON' not in fake.calls


@pytest.mark.parametrize('scope', ['transient-cv', 'transient-cr', 'transient-cw'])
def test_new_transient_validation_refuses_wrong_scope_before_visa(monkeypatch, scope):
    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    with pytest.raises(LoadError) as error:
        control.execute('set-transient-cc', hardware_validation=scope, resource=RESOURCE,
                        transient=transient())
    assert error.value.code == 'argument_error'


@pytest.mark.parametrize("command,readings,expected", [
    ("von-status", {"VOLT:ON?": "0.125000000000000001", "VOLT:LATC?": "0"},
     {"threshold_v": "0.125000000000000001", "latch": "off"}),
    ("short-status", {"INP:SHOR:STAT?": "1"}, {"short_state": "ON"}),
    ("average-status", {"SENS:AVER:COUN?": "8e0"}, {"average_count": 8}),
])
def test_configuration_readback_auxiliary_independent(fake_visa, command, readings, expected):
    fake_visa.responses.update(readings)
    result = control.execute(command, resource=RESOURCE, hardware_validation="readback")
    assert expected.items() <= result.items()
    assert result["state_source"] == "instrument_readback"
    assert {call for call in fake_visa.calls if isinstance(call, str) and "?" in call} == {
        "*IDN?", *readings,
    }


@pytest.mark.parametrize("function,name,waveform,extras,expected", [
    ("CURR", "CC", "CONT", {"CURR:RANG?": "6", "CURR:SLEW:POS?": "0.11", "CURR:SLEW:NEG?": "0.22",
                            "CURR:TRAN:AWID?": "0.001", "CURR:TRAN:BWID?": "0.002"},
     {"range": "low", "rise_slew_a_per_us": "0.11", "width_a_s": 0.001, "width_b_s": 0.002}),
    ("VOLT", "CV", "PULS", {"VOLT:RANG?": "18", "VOLT:TRAN:AWID?": "0.0123456789"},
     {"range": "low", "pulse_width_s": "0.0123456789"}),
    ("RES", "CR", "TOGG", {"RES:RANG?": "10"}, {"range": "low", "level_a_ohm": "1"}),
    ("POW", "CW", "TOGGLE", {}, {"range": None, "level_b_w": 2.0}),
])
def test_configuration_readback_transient_without_setup_or_trigger_assumptions(
        fake_visa, function, name, waveform, extras, expected):
    readings = {f"{function}:TRAN:MODE?": waveform, f"{function}:TRAN:ALEV?": "1",
                f"{function}:TRAN:BLEV?": "2", **extras}
    fake_visa.responses.update({"FUNC:MODE?": "FIX", "FUNC?": function, "TRAN?": "0", **readings})
    result = control.execute("transient-status", resource=RESOURCE, hardware_validation="readback")
    assert result["configured"] and result["function"] == name and result["enabled"] is False
    assert expected.items() <= result["configuration"].items()
    assert result["input_state"] == "ON" and result["last_trigger"] is None
    assert {call for call in fake_visa.calls if isinstance(call, str) and "?" in call} == {
        "*IDN?", "FUNC:MODE?", "FUNC?", "TRAN?", "INP:STAT?", *readings,
    }


def test_configuration_readback_transient_summary_and_range_guard(fake_visa):
    fake_visa.responses.update({"FUNC:MODE?": "FIX", "FUNC?": "CURR", "TRAN?": "1",
                                "INP:SHOR:STAT?": "0", "CURR:RANG?": "6",
                                "CURR:TRAN:MODE?": "CONT", "CURR:TRAN:ALEV?": "1",
                                "CURR:TRAN:BLEV?": "2"})
    summary = readonly.execute("readback", resource=RESOURCE, hardware_validation="readback")
    assert summary["active_mode"] == "transient" and summary["transient_enabled"]
    assert summary["configuration"] == {"transient_mode": "continuous", "range": "low",
                                        "range_readback": 6, "level_a_a": 1, "level_b_a": 2}
    assert not any("AWID?" in str(call) or "BWID?" in str(call) or "SLEW" in str(call)
                   for call in fake_visa.calls)
    fake_visa.responses["CURR:TRAN:ALEV?"] = "60"
    with pytest.raises(LoadError) as failure:
        readonly.execute("readback", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_response" and failure.value.details["field"] == "level_a"


def test_configuration_readback_list_reads_observed_steps_and_rejects_bad_count(fake_visa):
    readings = {"LIST:RANG?": "15", "LIST:STEP?": "3", "LIST:COUN?": "65536"}
    for index in range(1, 4):
        readings.update({f"LIST:LEV? {index}": str(index), f"LIST:SLEW? {index}": "0.1",
                         f"LIST:WID? {index}": "0.01234567890123456789"})
    readings["LIST:WID? 1"] = "3600"
    fake_visa.responses.update({"FUNC:MODE?": "LIST", "FUNC?": "CURR", "TRAN?": "0", **readings})
    result = control.execute("list-status", resource=RESOURCE, hardware_validation="readback")
    actual = result["configuration"]
    assert result["configured"] and result["selected"] is True and result["slot"] is None
    assert actual["range_readback_a"] == 15 and actual["range"] is None
    assert actual["step_count"] == 3 and actual["repeat_count"] == 65536
    assert [step["current_a"] for step in actual["steps"]] == [1, 2, 3]
    assert actual["steps"][0]["width_s"] == "3600"
    assert actual["steps"][2]["width_s"] == "0.01234567890123456789"
    assert {call for call in fake_visa.calls if isinstance(call, str) and "?" in call} == {
        "*IDN?", "FUNC:MODE?", "FUNC?", "TRAN?", "INP:STAT?", *readings,
    }

    fake_visa.calls.clear()
    fake_visa.responses["INP:SHOR:STAT?"] = "0"
    summary = readonly.execute("readback", resource=RESOURCE, hardware_validation="readback")
    assert summary["active_mode"] == "list" and summary["configuration"]["step_count"] == 3
    assert "steps" not in summary["configuration"] and "LIST:LEV? 1" not in fake_visa.calls

    fake_visa.responses["LIST:WID? 1"] = "1E+40"
    with pytest.raises(LoadError) as failure:
        control.execute("list-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_response" and failure.value.exit_code == 3
    assert failure.value.details == {"query": "LIST:WID? 1", "step": 1, "observed": "1E+40"}

    fake_visa.responses["LIST:WID? 1"] = readings["LIST:WID? 1"]
    fake_visa.responses.update({"LIST:RANG?": "6", "LIST:SLEW? 1": "2"})
    with pytest.raises(LoadError) as failure:
        control.execute("list-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_response" and failure.value.details["query"] == "LIST:SLEW? 1"

    fake_visa.calls.clear()
    fake_visa.responses["LIST:STEP?"] = "85"
    with pytest.raises(LoadError) as failure:
        control.execute("list-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_response" and failure.value.exit_code == 3
    assert "LIST:LEV? 1" not in fake_visa.calls


def test_configuration_readback_mismatch_in_simulated_von_write_is_reported():
    from electronicload_tool_core.simulator import ControlSimulator

    class IgnoredLatch(ControlSimulator):
        def query(self, command):
            return "1" if command == "VOLT:LATC?" else super().query(command)

    with pytest.raises(LoadError) as failure:
        control.execute("set-von", mode="simulate", simulator=IgnoredLatch(), voltage="2", latch="off")
    assert failure.value.code == "readback_mismatch"


@pytest.fixture
def sequence_visa(fake_visa, monkeypatch):
    fake = ValidationFake()
    fake.values.update({"INP:TIM": "0", "MEAS:VOLT": "5", "MEAS:CURR": ".1", "FETC:POW": ".5"})
    monkeypatch.setattr(fake_visa.session, "query", lambda command: fake.query(command))
    monkeypatch.setattr(fake_visa.session, "write", fake.write, raising=False)

    # Normal session creation still uses the recording factory.
    return fake, fake_visa.calls


SEQUENCE_CC = ("set-cc", {"current": 0.1, "range": "auto"})


def sequence_trial_document(setup, loops, wait):
    steps = (setup, ("input-on", {}), ("input-state", {}), ("wait", {"seconds": wait}),
             ("measure", {}), ("input-off", {}), ("input-state", {}))
    return {"version": 1, "loop_count": loops,
            "steps": [{"action": action, "arguments": args} for action, args in steps]}


def run_sequence(setup=SEQUENCE_CC, *, loops=1, wait=0.25, **kwargs):
    from electronicload_tool_core.sequence import execute

    return execute(sequence_trial_document(setup, loops, wait),
                   **{"resource": RESOURCE, "hardware_validation": "sequence", "confirmed": True, **kwargs})


def sequence_cli_error(tmp_path, capsys):
    file = tmp_path / "sequence.json"
    file.write_text(json.dumps(sequence_trial_document(SEQUENCE_CC, 1, 0.25)), encoding="utf-8")
    assert cli.main(["sequence", "--file", str(file), "--resource", RESOURCE,
                     "--hardware-validation", "sequence", "--confirm", "--json"]) == 3
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "sequence_cleanup_unconfirmed"
    assert "Independently switch off" in error["message"]
    return error["details"]


@pytest.mark.parametrize(("setup", "write", "query"), [
    (SEQUENCE_CC, "CURR 0.1", "CURR?"),
    (("set-cv", {"voltage": 3, "range": "auto"}), "VOLT 3", "VOLT?"),
    (("set-cr", {"resistance": 1000, "range": "auto"}), "RES 1000", "RES?"),
    (("set-cw", {"power": 0.10}), "POW 0.1", "POW?"),
])
def test_sequence_live_fixed_modes_in_one_session(sequence_visa, setup, write, query):
    fake, calls = sequence_visa
    if setup == SEQUENCE_CC:
        def rounded_cc_readback(inst, command):
            if command == write:
                inst.current = 0.10001

        fake.after_write = rounded_cc_readback
    result = run_sequence(setup)
    assert calls.count("open_resource") == 1
    assert calls[-2:] == ["session_close", "manager_close"]
    assert fake.calls.count("*IDN?") == 1
    assert fake.calls.count("INP ON") == fake.calls.count("INP OFF") == 1
    index = fake.calls.index(write)
    assert fake.calls[index - 1] == "INP:STAT?"
    first_on = fake.calls.index("INP ON", index)
    assert query in fake.calls[index:first_on]
    remote = max(i for i in range(index, first_on) if fake.calls[i] == "SYST:REM")
    assert query in fake.calls[remote:first_on] and "INP:SHOR:STAT?" in fake.calls[remote:first_on]
    assert not any(call.startswith(("RES:LED:STAT ", "*RST", "SYST:LOC", "TRAN OFF", "SYST:ERR?"))
                   for call in fake.calls)
    assert result["validation_only"] and result["cleanup"]["off_confirmed"] and not fake.input
    assert result["measurements"][0]["step_index"] == 5


@pytest.mark.parametrize(("options", "code"), [
    ({"backend": "py"}, "unsupported_backend"),
    ({"resource": "TCPIP::SYNTHETIC::INSTR"}, "invalid_resource"),
    ({"model_id": "unregistered"}, "unsupported_model"),
    ({"confirmed": False}, "confirmation_required"),
    ({"hardware_validation": None}, "unsupported_live_scope"),
    ({"loops": 2}, "unsupported_live_scope"),
    ({"wait": 3600}, "unsupported_live_scope"),
    ({"setup": ("set-cc", {"current": 1, "range": "auto"})}, "unsupported_live_scope"),
    ({"setup": ("set-cc", {"current": 0.1, "range": "auto", "rise_slew": 0.1, "fall_slew": 0.1})},
     "unsupported_live_scope"),
])
def test_sequence_live_scope_and_confirmation_refuse_before_io(sequence_visa, options, code):
    fake, calls = sequence_visa
    with pytest.raises(LoadError) as error:
        run_sequence(**options)
    assert error.value.code == code and "manager" not in calls and fake.calls == []


@pytest.mark.parametrize(("condition", "code"), [
    ("wrong_identity", "identity_mismatch"),
    ("initial_on", "input_not_off"),
    ("already_list", "list_selected"),
    ("cr_led", "cr_led_enabled"),
    ("remote_short", "unsafe_mode"),
    ("remote_transient", "unsafe_mode"),
    ("remote_input", "sequence_cleanup_unconfirmed"),
    ("remote_stop", "sequence_cleanup_unconfirmed"),
    ("setup_remote_stop", "sequence_cleanup_unconfirmed"),
    ("remote_setpoint", "readback_mismatch"),
])
def test_sequence_live_guards_before_writes_and_after_remote(sequence_visa, condition, code):
    fake, calls = sequence_visa
    stop = Event()
    if condition == "wrong_identity":
        fake.values["*IDN"] = "ITECH Ltd,IT8812B,SYNTHETIC,1.0"
    elif condition == "initial_on":
        fake.input = True
    elif condition == "already_list":
        fake.mode = "LIST"
    elif condition == "cr_led":
        fake.function = "RES"
        fake.values["RES:LED:STAT"] = "1"
    elif condition.endswith("remote_stop"):
        remote_count = 1 if condition == "setup_remote_stop" else 2

        def stop_on_remote(inst):
            if inst.calls.count("SYST:REM") == remote_count:
                inst.input = True
                stop.set()

        fake.after_remote = stop_on_remote
    else:
        attr, value = {"remote_short": ("short", True), "remote_transient": ("transient", True),
                       "remote_input": ("input", True), "remote_setpoint": ("current", 0.2)}[condition]
        fake.after_remote = lambda inst: setattr(inst, attr, value) if inst.calls.count("SYST:REM") == 2 else None
    with pytest.raises(LoadError) as error:
        run_sequence(stop=stop)
    assert error.value.code == code
    assert "INP ON" not in fake.calls and "INP OFF" not in fake.calls
    assert calls.count("open_resource") == 1
    if condition == "initial_on":
        assert fake.input and fake.calls == ["*IDN?", "INP:STAT?"]
    if code == "sequence_cleanup_unconfirmed":
        details = error.value.details
        # The setup guard reads Input even when Stop is set; the ON guard checks Stop first.
        observed_on = condition != "remote_stop"
        assert details["original_error"]["code"] == ("input_not_off" if observed_on else "stopping")
        assert details["input_state"] == ("ON" if observed_on else "UNKNOWN")
        assert details["cleanup"]["reason"] == ("input_not_off" if observed_on else "remote_input_unconfirmed")
        assert not details["cleanup"]["off_confirmed"] and not details["cleanup"]["attempted"]
        assert "Independently switch off" in str(error.value) and fake.input
    elif condition in {"remote_short", "remote_transient", "remote_setpoint"}:
        assert error.value.details["cleanup"]["off_confirmed"]


def test_sequence_lost_remote_reply_requires_manual_off(sequence_visa, tmp_path, capsys):
    fake, calls = sequence_visa

    def lose_input_state(inst):
        if inst.calls.count("SYST:REM") == 2:
            inst.response_errors["INP:STAT?"] = TimeoutError("PRIVATE")

    fake.after_remote = lose_input_state
    details = sequence_cli_error(tmp_path, capsys)
    assert details["failed_step"]["action"] == "input-on"
    assert details["original_error"]["code"] == "query_failed"
    assert details["input_state"] == "UNKNOWN"
    assert details["cleanup"]["reason"] == "session_untrusted"
    assert not details["cleanup"]["attempted"] and not details["cleanup"]["off_confirmed"]
    assert calls.count("open_resource") == 1
    assert fake.calls[-2:] == ["SYST:REM", "INP:STAT?"]
    assert "INP ON" not in fake.calls and "INP OFF" not in fake.calls


@pytest.mark.parametrize("condition", ["stop", "off_confirmation", "lost_reply", "uncertain_write", "interrupted_io"])
def test_sequence_live_cleans_up_on_stop_and_reports_uncertain_off(sequence_visa, monkeypatch, condition):
    fake, calls = sequence_visa
    stop = Event()
    query = fake.query
    if condition in {"stop", "off_confirmation"}:
        def read(command):
            response = query(command)
            if command == "FETC:POW?":
                stop.set()
            return response

        monkeypatch.setattr(fake, "query", read)
        # The recording VISA session delegates to the fake query.
        monkeypatch.setattr(fake, "after_write", (
            (lambda inst, cmd: setattr(inst, "input", True) if cmd == "INP OFF" else None)
            if condition == "off_confirmation" else None))
        monkeypatch.setattr(control, "INPUT_STATE_CONFIRM_TIMEOUT_S", 0.01)
    elif condition in {"lost_reply", "interrupted_io"}:
        fake.values.pop("FETC:POW")
        fake.response_errors["FETC:POW?"] = (
            TimeoutError("PRIVATE") if condition == "lost_reply" else KeyboardInterrupt())
    else:
        fake.write_fail = "INP ON"
    with pytest.raises(LoadError) as error:
        run_sequence(stop=stop)
    details = error.value.details
    trusted = condition in {"stop", "off_confirmation"}
    assert calls.count("open_resource") == 1
    assert fake.calls.count("INP ON") == 1
    assert fake.calls.count("INP OFF") == int(trusted)
    assert details["cleanup"]["attempted"] is trusted
    assert error.value.code == ("stopping" if condition == "stop" else "sequence_cleanup_unconfirmed")
    assert details["input_state"] == ("OFF" if condition == "stop" else "UNKNOWN")
    if trusted:
        assert details["measurements"][0]["power_w"] == 0.5
        assert details["failed_step"]["action"] == "input-off"
    else:
        assert details["cleanup"]["reason"] == "session_untrusted"
    assert "PRIVATE" not in json.dumps(error.value.as_dict())


@pytest.mark.parametrize(("outcome", "cause", "reason", "state"), [
    ("explicit_off", "input_off_not_confirmed", "explicit_off_unconfirmed", "UNKNOWN"),
    ("loop_end_lost_reply", "query_failed", "session_untrusted", "UNKNOWN"),
    ("loop_end_on", "input_not_off", "input_not_off", "ON"),
    ("stop_after_reading_on", "stopping", "input_not_off", "ON"),
])
def test_sequence_does_not_resend_an_unconfirmed_explicit_off(
    sequence_visa, monkeypatch, tmp_path, capsys, outcome, cause, reason, state,
):
    fake, calls = sequence_visa
    stop = Event()
    if outcome == "explicit_off":
        monkeypatch.setattr(control, "INPUT_STATE_CONFIRM_TIMEOUT_S", 0.01)
        fake.after_write = lambda inst, cmd: setattr(inst, "input", True) if cmd == "INP OFF" else None
    else:
        query = fake.query

        def read_final_input_state(command):
            if command == "INP:STAT?" and "INP OFF" in fake.calls:
                after_off = fake.calls[fake.calls.index("INP OFF") + 1:]
                reads_since_off = after_off.count(command)
                # First confirm the OFF write, then read the last step's Input state.
                if outcome == "stop_after_reading_on" and reads_since_off == 1:
                    fake.input = True
                    stop.set()
                elif reads_since_off == 2:
                    if outcome == "loop_end_on":
                        fake.input = True
                    else:
                        fake.response_errors[command] = TimeoutError("PRIVATE")
            return query(command)

        monkeypatch.setattr(fake, "query", read_final_input_state)
    if outcome == "stop_after_reading_on":
        with pytest.raises(LoadError) as error:
            run_sequence(stop=stop)
        assert error.value.code == "sequence_cleanup_unconfirmed"
        assert "Independently switch off" in str(error.value)
        details = error.value.details
    else:
        details = sequence_cli_error(tmp_path, capsys)
    assert calls.count("open_resource") == 1
    assert fake.calls.count("INP OFF") == 1
    assert details["original_error"]["code"] == cause
    assert details["input_state"] == state
    assert details["cleanup"]["reason"] == reason
    assert not details["cleanup"]["attempted"] and not details["cleanup"]["off_confirmed"]
    if outcome != "explicit_off":
        assert details["failed_step"] is None
        assert details["input_states"][-1]["input_state"] == ("ON" if stop.is_set() else "OFF")
