"""Offline acceptance of TRACE validation, native I/O, and CSV output."""

import csv
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest
from pyvisa.constants import StatusCode
from pyvisa.errors import VisaIOError

from electronicload_tool_cli.cli import main
from electronicload_tool_core import control, readonly, trace
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.simulator import ControlSimulator


RESOURCE = "USB0::0xFFFF::0x0001::SYNTHETIC::INSTR"


class RecordingVISA(ControlSimulator):
    def __init__(self, responses=None):
        super().__init__()
        self.responses = responses or {}
        self.calls = []

    def write(self, command):
        self.calls.append(command)
        super().write(command)

    def query(self, command):
        self.calls.append((command, self.timeout))
        if command in self.responses:
            result = self.responses[command]
            if isinstance(result, Exception):
                raise result
            return result
        return super().query(command)


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 0.0
        waits = []

        def sleep(self, seconds):
            self.waits.append(seconds)
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(trace, "monotonic", lambda: clock.now)
    monkeypatch.setattr(trace, "sleep", clock.sleep)
    return clock


def configure(session, source="TWO", points=4, interval="1"):
    return trace.execute("trace-configure", mode="simulate", simulator=session,
                         arguments={"source": source, "points": points, "interval_s": interval})


def start(session, **options):
    return trace.execute("trace-start", mode="simulate", simulator=session, arguments=options)


@pytest.mark.parametrize(("source", "points", "interval", "sample_count", "valid"), [
    ("VOLT", 2, "0.001", 2, True),
    ("CURR", 1000, "0.001", 1000, True),
    ("TWO", 2, "3600", 1, True),
    ("TWO", 1000, "0.001", 500, True),
    ("VOLT", 25, "3600", 25, True),
    ("TWO", 50, "3600", 25, True),
    ("VOLT", 26, "3600", None, False),
    ("VOLT", 25, "3600.0000000000000000001", None, False),
    ("TWO", 3, "1", None, False),
    ("TWO", 1, "1", None, False),
    ("CURR", 1, "1", None, False),
    ("VOLT", 1001, "1", None, False),
    ("VOLT", 2, "0.0009999999999999999999", None, False),
    ("VOLT", 2, "NaN", None, False),
    ("VOLT", True, "1", None, False),
])
def test_trace_configuration_checks_bounds_before_io(source, points, interval, sample_count, valid):
    session = RecordingVISA()
    if valid:
        assert configure(session, source, points, interval)["sample_count"] == sample_count
    else:
        with pytest.raises(LoadError):
            configure(session, source, points, interval)
        assert session.calls == []


@pytest.mark.parametrize("reply", ["1", "1.000000", "1E+0"])
def test_trace_configure_accepts_equal_interval_with_different_format(reply):
    session = RecordingVISA({"TRAC:TIM?": reply, "TRAC:FEED?": "CURRENT"})
    session.mode = "LIST"
    session.transient_enabled = True
    session.short_on = True
    session.input_timer_enabled = True
    assert configure(session, source="CURR")["interval_s"] == "1"
    writes = [call for call in session.calls if isinstance(call, str)]
    assert writes == ["SYST:REM", "TRAC:FEED:CONT NEVER", "TRAC:FEED CURR", "TRAC:POIN 4",
                      "TRAC:TIM 1", "TRAC:DEL 0", "TRAC:FILT 0", "TRAC:CLE"]
    assert ("TRAC:FILT?", 5000) in session.calls
    assert session.mode == "LIST" and session.transient_enabled and session.short_on
    assert session.input_timer_enabled and not session.input_on


@pytest.mark.parametrize(("reply", "code"), [
    ("0.001", "trace_interval_mismatch"),
    ("0.0013000000000000000001", "trace_interval_mismatch"),
    ("not a number", "trace_session_untrusted"),
    (VisaIOError(StatusCode.error_timeout), "trace_session_untrusted"),
])
def test_trace_configure_refuses_rounded_or_unknown_interval(reply, code):
    session = RecordingVISA({"TRAC:TIM?": reply})
    with pytest.raises(LoadError) as caught:
        configure(session, interval="0.0013")
    assert caught.value.code == code
    if code == "trace_interval_mismatch":
        assert caught.value.details == {"requested_interval_s": "0.0013", "actual_interval_s": reply}
    assert "TRIG" not in session.calls
    assert session.calls[-1] == ("TRAC:TIM?", 5000)


