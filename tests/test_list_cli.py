"""Canonical LIST JSON input, files, machine output and offline-only exposure."""

import json

import pytest

from electronicload_tool_cli.cli import main
from electronicload_tool_core.control import LIST_COMMANDS


LIST_JSON = ('{"range":"auto","repeat_count":65536,"steps":['
             '{"current_a":1,"slew_a_per_us":0.12345678901234567890123456789,'
             '"width_s":0.00002000000000000000000000001},'
             '{"current_a":2,"slew_a_per_us":0.2,"width_s":1}]}')


@pytest.mark.parametrize("mode", ["simulate", "dry-run"])
def test_list_inline_and_utf8_file_have_same_result(mode, tmp_path, capsys):
    path = tmp_path / "list.json"
    path.write_text(LIST_JSON, encoding="utf-8-sig")
    results = []
    for source in (("--arguments-json", LIST_JSON), ("--file", str(path))):
        assert main(["set-list-cc", f"--{mode}", *source, "--json"]) == 0
        output = capsys.readouterr()
        assert output.err == ""
        payload = json.loads(output.out)
        assert payload["ok"] and payload["command"] == "set-list-cc"
        result = payload["result"]
        settings = result["configuration"] if mode == "simulate" else result
        assert settings["repeat_count"] == 65536 and settings["range"] == "low"
        assert settings["steps"][0]["slew_a_per_us"] == "0.12345678901234567890123456789"
        assert settings["steps"][0]["width_s"] == "0.00002000000000000000000000001"
        results.append(result)
    assert results[0] == results[1]


@pytest.mark.parametrize("data", [
    "{", "[]", "null", "1", '{}',
    LIST_JSON.replace('"current_a":1', '"current_a":true'),
    LIST_JSON.replace('"current_a":1', '"current_a":"1"'),
    LIST_JSON.replace('"current_a":1', '"current_a":NaN'),
    LIST_JSON.replace('"current_a":1', '"current_a":1e9999999999999999999'),
    LIST_JSON.replace('"current_a":1', '"current_a":1.000000000000000000001'),
    LIST_JSON.replace('"repeat_count":65536', '"repeat_count":65536.0'),
    LIST_JSON.replace('"range":"auto"', '"range":"low","range":"auto"'),
    LIST_JSON.replace('"width_s":1', '"width_s":1,"width_s":2'),
])
def test_list_invalid_inline_and_file_return_one_json_error(data, tmp_path, capsys):
    path = tmp_path / "invalid.json"
    path.write_text(data, encoding="utf-8")
    for source in (("--arguments-json", data), ("--file", str(path))):
        assert main(["set-list-cc", "--simulate", *source, "--json"]) == 2
        output = capsys.readouterr()
        assert output.err == "" and not json.loads(output.out)["ok"]


def test_list_file_and_parser_errors(capsys, tmp_path):
    bad_encoding = tmp_path / "invalid.json"
    bad_encoding.write_bytes(b"\xff\xfe\xff")
    for arguments in ([], ["--file", str(tmp_path / "missing.json")],
                      ["--file", str(tmp_path)], ["--file", str(bad_encoding)],
                      ["--file", "unused.json", "--arguments-json", LIST_JSON]):
        assert main(["set-list-cc", "--simulate", *arguments, "--json"]) == 2
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "argument_error"
    assert main(["set-list-cc", "--simulate", "--arguments-json", "{bad"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "argument_error" in output.err


@pytest.mark.parametrize("command", LIST_COMMANDS)
def test_list_cli_dry_run_live_refusal_and_manifest(command, capsys):
    arguments = (["--arguments-json", LIST_JSON] if command == "set-list-cc" else
                 ["--slot", "1"] if command in {"list-save", "list-recall"} else [])
    assert main([command, "--dry-run", *arguments, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["hardware_access"] is False
    assert main([command, "--resource", "USB0::0xFFFF::0x0001::SYNTHETIC::INSTR",
                 "--validation-allow-pending-live-support", *arguments, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "unsupported_live_scope"
    assert main(["manifest", "--json"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["schema_version"] == 2 and manifest["worker_protocol"]["schema_versions"] == [3]
    assert manifest["commands"][command] == {"kind": "async_job"}


def test_list_standalone_simulation_keeps_fresh_state(capsys):
    assert main(["set-list-cc", "--simulate", "--arguments-json", LIST_JSON, "--json"]) == 0
    capsys.readouterr()
    assert main(["list-status", "--simulate", "--json"]) == 0
    assert not json.loads(capsys.readouterr().out)["result"]["configured"]
    assert main(["list-recall", "--simulate", "--slot", "1", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "list_slot_empty"
