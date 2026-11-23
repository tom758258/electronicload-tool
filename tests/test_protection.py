"""Protection contracts with simulated state and recording IT8812C I/O."""

import json
from contextlib import contextmanager
from threading import Event

import pytest

from electronicload_tool_cli.cli import main
from electronicload_tool_core import protection
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.simulator import ControlSimulator

RESOURCE = "USB0::0xFFFF::0x0001::SYNTHETIC::INSTR"


class Instrument:
    def __init__(self):
        self.calls = []
        self.replies = {
            "*IDN?": "ITECH Ltd,IT8812C,FAKE,1.0", "INP:STAT?": "0",
            "CURR:PROT:LEV?": "60", "CURR:PROT:DEL?": "3", "CURR:PROT:STAT?": "0.0E+0",
            "POW:PROT?": "260", "POW:PROT:DEL?": "3", "POW:CONF?": "225",
            "POW:PROT? MAX": "260", "POW:CONF? MAX": "250",
            "STAT:QUES:COND?": "8194", "STAT:QUES:EVEN?": "+8194",
            "FUNC:MODE?": "FIX", "FUNC?": "CURR", "CURR:RANG?": "6", "CURR?": "0.2",
            "TRAN?": "0", "INP:SHOR:STAT?": "0", "INP:TIM?": "0",
            "CURR:SLEW:STAT?": "0", "VOLT:ON?": "0",
            "MEAS:VOLT?": "5", "MEAS:CURR?": "0.1", "FETC:POW?": "0.5",
        }
        self.writes = []
        self.remote = False
        self.on_clear = lambda: self.replies.update({"STAT:QUES:COND?": "0"})
        self.on_write = lambda command: None
        self.ignored = None

    def query(self, command):
        self.calls.append(command)
        reply = self.replies[command]
        if isinstance(reply, Exception):
            raise reply
        if command == "STAT:QUES:EVEN?":
            self.replies[command] = "0"
        return reply

    def write(self, command):
        self.calls.append(command)
        self.writes.append(command)
        if command == "SYST:REM":
            self.remote = True
        assert self.remote
        self.on_write(command)
        if command == self.ignored:
            return
        if command == "PROT:CLE":
            self.on_clear()
        elif command in ("INP ON", "INP OFF"):
            self.replies["INP:STAT?"] = "1" if command == "INP ON" else "0"
        elif command != "SYST:REM":
            key, value = command.split(" ", 1)
            self.replies[key + "?"] = self.replies[key + "? MAX"] if value == "MAX" else value


@pytest.fixture
def instrument(monkeypatch):
    fake = Instrument()

    @contextmanager
    def open_session(resource):
        assert resource == RESOURCE
        yield fake, {"library": "FAKE", "manufacturer": "FAKE"}

    monkeypatch.setattr(protection, "open_session", open_session)
    return fake


def physical(command, **arguments):
    return protection.execute(command, resource=RESOURCE, hardware_validation=True, **arguments)


@pytest.mark.parametrize("query,reply,diagnostic", [
    ("POW:CONF? MAX", "+2.50E+2", "returned 250"),
    ("POW:CONF? MAX", "251", "returned 251"),
    ("POW:CONF? MAX", "NaN", "failed: invalid_response"),
    ("POW:CONF?", "251", "returned 251"),
    ("POW:CONF?", "NaN", None),
])
def test_hardware_power_readback_keeps_limits_and_private_diagnostics(
    instrument, caplog, query, reply, diagnostic,
):
    instrument.replies[query] = reply
    with caplog.at_level("DEBUG", logger=protection.__name__):
        if reply == "+2.50E+2":
            result = physical("protection-set", arguments={
                "restore_defaults": "hardware-power", "confirm": True,
            })
            assert result["applied"] == {"hardware_power_limit": "250"}
            assert instrument.writes == ["SYST:REM", "POW:CONF MAX"]
        else:
            with pytest.raises(LoadError) as failure:
                if query.endswith(" MAX"):
                    physical("protection-set", arguments={
                        "restore_defaults": "hardware-power", "confirm": True,
                    })
                else:
                    physical("protection-status")
            assert failure.value.code == "invalid_response"
            assert failure.value.details["query"] == query
            assert reply not in str(failure.value.details)
            assert instrument.writes == []
    if diagnostic:
        assert f"Protection {query} {diagnostic}" in caplog.text
    else:
        assert not caplog.records


