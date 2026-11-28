"""Core policy, query safety, and communication lifetime without hardware."""

import pytest
import pyvisa
from types import SimpleNamespace

from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.identity import MODEL_ID, parse_identity
from electronicload_tool_core.readonly import execute
from electronicload_tool_core.visa import mark_session_untrusted

RESOURCE = "USB0::0xFFFF::0x0001::SYNTHETIC::INSTR"
SYSTEM_SCOPE = {"resource": RESOURCE, "hardware_validation": "system-query"}
STANDALONE_SCOPE = {"resource": RESOURCE, "standalone_cli": True}

NATIVE_CASES = [
    ("measure-min-max", None, ["MEAS:VOLT:MIN?", "MEAS:VOLT:MAX?", "MEAS:CURR:MIN?", "MEAS:CURR:MAX?"],
     {"voltage_min_v": 11.9, "voltage_max_v": 12.1, "current_min_a": 0.4, "current_max_a": 0.6}),
    ("measure-min-max", "voltage-min", ["MEAS:VOLT:MIN?"], {"voltage_min_v": 11.9}),
    ("measure-min-max", "voltage-max", ["MEAS:VOLT:MAX?"], {"voltage_max_v": 12.1}),
    ("measure-min-max", "current-min", ["MEAS:CURR:MIN?"], {"current_min_a": 0.4}),
    ("measure-min-max", "current-max", ["MEAS:CURR:MAX?"], {"current_max_a": 0.6}),
    ("measure-discharge", None, ["MEAS:CAP?", "MEAS:TIME?"],
     {"capacity_ah": 0.25, "discharge_time_s": 1800.0}),
    ("measure-discharge", "capacity", ["MEAS:CAP?"], {"capacity_ah": 0.25}),
    ("measure-discharge", "time", ["MEAS:TIME?"], {"discharge_time_s": 1800.0}),
]


@pytest.mark.parametrize("command,query,reply,expected", [
    ("scpi-version", "SYST:VERS?", " 2020.01\n", {"scpi_version": "2020.01"}),
    ("display-mode", "DISP:MODE?", " norm\n", {"display_mode": "NORMAL"}),
    ("display-mode", "DISP:MODE?", "TEXT\n", {"display_mode": "TEXT"}),
    ("error", "SYST:ERR?", ' +120 , "Parameter, overflowed"\n',
     {"error_code": 120, "message": "Parameter, overflowed"}),
    ("error", "SYST:ERR?", "0, No Error", {"error_code": 0, "message": "No Error"}),
])
@pytest.mark.parametrize("standalone", [True, False])
def test_system_query_uses_exact_queries(fake_visa, command, query, reply, expected, standalone):
    fake_visa.responses[query] = reply
    scope = STANDALONE_SCOPE if standalone else SYSTEM_SCOPE
    plan = execute(command, mode="dry-run", **scope)
    assert plan["queries"] == ["*IDN?", query]
    assert plan["live_access"] == ("product" if standalone else "blocked")
    assert fake_visa.calls == []
    result = execute(command, **scope)
    validation = {} if standalone else {"validation_only": True, "validation_scope": "system-query"}
    assert result == {**expected, **validation,
                      "visa": {"library": "FAKE-VISA.dll", "manufacturer": "FAKE VISA"}}
    assert fake_visa.calls[-4:] == ["*IDN?", query, "session_close", "manager_close"]


