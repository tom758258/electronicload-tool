"""Offline workflow timing, measurement, CSV and safety contracts."""

import csv
import json
from threading import Event

import pytest

from electronicload_tool_cli.cli import main
from electronicload_tool_core import control, workflows
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.simulator import ControlSimulator


class Clock(Event):
    def __init__(self):
        super().__init__()
        self.now = 0.0

    def wait(self, timeout=None):
        if not self.is_set():
            self.now += timeout
        return self.is_set()


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(workflows, "monotonic", lambda: clock.now)
    monkeypatch.setattr(control, "monotonic", lambda: clock.now)
    monkeypatch.setattr(control, "sleep", clock.wait)
    return clock


class RecordingLoad(ControlSimulator):
    def __init__(self, replies=None):
        super().__init__()
        self.replies = {key: iter(values) for key, values in (replies or {}).items()}
        self.io = []

    def query(self, command):
        self.io.append(command)
        if command in self.replies:
            reply = next(self.replies[command])
            if isinstance(reply, Exception):
                raise reply
            return reply
        return super().query(command)

    def write(self, command):
        self.io.append(command)
        super().write(command)


def run(command, simulator, clock, **arguments):
    return workflows.execute(command, mode="simulate", arguments=arguments,
                             simulator=simulator, stop=clock)


def test_measure_log_cadence_and_csv_never_change_existing_input(clock, tmp_path):
    load = RecordingLoad()
    load.input_on = True
    load.current_a = 0.5
    path = tmp_path / "logs" / "readings.csv"
    result = run("measure-log", load, clock, interval_s=0.5, count=3, csv=str(path))
    assert result == {
        "stop_reason": "completed", "sample_count": 3, "elapsed_s": 1,
        "last_measurement": {"voltage_v": 12, "current_a": 0.5, "power_w": 6},
        "csv_written": True,
    }
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [float(row["elapsed_s"]) for row in rows] == [0, 0.5, 1]
    assert [row["sample_index"] for row in rows] == ["1", "2", "3"]
    assert set(rows[0]) == {"sample_index", "elapsed_s", "voltage_v", "current_a", "power_w"}
    assert load.input_on and all(query.endswith("?") for query in load.io)


@pytest.mark.parametrize(("operator", "expected"), [("le", "condition_met"), ("lt", "timeout")])
def test_measure_until_reads_only_selected_quantity_and_has_a_deadline(clock, operator, expected):
    load = RecordingLoad({"MEAS:VOLT?": ["5", "4.5"]})
    load.input_on = True
    arguments = dict(interval_s=1, timeout_s=2, metric="voltage", operator=operator, threshold=4.5)
    if expected == "timeout":
        with pytest.raises(LoadError) as failed:
            run("measure-until", load, clock, **arguments)
        assert failed.value.code == "workflow_timeout"
        result = failed.value.details
        assert result["elapsed_s"] == 2
    else:
        result = run("measure-until", load, clock, **arguments)
        assert result["elapsed_s"] == 1
    assert result["stop_reason"] == expected and result["sample_count"] == 2
    assert result["last_measurement"] == {"voltage_v": 4.5}
    assert load.input_on and load.io == ["*IDN?", "MEAS:VOLT?", "MEAS:VOLT?"]


def test_periodic_sampling_skips_missed_intervals(clock, tmp_path):
    class SlowFirstReading(RecordingLoad):
        def query(self, command):
            if command == "MEAS:VOLT?" and command not in self.io:
                clock.now += 1.25
            return super().query(command)

    path = tmp_path / "slow.csv"
    run("measure-log", SlowFirstReading(), clock, interval_s=1, count=3, csv=str(path))
    with path.open(encoding="utf-8", newline="") as file:
        assert [float(row["elapsed_s"]) for row in csv.DictReader(file)] == [1.25, 2, 3]