def test_trace_configure_requires_input_off_and_bounded_clear(clock):
    session = RecordingVISA()
    session.input_on = True
    with pytest.raises(LoadError, match="confirmed Input OFF"):
        configure(session)
    assert all(isinstance(call, tuple) for call in session.calls)
    session.input_on = False
    session.responses["STAT:QUES:COND?"] = "32768"
    with pytest.raises(LoadError) as caught:
        configure(session)
    assert caught.value.code == "trace_buffer_not_clear"
    assert clock.now == 5
    assert session.calls.count("TRAC:CLE") == 1


@pytest.mark.parametrize("input_on", [False, True])
@pytest.mark.parametrize(("query", "reply"), [
    ("TRAC:DEL?", "1E-9"),
    ("TRAC:FILT?", "1"),
    ("TRAC:FEED:CONT?", "NEXT"),
    ("STAT:QUES:COND?", "32768"),
    ("FUNC:MODE?", "LIST"),
    ("FUNC:MODE?", "UNKNOWN"),
    ("FUNC?", "UNKNOWN"),
    ("TRAN?", "1"),
    ("INP:SHOR:STAT?", "1"),
    ("INP:TIM?", "1"),
    ("INP:STAT?", "UNKNOWN"),
    ("TRAC:TIM?", "0.000999"),
])
def test_trace_start_refuses_unsafe_state_before_csv_or_trigger(query, reply, input_on, tmp_path):
    session = RecordingVISA({query: reply})
    session.input_on = input_on
    path = tmp_path / "missing" / "capture.csv"
    with pytest.raises(LoadError):
        start(session, csv=str(path))
    assert all(isinstance(call, tuple) for call in session.calls)
    assert not path.parent.exists() and session.input_on == input_on


def test_trace_start_revalidates_the_capture_duration(tmp_path):
    session = RecordingVISA({"TRAC:POIN?": "26", "TRAC:TIM?": "3600", "TRAC:FEED?": "CURR"})
    with pytest.raises(LoadError) as caught:
        start(session, csv=str(tmp_path / "batch.csv"))
    assert caught.value.code == "trace_duration_limit"
    assert all(isinstance(call, tuple) for call in session.calls)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("ignored_write", ["TRAC:FEED:CONT NEXT", "TRIG"])
def test_trace_does_not_accept_stale_data_if_arm_or_trigger_was_ignored(clock, ignored_write):
    session = RecordingVISA({"STAT:QUES:COND?": "0"})
    session.trace_data = "99 8,98 7"
    write = session.write

    def ignore_write(command):
        if command == ignored_write:
            session.calls.append(command)
        else:
            write(command)

    session.write = ignore_write
    with pytest.raises(LoadError) as caught:
        start(session)
    if ignored_write == "TRIG":
        assert caught.value.code == "trace_timeout"
        assert caught.value.details["feed"] == "NEVER"
        assert session.calls.count("TRIG") == 1
    else:
        assert caught.value.code == "trace_arm_unconfirmed"
        assert "TRIG" not in session.calls
    assert ("TRAC:DATA?", 30000) not in session.calls
    assert session.calls.count("TRAC:FEED:CONT NEXT") == 1
    assert "TRAC:CLE" not in session.calls
    assert session.trace_data == "99 8,98 7"


@pytest.mark.parametrize(("source", "function", "payload", "sample", "status"), [
    ("VOLT", "CURR", "2.5,2.6,", {"voltage_v": 2.5}, "0"),
    ("CURR", "VOLT", "0.1,0.2", {"current_a": 0.1}, "32768"),
    ("TWO", "RES", "+2.5E+0 +1E-1,2.6 0.2", {"voltage_v": 2.5, "current_a": 0.1}, "32768"),
    ("TWO", "POW", "2.5,0.1,2.6,0.2", {"voltage_v": 2.5, "current_a": 0.1}, "0"),
])
def test_trace_start_reads_one_valid_batch_with_independent_visa_timeout(
    source, function, payload, sample, status, clock,
):
    session = RecordingVISA({"TRAC:DATA?": payload, "SYST:ERR?": "0, No Error"})
    session.trace_source = source
    session.trace_points = 4 if source == "TWO" else 2
    session.input_on = True
    session.function = function
    original_query = session.query

    def query(command):
        if command == "STAT:QUES:COND?" and "TRIG" in session.calls:
            session.responses[command] = status
        return original_query(command)

    session.query = query
    result = start(session)
    assert set(result) == {"source", "points", "interval_s", "sample_count",
                           "read_timestamp_utc_plus_8", "samples"}
    assert result["sample_count"] == 2 and result["samples"][0] == {"sample_index": 1, **sample}
    assert datetime.fromisoformat(result["read_timestamp_utc_plus_8"]).utcoffset() == timedelta(hours=8)
    assert [call for call in session.calls if isinstance(call, str)] == ["TRAC:FEED:CONT NEXT", "TRIG"]
    arm = session.calls.index("TRAC:FEED:CONT NEXT")
    assert session.calls[arm + 1:arm + 3] == [("TRAC:FEED:CONT?", 5000), "TRIG"]
    assert session.calls[-3:] == [("STAT:QUES:COND?", 5000), ("TRAC:DATA?", 30000), ("SYST:ERR?", 5000)]
    assert session.input_on and session.function == function and session.timeout == 5000


