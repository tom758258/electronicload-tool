# Electronic Load Tool

Electronic Load Tool provides read-only identification and measurements,
plus fixed-CC configuration, offline CV/CR/CW/Transient CC and deliberate Input ON/OFF through a shared Core,
Standalone CLI and bundled Common v3 Worker. The initial registered model is
ITECH IT8812C (`itech-it8812c`).

Product LIVE supports `identify`, `input-state`, `measure`, `set-cc`,
`input-on` and `input-off` only for the exact IT8812C identity with an
operator-supplied USB INSTR Resource and System VISA (target: Keysight IO
Libraries Suite). Core enforces model/range limits without any optional Safety Config. Input ON still requires
deliberate confirmation. Simulation and dry-run never grant hardware authorization.

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

## Fixed CC, offline Fixed CV/CR/CW, Input and Common v3 Worker

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

Core enforces the IT8812C 6 A/60 A CC range and programming resolution.
No user-defined Safety Config or DUT current ceiling is imposed. Range checks
do not guarantee DUT safety or the entire voltage/power operating envelope. The standalone
LIVE Input ON command additionally requires `--confirm`, and the live Worker additionally requires the
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

Normal Fixed CV use is available for **dry-run and simulation only** until exact
IT8812C voltage-range programming/readback is confirmed on physical hardware.
Use `set-cv --voltage <V> --range low|high` (Low 0.1–18 V, 1 mV;
High 0.1–120 V, 10 mV). Invalid values and unrepresentable resolutions
are rejected without rounding. The simulator supports CV configuration and
confirmed simulated Input ON/OFF; normal physical CV Input ON is blocked even
with ON confirmation. Controlled physical trials require the separate
[hardware-validation path](#controlled-instrument-validation). Fixed CC and previously supported LIVE commands
remain unchanged.

Fixed CR is also available for **dry-run and simulation only**. Use
`set-cr --resistance <ohms> --range low|high`: Low **0.05–10 Ω** and
High **10–7,500 Ω**, including 10 Ω in either explicitly selected range.
Core accepts finite numeric values within that range, without imposing decimal
places, a fixed step, automatic range selection or rounding. The published
16-bit resolution is not interpreted as a decimal step. Offline validation
does not certify hardware precision or acceptance.

CR setup verifies simulated range/setpoint readback and leaves Input OFF.
Simulated Input ON requires explicit confirmation; Input OFF remains explicit.
The simulator provides setting/state behavior, not a circuit or DUT model.
Ordinary physical `set-cr` and CR Input ON are blocked, including with the
legacy contributor override. The explicit hardware-validation path is separate.
Results use `resistance_ohm` and `range`; simulated readback also includes
`range_readback_ohm` (a simulator convention only).

Fixed CW (Constant Power) supports **dry-run and simulation only** through
`set-cw --power <W>`. Power is required, from **0 to 250 W inclusive** in
**0.01 W** steps; 0 W is allowed. Invalid, nonfinite, out-of-range and
non-step-aligned values are rejected without rounding or clamping. CW has no
Range option. These are IT8812C offline limits, not physical programming
evidence or DUT safety limits.

CW simulation verifies Fixed mode and the power setpoint, reports `power_w`,
and leaves Input OFF. Setup while Input is ON is refused; simulated ON requires
explicit confirmation and OFF remains explicit. Dry-run reports
`hardware_access: false` without changing simulation state. There is no
CW circuit/DUT model or setpoint-derived V/I/P accuracy guarantee. Ordinary
LIVE `set-cw` is refused before opening VISA, and normal physical CW Input ON
is refused before writes, including with the legacy contributor override.
Controlled physical trials require the separate hardware-validation path.

```powershell
.\.venv\Scripts\electronicload-tool.exe set-cc --simulate --current 1.5 --range low --json
.\.venv\Scripts\electronicload-tool.exe set-cv --simulate --voltage 5 --range low --json
.\.venv\Scripts\electronicload-tool.exe set-cr --simulate --resistance 5 --range low --json
.\.venv\Scripts\electronicload-tool.exe set-cr --dry-run --resistance 100 --range high --json
.\.venv\Scripts\electronicload-tool.exe set-cw --simulate --power 12.34 --json
.\.venv\Scripts\electronicload-tool.exe set-cw --dry-run --power 0 --json
.\.venv\Scripts\electronicload-tool.exe input-on --simulate --confirm --json
.\.venv\Scripts\electronicload-tool.exe input-off --simulate --json
.\.venv\Scripts\electronicload-tool.exe manifest --json
.\.venv\Scripts\electronicload-tool.exe worker --mode simulate --control-port 0
```

Standalone simulation commands each start with fresh state. To exercise a CV/CR/CW
setup followed by simulated ON/OFF, send the commands to the same simulated
Worker, with `confirm_input_on: true` on the ON request.

A Worker prints Common v3 JSONL on stdout and offers local `GET /status`,
`POST /command`, and `POST /stop` on numeric loopback only. The Worker
runs one async job at a time, reports completion via `job_finished` and
`last_job`, and does not create request/result artifact files or a job
history. Stop does **not** imply Input OFF. For domain argument shapes and
permissions, see [Electronic Load Worker Contract](docs/contracts/electronicload-worker-contract.md).

### Offline Transient CC (Dynamic Mode)

Transient CC supports **Continuous, Pulse and Toggle** in Core, CLI, Simulator
and the Common v3 Worker through `--simulate` and `--dry-run`. Submit the whole
configuration with `set-transient-cc`; setup, enable, confirmed Input ON, software
trigger, Input OFF and disable are separate operations.

| Setting | Accepted values |
| --- | --- |
| `--transient-mode` | `continuous`, `pulse`, `toggle` |
| `--range` | `auto`, `low`, `high` |
| `--level-a`, `--level-b` | Low: 0–6 A, 0.0001 A steps; High: 0–60 A, 0.001 A steps |
| `--rise-slew`, `--fall-slew` | Low: 0.0001–0.25 A/μs; High: 0.001–2.5 A/μs |
| Continuous widths | Required `--width-a` and `--width-b`, in seconds |
| Pulse width | Required `--pulse-width`, in seconds; corresponds to the Level A pulse width |
| Toggle widths | No width arguments accepted |

Widths are **0.00002–3600 s**, in **0.000001 s** steps. Core validates every
parameter before changing state and rejects nonfinite, out-of-range or
unrepresentable values without rounding or clipping. Auto chooses Low only if
both levels, their resolution and both slew rates fit; otherwise it tries High.
An explicit range is never changed. Slew is checked against the documented
bounds; the manuals do not establish a fixed decimal step. Slew result fields
are decimal **strings** to preserve every accepted digit in JSON.

These limits follow the IT8812C table in the
[IT8800 User Manual V2.7](https://media.digikey.com/pdf/Data%20Sheets/ITech%20Elect%20PDFs/IT8800%20User%20Manual.pdf)
(printed page 40), with mode/width semantics from the
[IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf)
(printed pages 19–20, 48, 50–51). The normal offline implementation sends no Transient SCPI. The explicit
contributor validation path below prepares physical commands without opening
normal Product LIVE support.

```powershell
.\.venv\Scripts\electronicload-tool.exe set-transient-cc --simulate --transient-mode continuous --range auto --level-a 1 --level-b 2 --width-a 0.001 --width-b 0.002 --rise-slew 0.1 --fall-slew 0.1 --json
.\.venv\Scripts\electronicload-tool.exe set-transient-cc --dry-run --transient-mode pulse --range low --level-a 2 --level-b 1 --pulse-width 0.001 --rise-slew 0.1 --fall-slew 0.1 --json
.\.venv\Scripts\electronicload-tool.exe set-transient-cc --dry-run --transient-mode toggle --range auto --level-a 1 --level-b 2 --rise-slew 0.1 --fall-slew 0.1 --json
```

Each standalone simulated command starts fresh. Use **one simulated Worker**
for the sequence `set-transient-cc` → `transient-enable` → confirmed `input-on`
→ `transient-trigger` → optional `transient-status` → `input-off` →
`transient-disable`. Wait for each successful `job_finished` before submitting
the next command. A successful Trigger job means the software trigger was
accepted/executed; **it does not mean a waveform finished**. Repeated triggers
are allowed while Transient is enabled and Input is ON.

Setup requires Input OFF and disabled Transient. Enable requires setup and
Input OFF; disable also requires Input OFF. None of these commands sends a
hidden Input ON/OFF, retry or cleanup. Input OFF leaves Transient enabled;
disable retains the setup. To return to Fixed operation, explicitly disable
Transient before Fixed setup; successful Fixed setup invalidates the previous
Transient configuration.

`transient-status` reports configured, mode, resolved range, enabled, configuration
and the most recent accepted software trigger. Query `input-state` separately.
The simulator models control state only, with no waveform, pulse timer, current
transition, DUT physics or physical-running/completion indication. Worker
`GET /status` retains its runtime/job meaning. At normal workflow completion,
the Orchestrator must explicitly attempt and confirm Input OFF before stopping
the Worker; Stop or process exit never proves Input OFF.

All five Transient commands are blocked for ordinary LIVE before opening VISA,
including with the legacy contributor override. Normal physical Input ON remains
Fixed CC only. The dedicated `--hardware-validation` path below is separate
from Product LIVE and is unavailable to the Worker.

### Unified CLI instrument validation

`scripts/live-cli-check.ps1` is the single hardware validation entry point.
Its default sequence covers all five modes: CC High/Low, CV Low/High,
CR Low/High, CW and Transient Continuous/Pulse/Toggle. Use `-Modes` to select
one or more modes; common read-only safety prechecks always apply.
CV/CR/CW/Transient use the explicit validation-only Core/CLI path.
`-PlanOnly` checks every selected CLI request without hardware; it does not
count planned cases as physical PASS.
See [Controlled instrument validation](#controlled-instrument-validation).

`scripts/preflight-cli.ps1` covers the offline CLI and ordinary LIVE refusal
behavior, including CV/CR/CW/Transient and Manifest exposure. Use
`-TransientOnly` for focused Transient checks. Preparing hardware scripts and
executing hardware tests are separate activities: physical runs require the
operator gates below. Normal CI continues to run no-hardware checks.

## Checks and builds

Run checks from the repository root after syncing the development environment.
Before the first pytest run, create the ignored repository-local test directory.
`pyproject.toml` uses `--basetemp=.tmp_tests/pytest`; without the parent
`.tmp_tests/` directory, pytest's `tmp_path` fixture can fail with
`FileNotFoundError` on Windows. The local directory also avoids relying on
system temporary-directory permissions.

```powershell
New-Item -ItemType Directory -Force .tmp_tests | Out-Null
```

Then run the relevant checks:

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
point. It defaults to all implemented modes, using supported LIVE commands and
the validation-only path where required. It is an explicit operator-
approved hardware validation workflow, **not** a general unattended control
command and not automatic Product LIVE promotion.

Preview the default **all-mode** plan without opening VISA or touching hardware.
Use a fictional resource only for offline checks; replace `<USB_RESOURCE>` with
your own approved resource for a real run:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource '<USB_RESOURCE>' -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -PlanOnly
```

`-Modes` is the sole mode selector and replaces `-IncludeValidationModes`.
Accepted values are `cc`, `cv`, `cr`, `cw`, and `transient-cc`; omit it for all
five. Select a single mode or a comma-separated list:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource '<USB_RESOURCE>' -Modes cc -PlanOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource '<USB_RESOURCE>' -Modes transient-cc -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -PlanOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource '<USB_RESOURCE>' -Modes cc,cv -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -PlanOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource '<USB_RESOURCE>' -Modes cc,transient-cc -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -PlanOnly
```

The selected modes run once each in the established CC, CV, CR, CW, Transient
order. Unknown or empty mode names are rejected. Unselected modes have no
validation cases or PASS counts; planned but unexecuted cases remain NOT RUN.

Whenever CV, CR, CW or Transient is selected, including the default all-mode
plan, explicitly declare source limits greater than zero and no more than
**2 A / 18 V**, even for PlanOnly. Before a real run, configure and verify these
limits on the source and confirm the DUT tolerates **every printed setting**.
The script does not control or verify the source. These script-only bounds do
not expand product support or substitute for a safe source/DUT setup.

The mode-specific trials use `-Voltage 5` for both CV ranges,
`-ResistanceLow 10`, `-ResistanceHigh 1000`, `-Power 1`, `-LevelA 0.1`,
`-LevelB 0.2`, `-RiseSlew 0.1`, `-FallSlew 0.1`, `-WidthA 0.001`,
`-WidthB 0.002` and `-PulseWidth 0.001` by default. Units are V, ohm, W, A,
A/us and seconds. Override these parameters for your approved setup; Core
checks exact model limits without rounding, and Transient levels also obey
the script's 2 A ceiling. A separate, printed Auto High trial deliberately
uses **0.5 A/us** for both slew rates while retaining the selected levels.
An unsuitable parameter combination fails the offline plan before hardware I/O.

The CV/CR/CW/Transient sequence covers CV Low/High, CR Low/High, CW, Continuous Auto,
Pulse Low, Toggle High, Continuous High and Pulse Auto High. Each trial
configures with Input OFF, explicitly confirms ON, measures, explicitly turns
OFF and independently checks OFF. Transient additionally enables before ON,
sends two software triggers, queries control status, and disables after OFF.
These checks do not measure waveform shape, timing, slew accuracy or completion;
such measurements require an appropriate external instrument and procedure.

For a separately approved, safe IT8812C + bounded source/DUT setup, run once
in an interactive Windows console:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Connection usb -Resource '<USB_RESOURCE>' -Backend system -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -ConfirmWrites -EnableInputOn
```

For a selected-mode run, keep the desired `-Modes` and source limits from the
preview command and replace `-PlanOnly` with `-ConfirmWrites -EnableInputOn`,
after separate approval of the physical setup. The same interactive
confirmation and failure handling apply to every selection.

The CC trial defaults to 0.1 A, with a script-only maximum of 2 A.
Both low/high configurations must satisfy the model range and programming resolution.
The single `-Current` applies to both ranges; low is selected last for the
bounded ON/OFF trial. These limits do not alter normal product CC support.

The complete LIVE validation requires **both** `-ConfirmWrites` and
`-EnableInputOn`, a user-supplied explicit USB INSTR Resource, interactive
operator confirmation, verified physical Input OFF, a safe source/DUT and
a manual OFF contingency. The script blocks missing gates/noninteractive
runs before any physical VISA connection. After offline preflight, the operator
presses **Enter once** to run; Ctrl+C cancels. No magic words, Suite, Stage
or extra prompt are required.

Every selection starts with Identify (precheck), Input State (must be OFF),
and V/I/P measurement with Input OFF. When CC is selected, its steps are
CC High and CC Low setting/readback (with OFF checks before writes);
deliberate Input ON with physical confirmation;
V/I/P measurement while ON; explicit Input OFF and confirmation; and a final
independent Input State OFF query. Selected CV/CR/CW/Transient trials follow
with the separate ON/OFF and status steps described above. Any failed
verification prevents further normal test cases. If Input ON might have been attempted, the script makes
an **explicit bounded Input OFF cleanup attempt** even after a failure, and
reports OFF as confirmed only on successful physical readback. If cleanup
fails or times out, the operator must act; neither process termination nor
VISA session closure proves Input OFF. No reset, `SYST:LOC`, protection clear,
SCPI error-queue consumption or automatic reconnect is added.

The CC trial uses normal Product LIVE permissions. CV/CR/CW/Transient trials
select `--hardware-validation cv|cr|cw|transient-cc` explicitly; the legacy
`--validation-allow-pending-live-support` does not enable these modes. Exact identity/USB/System VISA and Input ON
confirmation requirements remain unchanged; the 2 A bound is only for
the controlled validation script. System VISA library/manufacturer
is reported for diagnostics and cross-command consistency, not independent
operator verification. The script does not validate Worker LIVE lifecycle or
nonzero-load current/power accuracy.

Each CLI command has a **30-second** process deadline; the full run has a
**240-second** I/O budget for CC alone, or **900 seconds** when any other mode
is selected, beginning at Identify. Each explicit cleanup command has a
separate 30-second allowance.
After a possibly attempted Transient enable, cleanup separately checks Input
OFF before attempting Transient Disable. If OFF cannot be confirmed, disable
is blocked and operator action is reported. Cleanup success never erases an
earlier failed case. The operator prompt is
not timed. Instrument VISA timeouts stay at 5000 ms. V/I/P are separate
measurements and power freshness/synchronization are not guaranteed.
One run validates a bounded device configuration, not every possible current,
load range, external DUT, failure condition, or Worker LIVE lifecycle.

The validation-only Core/CLI path verifies exact IT8812C identity and USB/System
VISA. Enable, ON and Trigger require `--validation-settings-json` with the
complete expected setup (the same field names as Worker setup arguments).
Core compares instrument readback with that setup before and after entering
remote mode, with an additional explicit `--confirm` for ON. Settings are not
cached across CLI processes. `transient-status` reports physical readback and
control state; it cannot reconstruct trigger history or prove waveform completion.
This flag is not accepted by Worker requests and does not change its manifest,
Common v3 protocol, Stop behavior or LIVE policy.

The prepared physical command mapping follows the
[Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf),
printed pages 40, 46, 48, 50–52, 54 and 56–57. Transient uses `FUNC:MODE FIX`,
`FUNC CURR`, separate `TRAN ON/OFF`, `TRIG:SOUR HOLD` and `TRIG:IMM`.
Pulse width maps to `CURR:TRAN:AWID`; Toggle writes neither width.
CV explicitly disables automatic range and selects 18/120 V; CR selects
10/7500 ohm. The validation path requires those corresponding numeric range
readbacks and exact requested setpoints before ON; unfamiliar responses fail
closed for investigation, with no guessed fallback or automatic adjustment.
These are validation requirements, not a claim of physical acceptance or
normal Product LIVE availability.

The existing report layout is retained under
`.tmp_tests/readonly-live-*/shareable/` with `report.json` and
`summary.md`. The report includes per-case PASS/FAIL/NOT RUN and a final
summary, printed case parameters and selected control readbacks; the resource
stays redacted. Planned and unexecuted cases remain NOT RUN. Default execution retains **no raw
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