def test_measure_log_duration_ends_without_sampling_at_deadline(clock):
    load = RecordingLoad()
    load.input_on = True
    result = run("measure-log", load, clock, interval_s=1, duration_s=2)
    assert result["stop_reason"] == "duration_elapsed" and result["elapsed_s"] == 2
    assert result["sample_count"] == load.io.count("MEAS:VOLT?") == 2
    assert load.input_on and all(query.endswith("?") for query in load.io)


def test_cc_timed_load_checks_settings_then_waits_then_verifies_off(clock):
    load = RecordingLoad()
    result = run("cc-timed-load", load, clock, current=0.5, duration_s=30, confirm_input_on=True)
    assert result["stop_reason"] == "duration_elapsed" and result["elapsed_s"] == 30
    assert result["input_state"] == "OFF" and result["cleanup"]["off_confirmed"] and not load.input_on
    assert load.io.index("INP:STAT?") < load.io.index("CURR 0.5")
    assert load.io.index("CURR 0.5") < load.io.index("CURR?") < load.io.index("INP ON")
    assert load.io.index("INP ON") < load.io.index("INP OFF") < len(load.io) - 1
    assert not any(command.startswith(("MEAS:", "FETC:")) for command in load.io)
    assert {"sample_count", "capacity_ah", "csv_written"}.isdisjoint(result)


def test_standalone_interrupt_during_timed_wait_verifies_off(monkeypatch):
    load = RecordingLoad()

    def interrupt_wait(seconds):
        assert seconds == 30 and load.input_on
        raise KeyboardInterrupt

    monkeypatch.setattr(workflows, "sleep", interrupt_wait)
    with pytest.raises(LoadError) as failed:
        workflows.execute("cc-timed-load", mode="simulate", simulator=load,
                          arguments={"current": 0.5, "duration_s": 30, "confirm_input_on": True})
    assert failed.value.code == "interrupted" and failed.value.details["stop_reason"] == "interrupted"
    assert failed.value.details["cleanup"]["off_confirmed"] and not load.input_on


@pytest.mark.parametrize("scenario", ["initial_on", "readback_mismatch", "csv_exists", "input_timer"])
def test_cc_preconditions_prevent_input_on(clock, tmp_path, scenario):
    load = RecordingLoad()
    arguments = dict(current=0.5, cutoff_voltage=3, timeout_s=20, confirm_input_on=True)
    code = scenario
    if scenario == "initial_on":
        load.input_on = True
        code = "input_not_off"
    elif scenario == "readback_mismatch":
        load.replies["CURR?"] = iter(["0"])
    elif scenario == "csv_exists":
        path = tmp_path / "existing.csv"
        path.write_text("original", encoding="utf-8")
        arguments["csv"] = str(path)
        code = "workflow_csv_exists"
    else:
        load.input_timer_enabled = True
        code = "input_timer_enabled"
    with pytest.raises(LoadError) as failed:
        run("cc-discharge", load, clock, **arguments)
    assert failed.value.code == code
    assert {"INP ON", "INP OFF"}.isdisjoint(load.io)
    if scenario == "initial_on":
        assert failed.value.details["input_state"] == "ON" and load.input_on
        assert all(command.endswith("?") for command in load.io)
    if scenario == "csv_exists":
        assert path.read_text(encoding="utf-8") == "original"
        assert not load.io


def test_stop_during_cc_setup_prevents_input_on(clock):
    class StopAfterSetup(RecordingLoad):
        def query(self, command):
            reply = super().query(command)
            if command == "CURR?":
                clock.set()
            return reply

    load = StopAfterSetup()
    with pytest.raises(LoadError) as failed:
        run("cc-timed-load", load, clock, current=0.5, duration_s=30, confirm_input_on=True)
    assert failed.value.code == "stopping" and failed.value.exit_code == 3
    assert failed.value.details["input_state"] == "OFF"
    assert not failed.value.details["cleanup"]["attempted"]
    assert {"INP ON", "INP OFF"}.isdisjoint(load.io)


