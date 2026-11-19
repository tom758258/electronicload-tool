"""Trigger Source behavior, physical command routing with recording fakes and CLI JSON."""

import json
from threading import Event

import pytest

from electronicload_tool_cli import cli
from electronicload_tool_core import control
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.simulator import ControlSimulator
from test_control_core import RESOURCE, session
from test_hardware_validation import ValidationFake, transient
from test_list_core import configuration as list_configuration


class TriggerFake(ValidationFake):
    def __init__(self):
        super().__init__()
        self.values.update({"TRIG:SOUR": "EXT", "TRIG:TIM": "0.375", "INP:TIM": "0",
                            "LIST:RANG": "6", "LIST:COUN": "1", "LIST:STEP": "2"})
        self.list_steps = {}

    def write(self, command):
        super().write(command)
        key, _, value = command.partition(" ")
        if self.remote and command != self.ignore:
            if command == "FUNC:MODE LIST":
                self.mode = "LIST"
            if key in {"LIST:LEV", "LIST:SLEW", "LIST:WID"}:
                index, token = value.split(",")
                self.list_steps[f"{key}? {index}"] = token

    def query(self, command):
        if command in self.list_steps:
            self.calls.append(command)
            return self.list_steps[command]
        return super().query(command)


def physical(command, **arguments):
    return control.execute(command, resource=RESOURCE, hardware_validation="trigger", **arguments)


@pytest.mark.parametrize("function", ["CC", "CV", "CR", "CW", "LIST"])
def test_trigger_setup_shares_sources_across_all_simulated_functions(function):
    simulator = ControlSimulator()

    def execute(command, mode="simulate", **arguments):
        return control.execute(command, mode=mode, simulator=simulator, **arguments)

    assert execute("trigger-status") == {"source": "HOLD", "interval_s": "0.01", "state_source": "simulate"}
    if function == "LIST":
        execute("set-list-cc", list_configuration=list_configuration())
        prefix = "list"
    else:
        execute(f"set-transient-{function.lower()}", transient=transient("pulse", function=function))
        execute("transient-enable")
        prefix = "transient"
    for source in ("MANUAL", "BUS", "HOLD", "TIMER", "EXTERNAL"):
        kwargs = {"trigger_interval": "0.125000000000000001"} if source == "TIMER" else {}
        settings = execute("trigger-configure", trigger_source=source, **kwargs)
        assert settings["source"] == source and not simulator.input_on
        execute("input-on", confirmed=True)
        with pytest.raises(LoadError, match="confirmed OFF"):
            execute("trigger-configure", trigger_source="BUS")
        before = execute(f"{prefix}-status")
        if source in {"MANUAL", "TIMER", "EXTERNAL"}:
            with pytest.raises(LoadError) as error:
                execute(f"{prefix}-trigger")
            assert error.value.code == "unsafe_trigger_source"
            assert execute(f"{prefix}-status") == before
        else:
            assert execute(f"{prefix}-trigger")["last_trigger"]["source"] == "software"
        assert execute("trigger-status") == settings
        execute("input-off")
    assert execute("trigger-status")["interval_s"] == "0.125000000000000001"


@pytest.mark.parametrize("source,interval", [
    ("other", None), ("TIMER", None), ("TIMER", True), ("TIMER", "NaN"),
    ("TIMER", "0.00999999999999999999"), ("TIMER", "999.99000000000000001"),
    ("BUS", "1"),
])
def test_trigger_setup_bad_parameters_are_rejected_without_simulation_change(source, interval):
    simulator = ControlSimulator()
    before = vars(simulator).copy()
    with pytest.raises(LoadError):
        control.execute("trigger-configure", mode="simulate", simulator=simulator,
                        trigger_source=source, trigger_interval=interval)
    assert vars(simulator) == before


def test_trigger_setup_dry_run_and_stop_preserve_input_and_configuration():
    simulator, stop = ControlSimulator(), Event()
    simulator.input_on = True
    stop.set()
    preview = control.execute("trigger-configure", mode="dry-run", simulator=simulator, stop=stop,
                              trigger_source="TIMER", trigger_interval="999.99")
    assert preview == {"command": "trigger-configure", "hardware_access": False,
                       "source": "TIMER", "interval_s": "999.99"}
    assert (simulator.trigger_source, simulator.input_on) == ("HOLD", True)
    simulator.input_on = False
    with pytest.raises(LoadError) as error:
        control.execute("trigger-configure", mode="simulate", simulator=simulator,
                        stop=stop, trigger_source="BUS")
    assert error.value.code == "stopping" and simulator.trigger_source == "HOLD"


