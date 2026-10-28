"""Public CLI behavior without hardware access."""

from importlib.metadata import version

import pytest

from electronicload_tool_cli.cli import main


def test_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr()
    assert "--help" in output.out
    assert "--version" in output.out
    assert output.err == ""


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    output = capsys.readouterr()
    assert output.out.strip() == f"electronicload-tool {version('electronicload-tool')}"
    assert output.err == ""


def test_unknown_argument(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--unsupported"])

    assert exc.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "--unsupported" in output.err