def test_cc_discharge_at_initial_cutoff_never_changes_input_or_configuration(clock):
    load = RecordingLoad()
    result = run("cc-discharge", load, clock, current=0.5, cutoff_voltage=12, timeout_s=2,
                 confirm_input_on=True)
    assert result["stop_reason"] == "voltage_cutoff" and result["initial_voltage_v"] == 12
    assert result["sample_count"] == result["capacity_ah"] == 0
    assert result["last_measurement"] is None and result["input_state"] == "OFF"
    assert result["cleanup"] == {"required": False, "attempted": False, "off_confirmed": True}
    assert not load.input_on and all(query.endswith("?") for query in load.io)


@pytest.mark.parametrize(
    ("voltage", "cutoff_ah", "timeout", "reason", "current_fails"),
    [
        ("3", None, 1.5, "voltage_cutoff", False),
        ("4", 0.0006, 4, "capacity_cutoff", False),
        ("3", 0.0006, 4, "voltage_cutoff", True),
        ("4", 0.0009, 2, "timeout", False),
    ],
)
def test_cc_discharge_cutoffs_use_measured_current_and_confirm_off(
    clock, voltage, cutoff_ah, timeout, reason, current_fails,
):
    class DelayedReading(RecordingLoad):
        def query(self, command):
            if command == "MEAS:CURR?" and command in self.io:
                clock.now += 0.25
                if current_fails:
                    raise OSError("No current reply after the voltage cutoff")
            if command == "FETC:POW?" and command in self.io:
                clock.now += 0.5
            return super().query(command)

    load = DelayedReading({"MEAS:VOLT?": ["12", "4", voltage], "MEAS:CURR?": ["1", "3"],
                           "FETC:POW?": ["4", "9.5"]})
    arguments = dict(current=0.5, cutoff_voltage=3, interval_s=1, timeout_s=timeout,
                     confirm_input_on=True)
    if cutoff_ah is not None:
        arguments["cutoff_capacity_ah"] = cutoff_ah
    if reason == "timeout":
        with pytest.raises(LoadError) as failed:
            run("cc-discharge", load, clock, **arguments)
        assert failed.value.code == "workflow_timeout"
        result = failed.value.details
    else:
        result = run("cc-discharge", load, clock, **arguments)
    expected = {"voltage_v": float(voltage)}
    if reason != "voltage_cutoff":
        expected["current_a"] = 3
    if reason == "timeout":
        expected["power_w"] = 9.5
    expected_capacity = 0 if reason == "voltage_cutoff" else (1 + 3) / 2 * 1.25 / 3600
    assert result["capacity_ah"] == pytest.approx(expected_capacity)
    assert result["stop_reason"] == reason and result["sample_count"] == 2
    assert result["last_measurement"] == expected
    assert result["elapsed_s"] == {"voltage_cutoff": 1, "capacity_cutoff": 1.25, "timeout": 2}[reason]
    assert result["cleanup"]["off_confirmed"] and not load.input_on
    assert load.io.count("INP OFF") == 1
    if reason != "timeout":
        assert load.io.count("FETC:POW?") == 1
    if reason == "voltage_cutoff":
        assert load.io.count("MEAS:CURR?") == 1


def test_cc_discharge_stops_when_capacity_exactly_reaches_cutoff(clock):
    load = RecordingLoad()
    result = run("cc-discharge", load, clock, current=0.4, cutoff_voltage=3,
                 cutoff_capacity_ah=0.001, timeout_s=9.5, confirm_input_on=True)
    assert result["stop_reason"] == "capacity_cutoff" and result["capacity_ah"] == 0.001
    assert result["sample_count"] == 10 and result["elapsed_s"] == 9
    assert result["cleanup"]["off_confirmed"] and not load.input_on


