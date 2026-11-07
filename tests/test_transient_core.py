"""Offline Transient validation, state guards and physical isolation."""
from copy import deepcopy
from decimal import Decimal
from threading import Event

import pytest

from electronicload_tool_core import control
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.simulator import ControlSimulator


def config(mode='continuous', **values):
    result = dict(transient_mode=mode, range='auto', level_a='1', level_b='2',
                  rise_slew='0.1', fall_slew='0.1')
    result.update({'width_a': '0.001', 'width_b': '0.002'} if mode == 'continuous'
                  else {'pulse_width': '0.001'} if mode == 'pulse' else {})
    return {**result, **values}


def run(sim, command, **kwargs):
    return control.execute(command, mode='simulate', simulator=sim, **kwargs)


@pytest.mark.parametrize('mode', ['continuous', 'pulse', 'toggle'])
def test_transient_complete_cycle_and_trigger_record(mode):
    sim = ControlSimulator()
    initial = run(sim, 'transient-status')
    assert initial == dict(configured=False, mode=None, range=None, enabled=False,
                           last_trigger=None, configuration=None)
    setup = run(sim, 'set-transient-cc', transient=config(mode))
    assert setup['configured'] and setup['range'] == 'low'
    assert not sim.input_on and not setup['enabled']
    run(sim, 'transient-enable')
    assert not sim.input_on
    run(sim, 'input-on', confirmed=True)
    first = run(sim, 'transient-trigger')
    second = run(sim, 'transient-trigger')
    assert first['last_trigger']['sequence'] == 1
    assert second['last_trigger']['sequence'] == 2
    assert second['last_trigger']['source'] == 'software'
    assert second['last_trigger']['accepted_at_utc']
    assert sim.input_on and second['enabled']
    run(sim, 'input-off')
    assert run(sim, 'transient-status')['enabled']  # OFF does not disable.
    disabled = run(sim, 'transient-disable')
    assert not disabled['enabled'] and disabled['configured']
    assert disabled['last_trigger'] == second['last_trigger']
    assert not sim.input_on
    assert 'waveform_complete' not in disabled and 'running' not in disabled
    run(sim, 'set-cc', current='0.1', range_name='low')
    assert run(sim, 'transient-status') == initial
    run(sim, 'input-on', confirmed=True)
    run(sim, 'input-off')


@pytest.mark.parametrize('values,selected', [
    ({}, 'low'),
    ({'level_a': '0', 'level_b': '6', 'rise_slew': '.0001', 'fall_slew': '.25'}, 'low'),
    ({'level_a': '60', 'level_b': '0', 'rise_slew': '.001', 'fall_slew': '2.5'}, 'high'),
    ({'level_a': '6.001'}, 'high'),
    ({'fall_slew': '.251'}, 'high'),
    ({'rise_slew': '.251'}, 'high'),
    ({'range': 'high'}, 'high'),
    ({'level_a': '0.0001'}, 'low'),
    ({'rise_slew': '0.12345678901234567890123456789'}, 'low'),
    ({'width_a': '.000020', 'width_b': '3600'}, 'low'),
])
def test_transient_auto_and_documented_bounds(values, selected):
    result = control.validate_transient_cc(config(**values))
    assert result['range'] == selected
    assert result['rise_slew'] == Decimal(values.get('rise_slew', '.1'))


@pytest.mark.parametrize('values,code', [
    ({'level_a': '-.001'}, 'no_matching_range'),
    ({'level_b': '60.001'}, 'no_matching_range'),
    ({'level_a': '1.00000000000000000000000000000000001'}, 'no_matching_range'),
    ({'level_a': '1e-1000000'}, 'no_matching_range'),
    ({'level_b': '1.00001'}, 'no_matching_range'),
    ({'level_a': '0.0001', 'rise_slew': '.3'}, 'no_matching_range'),
    ({'level_b': '7', 'fall_slew': '.0001'}, 'no_matching_range'),
    ({'rise_slew': '0'}, 'no_matching_range'),
    ({'fall_slew': '2.500000000000000001'}, 'no_matching_range'),
    ({'range': 'low', 'level_b': '6.001'}, 'current_out_of_range'),
    ({'range': 'high', 'level_a': '.0001'}, 'invalid_current_resolution'),
    ({'range': 'low', 'fall_slew': '.251'}, 'slew_out_of_range'),
    ({'range': []}, 'invalid_range'),
    ({'transient_mode': []}, 'argument_error'),
    ({'level_a': True}, 'invalid_transient_value'),
    ({'level_b': 'NaN'}, 'invalid_transient_value'),
    ({'rise_slew': 'Infinity'}, 'invalid_transient_value'),
    ({'fall_slew': None}, 'invalid_transient_value'),
    ({'width_a': '.000019'}, 'width_out_of_range'),
    ({'width_b': '3600.000000000000001'}, 'width_out_of_range'),
    ({'width_a': '.00002000000000000000000000000001'}, 'invalid_width_resolution'),
    ({'width_b': '.0000205'}, 'invalid_width_resolution'),
    ({'pulse_width': '.001'}, 'argument_error'),
    ({'extra': 1}, 'argument_error'),
])
def test_transient_invalid_setup_is_atomic(values, code):
    sim = ControlSimulator()
    run(sim, 'set-transient-cc', transient=config())
    before = deepcopy(vars(sim))
    with pytest.raises(LoadError) as error:
        run(sim, 'set-transient-cc', transient=config(**values))
    assert error.value.code == code
    assert vars(sim) == before


