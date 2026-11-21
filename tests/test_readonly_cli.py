"""CLI output and exit contracts, including the real Core-to-fake-VISA path."""

import json

import pytest

from electronicload_tool_cli.cli import main

RESOURCE = "USB0::0xFFFF::0x0001::SYNTHETIC::INSTR"


@pytest.mark.parametrize("command,arguments,expected", [
    ("scpi-version", [], {"scpi_version": "1999.0"}),
    ("display-mode", [], {"display_mode": "NORMAL"}),
    ("scpi-error-next", [], {"error_code": 0, "message": "No error"}),
    ("scpi-error-next", ["--all"], {"errors": [], "read_count": 1, "max_reads": 20,
                                    "empty_confirmed": True, "limit_reached": False}),
])
def test_system_query_cli_simulation_and_live_refusal(capsys, fake_visa, command, arguments, expected):
    assert main([command, "--simulate", "--json", *arguments]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["result"] == expected
    for flags in ([], ["--validation-allow-pending-live-support"]):
        assert main([command, "--resource", RESOURCE, "--json", *arguments, *flags]) == 2
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported_live_scope"
    assert fake_visa.calls == []


def test_system_query_cli_error_code_is_data_and_malformed_reply_fails(fake_visa, capsys):
    args = ["scpi-error-next", "--resource", RESOURCE, "--hardware-validation", "system-query", "--json"]
    fake_visa.responses["SYST:ERR?"] = '-222,"Data, out of range"'
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["result"]["error_code"] == -222
    fake_visa.responses["SYST:ERR?"] = "bad response"
    assert main(args) == 3
    failure = json.loads(capsys.readouterr().out)
    assert failure["ok"] is False and failure["error"]["code"] == "invalid_response"


def test_system_query_cli_all_preview_and_argument_boundaries(fake_visa, capsys):
    assert main(["scpi-error-next", "--all", "--dry-run", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)["result"]
    assert plan["queries"] == ["*IDN?", "SYST:ERR?"] and plan["max_reads"] == 20
    for command, options in (("scpi-version", ["--all"]), ("display-mode", ["--all"]),
                             ("scpi-error-next", ["--all=true"]),
                             ("identify", ["--hardware-validation", "system-query"])):
        assert main([command, "--dry-run", "--json", *options]) == 2
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "argument_error"
    with pytest.raises(SystemExit) as help_exit:
        main(["scpi-error-next", "--help"])
    assert help_exit.value.code == 0
    output = capsys.readouterr().out
    assert "--all" in output and "remove" in output.lower()
    assert fake_visa.calls == []


@pytest.mark.parametrize("command,metric,fields", [
    ("measure-min-max", None, {"voltage_min_v", "voltage_max_v", "current_min_a", "current_max_a"}),
    ("measure-min-max", "voltage-min", {"voltage_min_v"}),
    ("measure-min-max", "voltage-max", {"voltage_max_v"}),
    ("measure-min-max", "current-min", {"current_min_a"}),
    ("measure-min-max", "current-max", {"current_max_a"}),
    ("measure-discharge", None, {"capacity_ah", "discharge_time_s"}),
    ("measure-discharge", "capacity", {"capacity_ah"}),
    ("measure-discharge", "time", {"discharge_time_s"}),
])
@pytest.mark.parametrize("mode", ["simulate", "dry-run"])
def test_native_measurement_cli_selection(capsys, command, metric, fields, mode):
    args = ["--metric", metric] if metric is not None else []
    assert main([command, f"--{mode}", "--json", *args]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["ok"] and payload["command"] == command and payload["mode"] == mode
    assert output.err == ""
    if mode == "simulate":
        assert payload["result"].keys() == fields
        assert all(isinstance(value, float) for value in payload["result"].values())
    else:
        assert len(payload["result"]["queries"]) == len(fields) + 1
        assert payload["result"]["live_access"] == "blocked"


@pytest.mark.parametrize("command", ["measure-min-max", "measure-discharge"])
@pytest.mark.parametrize("args", [["--metric", "unknown"], ["--metric", ""], ["--metric"]])
def test_native_measurement_cli_invalid_metric(fake_visa, capsys, command, args):
    assert main([command, "--resource", RESOURCE, "--json", *args]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "argument_error"
    assert fake_visa.calls == []


@pytest.mark.parametrize("command", ["measure-min-max", "measure-discharge", "set-average", "average-status"])
@pytest.mark.parametrize("override", [[], ["--validation-allow-pending-live-support"]])
def test_measurement_cli_live_blocked_before_visa(fake_visa, capsys, command, override):
    args = ["--count", "8"] if command == "set-average" else []
    assert main([command, "--resource", RESOURCE, "--json", *args, *override]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported_live_scope"
    assert fake_visa.calls == []


@pytest.mark.parametrize("command", ["measure-min-max", "measure-discharge", "set-average", "average-status"])
def test_measurement_cli_rejects_hardware_validation(fake_visa, capsys, command):
    args = ["--count", "8"] if command == "set-average" else []
    assert main([command, "--resource", RESOURCE, "--hardware-validation", "input-timer",
                 "--json", *args]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "argument_error"
    assert fake_visa.calls == []


def test_native_measurement_cli_scpi_failure(fake_visa, capsys, monkeypatch):
    from electronicload_tool_core import readonly
    monkeypatch.setattr(readonly, "Simulator", lambda: fake_visa.session)
    fake_visa.responses["MEAS:CAP?"] = TimeoutError("PRIVATE-RESOURCE")
    assert main(["measure-discharge", "--simulate", "--json"]) == 3
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["ok"] is False and "result" not in payload
    assert payload["error"]["code"] == "query_failed"
    assert payload["error"]["details"]["query"] == "MEAS:CAP?"
    assert fake_visa.calls == ["*IDN?", "MEAS:CAP?"]
    assert output.err == "" and "PRIVATE-RESOURCE" not in output.out


def test_measurement_manifest(capsys):
    assert main(["manifest", "--json"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["schema_version"] == 2
    assert manifest["worker_protocol"]["schema_versions"] == [3]
    for command in ("measure-min-max", "measure-discharge", "set-average", "average-status"):
        assert manifest["commands"][command] == {"kind": "async_job"}


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
    (["measure", "--resource", RESOURCE, "--backend", "@py"], "unsupported_backend"),
    (["input-state", "--resource", RESOURCE, "--model", "other"], "unsupported_model"),
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


def test_product_live_readonly_flow(fake_visa, capsys):
    assert main(["identify", "--resource", RESOURCE, "--json"]) == 0
    assert "PRIVATE-SERIAL" not in capsys.readouterr().out
    assert main(["input-state", "--resource", RESOURCE, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["input_state"] == "ON"
    assert main(["measure", "--resource", RESOURCE, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["power_w"] == 6.0


def test_runtime_error_json(fake_visa, capsys):
    fake_visa.responses["INP:STAT?"] = TimeoutError("PRIVATE-RESOURCE")
    assert main(["input-state", "--resource", RESOURCE, "--json"]) == 3
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


def test_measure_dry_run_lists_fetch_query(capsys):
    assert main(["measure", "--dry-run", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["result"]["queries"] == [
        "*IDN?", "MEAS:VOLT?", "MEAS:CURR?", "FETC:POW?",
    ]


def test_power_query_failure_is_cli_error(fake_visa, capsys):
    fake_visa.responses["FETC:POW?"] = TimeoutError("PRIVATE-RESOURCE")
    assert main(["measure", "--resource", RESOURCE, "--json"]) == 3
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["ok"] is False
    assert result["error"]["code"] == "query_failed"
    assert "PRIVATE-RESOURCE" not in output.out
    assert output.err == ""
    assert fake_visa.calls.count("FETC:POW?") == 1
    assert "MEAS:POW?" not in fake_visa.calls
    assert fake_visa.calls[-2:] == ["session_close", "manager_close"]
