"""CLI output and exit contracts, including the real Core-to-fake-VISA path."""

import json

import pytest

from electronicload_tool_cli.cli import main

RESOURCE = "USB0::0xFFFF::0x0001::SYNTHETIC::INSTR"

@pytest.mark.parametrize("command,metric,query,field", [
    ("measure-min-max", "current-max", "MEAS:CURR:MAX?", "current_max_a"),
    ("measure-discharge", "time", "MEAS:TIME?", "discharge_time_s"),
])
def test_native_measurement_contributor_cli_reads_verified_hardware(fake_visa, capsys, command, metric,
                                                           query, field):
    fake_visa.responses[query] = "7.5"
    args = [command, "--metric", metric, "--resource", RESOURCE, "--json"]
    scope = ["--hardware-validation", "measurement-extensions"]
    assert main([*args, *scope, "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)["result"]
    assert plan["queries"] == ["*IDN?", query] and fake_visa.calls == []
    assert main([*args, *scope]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert result[field] == 7.5 and result["validation_scope"] == "measurement-extensions"
    assert [call for call in fake_visa.calls if isinstance(call, str) and call.endswith("?")] == [
        "*IDN?", query,
    ]


@pytest.mark.parametrize("command,arguments,expected", [
    ("scpi-version", [], {"scpi_version": "1999.0"}),
    ("display-mode", [], {"display_mode": "NORMAL"}),
    ("error", [], {"error_code": 0, "message": "No error"}),
    ("error", ["--all"], {"errors": [], "read_count": 1, "max_reads": 20,
                                    "empty_confirmed": True, "limit_reached": False}),
])
def test_system_query_cli_simulation_and_usb_live(capsys, fake_visa, command, arguments, expected):
    assert main([command, "--simulate", "--json", *arguments]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["result"] == expected
    query, reply = {
        "scpi-version": ("SYST:VERS?", "1999.0"),
        "display-mode": ("DISP:MODE?", "NORMAL"),
        "error": ("SYST:ERR?", '0,"No error"'),
    }[command]
    fake_visa.responses[query] = reply
    assert main([command, "--dry-run", "--json", *arguments]) == 0
    plan = json.loads(capsys.readouterr().out)["result"]
    assert plan["queries"] == ["*IDN?", query] and plan["live_access"] == "product"
    assert fake_visa.calls == []
    assert main([command, "--resource", RESOURCE, "--json", *arguments]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["command"] == command and payload["mode"] == "live"
    assert payload["result"] == {
        **expected, "visa": {"library": "FAKE-VISA.dll", "manufacturer": "FAKE VISA"},
    }
    assert fake_visa.calls[-4:] == ["*IDN?", query, "session_close", "manager_close"]


def test_system_query_cli_error_code_is_data_and_malformed_reply_fails(fake_visa, capsys):
    args = ["error", "--resource", RESOURCE, "--json"]
    fake_visa.responses["SYST:ERR?"] = '-222,"Data, out of range"'
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["result"]["error_code"] == -222
    fake_visa.responses["SYST:ERR?"] = "bad response"
    assert main(args) == 3
    failure = json.loads(capsys.readouterr().out)
    assert failure["ok"] is False and failure["error"]["code"] == "invalid_response"


def test_system_query_cli_all_preview_and_argument_boundaries(fake_visa, capsys):
    assert main(["error", "--all", "--dry-run", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)["result"]
    assert plan["queries"] == ["*IDN?", "SYST:ERR?"] and plan["max_reads"] == 20
    for command, options in (("scpi-error-next", []),
                             ("scpi-version", ["--all"]), ("display-mode", ["--all"]),
                             ("error", ["--all=true"]),
                             ("identify", ["--hardware-validation", "system-query"])):
        assert main([command, "--dry-run", "--json", *options]) == 2
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "argument_error"
    with pytest.raises(SystemExit) as help_exit:
        main(["error", "--help"])
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


def test_asrl_cli_preview_is_validation_only_and_keeps_omitted_options(capsys):
    assert main(["measure", "--resource", "asrl999::instr", "--hardware-validation", "asrl-readonly",
                 "--serial-baud-rate", "19200", "--serial-write-termination", "CRLF",
                 "--dry-run", "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)["result"]
    assert captured.err == "" and "asrl999" not in captured.out
    assert result["transport"] == "asrl" and result["live_access"] == "blocked"
    assert result["validation_scope"] == "asrl-readonly" and result["validation_only"] is True
    assert result["serial_options"] == {"baud_rate": 19200, "write_termination": "\r\n"}
    assert result["open_timeout_ms"] == result["idn_timeout_ms"] == 1000
    assert result["timeout_ms"] == 5000


@pytest.mark.parametrize("args,code", [
    (["identify", "--resource", "ASRL999::INSTR"], "invalid_resource"),
    (["error", "--resource", "ASRL999::INSTR"], "invalid_resource"),
    (["error", "--resource", "ASRL999::INSTR", "--validation-connection", "asrl"],
     "unsupported_live_scope"),
    (["identify", "--resource", "ASRL999::RAW", "--hardware-validation", "asrl-readonly"], "invalid_resource"),
    (["measure", "--resource", "TCPIP0::example.invalid::INSTR", "--hardware-validation", "asrl-readonly"],
     "invalid_resource"),
    (["measure", "--resource", RESOURCE, "--serial-baud-rate", "19200"], "argument_error"),
    (["input-on", "--resource", "ASRL999::INSTR", "--confirm"], "invalid_resource"),
    (["input-on", "--resource", "ASRL999::INSTR", "--hardware-validation", "asrl-readonly", "--confirm"],
     "argument_error"),
])
def test_asrl_cli_refusals_before_visa(capsys, args, code):
    assert main([*args, "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == code
    assert captured.err == ""


def test_asrl_worker_live_resource_is_refused_before_startup(capsys):
    assert main(["worker", "--mode", "live", "--resource", "ASRL999::INSTR", "--allow-input-on"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "invalid_resource" in captured.err


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


def test_configuration_readback_cli_preview_refusal_and_invalid_live_reply(capsys, fake_visa):
    for command, scope in (("readback", "readback"), ("fixed-status", "readback"),
                           ("fixed-slew-status", "readback"), ("trace-status", "trace")):
        assert main([command, "--simulate", "--json"]) == 0
        actual = json.loads(capsys.readouterr().out)
        assert actual["ok"] and actual["command"] == command
        assert actual["result"]["state_source"] == "simulate"
        assert main([command, "--dry-run", "--hardware-validation", scope, "--json"]) == 0
        plan = json.loads(capsys.readouterr().out)["result"]
        assert plan["hardware_access"] is False and plan["planned_writes"] == []
        assert main([command, "--resource", RESOURCE, "--json",
                     "--validation-allow-pending-live-support"]) == 2
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported_live_scope"
    assert fake_visa.calls == []

    for reply, error, expected_exit in (("1", "mode_not_fixed", 2), ("?", "invalid_response", 3)):
        fake_visa.responses.update({"FUNC:MODE?": "FIX", "FUNC?": "CURR", "TRAN?": reply})
        assert main(["fixed-status", "--resource", RESOURCE, "--hardware-validation",
                     "readback", "--json"]) == expected_exit
        output = capsys.readouterr()
        assert output.err == ""
        assert json.loads(output.out)["error"]["code"] == error
