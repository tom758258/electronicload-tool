"""Offline LIST limits, complete requests, explicit mode changes and Input safety."""

from copy import deepcopy
from decimal import Decimal
from threading import Event

import pytest

from electronicload_tool_core import control
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.simulator import ControlSimulator


def configuration(**changes):
    return {"range": "auto", "repeat_count": 1, "steps": [
        {"current_a": 1, "slew_a_per_us": Decimal("0.1"), "width_s": Decimal("0.001")},
        {"current_a": 2, "slew_a_per_us": Decimal("0.2"), "width_s": Decimal("0.002")},
    ], **changes}


def run(simulator, command, **arguments):
    return control.execute(command, mode="simulate", simulator=simulator, **arguments)


def test_list_cycle_save_recall_overwrite_and_snapshot_isolation():
    sim = ControlSimulator()
    initial = run(sim, "list-status")
    assert not initial["configured"] and not initial["selected"]
    assert initial["slot"] is None and initial["last_trigger"] is None
    setup = run(sim, "set-list-cc", list_configuration=configuration(repeat_count=65536))
    assert setup["selected"] and setup["range"] == "low"
    assert setup["configuration"]["repeat_count"] == 65536
    assert setup["state_source"] == "simulate" and not sim.input_on
    for slot in (1, 7):
        assert run(sim, "list-save", slot=slot)["slot"] == slot
    run(sim, "input-on", confirmed=True)
    first = run(sim, "list-trigger")
    second = run(sim, "list-trigger")
    assert first["last_trigger"]["sequence"] == 1
    assert second["last_trigger"]["sequence"] == 2
    assert second["last_trigger"]["source"] == "software"
    assert second["last_trigger"]["accepted_at_utc"]
    assert sim.input_on and run(sim, "list-status") == second
    run(sim, "input-off")
    exited = run(sim, "list-exit")
    assert exited["function_mode"] == "FIX" and not exited["selected"]
    assert exited["configuration"] == setup["configuration"]
    assert exited["last_trigger"] == second["last_trigger"]
    run(sim, "set-cv", voltage=5, range_name="low")
    recalled = run(sim, "list-recall", slot=1)
    assert sim.function == "CURR" and recalled["selected"] and not sim.input_on
    assert recalled["configuration"] == setup["configuration"]
    assert recalled["last_trigger"] is None
    # Mutating returned snapshots cannot change active configuration or saved slots.
    recalled["configuration"]["steps"][0]["current_a"] = 50
    assert run(sim, "list-status")["configuration"] == setup["configuration"]
    run(sim, "set-list-cc", list_configuration=configuration(repeat_count=3))
    run(sim, "list-save", slot=1)  # Explicit overwrite, without another confirmation.
    assert run(sim, "list-recall", slot=7)["configuration"]["repeat_count"] == 65536
    assert run(sim, "list-recall", slot=1)["configuration"]["repeat_count"] == 3
    assert not ControlSimulator().list_slots
    assert all(key not in second for key in ("completed", "running", "waveform_complete"))


@pytest.mark.parametrize("level,slew,selected", [
    (0, "0.0001", "low"), (6, "0.25", "low"), (60, "2.5", "high"),
    (Decimal("6.001"), "0.1", "high"), (2, "0.251", "high"),
    (Decimal("0.0001"), "0.1", "low"),
])
def test_list_auto_considers_every_step(level, slew, selected):
    data = configuration()
    data["steps"][1].update(current_a=level, slew_a_per_us=Decimal(slew))
    result = control.validate_list_cc(data)
    assert result["range"] == selected
    assert data["range"] == "auto"


@pytest.mark.parametrize("count", [2, 84])
def test_list_step_bounds_and_exact_width_slew(count):
    slew = Decimal("0.12345678901234567890123456789")
    # LIST has width bounds, but no established fixed decimal step.
    width = Decimal("0.00002000000000000000000000001")
    steps = [{"current_a": 0, "slew_a_per_us": slew, "width_s": width} for _ in range(count)]
    steps[-1]["width_s"] = 3600
    sim = ControlSimulator()
    result = run(sim, "set-list-cc", list_configuration=configuration(steps=steps))
    assert len(result["configuration"]["steps"]) == count
    assert result["configuration"]["steps"][0]["width_s"] == str(width)
    assert result["configuration"]["steps"][0]["slew_a_per_us"] == str(slew)
    assert run(sim, "list-status") == result


