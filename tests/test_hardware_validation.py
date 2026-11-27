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


@pytest.mark.parametrize('trial', ['von', 'slew', 'short', 'slew_unrestorable', 'short_untrusted', 'short_unexpected',
                                  'von_transient', 'slew_transient', 'short_transient', 'short_readback',
                                  'von_cv', 'slew_cr', 'short_cw', 'short_list'])
def test_isolated_auxiliary_trial_restores_or_stops_without_input_on(monkeypatch, capsys, trial):
    fake = ValidationFake()
    fake.range = 6
    fake.values.update({'INP:TIM': '0', 'VOLT:ON': '.5', 'VOLT:LATC': '1'})
    session(fake, monkeypatch)
    command = trial.split('_')[0]
    if trial.endswith(('_cv', '_cr', '_cw')):
        fake.function = {'cv': 'VOLT', 'cr': 'RES', 'cw': 'POW'}[trial.rsplit('_', 1)[-1]]
    if trial.endswith('_list'):
        fake.mode = 'LIST'
    if trial.endswith(('_transient', '_readback')):
        fake.transient = True
        fake.values['TRIG:TIM'] = '5'
    args = ['contributor-settings', '--trial', command, '--resource', RESOURCE, '--json']
    if trial.endswith('_readback'):
        args.append('--readback-trial')
    assert cli.main(args) == 2 and not fake.calls
    capsys.readouterr()

    def handle_short(device, write):
        if write in {'INP:SHOR 0', 'INP:SHOR 1'}:
            device.short = write.endswith('1')
            if trial == 'short_untrusted':
                device.response_errors['INP:SHOR:STAT?'] = TimeoutError()
            if trial == 'short_unexpected' and device.short:
                device.input = True

    fake.after_write = handle_short
    if trial == 'slew_unrestorable':
        fake.values['CURR:SLEW:POS'] = '.5'
    original = dict(fake.values)
    exit_code = cli.main([*args, '--confirm-isolated'])
    output = capsys.readouterr()
    result = json.loads(output.out)
    if trial in {'von', 'slew'}:
        baseline = json.loads(output.err.removeprefix('Settings recovery baseline (private): '))
        assert baseline['trial'] == trial
        if trial == 'von':
            assert Decimal(baseline['threshold_v']) == Decimal('.5') and baseline['latch'] == 'on'
        else:
            assert baseline['range'] == 'low'
            assert Decimal(baseline['rise_slew_a_per_us']) == Decimal(original['CURR:SLEW:POS'])
            assert Decimal(baseline['fall_slew_a_per_us']) == Decimal(original['CURR:SLEW:NEG'])
    assert 'INP ON' not in fake.calls
    if trial.endswith(('_transient', '_cv', '_cr', '_cw', '_list')):
        assert exit_code != 0 and result['error']['details']['no_settings_written']
        assert all(call.endswith('?') for call in fake.calls)
        if trial.endswith('_transient'):
            assert fake.transient
        elif trial.endswith('_list'):
            assert fake.mode == 'LIST'
        else:
            assert fake.function == {'cv': 'VOLT', 'cr': 'RES', 'cw': 'POW'}[trial.rsplit('_', 1)[-1]]
    elif trial == 'short_readback':
        assert exit_code == 0 and result['result']['restored']
        assert fake.transient and not fake.input and not fake.short
        assert [call for call in fake.calls if '?' not in call] == [
            'SYST:REM', 'TRAN OFF', 'INP:SHOR 1', 'INP:SHOR 0', 'TRAN ON']
    elif trial == 'slew_unrestorable':
        assert exit_code == 0 and result['result']['restored']
        assert result['result']['baseline_initialized'] is True
        assert result['result']['original_settings_restored'] is False
        assert Decimal(fake.values['CURR:SLEW:POS']) == Decimal('.1')
        assert Decimal(fake.values['CURR:SLEW:NEG']) == Decimal('.1')
        assert 'CURR:SLEW:POS 0.1' in fake.calls
        assert 'INP ON' not in fake.calls
    elif trial == 'short_untrusted':
        assert exit_code == 3 and result['error']['details']['manual_action_required']
        assert [call for call in fake.calls if '?' not in call] == ['SYST:REM', 'INP:SHOR 1']
    elif trial == 'short_unexpected':
        assert exit_code != 0 and result['error']['details']['restored']
        assert fake.calls.index('INP OFF') < fake.calls.index('INP:SHOR 0')
        assert not (fake.short or fake.input)
    else:
        assert exit_code == 0 and result['result']['restored'] and result['result']['input_state'] == 'OFF'
        assert not (fake.short or fake.input)
        assert original.keys() == fake.values.keys()
        for field in ('VOLT:ON', 'VOLT:LATC', 'CURR:SLEW:POS', 'CURR:SLEW:NEG'):
            assert Decimal(original[field]) == Decimal(fake.values[field])
        assert not any(call.startswith('CURR:RANG ') for call in fake.calls)