@pytest.mark.parametrize("nonzero_count,confirmed", [(0, True), (2, True), (19, True), (20, False)])
@pytest.mark.parametrize("scope", [STANDALONE_SCOPE, SYSTEM_SCOPE], ids=["standalone", "contributor"])
def test_system_query_queue_read_bound_in_one_visa_session(fake_visa, monkeypatch, nonzero_count, confirmed, scope):
    entries = [(-222, 'Data, "out" of range'), (120, "Overflow")] * 10
    responses = (['-222,"Data, ""out"" of range"', '+120,"Overflow"'] * 10)[:nonzero_count]
    if confirmed:
        responses.append('0,"No error"')
    replies = iter(responses)
    query = type(fake_visa.session).query

    def next_entry(session, command):
        if command == "SYST:ERR?":
            fake_visa.responses[command] = next(replies)
        return query(session, command)

    monkeypatch.setattr(type(fake_visa.session), "query", next_entry)
    result = execute("error", all_errors=True, **scope)
    assert result["errors"] == [{"error_code": code, "message": message}
                                for code, message in entries[:nonzero_count]]
    assert result["read_count"] == nonzero_count + int(confirmed)
    assert result["max_reads"] == 20
    assert result["empty_confirmed"] is confirmed and result["limit_reached"] is not confirmed
    assert fake_visa.calls.count("open_resource") == 1
    assert fake_visa.calls[-(result["read_count"] + 3):] == [
        "*IDN?", *(["SYST:ERR?"] * result["read_count"]), "session_close", "manager_close",
    ]


@pytest.mark.parametrize("command,query,reply", [
    ("error", "SYST:ERR?", TimeoutError()),
    ("error", "SYST:ERR?", '0,"No error'),
    ("error", "SYST:ERR?", "0"),
    ("scpi-version", "SYST:VERS?", "v1.3.1"),
    ("display-mode", "DISP:MODE?", "GRAPH"),
])
@pytest.mark.parametrize("scope", [STANDALONE_SCOPE, SYSTEM_SCOPE], ids=["standalone", "contributor"])
def test_system_query_invalid_response_or_query_failure_stops_and_closes(fake_visa, command, query, reply, scope):
    fake_visa.responses[query] = reply
    with pytest.raises(LoadError) as failure:
        execute(command, all_errors=command == "error", **scope)
    assert failure.value.code == ("query_failed" if isinstance(reply, Exception) else "invalid_response")
    assert failure.value.exit_code == 3 and failure.value.details["query"] == query
    assert fake_visa.calls[-4:] == ["*IDN?", query, "session_close", "manager_close"]


@pytest.mark.parametrize("scope", [STANDALONE_SCOPE, SYSTEM_SCOPE], ids=["standalone", "contributor"])
def test_system_query_queue_failure_keeps_parsed_entries(fake_visa, monkeypatch, scope):
    replies = iter(['-222,"Data, out of range"', TimeoutError()])
    query = type(fake_visa.session).query

    def next_entry(session, command):
        if command == "SYST:ERR?":
            fake_visa.responses[command] = next(replies)
        return query(session, command)

    monkeypatch.setattr(type(fake_visa.session), "query", next_entry)
    with pytest.raises(LoadError) as failure:
        execute("error", all_errors=True, **scope)
    assert failure.value.code == "query_failed" and failure.value.exit_code == 3
    assert failure.value.details["read_count"] == 1
    assert failure.value.details["errors"] == [{"error_code": -222, "message": "Data, out of range"}]
    assert fake_visa.calls[-5:] == ["*IDN?", "SYST:ERR?", "SYST:ERR?", "session_close", "manager_close"]


@pytest.mark.parametrize("scope,overrides,code", [
    (STANDALONE_SCOPE, {"backend": "other"}, "unsupported_backend"),
    (SYSTEM_SCOPE, {"backend": "other"}, "unsupported_backend"),
    (STANDALONE_SCOPE, {"resource": None}, "missing_resource"),
    (SYSTEM_SCOPE, {"resource": None}, "missing_resource"),
    (STANDALONE_SCOPE, {"resource": "ASRL1::INSTR"}, "invalid_resource"),
    (SYSTEM_SCOPE, {"resource": "ASRL1::INSTR"}, "invalid_resource"),
    (STANDALONE_SCOPE, {"model_id": "other"}, "unsupported_model"),
    (SYSTEM_SCOPE, {"model_id": "other"}, "unsupported_model"),
    (SYSTEM_SCOPE, {"hardware_validation": None, "validation": True}, "unsupported_live_scope"),
    (SYSTEM_SCOPE, {"mode": "simulate"}, "argument_error"),
])
def test_system_query_rejects_wrong_scope_before_visa(fake_visa, scope, overrides, code):
    with pytest.raises(LoadError) as failure:
        execute("display-mode", **{**scope, **overrides})
    assert failure.value.code == code and fake_visa.calls == []