@pytest.mark.parametrize(("reason", "off_at"), [("voltage_cutoff", 1), ("timeout", 1.5), ("stopped", 1)])
def test_discharge_finishes_off_cleanup_before_slow_csv_rows(clock, tmp_path, monkeypatch, reason, off_at):
    class TimedLoad(RecordingLoad):
        def query(self, command):
            reply = super().query(command)
            if command == "FETC:POW?" and clock.now == 1 and reason == "stopped":
                clock.set()
            return reply

        def write(self, command):
            if command == "INP OFF":
                assert clock.now == off_at
            super().write(command)

    load = TimedLoad({"MEAS:VOLT?": ["12", "4", "3" if reason == "voltage_cutoff" else "4"]})
    original_write = csv.DictWriter.writerows

    def slow_write(writer, rows):
        for row in rows:
            assert not load.input_on and load.io[-1] == "INP:STAT?"
            clock.now += 2
        return original_write(writer, rows)

    monkeypatch.setattr(csv.DictWriter, "writerows", slow_write)
    path = tmp_path / "discharge.csv"
    arguments = dict(current=0.5, cutoff_voltage=3, timeout_s=1.5, confirm_input_on=True, csv=str(path))
    if reason == "voltage_cutoff":
        result = run("cc-discharge", load, clock, **arguments)
        assert result["csv_written"]
    else:
        with pytest.raises(LoadError) as failed:
            run("cc-discharge", load, clock, **arguments)
        result = failed.value.details
        assert result["csv_incomplete"]
    assert result["stop_reason"] == reason and result["cleanup"]["off_confirmed"]
    assert result["elapsed_s"] == off_at + 4
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["sample_index"] for row in rows] == ["1", "2"]
    assert float(rows[0]["power_w"]) == 6
    if reason == "voltage_cutoff":
        assert rows[-1]["voltage_v"] == "3.0"
        assert rows[-1]["current_a"] == rows[-1]["power_w"] == ""
    else:
        assert float(rows[-1]["power_w"]) == result["last_measurement"]["power_w"]


@pytest.mark.parametrize("scenario", ["untrusted", "off_unconfirmed", "csv_write_failed"])
def test_discharge_failure_reports_safe_state_and_retains_csv(clock, tmp_path, monkeypatch, scenario):
    class FaultyLoad(RecordingLoad):
        def write(self, command):
            if command == "INP OFF" and scenario == "off_unconfirmed":
                self.io.append(command)
                return
            super().write(command)

    def fail_write(self, rows):
        assert scenario == "csv_write_failed", "Skip row export when OFF is unconfirmed"
        assert rows
        raise OSError("Unwritable file")

    monkeypatch.setattr(csv.DictWriter, "writerows", fail_write)

    voltage = OSError("No reply") if scenario == "untrusted" else "3"
    load = FaultyLoad({"MEAS:VOLT?": ["12", "4", voltage]})
    path = tmp_path / "fault.csv"
    with pytest.raises(LoadError) as failed:
        run("cc-discharge", load, clock, current=0.5, cutoff_voltage=3, timeout_s=2,
            confirm_input_on=True, csv=str(path))
    error = failed.value
    assert path.is_file() and error.details["csv_incomplete"] is True
    if scenario == "csv_write_failed":
        assert error.code == "workflow_csv_failed"
        assert error.details["cleanup"]["off_confirmed"] and not load.input_on
    else:
        assert error.code == "workflow_cleanup_unconfirmed"
        assert error.details["manual_action_required"] is True
        assert error.details["input_state"] == "UNKNOWN" and load.input_on
        assert error.details["sample_count"] >= 1
        with path.open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            assert reader.fieldnames and list(reader) == []
        if scenario == "untrusted":
            assert error.details["original_error"]["code"] == "query_failed"
            assert load.io[-1] == "MEAS:VOLT?" and "INP OFF" not in load.io
        else:
            assert error.details["cleanup"]["error"]["code"] == "input_off_not_confirmed"
            assert load.io.count("INP OFF") == 1