@pytest.mark.parametrize(("responses", "code"), [
    ({"TRAC:DATA?": ""}, "trace_incomplete"),
    ({"TRAC:DATA?": "1,2"}, "trace_incomplete"),
    ({"TRAC:DATA?": "2.5,NEVER,2.6,0.2"}, "trace_session_untrusted"),
    ({"TRAC:DATA?": "2.5,1E999,2.6,0.2"}, "trace_session_untrusted"),
    ({"TRAC:DATA?": "2.5 0.1,2.6,0.2"}, "trace_session_untrusted"),
    ({"TRAC:DATA?": VisaIOError(StatusCode.error_timeout)}, "trace_session_untrusted"),
    ({"SYST:ERR?": '-120,"Numeric data error"'}, "trace_instrument_error"),
])
def test_trace_unsuccessful_batch_retains_header_and_never_retriggers(responses, code, clock, tmp_path):
    session = RecordingVISA(responses)
    path = tmp_path / "new" / "capture.csv"
    with pytest.raises(LoadError) as caught:
        start(session, csv=str(path))
    assert caught.value.code == code and caught.value.details["csv_incomplete"]
    assert path.read_text().splitlines() == ["read_timestamp_utc_plus_8,sample_index,voltage_v,current_a"]
    assert [call for call in session.calls if isinstance(call, str)] == ["TRAC:FEED:CONT NEXT", "TRIG"]
    assert session.calls.count(("TRAC:DATA?", 30000)) == 1
    if "TRAC:DATA?" in responses:
        assert session.calls[-1] == ("TRAC:DATA?", 30000)


def test_trace_data_timeout_blocks_reuse_of_same_core_session(clock):
    session = RecordingVISA({"TRAC:DATA?": VisaIOError(StatusCode.error_timeout)})
    with pytest.raises(LoadError) as caught:
        start(session)
    assert caught.value.code == "trace_session_untrusted"
    prior_calls = session.calls.copy()
    for command in (configure, start,
                    lambda device: readonly.execute("measure", mode="simulate", simulator=device),
                    lambda device: control.execute("input-off", mode="simulate", simulator=device)):
        with pytest.raises(LoadError) as caught:
            command(session)
        assert caught.value.code == "trace_session_untrusted"
    assert session.calls == prior_calls
    assert configure(RecordingVISA())["points"] == 4


def test_trace_host_deadline_keeps_long_polling_bounded_and_cleans_feed(clock):
    session = RecordingVISA()
    session.trace_points = 50
    session.trace_interval = "3600"
    original_query = session.query
    poll_times = []

    def query(command):
        if command == "TRAC:FEED:CONT?" and session.trace_feed == "NEXT" and "TRIG" in session.calls:
            poll_times.append(clock.now)
            session.trace_capturing = False
        return original_query(command)

    session.query = query
    with pytest.raises(LoadError) as caught:
        start(session)
    assert caught.value.code == "trace_timeout" and caught.value.details["feed"] == "NEVER"
    assert poll_times[0] == 60 and max(poll_times) < 103680
    assert 86395 in poll_times and 86400 in poll_times and 86401 in poll_times
    assert 86465 in poll_times and 86406 not in poll_times
    assert clock.now == 103680
    assert session.calls[-2:] == ["TRAC:FEED:CONT NEVER", ("TRAC:FEED:CONT?", 5000)]
    assert ("TRAC:DATA?", 30000) not in session.calls


