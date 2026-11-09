"""Input Timer offline behavior and CLI-only hardware validation using recording fakes."""
import json
from decimal import Decimal
from threading import Event

import pytest

from electronicload_tool_cli.cli import main
from electronicload_tool_core import control
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.simulator import ControlSimulator
from test_control_core import RESOURCE, session
from test_hardware_validation import ValidationFake


def execute(command, sim, **arguments):
    return control.execute(command, mode='simulate', simulator=sim, **arguments)


@pytest.mark.parametrize('seconds', [None, 0, 60001, -1, 1.0, 1.5, True, False,
                                    '1', 'NaN', float('inf'), Decimal('1'), {}, []])
def test_input_timer_invalid_values_are_atomic(seconds):
    sim = ControlSimulator()
    before = vars(sim).copy()
    with pytest.raises(LoadError, match='integer'):
        execute('set-input-timer', sim, seconds=seconds)
    assert vars(sim) == before


@pytest.mark.parametrize('seconds', [1, 60000])
@pytest.mark.parametrize('setup,settings', [
    ('set-cc', {'current': '0.1', 'range_name': 'low'}),
    ('set-cv', {'voltage': '5', 'range_name': 'low'}),
    ('set-cr', {'resistance': '10', 'range_name': 'low'}),
    ('set-cw', {'power': '1'}),
])
def test_input_timer_fixed_cycle_off_guards_and_independent_von(setup, settings, seconds, monkeypatch):
    sim = ControlSimulator()
    execute(setup, sim, **settings)
    execute('set-von', sim, voltage='5', latch='off')
    assert execute('set-input-timer', sim, seconds=seconds) == {'seconds': seconds, 'enabled': False}
    enabled = execute('input-timer-enable', sim)
    assert enabled == {'seconds': seconds, 'enabled': True}
    execute('input-on', sim, confirmed=True)
    # Advancing software time must not expire the simulated Timer or turn Input OFF.
    monkeypatch.setattr(control, 'monotonic', lambda: 1000000)
    for command in control.INPUT_TIMER_COMMANDS:
        if command == 'input-timer-status':
            assert execute(command, sim) == enabled
            continue
        with pytest.raises(LoadError) as error:
            execute(command, sim, **({'seconds': 2} if command == 'set-input-timer' else {}))
        assert error.value.code == 'input_not_off'
    assert sim.input_on
    assert execute('von-status', sim) == {'threshold_v': '5', 'latch': 'off'}
    with pytest.raises(LoadError) as error:
        execute('short-on', sim)
    assert error.value.code == 'input_timer_enabled'
    execute('input-off', sim)
    assert execute('input-timer-status', sim) == enabled
    assert execute('input-timer-disable', sim) == {'seconds': seconds, 'enabled': False}
    assert enabled['enabled'] is True  # Earlier snapshots are independent.


def test_input_timer_short_stop_dry_run_and_unknown_input():
    sim = ControlSimulator()
    execute('input-on', sim, confirmed=True)
    execute('short-on', sim)
    execute('input-off', sim)
    with pytest.raises(LoadError) as error:
        execute('input-timer-enable', sim)
    assert error.value.code == 'unsafe_mode'
    execute('short-off', sim)
    stop = Event()
    stop.set()
    before = vars(sim).copy()
    for command in control.INPUT_TIMER_COMMANDS:
        arguments = {'seconds': 60} if command == 'set-input-timer' else {}
        assert control.execute(command, mode='dry-run', simulator=sim, stop=stop,
                               **arguments)['hardware_access'] is False
        if command != 'input-timer-status':
            with pytest.raises(LoadError) as error:
                execute(command, sim, stop=stop, **arguments)
            assert error.value.code == 'stopping'
    assert vars(sim) == before
    sim.query = lambda _: 'unknown'
    with pytest.raises(LoadError) as error:
        execute('set-input-timer', sim, seconds=5)
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('mode', ['TRAN', 'LIST'])
def test_input_timer_nonfixed_setup_enable_refused_disable_and_status_available(mode):
    sim = ControlSimulator()
    sim.mode = mode
    for command in ('set-input-timer', 'input-timer-enable'):
        with pytest.raises(LoadError) as error:
            execute(command, sim, **({'seconds': 5} if command == 'set-input-timer' else {}))
        assert error.value.code == 'unsafe_mode'
    assert execute('input-timer-disable', sim) == execute('input-timer-status', sim)


