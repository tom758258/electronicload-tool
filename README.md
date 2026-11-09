# Electronic Load Tool

Electronic Load Tool provides read-only identification and measurements,
plus fixed-CC configuration, offline CV/CR/CW/Transient/LIST CC and deliberate Input ON/OFF through a shared Core,
Standalone CLI and bundled Common v3 Worker. The initial registered model is
ITECH IT8812C (`itech-it8812c`).

Product LIVE supports `identify`, `input-state`, `measure`, `set-cc`,
`input-on` and `input-off` only for the exact IT8812C identity with an
operator-supplied USB INSTR Resource and System VISA (target: Keysight IO
Libraries Suite). Core enforces model/range limits without any optional Safety Config. Input ON still requires
deliberate confirmation. Simulation and dry-run never grant hardware authorization.

## Features

- Read-only identification, Input state and measurements for the exact supported IT8812C Product LIVE scope.
- Fixed CC and deliberate Input ON/OFF with the existing hardware safety gates.
- Offline Fixed CV/CR/CW and software Auto Range for Fixed CC/CV/CR.
- Offline Fixed CC paired optional Slew, standalone VON setup/status, and Fixed-mode Short ON/OFF/status.
- Offline Transient CC/CV/CR/CW (Continuous, Pulse, Toggle) and native LIST CC.
- Hardware-free simulation and dry-run; Common v3 Worker with JSON/JSONL for automation.
- Fail-closed model, command, transport and backend permissions; no implicit Input ON.

## Project Structure

- `pyproject.toml` defines the single `electronicload-tool` distribution.
- `src/electronicload_tool_core/` owns Core functionality.
- `src/electronicload_tool_cli/` owns the CLI entry point.

Core owns profiles, identity, support policy, queries, parsing, VISA lifetime,
and simulation. CLI handles arguments and presentation. Core must not import
CLI. WebUI is not implemented. The bundled Worker is part of the CLI package and uses the same Core.

## Key Dependencies

| Component | Purpose |
| --- | --- |
| Python 3.10+ | Runtime |
| [PyVISA](https://pyvisa.readthedocs.io/) | VISA communication |
| System VISA (PyVISA `@ivi`) | Required external runtime for authorized USB INSTR LIVE commands |
| [uv](https://docs.astral.sh/uv/) | Locked local development environment |
| pytest / Ruff / build | Optional development tools declared in `pyproject.toml` |

The single Python distribution and its dependencies are defined in
[pyproject.toml](pyproject.toml) and [uv.lock](uv.lock).
There is no implemented WebUI or Desktop component.

## Install

Python 3.10 or newer is required. From the repository root in PowerShell, install
uv if needed:

```powershell
py -m pip install --user uv
uv --version
```

Create or reuse the repository-local `.venv` and install the project and its
development dependencies from the committed lockfile:

```powershell
uv sync --all-extras --locked --link-mode=copy
```

This follows the same uv workflow as Meters Tool and Powers Tool. Share
`pyproject.toml` and `uv.lock` through Git; recreate `.venv` on each machine rather
than copying it. After pulling changes, run the same locked sync command. It
fails if the lockfile needs updating instead of silently changing dependencies.

When intentionally changing dependencies, update `pyproject.toml`, run the
following command, and review both files together:

```powershell
uv sync --all-extras --link-mode=copy
```

The project is installed in editable mode, so Python source edits are available
without reinstalling. The commands below use `.venv` directly and do not require
activation.

## Quick Start

From the repository root, run safe no-hardware commands:

```powershell
.\.venv\Scripts\electronicload-tool.exe --help
.\.venv\Scripts\electronicload-tool.exe identify --simulate --json
.\.venv\Scripts\electronicload-tool.exe set-cc --simulate --current 1 --range auto --json
.\.venv\Scripts\electronicload-tool.exe set-cc --dry-run --current 1 --range low --rise-slew 0.1 --fall-slew 0.2 --json
```

Each standalone simulation starts with fresh state. Use one simulated Worker
for multi-command sequences such as setup, confirmed Input ON, Short ON/OFF,
and explicit Input OFF. See the [CLI README](docs/cli/README.md) for detailed
commands, limits, mode sequences and safe usage.

## Test

Follow [Testing Guidelines](docs/testing-guidelines.md) and [AGENTS.md](AGENTS.md).
From the repository root, use the narrowest no-hardware checks for the files or
behavior actually changed. Prepare the ignored test directory first:

```powershell
New-Item -ItemType Directory -Force .tmp_tests | Out-Null
.\.venv\Scripts\python.exe -m pytest -q tests/test_foundation.py
```

The focused CLI preflight is available as
`scripts/preflight-cli.ps1 -FixedSlewVonShortOnly`. It does not authorize
LIVE operation. See [CLI validation scripts](docs/cli/README.md#cli-validation-scripts)
and [controlled instrument validation](docs/cli/README.md#controlled-instrument-validation)
for procedures and limitations. No routine full local suite or real-device test
is implied by a documentation change.

## Documentation

- [CLI README — detailed commands and validation](docs/cli/README.md)
- [Electronic Load Worker Contract](docs/contracts/electronicload-worker-contract.md)
- [Common Worker Protocol](docs/contracts/common-worker-protocol.md)
- [Common CLI JSON / JSONL Contract](docs/contracts/common-cli-jsonl-contract.md)
- [Testing Guidelines](docs/testing-guidelines.md)
- [AGENTS.md](AGENTS.md) — contribution and safety boundaries

## License and Disclaimer

MIT. See [LICENSE](LICENSE). This is an independent, unofficial software tool
and is not affiliated with or endorsed by ITECH. Hardware operation requires
appropriate instrument, source/DUT safety precautions and explicit authorization.
Offline results do not prove physical hardware support.