@pytest.mark.parametrize("changes,code", [
    ({"repeat_count": value}, "invalid_repeat_count")
    for value in (0, -1, 65537, True, 1.0, "65536", None)
] + [
    ({"range": []}, "invalid_range"), ({"range": "medium"}, "invalid_range"),
    ({"steps": []}, "invalid_list_steps"), ({"steps": [{}]}, "invalid_list_steps"),
    ({"steps": [{}] * 85}, "invalid_list_steps"),
    ({"steps": "file.json"}, "invalid_list_steps"), ({"extra": 1}, "argument_error"),
])
def test_list_invalid_complete_request_is_atomic(changes, code):
    sim = ControlSimulator()
    run(sim, "set-list-cc", list_configuration=configuration())
    before = deepcopy(vars(sim))
    with pytest.raises(LoadError) as error:
        run(sim, "set-list-cc", list_configuration=configuration(**changes))
    assert error.value.code == code and vars(sim) == before


@pytest.mark.parametrize("field,value,code", [
    ("current_a", -1, "no_matching_range"), ("current_a", 61, "no_matching_range"),
    ("current_a", Decimal("1.00000000000000000000000000001"), "no_matching_range"),
    ("current_a", Decimal("1e-1000000"), "no_matching_range"),
    ("current_a", True, "invalid_list_value"), ("current_a", "1", "invalid_list_value"),
    ("slew_a_per_us", None, "invalid_list_value"),
    ("slew_a_per_us", float("inf"), "invalid_list_value"),
    ("width_s", Decimal("NaN"), "invalid_list_value"),
    ("width_s", Decimal("0.00001999999999999999"), "width_out_of_range"),
    ("width_s", Decimal("3600.000000000000001"), "width_out_of_range"),
    ("slew_a_per_us", Decimal("2.500000000000000001"), "no_matching_range"),
    ("slew_a_per_us", 0, "no_matching_range"),
])
def test_list_rejects_invalid_final_step_without_partial_setup(field, value, code):
    sim = ControlSimulator()
    data = configuration()
    data["steps"][-1][field] = value
    before = deepcopy(vars(sim))
    with pytest.raises(LoadError) as error:
        run(sim, "set-list-cc", list_configuration=data)
    assert error.value.code == code and vars(sim) == before


def test_list_does_not_mix_ranges_or_change_explicit_range():
    data = configuration()
    data["steps"][0]["current_a"] = Decimal("0.0001")
    data["steps"][1]["current_a"] = 7
    for selected, code in (("auto", "no_matching_range"), ("low", "current_out_of_range"),
                           ("high", "invalid_current_resolution")):
        with pytest.raises(LoadError) as error:
            control.validate_list_cc({**data, "range": selected})
        assert error.value.code == code


@pytest.mark.parametrize("slot", [0, 8, -1, True, "1", 1.0, None])
@pytest.mark.parametrize("command", ["list-save", "list-recall"])
def test_list_invalid_slots_are_rejected(slot, command):
    with pytest.raises(LoadError) as error:
        run(ControlSimulator(), command, slot=slot)
    assert error.value.code == "invalid_list_slot"


def test_list_guards_preserve_state_and_require_explicit_mode_exit():
    sim = ControlSimulator()

    def refused(command, code, **arguments):
        before = deepcopy(vars(sim))
        with pytest.raises(LoadError) as error:
            run(sim, command, **arguments)
        assert error.value.code == code and vars(sim) == before

    refused("list-save", "list_not_configured", slot=1)
    refused("list-recall", "list_slot_empty", slot=1)
    refused("list-trigger", "list_not_selected")
    refused("list-exit", "list_not_selected")
    transient = dict(transient_mode="toggle", range="auto", level_a=1, level_b=2,
                     rise_slew=.1, fall_slew=.1)
    run(sim, "set-transient-cc", transient=transient)
    run(sim, "transient-enable")
    refused("set-list-cc", "transient_enabled", list_configuration=configuration())
    refused("list-recall", "transient_enabled", slot=1)
    run(sim, "transient-disable")
    run(sim, "set-list-cc", list_configuration=configuration())
    refused("list-trigger", "input_not_on")
    for command, arguments in (("set-cc", {"current": .1, "range_name": "low"}),
                               ("set-cv", {"voltage": 5, "range_name": "low"}),
                               ("set-cr", {"resistance": 5, "range_name": "low"}),
                               ("set-cw", {"power": 1}),
                               ("set-transient-cc", {"transient": transient}),
                               ("transient-enable", {}), ("transient-disable", {})):
        refused(command, "list_selected", **arguments)
    refused("input-on", "confirmation_required")
    run(sim, "input-on", confirmed=True)
    for command, arguments in (("set-list-cc", {"list_configuration": configuration()}),
                               ("list-save", {"slot": 1}), ("list-recall", {"slot": 1}),
                               ("list-exit", {})):
        refused(command, "input_not_off", **arguments)
    stop = Event()
    stop.set()
    refused("list-trigger", "stopping", stop=stop)
    assert sim.input_on
    run(sim, "input-off")
    refused("input-on", "stopping", confirmed=True, stop=stop)
    run(sim, "list-exit")
    refused("list-trigger", "list_not_selected")
    run(sim, "set-transient-cc", transient=transient)
    run(sim, "transient-enable")
    run(sim, "input-on", confirmed=True)
    run(sim, "input-off")