def test_measure_stop_keeps_partial_results_and_does_not_write_input(clock, tmp_path):
    class StopAfterSample(RecordingLoad):
        def query(self, command):
            reply = super().query(command)
            if command == "FETC:POW?":
                clock.set()
            return reply

    load = StopAfterSample()
    load.input_on = True
    path = tmp_path / "partial.csv"
    with pytest.raises(LoadError) as failed:
        run("measure-log", load, clock, interval_s=1, count=5, csv=str(path))
    assert failed.value.code == "stopping" and failed.value.details["sample_count"] == 1
    assert failed.value.details["stop_reason"] == "stopped"
    assert failed.value.details["csv_incomplete"] and load.input_on
    assert all(command.endswith("?") for command in load.io)
    with path.open(encoding="utf-8", newline="") as file:
        assert len(list(csv.DictReader(file))) == 1


@pytest.mark.parametrize(
    ("command", "flags"),
    [
        ("measure-log", ["--interval-s", ".001", "--count", "1"]),
        ("measure-log", ["--interval-s", "1", "--duration-s", ".01"]),
        ("measure-until", ["--interval-s", ".01", "--metric", "voltage",
                           "--operator", "ge", "--threshold", "12", "--timeout-s", "1"]),
        ("cc-timed-load", ["--current", ".5", "--duration-s", ".001", "--confirm"]),
        ("cc-discharge", ["--current", ".5", "--cutoff-voltage", "12", "--timeout-s", "1",
                          "--confirm"]),
    ],
)
def test_cli_workflows_use_json_with_simulator_and_refuse_live(command, flags, capsys):
    assert main([command, *flags, "--simulate"]) == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["ok"] and outcome["command"] == command
    assert main([command, *flags]) == 2
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["error"]["code"] == "unsupported_live_scope"


@pytest.mark.parametrize("flags", [[], ["--count", "1", "--duration-s", "1"]])
def test_cli_measure_log_requires_one_stop_option(flags, capsys):
    assert main(["measure-log", "--dry-run", "--interval-s", "1", *flags]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "argument_error"


def test_dry_run_previews_without_simulator_io_or_csv_creation(capsys, tmp_path, monkeypatch):
    def no_simulator():
        pytest.fail("dry-run must not create a simulator")

    monkeypatch.setattr(workflows, "ControlSimulator", no_simulator)
    path = tmp_path / "new-directory" / "discharge.csv"
    assert main(["cc-discharge", "--dry-run", "--current", ".5000", "--cutoff-voltage", "3",
                 "--timeout-s", "1800", "--csv", str(path)]) == 0
    plan = json.loads(capsys.readouterr().out)["result"]
    assert plan["hardware_access"] is False and plan["csv_requested"] is True
    assert plan["input_on_requires_confirmation"] is True
    assert plan["arguments"]["current"] == "0.5000" and plan["arguments"]["range"] == "low"
    assert str(path) not in json.dumps(plan) and not path.parent.exists()


@pytest.mark.parametrize(
    ("command", "arguments", "code"),
    [
        ("measure-log", {"interval_s": 1, "count": True}, "argument_error"),
        ("measure-log", {"interval_s": 1, "count": 10001}, "argument_error"),
        ("measure-log", {"interval_s": 3600, "count": 25}, "argument_error"),
        ("measure-log", {"interval_s": 1, "duration_s": 10000.001}, "argument_error"),
        ("measure-until", {"interval_s": 0.001, "metric": "power", "operator": "gt",
                           "timeout_s": 11, "threshold": 0}, "argument_error"),
        ("cc-timed-load", {"current": 0.5, "duration_s": 86401}, "argument_error"),
        ("cc-timed-load", {"current": 0.5, "duration_s": 1, "csv": "timed.csv"}, "argument_error"),
        ("cc-timed-load", {"current": 0.5, "duration_s": 1}, "confirmation_required"),
        ("cc-discharge", {"current": 0.5, "timeout_s": 5}, "argument_error"),
        ("cc-discharge", {"current": 0.5, "cutoff_voltage": 3, "cutoff_capacity_ah": 0,
                          "timeout_s": 5}, "argument_error"),
    ],
)
def test_invalid_requests_fail_before_simulation(command, arguments, code, clock):
    load = RecordingLoad()
    with pytest.raises(LoadError) as failed:
        run(command, load, clock, **arguments)
    assert failed.value.code == code and not load.io
