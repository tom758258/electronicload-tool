"""CLI output and exit contracts, including the real Core-to-fake-VISA path."""

import json

import pytest

from electronicload_tool_cli.cli import main

RESOURCE = "USB0::0xFFFF::0x0001::SYNTHETIC::INSTR"


@pytest.mark.parametrize("command", ["identify", "input-state", "measure"])
@pytest.mark.parametrize("mode", ["simulate", "dry-run"])
def test_offline_json_contract(capsys, command, mode):
    assert main([command, f"--{mode}", "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result.keys() == {"ok", "command", "mode", "model_id", "result"}
    assert result["ok"] is True
    assert result["command"] == command and result["mode"] == mode
    assert captured.err == ""


@pytest.mark.parametrize("args,code", [
    (["measure"], "missing_resource"),
    (["input-state", "--resource", RESOURCE], "unsupported_live_scope"),
    (["measure", "--resource", RESOURCE], "unsupported_live_scope"),
    (["measure", "--dry-run", "--simulate"], "argument_error"),
    (["identify", "--live"], "argument_error"),
    (["identify", "--resource"], "argument_error"),
    (["identify", "--help"], "argument_error"),
    (["measure", "--simulate", "--model", "other"], "unsupported_model"),
])
def test_refusals_are_one_json_object(capsys, args, code):
    assert main([*args, "--json"]) == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["ok"] is False
    assert result["error"]["code"] == code
    assert captured.err == ""
    if not any(flag in args for flag in ("--simulate", "--dry-run")):
        assert result["mode"] == "live"


def test_diagnostic_and_validation_policy_flow(fake_visa, capsys):
    assert main(["identify", "--resource", RESOURCE, "--json"]) == 0
    assert "PRIVATE-SERIAL" not in capsys.readouterr().out
    assert main(["measure", "--resource", RESOURCE,
                 "--validation-allow-pending-live-support", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["power_w"] == 6.0


def test_runtime_error_json(fake_visa, capsys):
    fake_visa.responses["INP:STAT?"] = TimeoutError("PRIVATE-RESOURCE")
    assert main(["input-state", "--resource", RESOURCE,
                 "--validation-allow-pending-live-support", "--json"]) == 3
    output = capsys.readouterr()
    error = json.loads(output.out)["error"]
    assert error["details"]["input_state"] == "UNKNOWN"
    assert "PRIVATE-RESOURCE" not in output.out
    assert output.err == ""


def test_text_units_and_error_channel(capsys):
    assert main(["measure", "--simulate"]) == 0
    output = capsys.readouterr()
    assert "12 V" in output.out and "0.5 A" in output.out and "6 W" in output.out
    assert output.err == ""
    assert main(["measure"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "missing_resource" in output.err