@pytest.mark.parametrize(("csv_format", "selection", "extras"), [
    ("data", "", []),
    ("data", "delta", ["estimated_delta_t_s"]),
    ("full", "elapsed", ["source", "points", "interval_s", "sample_count", "estimated_elapsed_s"]),
    ("full", "elapsed,delta", ["source", "points", "interval_s", "sample_count",
                               "estimated_elapsed_s", "estimated_delta_t_s"]),
])
def test_trace_csv_pretrigger_header_and_complete_batch(clock, tmp_path, csv_format, selection, extras):
    session = RecordingVISA()
    session.trace_interval = "0.001000000000000000000000000000000001"
    path = tmp_path / "created" / "capture.csv"
    original_write = session.write

    def write(command):
        if command == "TRIG":
            assert len(path.read_text().splitlines()) == 1
        original_write(command)

    session.write = write
    result = start(session, csv=str(path), csv_format=csv_format, csv_time_columns=selection)
    with path.open(newline="") as file:
        reader = csv.DictReader(file)
        assert reader.fieldnames == ["read_timestamp_utc_plus_8", "sample_index",
                                     "voltage_v", "current_a", *extras]
        first, second = reader
    assert first["read_timestamp_utc_plus_8"] == second["read_timestamp_utc_plus_8"]
    assert second["read_timestamp_utc_plus_8"] == result["read_timestamp_utc_plus_8"]
    if "elapsed" in selection:
        assert Decimal(first["estimated_elapsed_s"]) == 0
        assert Decimal(second["estimated_elapsed_s"]) == Decimal(session.trace_interval)
    if "delta" in selection:
        assert first["estimated_delta_t_s"] == ""
        assert Decimal(second["estimated_delta_t_s"]) == Decimal(session.trace_interval)


def test_trace_csv_exclusive_creation_and_write_failure(clock, tmp_path, monkeypatch):
    path = tmp_path / "capture.csv"
    path.write_text("pre-existing data")
    session = RecordingVISA()
    with pytest.raises(LoadError) as caught:
        start(session, csv=str(path))
    assert caught.value.code == "trace_csv_exists"
    assert path.read_text() == "pre-existing data" and "TRIG" not in session.calls

    path = tmp_path / "partial.csv"
    original = csv.DictWriter.writerow

    def fail_on_second(self, row):
        if row["sample_index"] == 2:
            raise OSError("simulated disk failure")
        return original(self, row)

    monkeypatch.setattr(csv.DictWriter, "writerow", fail_on_second)
    with pytest.raises(LoadError) as caught:
        start(session, csv=str(path))
    assert caught.value.code == "trace_csv_failed" and caught.value.details["csv_incomplete"]
    assert len(path.read_text().splitlines()) == 2 and session.calls.count("TRIG") == 1


