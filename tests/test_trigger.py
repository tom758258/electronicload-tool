"""Trigger Source behavior, physical command routing with recording fakes and CLI JSON."""

import json
from threading import Event
from time import monotonic_ns

import pytest

from electronicload_tool_cli import cli
from electronicload_tool_core import control
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.trigger import sampled_waveform_evidence
from electronicload_tool_core.simulator import ControlSimulator
from test_control_core import RESOURCE, session
from test_hardware_validation import ValidationFake, transient
from test_list_core import configuration as list_configuration


class TriggerFake(ValidationFake):
    def __init__(self):
        super().__init__()
        self.values.update({"TRIG:SOUR": "EXT", "TRIG:TIM": "0.375", "INP:TIM": "0",
                            "LIST:RANG": "6", "LIST:COUN": "1", "LIST:STEP": "2", "STAT:QUES:COND": "0"})
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


@pytest.mark.parametrize("scenario", [
    "recall_works", "recall_ignored", "unrestorable", "stale_slot", "unknown_slot", "lost_after_recall",
    "second_save_ignored", "unknown_stale_slot", "transient_enabled", "fixed_cv", "selected_list",
])
def test_contributor_list_requires_independent_recall_then_restores_working_buffer(
    monkeypatch, capsys, scenario,
):
    class SettingsList(TriggerFake):
        def __init__(self):
            super().__init__()
            self.list_steps = {f"LIST:{key}? {index}": value for index in (1, 2)
                               for key, value in (("LEV", ".2"), ("SLEW", ".1"), ("WID", "0.3"))}
            self.slots = {1: {**self.list_steps, "LIST:WID? 1": "1"}}
            self.save_count = 0

        def write(self, command):
            super().write(command)
            if command == "LIST:SAV 1":
                self.save_count += 1
                if (scenario not in {"stale_slot", "unknown_stale_slot"}
                        and not (scenario == "second_save_ignored" and self.save_count == 2)):
                    self.slots[1] = self.list_steps.copy()
            if command == "LIST:RCL 1":
                if scenario not in {"recall_ignored", "lost_after_recall"}:
                    self.list_steps = self.slots[1].copy()
                if scenario == "lost_after_recall":
                    self.response_errors["FUNC:MODE?"] = TimeoutError()

    fake = SettingsList()
    fake.range = 6
    fake.transient = scenario == "transient_enabled"
    if scenario == "fixed_cv":
        fake.function = "VOLT"
    if scenario == "selected_list":
        fake.mode = "LIST"
    if scenario == "unrestorable":
        fake.values["LIST:RANG"] = "0.1"  # Readable number without a restorable LIST range mapping.
    start = fake.list_steps.copy()
    session(fake, monkeypatch)
    args = ["contributor-settings", "--trial", "list-cc", "--slot", "1", "--resource", RESOURCE, "--json"]
    if scenario not in {"unknown_slot", "unknown_stale_slot"}:
        args += ["--previous-slot-width", "1"]
    assert cli.main(args) == 2 and not fake.calls
    capsys.readouterr()
    status = cli.main([*args, "--confirm-isolated"])
    output = capsys.readouterr()
    result = json.loads(output.out)
    if scenario in {"transient_enabled", "fixed_cv", "selected_list"}:
        assert status != 0 and result["error"]["details"]["no_settings_written"]
        assert fake.save_count == 0
        assert fake.transient == (scenario == "transient_enabled")
        assert fake.mode == ("LIST" if scenario == "selected_list" else "FIX")
        if scenario == "fixed_cv":
            assert fake.function == "VOLT"
        assert all("?" in command for command in fake.calls)
        return
    if scenario == "unrestorable":
        assert status == 3 and result["error"]["code"] == "list_baseline_unrestorable"
        assert result["error"]["details"]["no_settings_written"]
        assert all("?" in command for command in fake.calls)
        assert not output.err
        return

    snapshot = json.loads(output.err.removeprefix("Settings recovery baseline (private): "))
    assert snapshot["working_list"]["steps"][0]["width_s"] == "0.3"
    assert snapshot["fixed_cc"]["range"] == "low"
    if scenario == "lost_after_recall":
        assert status == 3 and result["error"]["details"]["manual_action_required"]
        assert fake.mode == "LIST" and fake.list_steps["LIST:WID? 1"] == "2"
        assert fake.calls[-2:] == ["LIST:RCL 1", "FUNC:MODE?"]
        return

    if scenario in {"recall_works", "unknown_slot"}:
        assert status == 0
        details = result["result"]
        assert details["recall_confirmed"] and details["restored"]
        assert details["save_confirmed"] is True and details["saved_slot"] == 1
        assert fake.save_count == 2
        assert fake.slots[1]["LIST:WID? 1"] == details["a_width_s"]
        assert details["a_width_s"] != details["b_width_s"]
    else:
        assert status == 3
        assert result["error"]["code"] == "readback_mismatch" and result["error"]["details"]["restored"]
    assert fake.list_steps == start and fake.mode == "FIX" and not fake.input
    writes = [command for command in fake.calls if "?" not in command]
    assert "INP ON" not in writes
    if scenario in {"recall_works", "unknown_slot"}:
        assert writes.count("LIST:SAV 1") == writes.count("LIST:RCL 1") == 2



