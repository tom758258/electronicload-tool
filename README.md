# Electronic Load Tool

Electronic Load Tool provides read-only identification and measurements,
plus fixed-CC configuration and deliberate Input ON/OFF through a shared Core,
Standalone CLI and bundled Common v3 Worker. The initial registered model is
ITECH IT8812C (`itech-it8812c`).

Product LIVE currently supports `identify`, `input-state`, and `measure`
only for the exact IT8812C identity through an operator-supplied USB INSTR
resource and System VISA (target: Keysight IO Libraries Suite). Newly added
`set-cc`, `input-on` and `input-off` control require separately approved
real-instrument validation before Product LIVE scope is opened. No
dry-run/simulation result grants LIVE control permission.

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
.\.venv\Scripts\electronicload-tool.exe list-resources
.\.venv\Scripts\electronicload-tool.exe list-resources --live-only --json
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
resource is missing. Read-only LIVE requests require an explicit USB INSTR
resource and exact IT8812C identity. Replace placeholders only for authorized
use. Operation commands never discover, guess, or select resources automatically.
Only the explicit `list-resources` diagnostic enumerates them.

For an authorized identification diagnostic:

```powershell
.\.venv\Scripts\electronicload-tool.exe identify --resource '<USB_RESOURCE>' --backend system --json
```

Identification sends only `*IDN?`. It checks the full manufacturer and model
fields before accepting the identity. The two supported manufacturer tokens
`ITECH Ltd` and `ITECH Ltd.` and model token `IT8812C` are matched without
case sensitivity; other spellings and models are rejected. Detected text,
canonical model token, and stable model ID remain distinct. CLI results omit
raw IDN and serial numbers. The same identity check precedes all LIVE
input-state and measurement queries.

### VISA resource discovery

`list-resources` lists all resource types returned by System VISA (`@ivi`),
including possibly stale entries. It does not open devices or send SCPI.
`list-resources --live-only` explicitly opens **only USB INSTR** resources
and sends `*IDN?`; it lists only responders with structurally valid IDN,
including unregistered brands/models. Unregistered models have `model_id: null`
and are **not** authorized for Product LIVE operation. Neither command
auto-selects a resource for any other command. There is no Discovery
`--dry-run`, `--simulate`, or alternate backend.

Both commands support `--json` (one success/error object). Text output uses
`Resources:` or `Live resources:` and displays `IDN:` for live responders.
Discovery text and JSON may contain **real resource names and IDN serials**:
use them only as local diagnostics; do not publish unredacted output.

### Results and exits

Successful simulation of `measure --simulate --json` returns:

```json
{"ok":true,"command":"measure","mode":"simulate","model_id":"itech-it8812c","result":{"voltage_v":12.0,"current_a":0.5,"power_w":6.0}}
```

A LIVE request without a resource is refused:

```json
{"ok":false,"command":"measure","mode":"live","error":{"code":"missing_resource","message":"LIVE requests require an explicit --resource."}}
```

For authorized read-only LIVE use:

```powershell
.\.venv\Scripts\electronicload-tool.exe input-state --resource '<USB_RESOURCE>' --backend system --json
.\.venv\Scripts\electronicload-tool.exe measure --resource '<USB_RESOURCE>' --backend system --json
```

Modes are `live`, `dry-run`, and `simulate`. `identify` results contain
`manufacturer`, detected `model`, `canonical_model`, and `firmware`;
`input-state` contains `input_state` (`OFF` or `ON`). Failed state queries report
`UNKNOWN` in `error.details.input_state`. Measurement keys carry their units:
`voltage_v`, `current_a`, and `power_w`. Partial or nonfinite measurements fail
the whole request. Voltage and current use separate MEASure queries. Power
uses FETCh to read the latest available power result; it does not guarantee
that a new sample is taken. The tool does not guarantee power-data freshness,
updates under nonzero load, power accuracy, or V/I/P synchronization.