@pytest.mark.parametrize("command", control.LIST_COMMANDS)
def test_list_dry_run_ignores_state_and_stop(command):
    sim = ControlSimulator()
    sim.input_on = True
    before = deepcopy(vars(sim))
    arguments = ({"list_configuration": configuration()} if command == "set-list-cc"
                 else {"slot": 1} if command in {"list-save", "list-recall"} else {})
    result = control.execute(command, mode="dry-run", simulator=sim, **arguments)
    assert result["command"] == command and result["hardware_access"] is False
    assert vars(sim) == before


@pytest.mark.parametrize("command", control.LIST_COMMANDS)
@pytest.mark.parametrize("validation", [False, True])
def test_list_live_is_blocked_before_visa(command, validation, monkeypatch):
    monkeypatch.setattr(control, "open_session", lambda *_: pytest.fail("VISA opened"))
    arguments = ({"list_configuration": configuration()} if command == "set-list-cc"
                 else {"slot": 1} if command in {"list-save", "list-recall"} else {})
    with pytest.raises(LoadError) as error:
        control.execute(command, resource="USB0::0xFFFF::0x0001::SYNTHETIC::INSTR",
                        validation=validation, **arguments)
    assert error.value.code == "unsupported_live_scope"


def test_physical_list_input_on_is_refused_before_any_write(fake_visa):
    fake_visa.responses.update({"INP:STAT?": "0", "FUNC?": "CURR", "FUNC:MODE?": "LIST",
                                "CURR:RANG?": "6", "CURR?": "1"})
    with pytest.raises(LoadError) as error:
        control.execute("input-on", resource="USB0::0xFFFF::0x0001::SYNTHETIC::INSTR",
                        confirmed=True, validation=True)
    assert error.value.code == "unsafe_mode"
    assert "SYST:REM" not in fake_visa.calls and "INP ON" not in fake_visa.calls


@pytest.mark.parametrize("state", ["1", "UNKNOWN"])
def test_list_setup_requires_confirmed_off(state):
    class UnsafeInput(ControlSimulator):
        def query(self, command):
            return state if command == "INP:STAT?" else super().query(command)

    sim = UnsafeInput()
    before = deepcopy(vars(sim))
    with pytest.raises(LoadError):
        run(sim, "set-list-cc", list_configuration=configuration())
    assert vars(sim) == before


@pytest.mark.parametrize("operation", ["input-on", "list-trigger"])
def test_list_short_and_stop_race_block_before_input_or_trigger(operation):
    stop = Event()

    class Guarded(ControlSimulator):
        short = False
        stopping = False

        def query(self, command):
            if command == "INP:SHOR:STAT?":
                if self.stopping:
                    stop.set()
                return "1" if self.short else "0"
            return super().query(command)

    sim = Guarded()
    run(sim, "set-list-cc", list_configuration=configuration())
    if operation == "list-trigger":
        run(sim, "input-on", confirmed=True)
    for short, stopping, code in ((True, False, "unsafe_mode"), (False, True, "stopping")):
        sim.short, sim.stopping = short, stopping
        before = deepcopy(vars(sim))
        with pytest.raises(LoadError) as error:
            run(sim, operation, confirmed=operation == "input-on", stop=stop)
        assert error.value.code == code and vars(sim) == before
