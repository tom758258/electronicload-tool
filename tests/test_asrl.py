"""Read-only ASRL validation through PyVISA's serial resource and a fake IVI library."""

from contextlib import nullcontext

import pytest
from pyvisa import constants, errors, highlevel

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
            self.status = SUCCESS
            self.attributes = {}
            self.replies = {
                "*IDN?": "ITECH Ltd,IT8812C,PRIVATE-SERIAL,1.2",
                "INP:STAT?": "0", "MEAS:VOLT?": "12", "MEAS:CURR?": "0.5", "FETC:POW?": "6",
            }

        def event(self, *event):
            self.events.append(event)
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
    assert asrl_library.events[-3:] == [("query", "*IDN?", 1000), ("close", 11), ("close", 10)]
    if termination is not None:
        assert asrl_library.last_message == termination


@pytest.mark.parametrize("command,queries,expected", [
    ("input-state", ["INP:STAT?"], {"input_state": "OFF"}),
    ("measure", ["MEAS:VOLT?", "MEAS:CURR?", "FETC:POW?"],
     {"voltage_v": 12.0, "current_a": 0.5, "power_w": 6.0}),
])
def test_asrl_only_uses_the_normal_timeout_after_identity(asrl_library, command, queries, expected):
    result = execute(command, **ASRL_SCOPE)
    assert expected.items() <= result.items()
    assert [event for event in asrl_library.events if event[0] == "query"] == [
        ("query", "*IDN?", 1000), *(("query", query, 5000) for query in queries),
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
