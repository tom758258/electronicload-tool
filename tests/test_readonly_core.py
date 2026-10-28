"""Core policy, query safety, and communication lifetime without hardware."""

import pytest
import pyvisa
from types import SimpleNamespace

from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.identity import MODEL_ID, parse_identity
from electronicload_tool_core.readonly import execute

RESOURCE = "USB0::0xFFFF::0x0001::SYNTHETIC::INSTR"


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
    "ITECH Ltd,IT8812C-extra,S,1.0", "ITECH Ltd,IT8812,S,1.0",
])
def test_identity_refusal_stops_queries_and_closes(fake_visa, reply):
    fake_visa.responses["*IDN?"] = reply
    with pytest.raises(LoadError) as exc:
        execute("measure", resource=RESOURCE, validation=True)
    assert exc.value.exit_code == 2
    assert [c for c in fake_visa.calls if isinstance(c, str) and c.endswith("?")] == ["*IDN?"]
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]


@pytest.mark.parametrize("kwargs,code", [
    ({}, "missing_resource"),
    ({"resource": RESOURCE}, "unsupported_live_scope"),
    ({"resource": RESOURCE, "validation": True, "backend": "@py"}, "unsupported_backend"),
    ({"resource": RESOURCE, "validation": True, "model_id": "other"}, "unsupported_model"),
    ({"resource": "TCPIP0::example.invalid::INSTR", "validation": True}, "invalid_resource"),
    ({"resource": "ASRL1::INSTR", "validation": True}, "invalid_resource"),
    ({"resource": "USB0::1::2::X::RAW", "validation": True}, "invalid_resource"),
    ({"resource": "USB0::65536::2::X::INSTR", "validation": True}, "invalid_resource"),
])
def test_rejected_scope_never_creates_a_manager(kwargs, code):
    with pytest.raises(LoadError, match=".") as exc:
        execute("measure", **kwargs)
    assert exc.value.code == code
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("command,queries,result", [
    ("identify", ["*IDN?"], {"model": "IT8812C"}),
    ("input-state", ["*IDN?", "INP:STAT?"], {"input_state": "ON"}),
    ("measure", ["*IDN?", "MEAS:VOLT?", "MEAS:CURR?", "MEAS:POW?"],
     {"voltage_v": 12.0, "current_a": 0.5, "power_w": 6.0}),
])
def test_exact_readonly_sequence_and_cleanup(fake_visa, command, queries, result):
    output = execute(command, resource=RESOURCE, validation=command != "identify")
    assert result.items() <= output.items()
    assert fake_visa.calls == [
        ("backend", "@ivi"), "manager", ("open", RESOURCE, 5000), "open_resource",
        ("timeout", 5000), ("read_termination", "\n"), ("write_termination", "\n"),
        "runtime_metadata", *queries, "session_close", "manager_close",
    ]
    assert output["visa"]["manufacturer"] == "FAKE VISA"


@pytest.mark.parametrize("reply,state", [("0\n", "OFF"), ("1", "ON"), ("ON", "UNKNOWN"),
                                          (TimeoutError("private details"), "UNKNOWN")])
def test_input_state_never_defaults_to_off(fake_visa, reply, state):
    fake_visa.responses["INP:STAT?"] = reply
    if state == "UNKNOWN":
        with pytest.raises(LoadError) as exc:
            execute("input-state", resource=RESOURCE, validation=True)
        assert exc.value.exit_code == 3
        assert exc.value.details["input_state"] == "UNKNOWN"
    else:
        assert execute("input-state", resource=RESOURCE, validation=True)["input_state"] == state
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]


@pytest.mark.parametrize("reply", ["", "not-a-number", "NaN", "inf", "1e999", "1_2", TimeoutError()])
def test_partial_measurement_is_failure_without_retry(fake_visa, reply):
    fake_visa.responses["MEAS:CURR?"] = reply
    with pytest.raises(LoadError) as exc:
        execute("measure", resource=RESOURCE, validation=True)
    assert exc.value.exit_code == 3
    assert fake_visa.calls.count("MEAS:CURR?") == 1
    assert "MEAS:POW?" not in fake_visa.calls
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]


@pytest.mark.parametrize("event,code,closes", [
    ("manager", "visa_manager_failed", []),
    ("open_resource", "visa_open_failed", ["manager_close"]),
    (("timeout", 5000), "visa_configure_failed", ["session_close", "manager_close"]),
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
    assert "voltage_v" not in plan and "input_state" not in plan
    assert execute(command, mode="simulate") == execute(command, mode="simulate")