@pytest.mark.parametrize("fault", [None, "input", "remote", "snapshot", "write", "interval"])
def test_trigger_physical_snapshot_write_and_restore(monkeypatch, fault):
    fake = TriggerFake()
    session(fake, monkeypatch)
    original = physical("trigger-status")
    assert original["source"] == "EXTERNAL" and original["interval_s"] == "0.375"
    assert all("?" in command for command in fake.calls)
    fake.calls.clear()
    if fault is not None:
        if fault == "input":
            fake.input = True
        elif fault == "remote":
            fake.after_remote = lambda device: setattr(device, "input", True)
        elif fault == "snapshot":
            fake.values["TRIG:SOUR"] = "UNKNOWN"
        elif fault == "write":
            fake.write_fail = "TRIG:TIM 0.25"
        else:
            fake.ignore = "TRIG:TIM 0.25"
        with pytest.raises(LoadError):
            physical("trigger-configure", trigger_source="TIMER", trigger_interval="0.25")
        expected = {"input": [], "remote": ["SYST:REM"], "snapshot": [],
                    "write": ["SYST:REM", "TRIG:TIM 0.25"],
                    "interval": ["SYST:REM", "TRIG:TIM 0.25"]}[fault]
        assert [command for command in fake.calls if "?" not in command] == expected
        return
    result = physical("trigger-configure", trigger_source="TIMER", trigger_interval="0.25")
    assert result["source"] == "TIMER" and result["interval_s"] == "0.25"
    assert fake.calls.index("TRIG:TIM?") < fake.calls.index("SYST:REM")
    assert [cmd for cmd in fake.calls if "?" not in cmd] == ["SYST:REM", "TRIG:TIM 0.25", "TRIG:SOUR TIM"]
    restored = physical("trigger-configure", trigger_source=original["source"],
                        trigger_interval=original["interval_s"])
    assert restored == original and not fake.input


@pytest.mark.parametrize("function,source,scpi", [
    ("CC", "HOLD", "TRIG:IMM"), ("CV", "BUS", "*TRG"), ("CR", "HOLD", "TRIG:IMM"),
    ("CW", "BUS", "*TRG"), ("LIST", "BUS", "*TRG"),
])
def test_native_trigger_scpi_dispatch_and_off(monkeypatch, function, source, scpi):
    fake = TriggerFake()
    session(fake, monkeypatch)
    physical("trigger-configure", trigger_source=source)
    if function == "LIST":
        settings = list_configuration()
        physical("set-list-cc", list_configuration=settings)
        trigger_command = "list-trigger"
    else:
        settings = transient("pulse", function=function)
        physical(f"set-transient-{function.lower()}", transient=settings)
        trigger_command = "transient-trigger"
    expected = {"function": function, "configuration": settings, "trigger": {"source": source}}
    if function != "LIST":
        physical("transient-enable", validation_settings=expected)
    assert physical("input-on", confirmed=True, validation_settings=expected)["input_state"] == "ON"
    fake.calls.clear()
    if function == "LIST":
        fake.values["LIST:COUN"] = "2"
        with pytest.raises(LoadError, match="differs"):
            physical(trigger_command, validation_settings=expected)
        assert all("?" in cmd for cmd in fake.calls)
        fake.values["LIST:COUN"] = "1"
        fake.calls.clear()
    result = physical(trigger_command, validation_settings=expected)
    assert result["waveform_completion_known"] is False
    assert [cmd for cmd in fake.calls if "?" not in cmd] == ["SYST:REM", scpi]
    assert physical("input-off")["input_state"] == "OFF"
    physical("list-exit" if function == "LIST" else "transient-disable")
    assert not fake.input and fake.mode == "FIX" and not fake.transient


def test_trigger_cli_and_native_refusals_do_not_open_visa(monkeypatch, capsys):
    monkeypatch.setattr(control, "open_session", lambda *_: pytest.fail("VISA opened"))
    for command, args in [("trigger-status", []), ("trigger-configure", ["--source", "BUS"]),
                          ("list-trigger", []), ("transient-trigger", [])]:
        assert cli.main([command, *args, "--json", "--resource", RESOURCE,
                         "--validation-allow-pending-live-support"]) == 2
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported_live_scope"
    assert cli.main(["trigger-configure", "--dry-run", "--source", "timer",
                     "--interval-s", "0.12500000000000001", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["interval_s"] == "0.12500000000000001"
    for source in ("MANUAL", "TIMER", "EXTERNAL"):
        setup = {"source": source}
        if source == "TIMER":
            setup["interval_s"] = 1
        with pytest.raises(LoadError, match="HOLD or BUS"):
            physical("list-trigger", validation_settings={"function": "LIST",
                     "configuration": list_configuration(), "trigger": setup})
    with pytest.raises(LoadError, match="finite LIST repeats"):
        physical("set-list-cc", list_configuration=list_configuration(repeat_count=65536))
