"""D11 discovery scope, errors and cleanup; fake VISA only."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import pyvisa

from electronicload_tool_core.discovery import discover_resources
from electronicload_tool_core.errors import LoadError

USB_GOOD = "USB0::0xFFFF::0x0001::KNOWN::INSTR"
USB_OTHER = "USB0::0xFFFF::0x0002::OTHER::INSTR"
USB_BAD = "USB1::0xFFFF::0x0003::BAD::INSTR"
TCP = "TCPIP0::192.0.2.1::INSTR"
ASRL = "ASRL1::INSTR"
USB_RAW = "USB0::0xFFFF::0x0004::RAW::RAW"


@pytest.fixture
def manager(monkeypatch):
    events = []
    resources = [USB_GOOD, TCP, ASRL, USB_OTHER, USB_BAD, USB_RAW]
    issues = {}

    class Manager:
        def list_resources(self, pattern):
            events.append(("list", pattern))
            if "list" in issues:
                raise issues["list"]
            return tuple(resources)

        def close(self):
            events.append("manager_close")
            if "close" in issues:
                raise issues["close"]

    def factory(backend):
        events.append(("backend", backend))
        if "manager" in issues:
            raise issues["manager"]
        return Manager()

    monkeypatch.setattr(pyvisa, "ResourceManager", factory)
    return SimpleNamespace(events=events, resources=resources, issues=issues)


@pytest.fixture
def probe(monkeypatch, manager):
    calls = []
    replies = {
        USB_GOOD: "ITECH Ltd.,IT8812C,PRIVATE-SERIAL,1.0",
        USB_OTHER: "OTHER INC,MODEL2,PRIVATE-SERIAL,1.0",
        USB_BAD: "invalid",
    }
    issues = {}

    @contextmanager
    def open_session(resource):
        calls.append(("open", resource))
        if resource in issues:
            raise issues[resource]

        class Session:
            def query(self, command):
                calls.append(("query", command, resource))
                answer = replies[resource]
                if isinstance(answer, Exception):
                    raise answer
                return answer

        try:
            yield Session(), {"manufacturer": "FAKE"}
        finally:
            calls.append(("close", resource))

    monkeypatch.setattr("electronicload_tool_core.discovery.open_session", open_session)
    return SimpleNamespace(calls=calls, replies=replies, issues=issues)


def test_all_resources_do_not_open_or_query(manager, probe):
    result = discover_resources()
    assert result["backend"] == "system"
    assert result["count"] == len(manager.resources)
    assert [row["resource"] for row in result["resources"]] == manager.resources
    assert manager.events == [("backend", "@ivi"), ("list", "?*"), "manager_close"]
    assert probe.calls == []


def test_empty_discovery(manager, probe):
    manager.resources.clear()
    for live_only in (False, True):
        result = discover_resources(live_only=live_only)
        assert result["resources"] == [] and result["count"] == 0
    assert probe.calls == []


def test_live_only_selects_usb_and_any_valid_idn(manager, probe):
    result = discover_resources(live_only=True)
    assert result["count"] == 2
    assert result["resources"][0]["model_id"] == "itech-it8812c"
    assert result["resources"][1]["model_id"] is None
    assert "PRIVATE-SERIAL" in result["resources"][1]["idn"]  # local-only output
    assert [call[1] for call in probe.calls if call[0] == "open"] == [
        USB_GOOD, USB_OTHER, USB_BAD,
    ]
    assert len([call for call in probe.calls if call[0] == "close"]) == 3
    assert [call[1] for call in probe.calls if call[0] == "query"] == ["*IDN?"] * 3


def test_probe_failures_do_not_block_other_resources(manager, probe):
    probe.issues[USB_GOOD] = LoadError("visa_open_failed", "synthetic", 3)
    probe.replies[USB_BAD] = RuntimeError("private resource")
    result = discover_resources(live_only=True)
    assert [row["resource"] for row in result["resources"]] == [USB_OTHER]
    assert ("open", USB_BAD) in probe.calls


def test_closed_session_must_not_be_presented_as_live(manager, probe, monkeypatch):
    @contextmanager
    def unsafe_session(resource):
        yield SimpleNamespace(query=lambda _: "ITECH Ltd,IT8812C,S,1"), None
        raise LoadError("cleanup_failed", "synthetic", 3)

    monkeypatch.setattr("electronicload_tool_core.discovery.open_session", unsafe_session)
    assert discover_resources(live_only=True)["resources"] == []


@pytest.mark.parametrize("issue,code", [
    ("manager", "visa_manager_failed"),
    ("list", "visa_list_failed"),
    ("close", "cleanup_failed"),
])
def test_manager_errors_are_explicit_and_redacted(manager, issue, code):
    manager.issues[issue] = RuntimeError("PRIVATE-RESOURCE")
    with pytest.raises(LoadError) as exc:
        discover_resources()
    assert exc.value.code == code and exc.value.exit_code == 3
    assert "PRIVATE-RESOURCE" not in str(exc.value.as_dict())


def test_list_failure_retains_cleanup_failure(manager):
    manager.issues.update(list=OSError(), close=OSError())
    with pytest.raises(LoadError) as exc:
        discover_resources()
    assert exc.value.code == "visa_list_failed"
    assert exc.value.details["cleanup_errors"][0]["code"] == "cleanup_failed"