@pytest.mark.parametrize("trial,function,enabled,fault", [
    ("fixed-cc", "VOLT", False, None), ("fixed-cv", "CURR", False, None),
    ("fixed-cr", "CURR", False, None), ("fixed-cw", "CURR", False, None),
    ("transient", "VOLT", False, None), ("average", "CURR", False, None),
    ("fixed-cc", "CURR", True, None), ("fixed-cc", "CURR", False, None),
    ("transient", "CURR", True, None),
    ("fixed-cc", "VOLT", False, "selector"), ("fixed-cc", "VOLT", False, "within"),
    ("fixed-cc", "VOLT", False, "outside"), ("fixed-cc", "VOLT", False, "zero_selector"),
    ("fixed-cc", "VOLT", False, "function_changed"),
])
def test_automatic_readback_trials_restore_original_mode_and_settings(
        monkeypatch, capsys, trial, function, enabled, fault):
    fake = ValidationFake()
    fake.function = function
    fake.transient = enabled
    fake.range = 6
    if trial == "fixed-cc" and function == "VOLT":
        fake.range = fake.current = 0.1
    elif trial == "fixed-cc" and not enabled:
        fake.range = 60
    if fault == "zero_selector":
        fake.range = 0
    original_range = fake.range
    if fault and fault != "zero_selector":
        def inject(device, command):
            if command == "CURR 0.2":
                if fault == "selector":
                    device.range = 6.0  # A Low range reply need not echo the candidate.
                elif fault == "function_changed":
                    device.function = "POW"
                else:
                    device.current = {"within": 0.20004, "outside": 0.20006}[fault]

        fake.after_write = inject
    fake.values.update({"INP:TIM": "0", "TRIG:TIM": "5", "SENS:AVER:COUN": "14"})
    initial = dict(fake.values)
    current = fake.current
    session(fake, monkeypatch)
    args = ["contributor-settings", "--trial", trial, "--resource", RESOURCE, "--json"]
    assert cli.main(args) == 2 and not fake.calls
    capsys.readouterr()
    failed = fault in {"outside", "zero_selector", "function_changed"}
    assert cli.main([*args, "--confirm-isolated"]) == (3 if failed else 0)
    output = capsys.readouterr()
    payload = json.loads(output.out)
    if fault == "zero_selector":
        assert payload["error"]["code"] == "readback_mismatch"
        assert payload["error"]["details"]["query"] == "CURR:RANG?"
        assert payload["error"]["details"]["no_settings_written"]
        assert all("?" in call for call in fake.calls)
        assert fake.current == current and fake.range == original_range and fake.function == function
        return
    if fault == "function_changed":
        assert payload["error"]["code"] == "settings_restore_unconfirmed"
        details = payload["error"]["details"]
        assert details["manual_action_required"] and details["original_error"]["code"] == "readback_mismatch"
        assert details["original_error"]["details"]["query"] == "FUNC?"
        assert fake.function == "POW" and not fake.input
        assert [call for call in fake.calls if call.startswith(("CURR:RANG ", "CURR "))] == [
            "CURR:RANG 0.2", "CURR 0.2"]
        assert "INP ON" not in fake.calls
        return
    if failed:
        assert payload["error"]["code"] == "readback_mismatch"
        result = payload["error"]["details"]
        assert result["query"] == "CURR?"
    else:
        result = payload["result"]
    assert result["restored"] and result["input_state"] == "OFF"
    assert fake.function == function and fake.mode == "FIX"
    assert not fake.input and fake.transient == enabled
    assert fake.current == current and fake.range == original_range
    if trial == "fixed-cc":
        writes = [call for call in fake.calls if call.startswith(("CURR:RANG ", "CURR "))]
        if original_range == 0.1:
            assert writes == ["CURR:RANG 0.2", "CURR 0.2", "CURR:RANG 0.1", "CURR 0.1"]
        elif original_range == 6:
            assert writes == ["CURR:RANG 0.1", "CURR 0.1", "CURR:RANG 6.0", "CURR 0.0"]
        else:
            assert writes == ["CURR 0.1", "CURR 0.0"]
    assert "INP ON" not in fake.calls
    for key, value in initial.items():
        try:
            assert Decimal(fake.values[key]) == Decimal(value)
        except ArithmeticError:
            assert fake.values[key] == value