@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize("standalone", [True, False])
def test_system_query_rejects_mismatched_identity_or_untrusted_session(fake_visa, trusted, standalone):
    if not trusted:
        mark_session_untrusted(fake_visa.session)
    fake_visa.responses["*IDN?"] = "ITECH Ltd,IT8812,TEST,1.2"
    with pytest.raises(LoadError) as failure:
        scope = STANDALONE_SCOPE if standalone else SYSTEM_SCOPE
        execute("error", **scope)
    assert failure.value.code == ("identity_mismatch" if trusted else "trace_session_untrusted")
    assert ("*IDN?" in fake_visa.calls) is trusted
    assert "SYST:ERR?" not in fake_visa.calls
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]


@pytest.mark.parametrize("command,metric,queries,result", NATIVE_CASES)
def test_native_measurement_queries_and_fields(fake_visa, command, metric, queries, result):
    fake_visa.responses.update(dict(zip(queries, (str(value) for value in result.values()))))
    assert execute(command, mode="simulate", metric=metric, simulator=fake_visa.session) == result
    assert fake_visa.calls == ["*IDN?", *queries]
    plan = execute(command, mode="dry-run", metric=metric)
    assert plan["queries"] == ["*IDN?", *queries]
    assert plan["live_access"] == "blocked"


@pytest.mark.parametrize("command", ["measure-min-max", "measure-discharge"])
@pytest.mark.parametrize("metric", ["unknown", "", True, [], {}])
def test_native_measurement_invalid_metric_before_io(fake_visa, command, metric):
    with pytest.raises(LoadError) as error:
        execute(command, resource=RESOURCE, metric=metric)
    assert error.value.code == "argument_error"
    assert fake_visa.calls == []


@pytest.mark.parametrize("command,query", [
    ("measure-min-max", "MEAS:CURR:MAX?"), ("measure-discharge", "MEAS:TIME?"),
])
@pytest.mark.parametrize("reply", ["invalid", "NaN", "inf", "1e999", TimeoutError()])
def test_native_measurement_failure_has_query_and_no_fallback(fake_visa, command, query, reply):
    queries = next(case[2] for case in NATIVE_CASES if case[:2] == (command, None))
    fake_visa.responses.update(dict.fromkeys(queries, "1.25e-2"))
    fake_visa.responses[query] = reply
    with pytest.raises(LoadError) as error:
        execute(command, mode="simulate", simulator=fake_visa.session)
    assert error.value.code == ("query_failed" if isinstance(reply, Exception) else "invalid_response")
    assert error.value.exit_code == 3 and error.value.details["query"] == query
    assert fake_visa.calls == ["*IDN?", *queries[:queries.index(query) + 1]]


def test_native_measurement_scientific_numeric_reply(fake_visa):
    fake_visa.responses["MEAS:CAP?"] = " +2.5E-1\n"
    assert execute("measure-discharge", mode="simulate", metric="capacity",
                   simulator=fake_visa.session) == {"capacity_ah": 0.25}


@pytest.mark.parametrize("command", ["measure-min-max", "measure-discharge"])
@pytest.mark.parametrize("validation", [False, True])
def test_native_measurement_live_blocked_before_visa(fake_visa, command, validation):
    with pytest.raises(LoadError) as error:
        execute(command, resource=RESOURCE, validation=validation)
    assert error.value.code == "unsupported_live_scope"
    assert fake_visa.calls == []


