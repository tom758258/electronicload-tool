"""Contributor ASRL validation through PyVISA and a fake IVI library."""

import json

from contextlib import nullcontext

import pytest
from pyvisa import constants, errors, highlevel

from electronicload_tool_cli.cli import main
from electronicload_tool_core import visa
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.readonly import execute
from electronicload_tool_core.visa import SerialOptions

ASRL_SCOPE = {"resource": "ASRL999::INSTR", "hardware_validation": "asrl-readonly"}
USB = "USB0::0xFFFF::0x0001::SYNTHETIC::INSTR"
SUCCESS = constants.StatusCode.success
ATTR = constants.ResourceAttribute


@pytest.fixture
def asrl_library(monkeypatch):
    class Library:
        library_path = "FAKE-IVI.dll"
        resource_manager = object()  # Existing PyVISA manager belongs to another transport.
        ignore_warning = staticmethod(lambda *args: nullcontext())

        def __init__(self):
            self.events = []
            self.failures = {}
            self.elapsed_ns = 0
            self.latency_ms = {}
            self.status = SUCCESS
            self.attributes = {}
            self.idn_fragments = []
            self.replies = {
                "*IDN?": "ITECH Ltd,IT8812C,PRIVATE-SERIAL,1.2",
                "INP:STAT?": "0", "MEAS:VOLT?": "12", "MEAS:CURR?": "0.5", "FETC:POW?": "6",
            }

        def event(self, *event):
            self.events.append(event)
            self.elapsed_ns += self.latency_ms.get(event[0], 0) * 1_000_000
            if event in self.failures:
                raise self.failures[event]

        def open_default_resource_manager(self):
            self.event("manager")
            return 10, SUCCESS

        def open(self, manager, resource, access_mode, timeout):
            assert manager == 10 and access_mode == constants.AccessModes.no_lock
            self.event("open", resource, timeout)
            return 11, self.status

        def set_attribute(self, session, attribute, value):
            assert session == 11
            self.event("attribute", attribute, value)
            self.attributes[attribute] = value
            return SUCCESS

        def get_attribute(self, session, attribute):
            assert session == 11 and attribute == ATTR.resource_manufacturer_name
            return "FAKE VISA", SUCCESS

        def write(self, session, message):
            assert session == 11
            self.last_query = message.decode().strip()
            self.event("query", self.last_query, self.attributes[ATTR.timeout_value])
            self.last_message = message
            return len(message), SUCCESS

        def read(self, session, count):
            assert session == 11
            self.event("read", self.last_query, self.attributes[ATTR.timeout_value])
            if self.last_query == "*IDN?" and self.idn_fragments:
                return self.idn_fragments.pop(0), constants.StatusCode.success_max_count_read
            reply = self.replies[self.last_query]
            if isinstance(reply, Exception):
                raise reply
            return (reply + "\r\n").encode(), SUCCESS

        def close(self, handle):
            self.event("close", handle)
            return SUCCESS

    library = Library()

    def open_library(backend):
        library.event("backend", backend)
        return library

    monkeypatch.setattr(highlevel, "open_visa_library", open_library)
    monkeypatch.setattr(visa, "monotonic_ns", lambda: library.elapsed_ns)
    return library


@pytest.mark.parametrize("options,attributes,termination", [
    (None, {}, None),
    (SerialOptions(baud_rate=19200), {ATTR.asrl_baud_rate: 19200}, None),
    (SerialOptions(19200, 7, "even", "2", "none", "CRLF", "CR"), {
        ATTR.asrl_baud_rate: 19200, ATTR.asrl_data_bits: 7, ATTR.asrl_parity: constants.Parity.even,
        ATTR.asrl_stop_bits: constants.StopBits.two, ATTR.asrl_flow_control: constants.ControlFlow.none,
        ATTR.termchar: 10, ATTR.termchar_enabled: True,
    }, b"*IDN?\r"),
])
def test_asrl_options_override_only_explicit_settings(asrl_library, options, attributes, termination):
    result = execute("identify", **ASRL_SCOPE, serial_options=options)
    assert result["model"] == "IT8812C" and "PRIVATE-SERIAL" not in str(result)
    assert result["transport"] == "asrl" and result["validation_scope"] == "asrl-readonly"
    assert asrl_library.attributes == {**attributes, ATTR.timeout_value: 1000}
    assert asrl_library.events[:3] == [("backend", "@ivi"), ("manager",),
                                       ("open", ASRL_SCOPE["resource"], 1000)]
    assert [event for event in asrl_library.events if event[0] in {"query", "read", "close"}] == [
        ("query", "*IDN?", 1000), ("read", "*IDN?", 1000), ("close", 11), ("close", 10),
    ]
    if termination is not None:
        assert asrl_library.last_message == termination