def test_input_timer_blocks_transient_and_list_selection_until_explicit_disable():
    sim = ControlSimulator()
    transient = dict(transient_mode='toggle', range='low', level_a=0, level_b=1,
                     rise_slew=0.1, fall_slew=0.1)
    listing = dict(range='low', repeat_count=1,
                   steps=[dict(current_a=i, slew_a_per_us=0.1, width_s=1) for i in (0, 1)])
    execute('set-list-cc', sim, list_configuration=listing)
    execute('list-save', sim, slot=1)
    execute('list-exit', sim)
    execute('input-timer-enable', sim)
    for command, settings in [('set-transient-cc', {'transient': transient}),
                              ('transient-enable', {}),
                              ('set-list-cc', {'list_configuration': listing}),
                              ('list-recall', {'slot': 1})]:
        before = vars(sim).copy()
        with pytest.raises(LoadError) as error:
            execute(command, sim, **settings)
        assert error.value.code == 'input_timer_enabled'
        assert vars(sim) == before
    execute('input-timer-disable', sim)
    execute('set-transient-cc', sim, transient=transient)
    execute('transient-enable', sim)
    execute('transient-disable', sim)
    execute('list-recall', sim, slot=1)
    assert sim.mode == 'LIST'


@pytest.mark.parametrize('command', control.INPUT_TIMER_COMMANDS)
@pytest.mark.parametrize('override', [{}, {'validation': True}, {'hardware_validation': 'cv'}])
def test_input_timer_product_live_refused_before_visa(command, override, monkeypatch):
    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    with pytest.raises(LoadError) as error:
        control.execute(command, resource=RESOURCE,
                        **({'seconds': 5} if command == 'set-input-timer' else {}), **override)
    assert error.value.code == 'unsupported_live_scope'


