"""Verify Sequence preflight and stateful execution without VISA."""

from decimal import Decimal

import pytest

from electronicload_tool_core import control, sequence
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.simulator import ControlSimulator


def step(action, **arguments):
    return {"action": action, "arguments": arguments}


def document(*steps, loops=1):
    return {"version": 1, "loop_count": loops, "steps": list(steps)}


CC = step("set-cc", current=Decimal("0.1"), range="auto")
ON, OFF = step("input-on"), step("input-off")
READ, MEASURE = step("input-state"), step("measure")


@pytest.mark.parametrize(("steps", "index", "reason"), [
    ([CC, ON, OFF, ON, OFF], 4, "fresh_fixed_setup_required"),
    ([CC, ON, CC, OFF], 3, "set_requires_input_off"),
    ([CC, ON, READ], 3, "loop_requires_input_off"),
    ([CC, step("configure")], 2, "invalid_step"),
    ([step("measure", metric="current")], 1, "argument_error"),
    ([step("set-cc", current=0.1, range="auto", rise_slew=0.1)], 1, "argument_error"),
    ([step("wait", seconds=Decimal("3600.00000000000000001"))], 1, "wait_out_of_range"),
])
def test_precheck_reports_location_before_simulator_io(steps, index, reason, monkeypatch):
    sim = ControlSimulator()
    monkeypatch.setattr(sim, "query", lambda _: pytest.fail("invalid document reached I/O"))
    with pytest.raises(LoadError) as error:
        sequence.execute(document(*steps, loops=2), mode="simulate", simulator=sim, confirmed=True)
    assert error.value.code == "sequence_precheck_failed"
    assert error.value.details["loop_index"] == 1
    assert error.value.details["step_index"] == index
    assert error.value.details["action"] == steps[index - 1]["action"]
    assert error.value.details["reason"] == reason


@pytest.mark.parametrize(("count", "loops"), [(0, 1), (256, 1), (1, 256), (40, 251)])
def test_document_size_limits(count, loops):
    with pytest.raises(LoadError) as error:
        sequence.execute(document(*([READ] * count), loops=loops), mode="dry-run")
    assert error.value.code == "argument_error"


def test_dry_run_keeps_existing_state_and_reports_confirmation():
    sim = ControlSimulator()
    sim.input_on = True
    sim.current_a = 0.25
    plan = sequence.execute(document(CC, ON, step("wait", seconds=0), OFF,
                                     step("wait", seconds=3600), loops=2),
                            mode="dry-run", simulator=sim)
    assert plan["hardware_access"] is False
    assert plan["input_on_requires_confirmation"] is True
    assert plan["expanded_step_count"] == 10
    assert plan["steps"][0]["arguments"] == {"current": "0.1", "range": "low"}
    assert sim.input_on and sim.current_a == 0.25
    assert sequence.execute(document(*([READ] * 40), loops=250), mode="dry-run")[
        "expanded_step_count"] == 10000
    assert sequence.execute(document(READ, loops=255), mode="dry-run")["loop_count"] == 255


def test_loops_keep_simulator_state_and_capture_positioned_results():
    sim = ControlSimulator()
    cc_slew = step("set-cc", **CC["arguments"], rise_slew=0.1, fall_slew=0.2)
    sim_steps = (cc_slew, ON, step("wait", seconds=Decimal(".001")), MEASURE, OFF, READ,
                 step("set-cv", voltage=3, range="auto"), ON, OFF,
                 step("set-cr", resistance=1000, range="auto"), ON, OFF,
                 step("set-cw", power=Decimal(".10")), ON, OFF)
    result = sequence.execute(document(*sim_steps, loops=2), mode="simulate",
                              confirmed=True, simulator=sim)
    assert result["completed_loops"] == 2 and result["completed_steps"] == 30
    assert result["measurements"] == [
        {"loop_index": index, "step_index": 4, "action": "measure",
         "voltage_v": 12, "current_a": 0.1, "power_w": pytest.approx(1.2)} for index in (1, 2)
    ]
    assert [value["input_state"] for value in result["input_states"]] == ["OFF", "OFF"]
    assert result["input_state"] == "OFF" and result["cleanup"]["off_confirmed"]
    assert sim.function == "POW" and sim.power_w == Decimal(".10")
    assert sim.fixed_cc_slew == {"rise_slew_a_per_us": "0.1", "fall_slew_a_per_us": "0.2"}