def test_protection_status_reads_on_and_consumes_events_only_when_requested(instrument):
    instrument.replies["INP:STAT?"] = "1"
    result = physical("protection-status")
    assert result["input_state"] == "ON"
    assert result["condition"] == {"raw": 8194, "flags": ["OC", "PS"]}
    assert result["settings"]["software_current_enabled"] is False
    assert "events" not in result and "STAT:QUES:EVEN?" not in instrument.calls
    assert physical("protection-status", arguments={"events": True})["events"] == result["condition"]
    assert physical("protection-status", arguments={"events": True})["events"] == {"raw": 0, "flags": []}
    assert instrument.writes == []
    instrument.replies["STAT:QUES:COND?"] = "65536"
    with pytest.raises(LoadError) as error:
        physical("protection-status", arguments={"events": True})
    assert error.value.code == "invalid_response"
    assert instrument.calls.count("STAT:QUES:EVEN?") == 2


@pytest.mark.parametrize("failure", [None, "input", "identity", "stop", "remote", "readback", "timeout"])
def test_protection_set_off_guards_and_reports_partial_writes(instrument, failure):
    stop = Event()
    if failure == "input":
        instrument.replies["INP:STAT?"] = "1"
    elif failure == "identity":
        instrument.replies["*IDN?"] = "ITECH Ltd,IT8812,FAKE,1.0"
    elif failure == "stop":
        stop.set()
    elif failure == "remote":
        instrument.on_write = lambda _: instrument.replies.update({"INP:STAT?": "1"})
    elif failure == "readback":
        instrument.ignored = "POW:PROT 0.50"
    elif failure == "timeout":
        def time_out(command):
            if command == "POW:PROT 0.50":
                raise TimeoutError()
        instrument.on_write = time_out
    args = {"confirm": True, "software_current_limit": "0.10", "software_power_limit": "0.50",
            "hardware_power_limit": "0.75"}
    wanted = ["SYST:REM", "CURR:PROT:LEV 0.10", "POW:PROT 0.50", "POW:CONF 0.75"]
    if failure is None:
        assert physical("protection-set", arguments=args, stop=stop)["applied"] == {
            "software_current_limit": "0.10", "software_power_limit": "0.50",
            "hardware_power_limit": "0.75",
        }
        assert instrument.writes == wanted
    else:
        with pytest.raises(LoadError) as error:
            physical("protection-set", arguments=args, stop=stop)
        if failure in ("readback", "timeout"):
            assert error.value.code == ("readback_mismatch" if failure == "readback" else "write_failed")
            assert instrument.writes == wanted[:3]
            assert error.value.details["confirmed"] == {"software_current_limit": "0.10"}
            assert error.value.details["unconfirmed"] == ["software_power_limit"]
            assert error.value.details["not_attempted"] == ["hardware_power_limit"]
        else:
            assert instrument.writes == (wanted[:1] if failure == "remote" else [])
    for write in instrument.writes:
        assert instrument.calls[instrument.calls.index(write) - 1] == "INP:STAT?"


@pytest.mark.parametrize(("group", "writes", "applied"), [
    ("software-power", ["POW:PROT MAX", "POW:PROT:DEL 0"],
     {"software_power_limit": "260", "software_power_delay": "0"}),
    ("hardware-power", ["POW:CONF MAX"], {"hardware_power_limit": "250"}),
])
def test_protection_defaults_are_independent_and_read_back(instrument, group, writes, applied):
    original = physical("protection-status", snapshot=True)["settings"]
    assert original["software_power_limit"] == "260"
    assert original["software_power_delay"] == "3"
    result = physical("protection-set", arguments={"confirm": True, "restore_defaults": group})
    assert result["applied"] == applied
    assert instrument.writes == ["SYST:REM", *writes]
    assert all(instrument.calls.index(key + "? MAX") < instrument.calls.index("SYST:REM")
               for key in ("POW:PROT", "POW:CONF") if key + " MAX" in writes)
    saved = {field: original[field] for field in applied}
    assert physical("protection-set", arguments={"confirm": True, **saved})["applied"] == saved
    assert physical("protection-status", snapshot=True)["settings"] == original