LIVE results also include `result.visa.library` and `result.visa.manufacturer`
for runtime inspection. These identify the VISA implementation, not the load
manufacturer. Library paths can contain local information; the validation script
keeps raw output only when `-DebugArtifacts` is specified and always writes
redacted shareable reports.
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
| `measure` | `*IDN?`, then `MEAS:VOLT?`, `MEAS:CURR?`, `FETC:POW?`. |

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
CLI. WebUI is not implemented. The bundled Worker is part of the CLI package and uses the same Core.

## Fixed CC, Input and Common v3 Worker

Fixed CC uses a complete `set-cc` request with amperes and an explicit
`low` (0–6 A) or `high` (0–60 A) range. Before LIVE configuration, Core
checks the exact IT8812C identity and physically confirms Input OFF, then
sends the programming-guide-required `SYST:REM` transition. It rechecks
Input OFF before **each** of the four subsequent CC writes; after all four
setting readbacks it verifies Input OFF once more. Configuration never turns
Input ON. The `CURR:RANG?` reply is a numeric range-selector value, not
necessarily a range ceiling: for IT8812C, 0–6 selects low resolution range,
and values greater than 6 through 60 select high. A numeric range reply of 0
does not prove that a requested nonzero current was applied: `CURR?` must
also agree with the requested setpoint. Mismatches include safe numeric
readback evidence in the error result without assuming success.

Create a local Safety Config, for example:

```toml
[safety]
max_current = 2.0
```

A valid Safety Config is mandatory for LIVE `set-cc` and `input-on`.
The allowed maximum is the smaller of the model/range rating and the
operator's `max_current`. The CLI reloads this file per invocation; a Worker
loads it once at startup. The standalone LIVE Input ON command additionally
requires `--confirm`, and the live Worker additionally requires the
startup `--allow-input-on` and per-request `confirm_input_on: true`.
Core checks the current **physical** fixed CC, current/range, Transient/Short
OFF and Input OFF state, not remembered setpoints. For Input ON, the complete
physical safety checks run before and after `SYST:REM` (and the Worker Stop
gate is checked immediately before `INP ON`). Explicit Input OFF also enters
remote mode before sending and verifying `INP OFF`. Read-only identify,
input-state, measure and resource discovery **never** send `SYST:REM`.
There is no automatic `SYST:LOC` or `SYST:RWL` command; closing VISA never
implies Input OFF or returning to local. An ON readback failure is UNKNOWN;
it is never claimed to be OFF or retried automatically.

```powershell
.\.venv\Scripts\electronicload-tool.exe set-cc --simulate --current 1.5 --range low --json
.\.venv\Scripts\electronicload-tool.exe input-on --simulate --confirm --json
.\.venv\Scripts\electronicload-tool.exe input-off --simulate --json
.\.venv\Scripts\electronicload-tool.exe manifest --json
.\.venv\Scripts\electronicload-tool.exe worker --mode simulate --control-port 0
```

A Worker prints Common v3 JSONL on stdout and offers local `GET /status`,
`POST /command`, and `POST /stop` on numeric loopback only. The Worker
runs one async job at a time, reports completion via `job_finished` and
`last_job`, and does not create request/result artifact files or a job
history. Stop does **not** imply Input OFF. For domain argument shapes and
permissions, see [Electronic Load Worker Contract](docs/contracts/electronicload-worker-contract.md).

### Unified CLI instrument validation