def test_identity_keeps_detected_and_canonical_fields_separate():
    identity = parse_identity(" ITECH Ltd, it8812c, PRIVATE-SERIAL, 1.2\n")
    assert identity.model == "it8812c"
    assert identity.canonical_model == "IT8812C"
    assert identity.model_id == MODEL_ID
    assert identity.serial == "PRIVATE-SERIAL"
    assert "PRIVATE-SERIAL" not in str(identity.public_result())
    assert "PRIVATE-SERIAL" not in repr(identity)


@pytest.mark.parametrize("reply", [
    "", "ITECH Ltd,IT8812C,S", "ITECH Ltd,IT8812C,,1.0",
    "Other,IT8812C,S,1.0", "ITECH Ltd extra,IT8812C,S,1.0",
    "ITECH Ltd..,IT8812C,S,1.0", "ITECH Ltd. extra,IT8812C,S,1.0",
    "ITECH Ltd,IT8812C-extra,S,1.0", "ITECH Ltd,IT8812,S,1.0",
])
def test_identity_refusal_stops_queries_and_closes(fake_visa, reply):
    fake_visa.responses["*IDN?"] = reply
    with pytest.raises(LoadError) as exc:
        execute("measure", resource=RESOURCE)
    assert exc.value.exit_code == 2
    assert [c for c in fake_visa.calls if isinstance(c, str) and c.endswith("?")] == ["*IDN?"]
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]


@pytest.mark.parametrize("command,queries", [
    ("identify", ["*IDN?"]),
    ("input-state", ["*IDN?", "INP:STAT?"]),
    ("measure", ["*IDN?", "MEAS:VOLT?", "MEAS:CURR?", "FETC:POW?"]),
])
def test_observed_itech_manufacturer_spelling_allows_exact_readonly_scope(
    fake_visa, command, queries
):
    # Observed manufacturer punctuation; no real serial or VISA resource is stored.
    fake_visa.responses["*IDN?"] = "ITECH Ltd., IT8812C, SYNTHETIC, 1.35-1.41"
    output = execute(command, resource=RESOURCE)
    assert [c for c in fake_visa.calls if isinstance(c, str) and c.endswith("?")] == queries
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]
    assert "SYNTHETIC" not in str(output)
    if command == "identify":
        assert output["manufacturer"] == "ITECH Ltd."
        assert output["model"] == "IT8812C"


@pytest.mark.parametrize("kwargs,code", [
    ({}, "missing_resource"),
    ({"resource": RESOURCE, "backend": "@py"}, "unsupported_backend"),
    ({"resource": RESOURCE, "model_id": "other"}, "unsupported_model"),
    ({"resource": "TCPIP0::example.invalid::INSTR"}, "invalid_resource"),
    ({"resource": "ASRL1::INSTR"}, "invalid_resource"),
    ({"resource": "USB0::1::2::X::RAW"}, "invalid_resource"),
    ({"resource": "USB0::65536::2::X::INSTR"}, "invalid_resource"),
])
def test_rejected_scope_never_creates_a_manager(kwargs, code):
    with pytest.raises(LoadError, match=".") as exc:
        execute("measure", **kwargs)
    assert exc.value.code == code
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("command,queries,result", [
    ("identify", ["*IDN?"], {"model": "IT8812C"}),
    ("input-state", ["*IDN?", "INP:STAT?"], {"input_state": "ON"}),
    ("measure", ["*IDN?", "MEAS:VOLT?", "MEAS:CURR?", "FETC:POW?"],
     {"voltage_v": 12.0, "current_a": 0.5, "power_w": 6.0}),
])
def test_exact_readonly_sequence_and_cleanup(fake_visa, command, queries, result):
    output = execute(command, resource=RESOURCE)
    assert result.items() <= output.items()
    assert fake_visa.calls == [
        ("backend", "@ivi"), "manager", ("open", RESOURCE, 5000), "open_resource",
        ("timeout", 5000), ("read_termination", "\n"), ("write_termination", "\n"),
        ("query_delay", 0.020),
        "runtime_metadata", *queries, "session_close", "manager_close",
    ]
    assert output["visa"]["manufacturer"] == "FAKE VISA"