@pytest.mark.parametrize("command,queries,expected", [
    ("input-state", ["INP:STAT?"], {"input_state": "OFF"}),
    ("measure", ["MEAS:VOLT?", "MEAS:CURR?", "FETC:POW?"],
     {"voltage_v": 12.0, "current_a": 0.5, "power_w": 6.0}),
])
def test_asrl_only_uses_the_normal_timeout_after_identity(asrl_library, command, queries, expected):
    asrl_library.latency_ms.update(open=900, query=400, read=550)
    result = execute(command, **ASRL_SCOPE)
    assert expected.items() <= result.items()
    assert [event for event in asrl_library.events if event[0] == "query"] == [
        ("query", "*IDN?", 1000), *(("query", query, 5000) for query in queries),
    ]
    assert [event for event in asrl_library.events if event[0] == "read"] == [
        ("read", "*IDN?", 600), *(("read", query, 5000) for query in queries),
    ]
    assert asrl_library.events[-2:] == [("close", 11), ("close", 10)]


@pytest.mark.parametrize("stage,code,closes", [
    ("manager", "visa_manager_failed", []),
    ("open", "visa_open_failed", [10]),
    ("not-ready", "visa_open_failed", [11, 10]),
    ("configure", "visa_configure_failed", [11, 10]),
    ("identity-timeout", "query_failed", [11, 10]),
    ("wrong-identity", "identity_mismatch", [11, 10]),
    ("query", "query_failed", [11, 10]),
])
def test_asrl_failures_stop_without_retry(asrl_library, stage, code, closes):
    timeout = errors.VisaIOError(constants.StatusCode.error_timeout)
    if stage == "not-ready":
        asrl_library.status = constants.StatusCode.success_device_not_present
    elif stage == "wrong-identity":
        asrl_library.replies["*IDN?"] = "ITECH Ltd,IT8812B,PRIVATE-SERIAL,1.2"
    elif stage == "identity-timeout":
        asrl_library.replies["*IDN?"] = timeout
    elif stage == "query":
        asrl_library.replies["MEAS:VOLT?"] = OSError("PRIVATE-PORT")
    else:
        event = {"manager": ("manager",), "open": ("open", ASRL_SCOPE["resource"], 1000),
                 "configure": ("attribute", ATTR.timeout_value, 1000)}[stage]
        asrl_library.failures[event] = timeout
    with pytest.raises(LoadError) as failure:
        execute("measure", **ASRL_SCOPE)
    assert failure.value.code == code and "PRIVATE" not in str(failure.value.as_dict())
    if stage in {"open", "identity-timeout"}:
        assert failure.value.details["visa_status"] == constants.StatusCode.error_timeout
    expected_queries = ["*IDN?", "MEAS:VOLT?"] if stage == "query" else (
        ["*IDN?"] if stage in {"identity-timeout", "wrong-identity"} else [])
    assert [event[1] for event in asrl_library.events if event[0] == "query"] == expected_queries
    assert [event[1] for event in asrl_library.events if event[0] == "close"] == closes
    assert sum(event[0] == "open" for event in asrl_library.events) == (stage != "manager")


@pytest.mark.parametrize("latency_ms,fragment,code,queries,read_timeouts", [
    ({"open": 1150}, b"", "visa_open_failed", [], []),
    ({"query": 1150}, b"", "query_failed", ["*IDN?"], []),
    ({"query": 570, "read": 570}, b"", "query_failed", ["*IDN?"], [430]),
    ({"query": 200, "read": 450}, b"ITECH Ltd,", "query_failed", ["*IDN?"], [800, 350]),
])
def test_asrl_rejects_late_native_success(asrl_library, latency_ms, fragment, code, queries, read_timeouts):
    asrl_library.latency_ms.update(latency_ms)
    if fragment:
        asrl_library.idn_fragments.append(fragment)
        asrl_library.replies["*IDN?"] = "IT8812C,PRIVATE-SERIAL,1.2"
    asrl_library.failures[("close", 11)] = OSError("PRIVATE-PORT")
    with pytest.raises(LoadError) as failure:
        execute("measure", **ASRL_SCOPE)
    assert failure.value.code == code and failure.value.exit_code == 3
    assert failure.value.details["visa_status"] == constants.StatusCode.error_timeout
    assert failure.value.details["cleanup_errors"][0]["code"] == "cleanup_failed"
    assert "PRIVATE" not in str(failure.value.as_dict())
    assert [event[1] for event in asrl_library.events if event[0] == "query"] == queries
    assert [event[2] for event in asrl_library.events if event[0] == "read"] == read_timeouts
    assert asrl_library.events[-2:] == [("close", 11), ("close", 10)]