@pytest.mark.parametrize("outcome", ["off", "restored_on", "off_refused", "lost", "still_active", "already_on"])
def test_protection_clear_observes_input_and_issues_at_most_one_bounded_off(instrument, outcome):
    if outcome in ("restored_on", "off_refused"):
        instrument.on_clear = lambda: instrument.replies.update({"INP:STAT?": "1", "STAT:QUES:COND?": "0"})
        if outcome == "off_refused":
            instrument.ignored = "INP OFF"
    elif outcome == "lost":
        instrument.on_clear = lambda: instrument.replies.update({"INP:STAT?": TimeoutError()})
    elif outcome == "still_active":
        instrument.on_clear = lambda: None
    elif outcome == "already_on":
        instrument.replies["INP:STAT?"] = "1"
    if outcome == "off":
        assert physical("protection-clear", arguments={"confirm": True})["input_state"] == "OFF"
        assert instrument.writes == ["SYST:REM", "PROT:CLE"]
    else:
        with pytest.raises(LoadError) as error:
            physical("protection-clear", arguments={"confirm": True})
        if outcome in ("restored_on", "off_refused"):
            assert error.value.code == "protection_clear_unexpected_on"
            assert error.value.details["input_state"] == ("OFF" if outcome == "restored_on" else "ON")
            assert error.value.details["operator_action_required"] == (outcome == "off_refused")
            assert instrument.calls[-4:] == ["INP:STAT?", "INP OFF", "INP:STAT?", "STAT:QUES:COND?"]
            assert instrument.writes == ["SYST:REM", "PROT:CLE", "INP OFF"]
        elif outcome == "already_on":
            assert error.value.code == "input_not_off" and not instrument.writes
        else:
            assert error.value.code == ("query_failed" if outcome == "lost" else "protection_still_active")
            assert instrument.writes == ["SYST:REM", "PROT:CLE"]
    if outcome != "already_on":
        assert instrument.calls[instrument.calls.index("PROT:CLE") + 1] == "INP:STAT?"
    assert "STAT:QUES:EVEN?" not in instrument.calls


def test_protection_simulation_preserves_mode_and_pending_events_until_read():
    sim = ControlSimulator()
    sim.protection_condition = 8194 | 128
    sim.protection_events = 8194
    sim.input_on = True

    def run(command, arguments=None, *, mode="simulate"):
        return protection.execute(command, arguments=arguments, mode=mode, simulator=sim)

    before = run("protection-status")
    assert before["condition"]["flags"] == ["OC", "RUN", "PS"]
    assert run("protection-status", {"events": True}, mode="dry-run")["events"]
    assert run("protection-status") == before
    with pytest.raises(LoadError, match="confirmed OFF"):
        run("protection-clear", {"confirm": True})
    sim.input_on = False
    change = {"confirm": True, "software_current_enabled": True, "software_current_limit": "0.10",
              "hardware_power_limit": "1.123456789012345678"}
    assert run("protection-set", change)["applied"]["software_current_enabled"]
    assert run("protection-set", {"confirm": True, "restore_defaults": "software-power"})["applied"] == {
        "software_power_limit": "260", "software_power_delay": "0",
    }
    assert run("protection-clear", {"confirm": True})["condition"]["flags"] == ["RUN"]
    first = run("protection-status", {"events": True})
    assert first["settings"]["hardware_power_limit"] == change["hardware_power_limit"]
    assert first["events"]["raw"] == 8194
    assert run("protection-status", {"events": True})["events"]["raw"] == 0
    assert sim.mode == "FIX" and not sim.input_on