@pytest.mark.parametrize("reply,state", [("0\n", "OFF"), ("1", "ON"), ("ON", "UNKNOWN"),
                                          (TimeoutError("private details"), "UNKNOWN")])
def test_input_state_never_defaults_to_off(fake_visa, reply, state):
    fake_visa.responses["INP:STAT?"] = reply
    if state == "UNKNOWN":
        with pytest.raises(LoadError) as exc:
            execute("input-state", resource=RESOURCE)
        assert exc.value.exit_code == 3
        assert exc.value.details["input_state"] == "UNKNOWN"
    else:
        assert execute("input-state", resource=RESOURCE)["input_state"] == state
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]


@pytest.mark.parametrize("query", ["MEAS:CURR?", "FETC:POW?"])
@pytest.mark.parametrize("reply", ["", "not-a-number", "NaN", "inf", "1e999", "1_2", TimeoutError()])
def test_partial_measurement_is_failure_without_retry(fake_visa, query, reply):
    fake_visa.responses[query] = reply
    with pytest.raises(LoadError) as exc:
        execute("measure", resource=RESOURCE)
    assert exc.value.exit_code == 3
    assert fake_visa.calls.count(query) == 1
    if query == "MEAS:CURR?":
        assert "FETC:POW?" not in fake_visa.calls
    assert "MEAS:POW?" not in fake_visa.calls
    assert "MEAS:POW:DC?" not in fake_visa.calls
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]


@pytest.mark.parametrize("event,code,closes", [
    ("manager", "visa_manager_failed", []),
    ("open_resource", "visa_open_failed", ["manager_close"]),
    (("timeout", 5000), "visa_configure_failed", ["session_close", "manager_close"]),
    (("query_delay", 0.020), "visa_configure_failed", ["session_close", "manager_close"]),
    ("*IDN?", "query_failed", ["session_close", "manager_close"]),
    ("session_close", "cleanup_failed", ["session_close", "manager_close"]),
    ("manager_close", "cleanup_failed", ["session_close", "manager_close"]),
])
def test_visa_failures_are_reported_and_remaining_handles_closed(fake_visa, event, code, closes):
    fake_visa.failures[event] = OSError("PRIVATE-RESOURCE")
    with pytest.raises(LoadError) as exc:
        execute("identify", resource=RESOURCE)
    assert exc.value.code == code
    assert exc.value.exit_code == 3
    assert "PRIVATE-RESOURCE" not in str(exc.value.as_dict())
    assert [c for c in fake_visa.calls if c in ("session_close", "manager_close")] == closes
    assert fake_visa.calls.count(event) == 1


def test_primary_and_both_cleanup_errors_survive(fake_visa):
    fake_visa.responses["*IDN?"] = "OTHER,IT8812C,S,1.0"
    fake_visa.failures.update(session_close=OSError(), manager_close=OSError())
    with pytest.raises(LoadError) as exc:
        execute("identify", resource=RESOURCE)
    assert exc.value.code == "identity_mismatch"
    assert len(exc.value.details["cleanup_errors"]) == 2
    assert exc.value.exit_code == 3


def test_failed_manager_close_is_not_retried_at_destruction(fake_visa, monkeypatch):
    manager = object.__new__(pyvisa.highlevel.ResourceManager)
    manager.session = 1
    manager._created_resources = ()
    manager._atexit_handler = lambda: None
    calls = []

    def close(handle):
        calls.append(handle)
        raise OSError("PRIVATE-RESOURCE")

    manager.visalib = SimpleNamespace(library_path="FAKE-VISA.dll", close=close)
    manager.open_resource = lambda *args, **kwargs: fake_visa.session
    monkeypatch.setattr(pyvisa, "ResourceManager", lambda backend: manager)
    with pytest.raises(LoadError) as exc:
        execute("identify", resource=RESOURCE)
    assert exc.value.exit_code == 3
    assert exc.value.code == "cleanup_failed"
    manager.__del__()
    assert calls == [1]