@pytest.mark.parametrize("close_failures,primary", [([11], False), ([10], False), ([11, 10], True)])
def test_asrl_native_cleanup_retains_failures_and_usb_can_open(asrl_library, fake_visa, close_failures, primary):
    usb_manager = asrl_library.resource_manager
    for handle in close_failures:
        asrl_library.failures[("close", handle)] = OSError("PRIVATE-PORT")
    if primary:
        asrl_library.replies["INP:STAT?"] = TimeoutError("PRIVATE-PORT")
    with pytest.raises(LoadError) as failure:
        execute("input-state", **ASRL_SCOPE, serial_options=SerialOptions(baud_rate=19200))
    assert failure.value.code == ("query_failed" if primary else "cleanup_failed")
    assert failure.value.details["input_state"] == "UNKNOWN"
    assert len(failure.value.details["cleanup_errors"]) == len(close_failures)
    assert "PRIVATE" not in str(failure.value.as_dict())
    assert asrl_library.events[-2:] == [("close", 11), ("close", 10)]
    assert asrl_library.resource_manager is usb_manager
    assert execute("identify", resource=USB)["model"] == "IT8812C"
    assert fake_visa.calls == [
        ("backend", "@ivi"), "manager", ("open", USB, 5000), "open_resource",
        ("timeout", 5000), ("read_termination", "\n"), ("write_termination", "\n"),
        ("query_delay", 0.15), "runtime_metadata", "*IDN?", "session_close", "manager_close",
    ]


@pytest.mark.parametrize("command,extra", [
    ("readback", {}), ("scpi-version", {}), ("measure-min-max", {}),
    ("measure", {"resource": None}), ("measure", {"resource": USB}),
    ("measure", {"model_id": "another-model"}), ("measure", {"backend": "@py"}),
    ("measure", {"mode": "simulate"}), ("measure", {"hardware_validation": None}),
    ("measure", {"resource": USB, "hardware_validation": None, "serial_options": SerialOptions(baud_rate=1)}),
    ("measure", {"serial_options": SerialOptions(data_bits=0)}),
    ("measure", {"serial_options": SerialOptions(baud_rate=1 << 32), "mode": "dry-run"}),
])
def test_asrl_scope_refuses_before_visa(command, extra):
    with pytest.raises(LoadError) as failure:
        execute(command, **(ASRL_SCOPE | extra))
    assert failure.value.exit_code == 2


@pytest.mark.parametrize("command,arguments,replies,traffic", [
    ("input-off", [], {}, ["SYST:REM", "INP OFF", "INP:STAT?"]),
    ("input-off", ["--hardware-validation", "cv"], {}, ["*IDN?", "SYST:REM", "INP OFF", "INP:STAT?"]),
    ("scpi-error-next", ["--hardware-validation", "system-query"],
     {"SYST:ERR?": '-120,"entry"'}, ["SYST:ERR?"]),
    ("short-status", ["--hardware-validation", "readback"],
     {"INP:SHOR:STAT?": "0"}, ["INP:SHOR:STAT?"]),
])
def test_asrl_contributor_reuses_command_after_timed_identity(
    asrl_library, capsys, command, arguments, replies, traffic,
):
    asrl_library.replies.update(replies)
    assert main([command, "--resource", ASRL_SCOPE["resource"], "--validation-connection", "asrl",
                 "--serial-baud-rate", "19200", "--json", *arguments]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["transport"] == "asrl" and result["validation_only"] is True
    assert [event[1:] for event in asrl_library.events if event[0] == "query"] == [
        ("*IDN?", 1000), *((token, 5000) for token in traffic),
    ]
    assert asrl_library.attributes[ATTR.asrl_baud_rate] == 19200
    assert asrl_library.events[-2:] == [("close", 11), ("close", 10)]


def test_asrl_contributor_refuses_late_identity_before_control_writes(asrl_library, capsys):
    asrl_library.latency_ms.update(query=570, read=570)
    assert main(["input-off", "--resource", ASRL_SCOPE["resource"], "--validation-connection", "asrl",
                 "--json"]) == 3
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "query_failed"
    assert [event[1] for event in asrl_library.events if event[0] == "query"] == ["*IDN?"]
    assert asrl_library.events[-2:] == [("close", 11), ("close", 10)]


@pytest.mark.parametrize("command,args,code", [
    ("input-off", [], "invalid_resource"),
    ("input-on", ["--validation-connection", "asrl"], "confirmation_required"),
    ("set-cv", ["--voltage", "3", "--range", "low", "--validation-connection", "asrl"],
     "unsupported_live_scope"),
    ("trace-status", ["--validation-connection", "asrl"], "unsupported_live_scope"),
    ("measure-log", ["--interval-s", "1", "--count", "1", "--validation-connection", "asrl"],
     "argument_error"),
])
def test_asrl_contributor_keeps_product_and_workflow_refusals(asrl_library, capsys, command, args, code):
    assert main([command, "--resource", ASRL_SCOPE["resource"], "--json", *args]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == code
    assert asrl_library.events == []