@pytest.mark.parametrize("ignored_width", [False, True])
def test_readback_list_buffer_without_slot_or_trigger(monkeypatch, capsys, ignored_width):
    fake = TriggerFake()
    fake.range = 6
    fake.list_steps = {f"LIST:{key}? {index}": value for index in (1, 2)
                       for key, value in (("LEV", ".2"), ("SLEW", "0.1234567890123456789"),
                                          ("WID", "0.3000000000000000001"))}
    original = fake.list_steps.copy()
    if ignored_width:
        fake.ignore = "LIST:WID 1,1"
    session(fake, monkeypatch)
    args = ["contributor-settings", "--trial", "list", "--resource", RESOURCE, "--json", "--confirm-isolated"]
    status = cli.main(args)
    outcome = json.loads(capsys.readouterr().out)
    if ignored_width:
        assert status == 3 and outcome["error"]["code"] == "readback_mismatch"
        assert outcome["error"]["details"]["restored"]
    else:
        assert status == 0
        result = outcome["result"]
        assert result["observed"]["selected"] and result["restored"]
        steps = result["observed"]["configuration"]["steps"]
        assert steps[0]["width_s"] == "1"
        assert steps[1]["width_s"] == original["LIST:WID? 2"]
        assert steps[0]["slew_a_per_us"] == original["LIST:SLEW? 1"]
    assert fake.list_steps == original and fake.mode == "FIX" and not fake.input
    assert not any(call.startswith(("LIST:SAV", "LIST:RCL", "INP ON", "TRIG")) and "?" not in call
                   for call in fake.calls)



@pytest.mark.parametrize("source,scenario", [
    ("HOLD", "expected"), ("HOLD", "missing"), ("HOLD", "short"),
    ("TIMER", "expected"), ("TIMER", "wrong_interval"),
    ("BUS", "expected"), ("BUS", "wrong_order"), ("EXTERNAL", "expected"),
    ("BUS", "stuck"), ("EXTERNAL", "stuck"), ("BUS", "extra_cycle"), ("BUS", "early_end"),
    ("BUS", "terminal_last"),
    ("HOLD", "sparse"),
])
def test_sampled_trigger_evidence_requires_levels_and_time_brackets(source, scenario):
    is_list = source in {"BUS", "EXTERNAL"}
    step = 1.0 if scenario == "sparse" else 0.2
    samples = []
    for index in range(int(14 / step) + 1):
        t = index * step
        current = 0.0 if is_list else 0.2
        if is_list:
            if 1 <= t < 3:
                current = 0.2 if scenario == "wrong_order" else 0.1
            elif 3 <= t < 5:
                current = 0.1 if scenario == "wrong_order" else 0.2
        elif scenario != "missing":
            starts = [1] if source == "HOLD" else [1, 8 if scenario == "wrong_interval" else 6]
            if any(start <= t < start + (0.4 if scenario == "short" else 2) for start in starts):
                current = 0.1
        sample = {"started_s": t, "completed_s": t + 0.02, "current_a": current}
        if is_list:
            end = 4 if scenario == "early_end" else 9 if scenario == "extra_cycle" else 5
            running = t >= 1 and (t < end or scenario == "stuck")
            if scenario == "extra_cycle" and 5 <= t < 9:
                sample["current_a"] = 0.1 if t < 7 else 0.2
            if scenario in {"stuck", "terminal_last"} and t >= 5:
                sample["current_a"] = 0.2
            sample.update(list_running=running, list_status_completed_s=t + 0.04)
        samples.append(sample)
        if is_list and t >= 1 and not running:
            break
    evidence = sampled_waveform_evidence(
        samples, source=source, level_a=control._decimal("0.1"), level_b=control._decimal("0.2"),
        width_a=2, width_b=2 if is_list else None, interval=5 if source == "TIMER" else None,
    )
    assert (evidence is not None) == (scenario in {"expected", "terminal_last"})
    if evidence:
        assert evidence["events_confirmed"] == (2 if source == "TIMER" else 1)
        if is_list:
            assert evidence["list_run_completed"] is True