@pytest.mark.parametrize("close_fails", [False, True])
def test_not_ready_visa_session_is_closed_without_clear_or_retry(monkeypatch, close_fails):
    calls = []

    def native_close(handle):
        calls.append(("close", handle))
        if close_fails:
            raise OSError("close failed")

    class Manager:
        session = 1
        visalib = SimpleNamespace(
            library_path="FAKE-VISA.dll",
            close=native_close,
        )

        def open_bare_resource(self, *args):
            calls.append("open")
            return 2, pyvisa.constants.StatusCode.success_device_not_present

        def open_resource(self, resource, *, open_timeout, resource_pyclass):
            session = resource_pyclass(self, resource)
            session.open(open_timeout=open_timeout)
            self.opened = session
            return session

        def close(self):
            self.opened.close()
            calls.append("manager_close")

    monkeypatch.setattr(pyvisa, "ResourceManager", lambda backend: Manager())
    with pytest.raises(LoadError) as exc:
        execute("identify", resource=RESOURCE)
    assert exc.value.code == "visa_open_failed"
    assert exc.value.exit_code == 3
    assert calls == ["open", ("close", 2), "manager_close"]
    assert bool(exc.value.details.get("cleanup_errors")) == close_fails


@pytest.mark.parametrize("command", ["identify", "input-state", "measure"])
def test_offline_modes_do_not_open_visa(command):
    plan = execute(command, mode="dry-run")
    assert plan["queries"][0] == "*IDN?"
    assert plan["live_access"] == "product"
    assert "voltage_v" not in plan and "input_state" not in plan
    assert execute(command, mode="simulate") == execute(command, mode="simulate")


@pytest.mark.parametrize("function,readings,expected", [
    ("CURRENT", {"CURR:RANG?": "0", "CURR?": "1.25e-1"}, {"range": "low", "current_a": 0.125}),
    ("VOLT", {"VOLT:RANG?": "18", "VOLT:RANG:AUTO?": "1", "VOLT?": "3.25"},
     {"range": "low", "range_auto": True, "voltage_v": 3.25}),
    ("VOLT", {"VOLT:RANG?": "25", "VOLT:RANG:AUTO?": "0", "VOLT?": "25"},
     {"range": None, "range_readback_v": 25, "voltage_v": 25}),
    ("RESISTANCE", {"RES:RANG?": "7500", "RES?": "50"}, {"range": "high", "resistance_ohm": 50}),
    ("POW", {"POW?": "2.5"}, {"range": None, "power_w": 2.5}),
])
def test_configuration_readback_fixed_queries_only_current_function(
        fake_visa, monkeypatch, function, readings, expected):
    writes = []
    monkeypatch.setattr(type(fake_visa.session), 'write', lambda _, command: writes.append(command), raising=False)
    fake_visa.responses.update({"FUNC:MODE?": "FIXED", "FUNC?": function, "TRAN?": "0",
                                "INP:SHOR:STAT?": "1", "RES:LED:STAT?": "0", **readings})
    result = execute("readback", resource=RESOURCE, hardware_validation="readback")
    assert result["active_mode"] == "fixed" and result["input_state"] == "ON"
    assert result["short_state"] == "ON" and result["state_source"] == "instrument_readback"
    assert expected.items() <= result["configuration"].items()
    assert result["validation_only"] is True
    assert {call for call in fake_visa.calls if isinstance(call, str) and "?" in call} == {
        "*IDN?", "FUNC:MODE?", "FUNC?", "TRAN?", "INP:STAT?", "INP:SHOR:STAT?", *readings,
        *({"RES:LED:STAT?"} if function == "RESISTANCE" else set()),
    }
    assert writes == []
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]
    fake_visa.failures["FUNC?"] = OSError("query link failed")
    with pytest.raises(LoadError) as failure:
        execute("readback", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "query_failed"
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]
    assert writes == []