def test_trace_cli_entry_points_and_live_refusal(capsys, tmp_path):
    args = ["--source", "TWO", "--points", "4", "--interval-s", "1.0000000000000000000001"]
    assert main(["trace-configure", *args, "--simulate", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["interval_s"] == args[-1]
    path = tmp_path / "output" / "capture.csv"
    assert main(["trace-start", "--dry-run", "--csv", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["hardware_access"] is False
    assert not path.parent.exists()
    for command, options in [("trace-configure", args), ("trace-start", ["--csv", str(path)])]:
        assert main([command, *options, "--resource", RESOURCE,
                     "--validation-allow-pending-live-support", "--json"]) == 2
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported_live_scope"
    assert not path.parent.exists()
    assert main(["trace-start", "--simulate", "--csv", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["sample_count"] == 2
    assert Path(path).is_file()


@pytest.mark.parametrize(("source", "points", "count"), [
    ("VOLT", 10, 10), ("CURR", 10, 10), ("TWO", 40, 20),
])
def test_trace_hardware_validation_cli_only_uses_native_capture(
    monkeypatch, clock, source, points, count,
):
    """A recording VISA object exercises the exact contributor-live dispatcher."""
    session = RecordingVISA()
    opened = []

    @contextmanager
    def fake_open(resource):
        opened.append(resource)
        yield session, {"library": "FAKE", "manufacturer": "FAKE"}

    monkeypatch.setattr(trace, "open_session", fake_open)
    configuration = {"source": source, "points": points, "interval_s": 1}
    result = trace.execute("trace-configure", mode="live", resource=RESOURCE,
                           hardware_validation="trace", arguments=configuration)
    assert result["validation_only"] and result["validation_scope"] == "trace"
    assert result["visa"]["library"] == "FAKE"
    acquired = trace.execute("trace-start", mode="live", resource=RESOURCE,
                             hardware_validation="trace", arguments={})
    assert len(acquired["samples"]) == count
    assert acquired["source"] == source and acquired["points"] == points
    assert acquired["validation_only"] and acquired["visa"]["manufacturer"] == "FAKE"
    assert opened == [RESOURCE, RESOURCE]
    assert session.calls.count("TRAC:FEED:CONT NEXT") == 1
    assert session.calls.count("TRIG") == 1
    assert session.calls.count(("TRAC:DATA?", 30000)) == 1
    assert "INP ON" not in session.calls and "INP OFF" not in session.calls


def test_trace_hardware_validation_refuses_ordinary_live_before_open(
    monkeypatch, capsys,
):
    @contextmanager
    def forbidden(_resource):
        pytest.fail("Unapproved TRACE LIVE may not open VISA.")
        yield

    monkeypatch.setattr(trace, "open_session", forbidden)
    for command, arguments in [
        ("trace-start", {}),
        ("trace-configure", {"source": "TWO", "points": 4, "interval_s": 1}),
    ]:
        with pytest.raises(LoadError) as caught:
            trace.execute(command, mode="live", resource=RESOURCE, arguments=arguments)
        assert caught.value.code == "unsupported_live_scope"
    assert main(["trace-start", "--resource", RESOURCE,
                 "--validation-allow-pending-live-support", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported_live_scope"
    assert main(["trace-start", "--hardware-validation", "trace",
                 "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)["result"]
    assert preview["hardware_access"] is False
    assert preview["validation_only"] and preview["validation_scope"] == "trace"
    with pytest.raises(LoadError, match="validation"):
        trace.execute("trace-start", mode="simulate", hardware_validation="trace")
    with pytest.raises(LoadError) as caught:
        trace.execute("trace-start", mode="live", resource=RESOURCE,
                      model_id="not-itech", hardware_validation="trace")
    assert caught.value.code == "unsupported_model"


def test_trace_hardware_validation_live_failed_session_cannot_reuse_same_object(
    monkeypatch, clock,
):
    session = RecordingVISA({"TRAC:DATA?": VisaIOError(StatusCode.error_timeout)})
    opened = []

    @contextmanager
    def fake_open(resource):
        opened.append(resource)
        yield session, {"library": "FAKE", "manufacturer": "FAKE"}

    monkeypatch.setattr(trace, "open_session", fake_open)
    with pytest.raises(LoadError) as caught:
        trace.execute("trace-start", mode="live", resource=RESOURCE,
                      hardware_validation="trace", arguments={})
    assert caught.value.code == "trace_session_untrusted"
    previous = session.calls.copy()
    with pytest.raises(LoadError) as caught:
        trace.execute("trace-configure", mode="live", resource=RESOURCE,
                      hardware_validation="trace",
                      arguments={"source": "TWO", "points": 4, "interval_s": 1})
    assert caught.value.code == "trace_session_untrusted"
    assert session.calls == previous
    assert opened == [RESOURCE, RESOURCE]


def test_configuration_readback_trace_status_is_query_only(fake_visa):
    fake_visa.responses.update({"TRAC:FEED?": "CURRENT", "TRAC:POIN?": "2000",
                                "TRAC:TIM?": "0.000020000000001", "TRAC:DEL?": "0.5",
                                "TRAC:FILT?": "1", "TRAC:FEED:CONT?": "NEXT"})
    actual = trace.execute("trace-status", resource=RESOURCE, hardware_validation="trace")
    assert actual["state_source"] == "instrument_readback" and actual["feed"] == "NEXT"
    assert actual["source"] == "CURR" and actual["points"] == 2000
    assert actual["delay_s"] == "0.5" and actual["filter_enabled"] is True
    assert actual["interval_s"] == "0.000020000000001"
    assert {call for call in fake_visa.calls if isinstance(call, str) and "?" in call} == {
        "*IDN?", "TRAC:FEED?", "TRAC:POIN?", "TRAC:TIM?", "TRAC:DEL?", "TRAC:FILT?", "TRAC:FEED:CONT?",
    }

    fake_visa.responses["TRAC:FILT?"] = "2"
    with pytest.raises(LoadError) as failure:
        trace.execute("trace-status", resource=RESOURCE, hardware_validation="trace")
    assert failure.value.code == "invalid_response" and failure.value.exit_code == 3


def test_configuration_readback_trace_status_does_not_progress_simulated_capture():
    simulator = ControlSimulator()
    simulator.trace_feed = "NEXT"
    simulator.trace_capturing = True
    before = simulator.__dict__.copy()
    status = trace.execute("trace-status", mode="simulate", simulator=simulator)
    assert status["state_source"] == "simulate" and status["feed"] == "NEXT"
    assert simulator.__dict__ == before