Use the single `scripts/live-cli-check.ps1` for all currently implemented CLI
instrument checks: Identify, Input State, V/I/P, fixed CC (high and low range),
explicit Input ON, on-state V/I/P, explicit Input OFF, and final Input OFF.
The same script retains the existing `-PlanOnly`, `-DebugArtifacts`, report
privacy, process deadlines and interactive Enter confirmation. There is no
separate control script or mode/suite selection; see
[Controlled instrument validation](#controlled-instrument-validation).

## Checks and builds

Run checks from the repository root after syncing the development environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_foundation.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_readonly_core.py tests/test_readonly_cli.py tests/test_discovery.py tests/test_discovery_cli.py
.\.venv\Scripts\python.exe -m ruff check src tests
.\scripts\preflight-cli.ps1
```

See [Testing Guidelines](docs/testing-guidelines.md) for verification scope and
installed-wheel checks. Contributor changes follow [AGENTS.md](AGENTS.md).

## Controlled instrument validation

`scripts/live-cli-check.ps1` is the **one** CLI hardware verification entry
point. It runs every currently implemented CLI function in order, rather than
requiring multiple scripts, Suites or Stages. It is an explicit operator-
approved hardware validation workflow, **not** a general unattended control
command and not automatic Product LIVE promotion.

Preview the same full command plan without opening VISA or touching hardware:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Connection usb -Resource '<USB_RESOURCE>' -Backend system -SafetyConfig '.\limits.toml' -Current 0.1 -PlanOnly
```

For a separately approved, safe IT8812C + bounded source/DUT setup, run once
in an interactive Windows console:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Connection usb -Resource '<USB_RESOURCE>' -Backend system -SafetyConfig '.\limits.toml' -Current 0.1 -ConfirmWrites -EnableInputOn
```

`-SafetyConfig` is optional: the bundled `examples/safety-config.toml`
is used otherwise. That **example permits 2.0 A** and must be reviewed for
the actual setup; passing a smaller operator-defined maximum is preferred
where appropriate. Both requested low/high configurations must be within
the configured limit and each range's programming resolution. The single
`-Current` is used for both ranges, and the low range is selected last for
the bounded Input ON trial. No automatic range choice is made in Core.

The complete LIVE validation requires **both** `-ConfirmWrites` and
`-EnableInputOn`, a user-supplied explicit USB INSTR Resource, interactive
operator confirmation, verified physical Input OFF, a safe source/DUT and
a manual OFF contingency. The script blocks missing gates/noninteractive
runs before any physical VISA connection. After offline preflight, the operator
presses **Enter once** to run; Ctrl+C cancels. No magic words, Suite, Stage
or extra prompt are required.

The fixed order is Identify (precheck); Input State (must be OFF);
V/I/P measurement with Input OFF; CC High and CC Low setting/readback
(with OFF checks before writes); deliberate Input ON with physical confirmation;
V/I/P measurement while ON; explicit Input OFF and confirmation; and a final
independent Input State OFF query. Any failed verification prevents further
normal test cases. If Input ON might have been attempted, the script makes
an **explicit bounded Input OFF cleanup attempt** even after a failure, and
reports OFF as confirmed only on successful physical readback. If cleanup
fails or times out, the operator must act; neither process termination nor
VISA session closure proves Input OFF. No reset, `SYST:LOC`, protection clear,
SCPI error-queue consumption or automatic reconnect is added.

Read-only queries use normal Product LIVE permissions. Pending control
commands are exercised only by the hidden contributor-validation permission
on the real CLI: this **does not open public Product LIVE control**.
The underlying exact identity/USB/System VISA and Core safety rules apply
unchanged. System VISA library/manufacturer is reported for diagnostics and
cross-command consistency, not independent operator verification.

Each CLI command has a **30-second** process deadline; the full run has a
**240-second** I/O budget beginning at Identify. An explicit OFF cleanup
has a separate 30-second allowance if needed. The operator prompt is
not timed. Instrument VISA timeouts stay at 5000 ms. V/I/P are separate
measurements and power freshness/synchronization are not guaranteed.
One run validates a bounded device configuration, not every possible current,
load range, external DUT, failure condition, or Worker LIVE lifecycle.

The existing report layout is retained under
`.tmp_tests/readonly-live-*/shareable/` with `report.json` and
`summary.md`. The report includes per-case PASS/FAIL/NOT RUN and a final
summary; the resource stays redacted. Default execution retains **no raw
CLI logs or private Resource file**. The existing `-DebugArtifacts` flag
additionally saves raw CLI stdout/stderr/exit information and Resource
details under `private/`. Raw files may contain lab or device identifiers:
review/redact them before sharing; do not commit validation artifacts.
`-DebugArtifacts` adds no instrument writes, no new permission and no
alternate validation flow.

Follow [Testing Guidelines](docs/testing-guidelines.md) and
[AGENTS.md](AGENTS.md).

## License

MIT. See [LICENSE](LICENSE).