@pytest.mark.parametrize('mode,field', [('continuous', 'width_a'), ('continuous', 'width_b'),
                                       ('pulse', 'pulse_width'), ('toggle', 'rise_slew')])
def test_transient_requires_complete_configuration(mode, field):
    arguments = config(mode)
    del arguments[field]
    with pytest.raises(LoadError, match='exactly'):
        control.validate_transient_cc(arguments)


@pytest.mark.parametrize('mode', ['pulse', 'toggle'])
def test_transient_rejects_inapplicable_width(mode):
    with pytest.raises(LoadError) as error:
        control.validate_transient_cc(config(mode, width_a='.001'))
    assert error.value.code == 'argument_error'


def test_transient_preserves_slew_precision_in_setup_and_status():
    sim = ControlSimulator()
    slew = '0.12345678901234567890123456789'
    setup = run(sim, 'set-transient-cc', transient=config(rise_slew=slew))
    assert setup['configuration']['rise_slew_a_per_us'] == slew
    assert run(sim, 'transient-status') == setup
    preview = control.execute('set-transient-cc', mode='dry-run',
                              transient=config(rise_slew=slew))
    assert preview['rise_slew_a_per_us'] == slew


@pytest.mark.parametrize('selection', ['low', 'auto'])
def test_transient_guards_do_not_perform_hidden_actions(selection):
    sim = ControlSimulator()

    def refused(command, code, **kwargs):
        before = deepcopy(vars(sim))
        with pytest.raises(LoadError) as error:
            run(sim, command, **kwargs)
        assert error.value.code == code
        assert vars(sim) == before

    refused('transient-enable', 'transient_not_configured')
    refused('transient-trigger', 'transient_not_enabled')
    run(sim, 'set-transient-cc', transient=config())
    refused('input-on', 'confirmation_required')
    refused('input-on', 'transient_not_enabled', confirmed=True)
    run(sim, 'transient-enable')
    refused('transient-trigger', 'input_not_on')
    refused('set-transient-cc', 'transient_enabled', transient=config())
    refused('set-cc', 'transient_enabled', current='.1', range_name=selection)
    run(sim, 'input-on', confirmed=True)
    for command in ('transient-enable', 'transient-disable'):
        refused(command, 'input_not_off')
    refused('set-transient-cc', 'input_not_off', transient=config())
    stop = Event()
    stop.set()
    refused('transient-trigger', 'stopping', stop=stop)
    assert sim.input_on and sim.transient_enabled
    run(sim, 'input-off')
    refused('input-on', 'stopping', confirmed=True, stop=stop)
    run(sim, 'transient-disable')
    refused('input-on', 'transient_not_enabled', confirmed=True)
    run(sim, 'set-transient-cc', transient=config('toggle'))
    assert run(sim, 'transient-status')['last_trigger'] is None


@pytest.mark.parametrize('command', control.TRANSIENT_COMMANDS)
def test_transient_dry_run_does_not_inspect_or_change_state(command):
    sim = ControlSimulator()
    sim.input_on = True
    before = deepcopy(vars(sim))
    kwargs = {'transient': config()} if command == 'set-transient-cc' else {}
    result = control.execute(command, mode='dry-run', simulator=sim, **kwargs)
    assert result['hardware_access'] is False and vars(sim) == before


@pytest.mark.parametrize('command', control.TRANSIENT_COMMANDS)
@pytest.mark.parametrize('validation', [False, True])
def test_transient_live_refused_before_visa(command, validation, monkeypatch):
    monkeypatch.setattr(control, 'open_session', lambda *_: pytest.fail('VISA opened'))
    kwargs = {'transient': config()} if command == 'set-transient-cc' else {}
    with pytest.raises(LoadError) as error:
        control.execute(command, resource='USB0::0xFFFF::0x0001::SYNTHETIC::INSTR',
                        validation=validation, **kwargs)
    assert error.value.code == 'unsupported_live_scope'