@pytest.mark.parametrize("arguments", [
    {"software_power_limit": "1"},
    {"confirm": True},
    {"confirm": True, "software_current_delay": 61},
    {"confirm": True, "software_power_limit": "NaN"},
    {"confirm": True, "software_power_limit": "260.01"},
    {"confirm": True, "hardware_power_limit": "250.01"},
    {"confirm": True, "hardware_power_limit": True},
    {"confirm": True, "restore_defaults": "software-power", "software_power_delay": 0},
])
def test_protection_set_bad_arguments_have_no_side_effects(instrument, arguments):
    with pytest.raises(LoadError):
        physical("protection-set", arguments=arguments)
    assert not instrument.calls


@pytest.mark.parametrize("trial", [
    "software-current", "software-power", "hardware-power-baseline", "hardware-power",
])
def test_protection_trial_requires_exact_profile_and_refuses_interfering_protection(instrument, monkeypatch, trial):
    monkeypatch.setattr(protection, "sleep", lambda _: None)
    instrument.replies["STAT:QUES:COND?"] = "0"
    for field, value in protection.TRIALS[trial].items():
        instrument.replies[protection.REGISTERS[field] + "?"] = "1" if value is True else str(value)
    physical("protection-status", snapshot=True)
    arguments = {"trial": trial, "arguments": {"confirm": True}}
    if trial == "hardware-power-baseline":
        instrument.replies["POW:CONF?"] = "0.50"
        with pytest.raises(LoadError, match="existing protection") as error:
            physical("input-on", **arguments)
        assert error.value.details["input_state"] == "OFF"
        assert instrument.writes == []
        instrument.replies.update({"POW:CONF?": "250", "MEAS:CURR?": "0.20", "FETC:POW?": "0.60"})
    instrument.replies["INP:TIM?"] = "1"
    with pytest.raises(LoadError, match="could affect the trial"):
        physical("input-on", **arguments)
    assert instrument.writes == []
    instrument.replies["INP:TIM?"] = "0"
    instrument.on_write = lambda command: (
        instrument.replies.update({"CURR?": "0.3"}) if command == "SYST:REM" else None
    )
    with pytest.raises(LoadError, match="0.20 A"):
        physical("input-on", **arguments)
    assert instrument.writes == ["SYST:REM"]
    instrument.on_write = lambda _: None
    instrument.replies["CURR?"] = "0.2"
    result = physical("input-on", **arguments)
    assert result["input_state"] == "ON" and instrument.writes[-2:] == ["SYST:REM", "INP ON"]
    if trial == "hardware-power":
        assert result["calculated_power_w"] == result["power_w"] == 0.5
    if trial == "hardware-power-baseline":
        assert result["calculated_power_w"] == 1
        assert result["power_w"] == 0.6


def test_protection_cli_json_confirmation_and_live_refusal(capsys):
    def call(*args):
        code = main([*args, "--json"])
        return code, json.loads(capsys.readouterr().out)

    code, result = call("protection-status", "--simulate", "--events")
    assert code == 0 and result["result"]["events"]["raw"] == 0
    code, result = call("protection-set", "--dry-run", "--confirm", "--software-current-enabled", "on",
                        "--software-current-limit", "0.10", "--software-current-delay", "1")
    assert code == 0 and result["result"]["planned_writes"] == [
        "SYST:REM", "CURR:PROT:LEV 0.10", "CURR:PROT:DEL 1", "CURR:PROT:STAT 1",
    ]
    assert call("protection-clear", "--dry-run")[1]["error"]["code"] == "confirmation_required"
    for command, args in (("protection-status", []), ("protection-clear", ["--confirm"]),
                          ("protection-set", ["--confirm", "--hardware-power-limit", "0.5"])):
        code, result = call(command, "--resource", RESOURCE, "--validation-allow-pending-live-support", *args)
        assert code == 2 and result["error"]["code"] == "unsupported_live_scope"
    assert call("input-on", "--dry-run", "--confirm", "--protection-trial",
                "hardware-power")[1]["error"]["code"] == "argument_error"