@pytest.mark.parametrize("function,readings", [
    ("CURR", {"CURR:RANG?": "6", "CURR?": "60"}),
    ("RES", {"RES:RANG?": "7500", "RES?": "0"}),
])
def test_configuration_readback_fixed_rejects_values_outside_known_range(fake_visa, function, readings):
    fake_visa.responses.update({"FUNC:MODE?": "FIX", "FUNC?": function, "TRAN?": "0", **readings})
    with pytest.raises(LoadError) as failure:
        execute("fixed-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_response" and failure.value.exit_code == 3


@pytest.mark.parametrize("mode,enabled", [("FIX", "1"), ("LIST", "0"), ("LIST", "1")])
def test_configuration_readback_fixed_refuses_list_and_enabled_transient(fake_visa, mode, enabled):
    fake_visa.responses.update({"FUNC:MODE?": mode, "FUNC?": "CURR", "TRAN?": enabled})
    with pytest.raises(LoadError, match="not in a Fixed mode") as failure:
        execute("fixed-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "mode_not_fixed"
    assert "CURR?" not in fake_visa.calls and "INP:STAT?" not in fake_visa.calls


def test_configuration_readback_fixed_slew_uses_instrument_values(fake_visa):
    fake_visa.responses.update({"FUNC:MODE?": "FIX", "FUNC?": "CURR", "TRAN?": "0",
                                "CURR:RANG?": "60",
                                "CURR:SLEW:POS?": "0.1234567890123456789",
                                "CURR:SLEW:NEG?": "2"})
    result = execute("fixed-slew-status", resource=RESOURCE, hardware_validation="readback")
    assert result["rise_slew_a_per_us"] == "0.1234567890123456789"
    assert result["fall_slew_a_per_us"] == "2"
    assert "CURR?" not in fake_visa.calls

    fake_visa.responses["CURR:RANG?"] = "6"
    with pytest.raises(LoadError) as failure:
        execute("fixed-slew-status", resource=RESOURCE, hardware_validation="readback")
    assert failure.value.code == "invalid_response" and failure.value.exit_code == 3
    assert failure.value.details == {"query": "CURR:SLEW:NEG?", "range": "low", "observed": "2",
                                     "minimum": "0.0001", "maximum": "0.5"}


@pytest.mark.parametrize("selector,rate,accepted", [
    ("6", "0.5", True), ("60", "5", True),
    ("6", "0.50001", False), ("60", "5.00001", False),
])
def test_it8812c_slew_readback_uses_scpi_max_without_widening_writes(fake_visa, selector, rate, accepted):
    fake_visa.responses.update({
        "FUNC:MODE?": "FIX", "FUNC?": "CURR", "TRAN?": "0",
        "CURR:RANG?": selector, "CURR:SLEW:POS?": rate, "CURR:SLEW:NEG?": rate,
    })
    if accepted:
        result = execute("fixed-slew-status", resource=RESOURCE, hardware_validation="readback")
        assert result["rise_slew_a_per_us"] == rate
        assert result["fall_slew_a_per_us"] == rate
    else:
        with pytest.raises(LoadError, match="slew is outside"):
            execute("fixed-slew-status", resource=RESOURCE, hardware_validation="readback")
    from electronicload_tool_core.control import validate_fixed_cc_slew
    with pytest.raises(LoadError) as failure:
        validate_fixed_cc_slew("low" if selector == "6" else "high", rate, rate)
    assert failure.value.code == "slew_out_of_range"