@pytest.mark.parametrize('mode', ['--simulate', '--dry-run'])
def test_input_timer_cli_json_and_manifest(mode, capsys):
    for command in control.INPUT_TIMER_COMMANDS:
        args = ['--seconds', '60000'] if command == 'set-input-timer' else []
        assert main([command, mode, '--json', *args]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['ok'] is True
        if mode == '--dry-run':
            assert result['result']['hardware_access'] is False
        else:
            assert type(result['result']['seconds']) is int
            assert type(result['result']['enabled']) is bool
    assert main(['manifest', '--json']) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest['schema_version'] == 2
    assert manifest['worker_protocol']['schema_versions'] == [3]
    assert all(manifest['commands'][c]['kind'] == 'async_job' for c in control.INPUT_TIMER_COMMANDS)


@pytest.mark.parametrize('args', [[], ['--seconds', '0'], ['--seconds', '60001'],
                                  ['--seconds', '1.0'], ['--seconds', 'true'],
                                  ['--seconds', 'NaN'], ['--seconds', '1', '--extra']])
def test_input_timer_cli_invalid_arguments(args, capsys):
    assert main(['set-input-timer', '--simulate', '--json', *args]) == 2
    assert json.loads(capsys.readouterr().out)['ok'] is False


@pytest.mark.parametrize('command', control.INPUT_TIMER_COMMANDS)
def test_input_timer_cli_live_refuses_even_with_override(command, capsys):
    args = ['--seconds', '5'] if command == 'set-input-timer' else []
    assert main([command, '--json', '--resource', RESOURCE,
                 '--validation-allow-pending-live-support', *args]) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'unsupported_live_scope'


def timer_validation(command, **kwargs):
    return control.execute(command, resource=RESOURCE, hardware_validation='input-timer', **kwargs)


def test_input_timer_validation_fake_cycle_and_read_only_status(monkeypatch):
    fake = ValidationFake()
    fake.current = 1
    fake.values.update({'INP:TIM:DEL': '10', 'INP:TIM': '0'})
    session(fake, monkeypatch)
    timer_validation('set-input-timer', seconds=5)
    assert 'INP:TIM:DEL 5' in fake.calls
    assert timer_validation('input-timer-enable')['enabled'] is True
    fake.calls.clear()
    assert timer_validation('input-timer-status')['state_source'] == 'instrument_readback'
    assert all(c.endswith('?') for c in fake.calls)
    timer_validation('input-on', confirmed=True,
                     validation_settings={'seconds': 5, 'current': 1, 'range': 'high'})
    assert fake.input
    assert timer_validation('input-timer-status')['input_state'] == 'ON'
    assert timer_validation('input-off')['input_state'] == 'OFF'
    assert timer_validation('input-timer-status')['enabled'] is True
    assert timer_validation('input-timer-disable')['enabled'] is False


@pytest.mark.parametrize('change', ['input', 'short', 'mode', 'stop', 'timer', 'current'])
@pytest.mark.parametrize('after_remote', [False, True])
def test_input_timer_validation_rechecks_on_guards(monkeypatch, change, after_remote):
    fake = ValidationFake()
    fake.current = 1
    fake.values.update({'INP:TIM:DEL': '5', 'INP:TIM': '1'})
    stop = Event()
    def unsafe(f):
        if change == 'input':
            f.input = True
        elif change == 'short':
            f.short = True
        elif change == 'mode':
            f.mode = 'LIST'
        elif change == 'stop':
            stop.set()
        elif change == 'timer':
            f.values['INP:TIM'] = '0'
        else:
            f.current = 2
    if after_remote:
        fake.after_remote = unsafe
    else:
        unsafe(fake)
    session(fake, monkeypatch)
    with pytest.raises(LoadError):
        timer_validation('input-on', confirmed=True, stop=stop,
                         validation_settings={'seconds': 5, 'current': 1, 'range': 'high'})
    assert 'INP ON' not in fake.calls


@pytest.mark.parametrize('reply', ['0', '60001', '1.5', 'NaN', 'unknown'])
def test_input_timer_validation_rejects_bad_readback(monkeypatch, reply):
    fake = ValidationFake()
    fake.current = 1
    fake.values.update({'INP:TIM:DEL': reply, 'INP:TIM': '0'})
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        timer_validation('input-timer-status')
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('failure', ['INP ON', 'query'])
def test_input_timer_validation_uncertain_on_never_retries_or_hides_off(monkeypatch, failure):
    fake = ValidationFake()
    fake.current = 1
    fake.values.update({'INP:TIM:DEL': '5', 'INP:TIM': '1'})
    if failure == 'query':
        def fail_after_on(f, command):
            if command == 'INP ON':
                f.response_errors['INP:STAT?'] = TimeoutError('synthetic')
        fake.after_write = fail_after_on
    else:
        fake.write_fail = failure
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        timer_validation('input-on', confirmed=True,
                         validation_settings={'seconds': 5, 'current': 1, 'range': 'high'})
    assert error.value.details['input_state'] == 'UNKNOWN'
    assert error.value.details['input_on_attempted'] is True
    assert fake.calls.count('INP ON') == 1 and 'INP OFF' not in fake.calls


@pytest.mark.parametrize('command,write,arguments', [
    ('set-input-timer', 'INP:TIM:DEL 5', {'seconds': 5}),
    ('input-timer-enable', 'INP:TIM 1', {}),
    ('input-timer-disable', 'INP:TIM 0', {}),
])
def test_input_timer_validation_requires_matching_write_readback(monkeypatch, command, write, arguments):
    fake = ValidationFake()
    fake.values.update({'INP:TIM:DEL': '10', 'INP:TIM': '1' if command.endswith('disable') else '0'})
    fake.ignore = write
    session(fake, monkeypatch)
    with pytest.raises(LoadError) as error:
        timer_validation(command, **arguments)
    assert error.value.code == 'readback_mismatch'
    assert fake.calls.count(write) == 1
    assert not fake.input and 'INP OFF' not in fake.calls and 'INP ON' not in fake.calls


def test_input_timer_validation_requires_expected_setup_confirmation_and_valid_scope(monkeypatch):
    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    for command, arguments in [('input-on', {}), ('input-on', {'confirmed': True}),
                               ('set-input-timer', {'seconds': True}),
                               ('input-timer-status', {'confirmed': True}),
                               ('set-cc', {'current': 1, 'range_name': 'low'})]:
        with pytest.raises(LoadError):
            timer_validation(command, **arguments)


def test_input_timer_set_and_fixed_mode_change_retain_enabled_state():
    sim = ControlSimulator()
    execute('input-timer-enable', sim)
    execute('set-input-timer', sim, seconds=60)
    execute('set-cv', sim, voltage=5, range_name='low')
    assert execute('input-timer-status', sim) == {'seconds': 60, 'enabled': True}
