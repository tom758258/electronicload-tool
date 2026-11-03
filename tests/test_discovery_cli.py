"""D11 CLI discovery adapter shape and errors; no real VISA access."""

import json

import pytest

from electronicload_tool_cli import cli
from electronicload_tool_core.errors import LoadError


def test_list_resources_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_resources", lambda **kw: {
        "backend": "system", "live_only": False, "count": 2,
        "resources": [{"resource": "USB0::...::INSTR"}, {"resource": "ASRL1::INSTR"}],
    })
    assert cli.main(["list-resources", "--json"]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["ok"] is True
    assert payload["command"] == "list-resources" and payload["mode"] == "live"
    assert payload["result"]["count"] == 2
    assert "model_id" not in payload and output.err == ""


def test_live_resources_json(monkeypatch, capsys):
    def discover(**kwargs):
        assert kwargs == {"live_only": True}
        return {
            "backend": "system", "live_only": True, "count": 1,
            "resources": [{
                "resource": "USB0::...::INSTR", "idn": "OTHER,M,SECRET,1", "model_id": None,
            }],
        }

    monkeypatch.setattr(cli, "discover_resources", discover)
    assert cli.main(["list-resources", "--live-only", "--json"]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["result"]["resources"][0]["model_id"] is None and output.err == ""


def test_discovery_text_empty(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_resources", lambda **kw: {
        "backend": "system", "live_only": kw["live_only"], "count": 0, "resources": [],
    })
    assert cli.main(["list-resources"]) == 0
    assert "<none>" in capsys.readouterr().out
    assert cli.main(["list-resources", "--live-only"]) == 0
    assert "No live resources found" in capsys.readouterr().out


def test_discovery_manager_failure_json(monkeypatch, capsys):
    def fail(**kwargs):
        raise LoadError("visa_list_failed", "Cannot enumerate VISA resources.", 3)

    monkeypatch.setattr(cli, "discover_resources", fail)
    assert cli.main(["list-resources", "--json"]) == 3
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["error"]["code"] == "visa_list_failed" and output.err == ""


@pytest.mark.parametrize("args", [
    ["--dry-run"], ["--simulate"], ["--resource", "ABC"], ["--backend", "@py"],
])
def test_discovery_rejects_unapproved_flags(capsys, args):
    assert cli.main(["list-resources", *args, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "argument_error"


def test_discovery_help_has_no_visa(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["list-resources", "--help"])
    assert exc.value.code == 0 and "--live-only" in capsys.readouterr().out
