# Electronic Load Tool

Electronic Load Tool provides read-only identification, input-state queries,
and single-shot voltage, current, and power measurements through Core and CLI.
The registered profile is ITECH IT8812C (`itech-it8812c`).

All three commands support hardware-free plans and deterministic simulation.
Normal LIVE access permits only restricted identification diagnostics on an
explicit USB INSTR resource through System VISA. Product LIVE input-state and
measurement access is closed. Load configuration and input switching are not
provided.

## Development environment

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

## CLI usage

```powershell
.\.venv\Scripts\electronicload-tool.exe --help
.\.venv\Scripts\electronicload-tool.exe --version
.\.venv\Scripts\electronicload-tool.exe identify --simulate --json
.\.venv\Scripts\electronicload-tool.exe input-state --dry-run
.\.venv\Scripts\electronicload-tool.exe measure --simulate --json
```

Help and version exit successfully. Running without arguments also shows help.
Version information comes from the installed distribution metadata. Use help
and version separately from `--json`.

| Option | Behavior |
| --- | --- |
| `--simulate` | Fixed simulated identity, input ON, 12 V, 0.5 A, and 6 W; no VISA access. |
| `--dry-run` | Validate the profile and connection options; list queries and settings without measurements or VISA access. |
| Neither option | LIVE request; an explicit `--resource` is required. |
| `--model` | Profile or expected live model; defaults to `itech-it8812c`. |
| `--backend` | Only `system` is accepted, mapped to PyVISA `@ivi`. |
| `--json` | Exactly one JSON object on stdout for either success or failure. |

`--dry-run` and `--simulate` are mutually exclusive. There is no `--live` flag
and no implicit simulation. For example, `measure` alone fails because the
resource is missing; `measure --resource "<USB_RESOURCE>"` requests LIVE and is
rejected by Product support policy. Replace placeholders with your own explicit
resource only for an authorized operation. Resources are never discovered,
guessed, or read from environment defaults.

For an authorized identification diagnostic:

```powershell
.\.venv\Scripts\electronicload-tool.exe identify --resource '<USB_RESOURCE>' --backend system --json
```

Identification sends only `*IDN?`. It checks the full manufacturer and model
fields before accepting the identity. The documented manufacturer token
`ITECH Ltd` and model token `IT8812C` are matched without case sensitivity;
other spellings and models are rejected. Detected text, canonical model token,
and stable model ID remain distinct. CLI results omit raw IDN and serial
numbers. Identification success does not open other LIVE commands.

### Results and exits

Successful simulation of `measure --simulate --json` returns:

```json
{"ok":true,"command":"measure","mode":"simulate","model_id":"itech-it8812c","result":{"voltage_v":12.0,"current_a":0.5,"power_w":6.0}}
```

A refused LIVE measurement returns:

```json
{"ok":false,"command":"measure","mode":"live","error":{"code":"unsupported_live_scope","message":"Product LIVE is not open for this command."}}
```

Modes are `live`, `dry-run`, and `simulate`. `identify` results contain
`manufacturer`, detected `model`, `canonical_model`, and `firmware`;
`input-state` contains `input_state` (`OFF` or `ON`). Failed state queries report
`UNKNOWN` in `error.details.input_state`. Measurement keys carry their units:
`voltage_v`, `current_a`, and `power_w`. Partial or nonfinite measurements fail
the whole request. V/I/P are separate acquisitions, not simultaneous samples.

LIVE results also include `result.visa.library` and `result.visa.manufacturer`
for runtime inspection. These identify the VISA implementation, not the load
manufacturer. Library paths can contain local information; validation saves
full output privately and removes the directory from the shareable report.
Neither `@ivi` nor a library filename alone proves Keysight runtime identity.

Exit codes follow the [Common contract](docs/contracts/common-cli-jsonl-contract.md):
`0` success, `2` argument/identity/validation/support-policy refusal, and `3`
communication or execution failure. Errors have `code`, `message`, and optional
`details`. Cleanup errors are preserved alongside an original error and force
exit `3`. JSON is emitted after cleanup, with no human-readable stdout messages.
Without `--json`, results use text stdout and errors use stderr. This interface
does not emit Worker JSONL.

### Instrument behavior

Core owns the following query sequences, based on the
[ITECH IT8800 Programming Guide, English V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf)
(2022-08-01, includes IT8812C; printed pages 23-24, 37-39, and 45):

| Command | Queries, in order |
| --- | --- |
| `identify` | `*IDN?` |
| `input-state` | `*IDN?`, then `INP:STAT?`; only `0` means OFF and `1` means ON. |
| `measure` | `*IDN?`, then `MEAS:VOLT?`, `MEAS:CURR?`, `MEAS:POW?`. |

LIVE uses USB INSTR and System VISA only, with a 5000 ms I/O timeout and
LF read/write termination. The VISA open call also receives 5000 ms, although
that parameter does not guarantee a connection deadline on every runtime.
The target runtime is Keysight IO Libraries Suite.
There is no backend fallback, retry, reconnect, error-queue read, reset,
protection clear, input change, or remote/local command. A not-ready VISA open
status is rejected without PyVISA's automatic device-clear recovery. Read-only queries do
not require input OFF. Closing the session and resource manager does not turn
input OFF. A failed query never proves OFF or produces a substitute zero value.

## Package layout

- `pyproject.toml` defines the single `electronicload-tool` distribution.
- `src/electronicload_tool_core/` owns Core functionality.
- `src/electronicload_tool_cli/` owns the CLI entry point.

Core owns profiles, identity, support policy, queries, parsing, VISA lifetime,
and simulation. CLI handles arguments and presentation. Core must not import
CLI. WebUI and Worker are not included.

## Checks and builds

Run checks from the repository root after syncing the development environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_foundation.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_readonly_core.py tests/test_readonly_cli.py
.\.venv\Scripts\python.exe -m ruff check src tests
.\scripts\preflight-cli.ps1
```

See [Testing Guidelines](docs/testing-guidelines.md) for verification scope and
installed-wheel checks. Contributor changes follow [AGENTS.md](AGENTS.md).

## Controlled instrument validation

Real-instrument validation requires separate authorization and an explicit
user-supplied resource. Preview the exact profile, connection, and query plan
without loading VISA:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Connection usb -Resource '<USB_RESOURCE>' -Backend system -PlanOnly
```

After separate authorization, run the same command without `-PlanOnly` in an
interactive console. It runs preflight, presents the plan, and requires `RUN`
before identification. The operator must then inspect the actual VISA library
and manufacturer information, verify the Keysight installation, and confirm
`KEYSIGHT` before state and measurement queries. Missing runtime evidence or
declined confirmation blocks the remaining commands. Noninteractive execution
is refused before any hardware access.

The script invokes CLI through its installed Python entry function. Each
command has a 30-second process deadline, and the batch has a 90-second I/O
budget starting at identification. Runtime review consumes that budget; the
operator prompt itself is not interrupted, but an expired budget blocks further
queries. Process deadline termination leaves session closure unconfirmed; it
sends no input-off command. Individual VISA operations retain their 5000 ms timeout.

`--validation-allow-pending-live-support` selects Core's validation policy for
this workflow. It relaxes only the Product-open gate; it cannot bypass profile,
identity, USB, backend, or query restrictions. It is not hardware authorization.
Validation success does not change Product support policy.

Reports are written below `.tmp_tests/readonly-live-*/`. `report.json` includes
redacted results, query plans, runtime confirmation, and per-command status.
Files marked `.private` retain local resource/library information; do not share
them. Keep all run evidence out of tracked documentation.

## License

MIT. See [LICENSE](LICENSE).