def test_contributor_trigger_observation_plan_and_refusal_before_visa(monkeypatch, capsys):
    fake = TriggerFake()
    session(fake, monkeypatch)
    expected = {"function": "CC", "trigger": {"source": "HOLD"},
                "configuration": {**transient("pulse", "low"), "pulse_width": "2"}}
    args = ["trigger-observe", "--hardware-validation", "trigger",
            "--validation-settings-json", json.dumps(expected), "--observation-seconds", "15",
            "--resource", RESOURCE, "--json"]
    assert cli.main(args) == 2 and not fake.calls
    capsys.readouterr()
    assert cli.main([*args, "--confirm", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)["result"]
    assert not plan["hardware_access"] and plan["planned_writes"] == ["SYST:REM", "TRIG:IMM"]
    assert "MEAS:CURR?" in plan["queries"] and not fake.calls
    assert cli.main([*args, "--confirm"]) == 2 and not fake.calls
    expected["configuration"]["pulse_width"] = "0.001"
    args[args.index("--validation-settings-json") + 1] = json.dumps(expected)
    assert cli.main([*args, "--confirm", "--dry-run"]) == 2 and not fake.calls
    capsys.readouterr()
    args[args.index("--observation-seconds") + 1] = "40"
    expected = {"function": "LIST", "trigger": {"source": "BUS"}, "configuration": {
        "range": "low", "repeat_count": 1, "steps": [
            {"current_a": 0.1, "slew_a_per_us": 0.1, "width_s": 20},
            {"current_a": 0.2, "slew_a_per_us": 0.1, "width_s": 20}]}}
    args[args.index("--validation-settings-json") + 1] = json.dumps(expected)
    assert cli.main([*args, "--confirm", "--dry-run"]) == 2 and not fake.calls
    assert "capture deadline" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_automatic_bus_does_not_dispatch_after_input_drops(monkeypatch, capsys):
    fake = TriggerFake()
    fake.range = 6
    fake.mode = "LIST"
    fake.input = True
    fake.values["TRIG:SOUR"] = "BUS"
    fake.values["LIST:COUN"] = "1"
    fake.values["MEAS:CURR"] = "0"
    steps = [{"current_a": 0.1, "slew_a_per_us": 0.1, "width_s": 2},
             {"current_a": 0.2, "slew_a_per_us": 0.1, "width_s": 2}]
    fake.list_steps = {f"LIST:{stem}? {index}": str(step[key])
                       for index, step in enumerate(steps, 1)
                       for stem, key in (("LEV", "current_a"), ("SLEW", "slew_a_per_us"), ("WID", "width_s"))}
    query = fake.query

    def drop_after_readback(command):
        response = query(command)
        if fake.remote and command == "LIST:WID? 2":
            fake.input = False
        return response

    monkeypatch.setattr(fake, "query", drop_after_readback)
    session(fake, monkeypatch)
    expected = {"function": "LIST", "trigger": {"source": "BUS"},
                "configuration": {"range": "low", "repeat_count": 1, "steps": steps}}
    args = ["trigger-observe", "--hardware-validation", "trigger",
            "--validation-settings-json", json.dumps(expected), "--observation-seconds", "15",
            "--deadline-ns", str(monotonic_ns() + 14_000_000_000), "--confirm",
            "--resource", RESOURCE, "--json"]
    assert cli.main(args) != 0
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "input_not_on"
    assert "MEAS:CURR?" in fake.calls and "*TRG" not in fake.calls


@pytest.mark.parametrize("function,sources", [
    ("CC", ("MANUAL", "BUS", "HOLD", "TIMER", "EXTERNAL")),
    ("CV", ("BUS",)),
    ("CR", ("HOLD",)),
    ("CW", ("TIMER",)),
    ("LIST", ("BUS", "EXTERNAL")),
])
def test_trigger_setup_shares_sources_across_all_simulated_functions(function, sources):
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
    for source in sources:
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
    expected_interval = "0.125000000000000001" if "TIMER" in sources else "0.01"
    assert execute("trigger-status")["interval_s"] == expected_interval


@pytest.mark.parametrize("source,interval", [
    ("other", None), ("TIMER", None), ("TIMER", True), ("TIMER", "NaN"),
    ("TIMER", "0.00999999999999999999"), ("TIMER", "999.99000000000000001"),
    ("BUS", "1"),
])
def test_trigger_setup_bad_parameters_are_rejected_without_simulation_change(source, interval):
    simulator = ControlSimulator()
    before = control.execute("trigger-status", mode="simulate", simulator=simulator)
    with pytest.raises(LoadError):
        control.execute("trigger-configure", mode="simulate", simulator=simulator,
                        trigger_source=source, trigger_interval=interval)
    assert control.execute("trigger-status", mode="simulate", simulator=simulator) == before
    assert simulator.input_on is False


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


@pytest.mark.parametrize("fault", [None, "input", "remote", "snapshot", "write", "interval", "source"])
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
        elif fault == "source":
            fake.ignore = "TRIG:SOUR TIM"
        else:
            fake.ignore = "TRIG:TIM 0.25"
        with pytest.raises(LoadError) as error:
            physical("trigger-configure", trigger_source="TIMER", trigger_interval="0.25")
        expected = {"input": [], "remote": ["SYST:REM"], "snapshot": [],
                    "write": ["SYST:REM", "TRIG:TIM 0.25"],
                    "interval": ["SYST:REM", "TRIG:TIM 0.25"],
                    "source": ["SYST:REM", "TRIG:TIM 0.25", "TRIG:SOUR TIM"]}[fault]
        assert [command for command in fake.calls if "?" not in command] == expected
        if fault in {"interval", "source"}:
            assert error.value.details["trigger_readbacks"] == (
                {"source": "UNCONFIRMED", "interval_s": "MISMATCH"} if fault == "interval" else
                {"source": "MISMATCH", "interval_s": "CONFIRMED"}
            )
        return
    result = physical("trigger-configure", trigger_source="TIMER", trigger_interval="0.25")
    assert result["source"] == "TIMER" and result["interval_s"] == "0.25"
    assert fake.calls.index("TRIG:TIM?") < fake.calls.index("SYST:REM")
    assert [cmd for cmd in fake.calls if "?" not in cmd] == ["SYST:REM", "TRIG:TIM 0.25", "TRIG:SOUR TIM"]
    restored = physical("trigger-configure", trigger_source=original["source"],
                        trigger_interval=original["interval_s"])
    assert restored == original and not fake.input
    # MANUAL must remain a readable/restorable OFF setting, even though
    # powered contributor MANUAL validation is not open.
    manual = physical("trigger-configure", trigger_source="MANUAL")
    assert manual["source"] == "MANUAL" and manual["input_state"] == "OFF"
    assert physical("trigger-status")["source"] == "MANUAL"
    assert physical("trigger-configure", trigger_source=original["source"],
                    trigger_interval=original["interval_s"]) == original


def prepare_native_trigger(monkeypatch, function, source):
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
    return fake, trigger_command, expected


@pytest.mark.parametrize("function,source,scpi", [
    ("CC", "HOLD", "TRIG:IMM"), ("CV", "BUS", "*TRG"), ("CR", "HOLD", "TRIG:IMM"),
    ("CW", "BUS", "*TRG"), ("LIST", "BUS", "*TRG"),
])
def test_native_trigger_scpi_dispatch_and_off(monkeypatch, function, source, scpi):
    fake, trigger_command, expected = prepare_native_trigger(monkeypatch, function, source)
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


@pytest.mark.parametrize("function,last_read", [("CC", "INP:TIM?"), ("LIST", "LIST:WID? 2")])
@pytest.mark.parametrize("trigger", [False, True])
def test_native_trigger_rechecks_input_after_settings_readback(monkeypatch, function, last_read, trigger):
    fake, trigger_command, expected = prepare_native_trigger(monkeypatch, function, "BUS")
    if trigger:
        physical("input-on", confirmed=True, validation_settings=expected)
    fake.calls.clear()
    original_query = fake.query

    def query(command):
        response = original_query(command)
        if command == last_read and "SYST:REM" in fake.calls:
            fake.input = not trigger
        return response

    monkeypatch.setattr(fake, "query", query)
    with pytest.raises(LoadError) as error:
        if trigger:
            physical(trigger_command, validation_settings=expected)
        else:
            physical("input-on", confirmed=True, validation_settings=expected)
    assert error.value.code == ("input_not_on" if trigger else "input_not_off")
    assert [command for command in fake.calls if "?" not in command] == ["SYST:REM"]


@pytest.mark.parametrize("operation,previous_write,last_read,blocked_write", [
    ("set-list-cc", None, "FUNC?", "SYST:REM"),
    ("set-list-cc", "LIST:WID 2,0.002", "INP:SHOR:STAT?", "FUNC:MODE LIST"),
    ("list-exit", "SYST:REM", "FUNC?", "FUNC:MODE FIX"),
])
def test_native_list_rechecks_off_before_mode_writes(
    monkeypatch, operation, previous_write, last_read, blocked_write,
):
    fake = TriggerFake()
    session(fake, monkeypatch)
    settings = list_configuration()
    arguments = {"list_configuration": settings} if operation == "set-list-cc" else {}
    if operation == "list-exit":
        physical("set-list-cc", list_configuration=settings)
    fake.calls.clear()
    original_query = fake.query

    def query(command):
        response = original_query(command)
        if command == last_read and (previous_write is None or previous_write in fake.calls):
            fake.input = True
        return response

    monkeypatch.setattr(fake, "query", query)
    with pytest.raises(LoadError) as error:
        physical(operation, **arguments)
    assert error.value.code == "input_not_off"
    assert blocked_write not in fake.calls


def test_native_list_setup_reports_selected_mode_after_settings_mismatch(monkeypatch):
    fake = TriggerFake()
    session(fake, monkeypatch)

    def mismatch_after_select(device, command):
        if command == "FUNC:MODE LIST":
            device.values["LIST:COUN"] = "2"

    fake.after_write = mismatch_after_select
    with pytest.raises(LoadError) as error:
        physical("set-list-cc", list_configuration=list_configuration())
    assert error.value.code == "readback_mismatch"
    assert error.value.details["list_selected"] is True
    assert not fake.input and fake.mode == "LIST"


def test_trigger_cli_and_native_refusals_do_not_open_visa(monkeypatch, capsys):
    monkeypatch.setattr(control, "open_session", lambda *_: pytest.fail("VISA opened"))
    for command, args in [("trigger-status", []), ("trigger-configure", ["--source", "BUS"])]:
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
    with pytest.raises(LoadError) as manual:
        physical("input-on", confirmed=True, validation_settings={
            "function": "CC", "configuration": transient("pulse"),
            "trigger": {"source": "MANUAL"},
        })
    assert manual.value.code == "unsupported_live_scope"
    with pytest.raises(LoadError, match="finite LIST repeats"):
        physical("set-list-cc", list_configuration=list_configuration(repeat_count=65536))