def test_read_only_rejects_initial_on_and_never_writes(monkeypatch):
    sim = ControlSimulator()
    monkeypatch.setattr(sim, "write", lambda _: pytest.fail("read-only sequence wrote to load"))
    data = document(READ, MEASURE)
    result = sequence.execute(data, mode="simulate", simulator=sim)
    assert result["completed_steps"] == 2 and result["input_state"] == "OFF"
    sim.input_on = True
    with pytest.raises(LoadError) as error:
        sequence.execute(data, mode="simulate", simulator=sim)
    assert error.value.code == "input_not_off"
    assert error.value.details["failed_step"] is None
    assert error.value.details["input_state"] == "ON" and sim.input_on


def test_simulator_rechecks_paired_slew_before_input_on(monkeypatch):
    sim = ControlSimulator()
    write = sim.write
    setup = step("set-cc", **CC["arguments"], rise_slew=0.1, fall_slew=0.2)

    def drift_on_remote(command):
        write(command)
        if command == "SYST:REM" and sim.fixed_cc_slew:
            sim.fixed_cc_slew["rise_slew_a_per_us"] = "0.2"

    monkeypatch.setattr(sim, "write", drift_on_remote)
    with pytest.raises(LoadError) as error:
        sequence.execute(document(setup, ON, OFF), mode="simulate", confirmed=True, simulator=sim)
    assert error.value.code == "readback_mismatch"
    assert error.value.details["failed_step"]["action"] == "input-on" and not sim.input_on


def test_simulator_refuses_sequence_on_with_enabled_input_timer():
    sim = ControlSimulator()
    control.execute("set-input-timer", mode="simulate", simulator=sim, seconds=5)
    control.execute("input-timer-enable", mode="simulate", simulator=sim)
    with pytest.raises(LoadError) as error:
        sequence.execute(document(CC, ON, OFF), mode="simulate", confirmed=True, simulator=sim)
    assert error.value.code == "input_timer_enabled"
    assert not sim.input_on and sim.input_timer_enabled


@pytest.mark.parametrize(("setup", "setting", "during", "code"), [
    (step("set-cr", resistance=1000, range="auto"), "cr_led_enabled", "setup", "cr_led_enabled"),
    (step("set-cv", voltage=3, range="auto"), "current_slow_rise_enabled", "on", "current_slow_rise_enabled"),
])
def test_simulator_preserves_interlocks_that_change_on_remote(monkeypatch, setup, setting, during, code):
    sim = ControlSimulator()
    write = sim.write
    commands = []
    source_function = sim.function if during == "setup" else "VOLT"

    def drift_on_remote(command):
        commands.append(command)
        write(command)
        # The selected function distinguishes setup Remote from Input ON Remote.
        if command == "SYST:REM" and sim.function == source_function:
            setattr(sim, setting, True)

    monkeypatch.setattr(sim, "write", drift_on_remote)
    with pytest.raises(LoadError) as error:
        sequence.execute(document(setup, ON, OFF), mode="simulate", confirmed=True, simulator=sim)
    assert error.value.code == code
    assert error.value.details["failed_step"]["action"] == (setup["action"] if during == "setup" else "input-on")
    assert "INP ON" not in commands and not sim.input_on
    assert "RES:LED:STAT 0" not in commands and getattr(sim, setting)


def test_each_loop_verifies_off_before_running_the_next(monkeypatch):
    sim = ControlSimulator()
    query = sim.query

    def read(command):
        if command == "FETC:POW?":
            sim.input_on = True
        return query(command)

    monkeypatch.setattr(sim, "query", read)
    monkeypatch.setattr(sim, "write", lambda _: pytest.fail("read-only Sequence wrote to the load"))
    with pytest.raises(LoadError) as error:
        sequence.execute(document(MEASURE, loops=2), mode="simulate", simulator=sim)
    assert error.value.code == "sequence_cleanup_unconfirmed"
    assert "Independently switch off" in str(error.value)
    details = error.value.details
    assert details["original_error"]["code"] == "input_not_off"
    assert details["cleanup"]["reason"] == "input_not_off"
    assert details["input_state"] == "ON" and not details["cleanup"]["off_confirmed"]
    assert details["completed_loops"] == 0
    assert len(details["measurements"]) == 1 and sim.input_on