def test_automatic_transient_readback_accepts_scpi_slew_max_and_retains_safe_baseline(
    monkeypatch, capsys,
):
    fake = ValidationFake()
    fake.range = 6
    fake.values.update({
        "CURR:TRAN:MODE": "PULS", "CURR:TRAN:AWID": "2",
        "CURR:SLEW:POS": "0.5", "CURR:SLEW:NEG": "0.5",
        "TRIG:SOUR": "HOLD", "TRIG:TIM": "1", "INP:TIM": "0",
    })
    session(fake, monkeypatch)
    status = cli.main([
        "contributor-settings", "--trial", "transient", "--readback-trial",
        "--resource", RESOURCE, "--json", "--confirm-isolated",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert status == 0, payload
    result = payload["result"]
    assert result["restored"] and result["input_state"] == "OFF"
    assert result["baseline_initialized"] is True
    assert result["original_settings_restored"] is False
    assert fake.values["CURR:SLEW:POS"] == "0.1"
    assert fake.values["CURR:SLEW:NEG"] == "0.1"
    assert Decimal(fake.values["CURR:TRAN:ALEV"]) == Decimal(".1")
    assert not fake.transient and not fake.input
    assert "INP ON" not in fake.calls


@pytest.mark.parametrize("origin", ["CURR", "VOLT", "RES", "POW", "LIST", "transient", "short", "timer", "slow", "led", "wrong-origin", "input"])
def test_isolated_mixed_handoff_then_standalone_guard(monkeypatch, capsys, origin):
    fake = ValidationFake()
    fake.range = 6
    fake.values.update({"INP:TIM": "0", "VOLT:ON": ".5", "VOLT:LATC": "1"})
    fake.function = origin if origin in {"CURR", "VOLT", "RES", "POW"} else "CURR"
    if origin == "LIST":
        fake.mode = "LIST"
    if origin == "input":
        fake.input = True
    if origin == "wrong-origin":
        fake.function = "VOLT"
    if origin == "transient":
        fake.transient = True
    if origin == "short":
        fake.short = True
    if origin == "timer":
        fake.values["INP:TIM"] = "1"
    if origin == "slow":
        fake.values["CURR:SLEW:STAT"] = "1"
    if origin == "led":
        fake.function = "RES"
        fake.values["RES:LED:STAT"] = "1"
    session(fake, monkeypatch)
    expected = {"CURR": "CC", "VOLT": "CV", "RES": "CR", "POW": "CW", "led": "CR"}.get(origin, "CC")
    args = ["contributor-settings", "--trial", "fixed-cc-handoff", "--origin-function", expected,
            "--resource", RESOURCE, "--json"]
    assert cli.main(args) == 2 and not fake.calls
    capsys.readouterr()
    code = cli.main([*args, "--confirm-isolated"])
    capsys.readouterr()
    writes = [command for command in fake.calls if "?" not in command]
    if origin in {"CURR", "VOLT", "RES", "POW"}:
        assert code == 0 and fake.function == "CURR" and not fake.input
        assert writes == ([] if origin == "CURR" else ["SYST:REM", "FUNC CURR"])
        assert cli.main(["contributor-settings", "--trial", "von", "--resource", RESOURCE,
                         "--json", "--confirm-isolated"]) == 0
        assert not fake.input
    else:
        assert code != 0 and writes == []


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
    assert failure.value.details == {
        "query": "LIST:WID? 1", "step": 1, "observed": "1E+40",
        "minimum": "0.00002", "maximum": "3600",
    }

    fake_visa.responses["LIST:WID? 1"] = readings["LIST:WID? 1"]
    fake_visa.responses.update({"LIST:RANG?": "6", "LIST:SLEW? 1": "2"})
    with pytest.raises(LoadError) as failure:
        control.execute("list-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_response"
    assert failure.value.details == {
        "query": "LIST:SLEW? 1", "step": 1, "observed": "2",
        "minimum": "0.0001", "maximum": "0.5",
    }

    fake_visa.calls.clear()
    fake_visa.responses["LIST:SLEW? 1"] = "not-a-number"
    with pytest.raises(LoadError) as failure:
        control.execute("list-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_response"
    assert failure.value.details == {
        "query": "LIST:SLEW? 1", "step": 1, "minimum": "0.0001", "maximum": "0.5",
        "parse_failure": True,
    }

    fake_visa.calls.clear()
    fake_visa.responses["LIST:SLEW? 1"] = readings["LIST:SLEW? 1"]
    fake_visa.responses["LIST:STEP?"] = "85"
    with pytest.raises(LoadError) as failure:
        control.execute("list-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_response" and failure.value.exit_code == 3
    assert failure.value.details == {
        "query": "LIST:STEP?", "observed": "85", "minimum": "2", "maximum": "84",
    }
    assert "LIST:LEV? 1" not in fake_visa.calls

    fake_visa.calls.clear()
    fake_visa.responses["LIST:STEP?"] = readings["LIST:STEP?"]
    fake_visa.responses["LIST:RANG?"] = "61"
    with pytest.raises(LoadError) as failure:
        control.execute("list-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_range_readback"
    assert failure.value.details == {
        "query": "LIST:RANG?", "observed": "61", "minimum": "0", "maximum": "60.0",
    }


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

    return fake, fake_visa.calls


SEQUENCE_CC = ("set-cc", {"current": 0.1, "range": "auto"})
SEQUENCE_CV = ("set-cv", {"voltage": 3, "range": "low"})


def sequence_trial_document(setup, *, wait=0.25):
    steps = (setup, ("input-on", {}), ("input-state", {}), ("wait", {"seconds": wait}),
             ("measure", {}), ("input-off", {}), ("input-state", {}))
    return {"version": 1, "loop_count": 1,
            "steps": [{"action": action, "arguments": args} for action, args in steps]}


def run_sequence(setup=SEQUENCE_CC, *, wait=0.25, confirmed=True, resource=RESOURCE, **kwargs):
    from electronicload_tool_core.sequence import execute

    return execute(sequence_trial_document(setup, wait=wait), resource=resource,
                   hardware_validation="sequence", confirmed=confirmed, **kwargs)


def sequence_cli_error(tmp_path, capsys, setup=SEQUENCE_CC):
    file = tmp_path / "sequence.json"
    file.write_text(json.dumps(sequence_trial_document(setup)), encoding="utf-8")
    assert cli.main(["sequence", "--file", str(file), "--resource", RESOURCE,
                     "--hardware-validation", "sequence", "--confirm", "--json"]) == 3
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "sequence_cleanup_unconfirmed"
    assert "Independently switch off" in error["message"]
    return error["details"]


@pytest.mark.parametrize(("setup", "write", "query"), [
    (SEQUENCE_CC, "CURR 0.1", "CURR?"),
    (SEQUENCE_CV, "VOLT 3", "VOLT?"),
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
    assert "session_close" in calls and "manager_close" in calls
    assert fake.calls.count("INP ON") == fake.calls.count("INP OFF") == 1
    index = fake.calls.index(write)
    assert fake.calls[index - 1] == "INP:STAT?"
    first_on = fake.calls.index("INP ON", index)
    remote = max(i for i in range(index, first_on) if fake.calls[i] == "SYST:REM")
    assert query in fake.calls[remote:first_on]
    assert not any(call.startswith(("RES:LED:STAT ", "*RST", "SYST:LOC", "TRAN OFF", "SYST:ERR?"))
                   for call in fake.calls)
    assert result["validation_only"] and result["cleanup"]["off_confirmed"] and not fake.input
    assert result["measurements"][0]["step_index"] == 5


@pytest.mark.parametrize(("options", "code"), [
    ({"resource": "TCPIP::SYNTHETIC::INSTR"}, "invalid_resource"),
    ({"confirmed": False}, "confirmation_required"),
    ({"wait": 3600}, "unsupported_live_scope"),
    ({"setup": ("set-cc", {"current": 1, "range": "auto"})}, "unsupported_live_scope"),
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
])
def test_sequence_live_refuses_unsafe_initial_state(sequence_visa, condition, code):
    fake, calls = sequence_visa
    if condition == "wrong_identity":
        fake.values["*IDN"] = "ITECH Ltd,IT8812B,SYNTHETIC,1.0"
    elif condition == "initial_on":
        fake.input = True
    else:
        fake.mode = "LIST"
    with pytest.raises(LoadError) as error:
        run_sequence()
    assert error.value.code == code
    assert all(command.endswith("?") for command in fake.calls)
    assert calls.count("open_resource") == 1
    if condition == "initial_on":
        assert fake.input


def test_sequence_reports_input_seen_during_fixed_readback(sequence_visa, monkeypatch, tmp_path, capsys):
    fake, calls = sequence_visa
    query = fake.query

    def changed_voltage(command):
        if command == "VOLT?":
            fake.input = True
            fake.values["VOLT"] = "4"
        return query(command)

    monkeypatch.setattr(fake, "query", changed_voltage)
    details = sequence_cli_error(tmp_path, capsys, SEQUENCE_CV)
    assert details["failed_step"]["action"] == "set-cv"
    assert details["original_error"]["code"] == "readback_mismatch"
    assert details["input_state"] == "ON" and not details["cleanup"]["off_confirmed"]
    assert calls.count("open_resource") == 1
    assert "INP ON" not in fake.calls and "INP OFF" not in fake.calls


@pytest.mark.parametrize(("condition", "code"), [
    ("short", "unsafe_mode"),
    ("stop", "sequence_cleanup_unconfirmed"),
])
def test_sequence_rechecks_safety_after_remote(sequence_visa, condition, code):
    fake, _ = sequence_visa
    stop = Event()

    def change_after_setup(inst):
        if inst.current != 0.1:
            return
        if condition == "short":
            inst.short = True
        else:
            stop.set()

    fake.after_remote = change_after_setup
    with pytest.raises(LoadError) as error:
        run_sequence(stop=stop)
    assert error.value.code == code
    assert "INP ON" not in fake.calls and "INP OFF" not in fake.calls
    details = error.value.details
    if condition == "stop":
        assert details["original_error"]["code"] == "stopping"
        assert details["input_state"] == "UNKNOWN" and not details["cleanup"]["off_confirmed"]
    else:
        assert details["input_state"] == "OFF" and details["cleanup"]["off_confirmed"]


@pytest.mark.parametrize("condition", ["stop", "off_confirmation", "lost_reply"])
def test_sequence_live_cleans_up_only_when_communication_is_trusted(sequence_visa, monkeypatch, condition):
    fake, calls = sequence_visa
    stop = Event()
    if condition == "lost_reply":
        fake.values.pop("FETC:POW")
        fake.response_errors["FETC:POW?"] = TimeoutError("PRIVATE")
    else:
        query = fake.query

        def stop_after_measurement(command):
            response = query(command)
            if command == "FETC:POW?":
                stop.set()
            return response

        monkeypatch.setattr(fake, "query", stop_after_measurement)
        if condition == "off_confirmation":
            monkeypatch.setattr(control, "INPUT_STATE_CONFIRM_TIMEOUT_S", 0.01)
            fake.after_write = lambda inst, cmd: setattr(inst, "input", True) if cmd == "INP OFF" else None
    with pytest.raises(LoadError) as error:
        run_sequence(stop=stop)
    details = error.value.details
    trusted = condition != "lost_reply"
    assert calls.count("open_resource") == 1
    assert fake.calls.count("INP OFF") == int(trusted)
    assert details["cleanup"]["attempted"] is trusted
    assert error.value.code == ("stopping" if condition == "stop" else "sequence_cleanup_unconfirmed")
    assert details["input_state"] == ("OFF" if condition == "stop" else "UNKNOWN")
    if not trusted:
        assert details["cleanup"]["reason"] == "session_untrusted"
        assert "PRIVATE" not in json.dumps(error.value.as_dict())


@pytest.mark.parametrize(("observed_on", "cause", "state"), [
    (False, "input_off_not_confirmed", "UNKNOWN"),
    (True, "input_not_off", "ON"),
])
def test_sequence_does_not_resend_explicit_off(
    sequence_visa, monkeypatch, tmp_path, capsys, observed_on, cause, state,
):
    fake, calls = sequence_visa
    if observed_on:
        query = fake.query

        def turn_on_after_confirmation(command):
            response = query(command)
            if command == "INP:STAT?" and "INP OFF" in fake.calls:
                fake.input = True
            return response

        monkeypatch.setattr(fake, "query", turn_on_after_confirmation)
    else:
        monkeypatch.setattr(control, "INPUT_STATE_CONFIRM_TIMEOUT_S", 0.01)
        fake.after_write = lambda inst, cmd: setattr(inst, "input", True) if cmd == "INP OFF" else None
    details = sequence_cli_error(tmp_path, capsys)
    assert calls.count("open_resource") == 1
    assert fake.calls.count("INP OFF") == 1
    assert details["original_error"]["code"] == cause
    assert details["input_state"] == state
    assert not details["cleanup"]["attempted"] and not details["cleanup"]["off_confirmed"]
