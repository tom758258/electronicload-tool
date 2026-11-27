# Electronic Load Tool CLI

## Documentation Set

- [Project README](../../README.md) — project overview, installation and build.
- [Supported Models](../core/supported-models.md) — exact LIVE support and Core architecture.
- [Electronic Load Worker Contract](../contracts/electronicload-worker-contract.md) — command arguments, results and Worker behavior.
- [Common Worker Protocol](../contracts/common-worker-protocol.md) and [Common CLI JSON / JSONL Contract](../contracts/common-cli-jsonl-contract.md) — automation interfaces.
- [Testing Guidelines](../testing-guidelines.md) and [AGENTS.md](../../AGENTS.md) — development and validation boundaries.

## Purpose and Current Scope

The CLI and bundled Common v3 Worker use the shared Core for ITECH IT8812C.
This reference covers command syntax, simulation, dry-run, normal LIVE operation
and separately authorized Contributor validation. See Supported Models for the
exact scope; the presence of a command does not imply LIVE support.
Commands below run from the repository root unless stated otherwise.
This documentation does not authorize physical tests.

## Package Contents

- `src/electronicload_tool_cli/cli.py`: arguments, CLI output and Core dispatch.
- `src/electronicload_tool_cli/worker.py` and `worker_client.py`: Common v3 Worker and client.
- `src/electronicload_tool_core/`: identity, capabilities, SCPI, validation, VISA lifetime and simulation.
- `scripts/preflight-cli.ps1` and `scripts/live-cli-check.ps1`: offline checks and controlled validation.

## Requirements

Python 3.10 or newer, uv for repository development, and PyVISA. Normal LIVE
requires the supported instrument, explicit resource and System VISA runtime.
Simulation and dry-run do not require an instrument or a VISA runtime.

## Install and Development

Follow [Install](../../README.md#install) in the project README. From the
repository root, synchronize the locked environment:

```powershell
uv sync --all-extras --locked --link-mode=copy
```

Use the installed CLI in `.venv` or `uv run --no-sync electronicload-tool`.
The editable installation reflects Python source changes without reinstalling.
Use the [Testing Guidelines](../testing-guidelines.md) for focused checks.

## Quick Start

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
| Neither option | Instrument-operation commands are LIVE and require an explicit `--resource`; local utilities and discovery have their own arguments. |
| `--model` | Profile or expected live model; defaults to `itech-it8812c`. |
| `--backend` | Only `system` is accepted, mapped to PyVISA `@ivi`. |
| `--json` | Exactly one JSON object on stdout for either success or failure. |

`--dry-run` and `--simulate` are mutually exclusive. There is no `--live` flag
and no implicit simulation. For example, `measure` alone fails because the
resource is missing. Product LIVE read-only requests require an explicit USB INSTR
resource and exact IT8812C identity. Set the operator-selected VISA resource
once in PowerShell and pass it explicitly to every LIVE command:

```powershell
$env:ELECTRONICLOAD_TOOL_RESOURCE = "USB0::...::INSTR"
```

PowerShell expands this documentation-only variable when passed explicitly as
`--resource` (CLI) or `-Resource` (validation script); neither reads it automatically. Operation
commands never discover, guess, or select resources automatically.
Only the explicit `list-resources` diagnostic enumerates them.

For an authorized identification diagnostic:

```powershell
.\.venv\Scripts\electronicload-tool.exe identify --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --backend system --json
```

Identification sends only `*IDN?`. It checks the full manufacturer and model
fields before accepting the identity. The two supported manufacturer tokens
`ITECH Ltd` and `ITECH Ltd.` and model token `IT8812C` are matched without
case sensitivity; other spellings and models are rejected. Detected text,
canonical model token, and stable model ID remain distinct. CLI results omit
raw IDN and serial numbers. The same identity check precedes all LIVE
input-state and measurement queries.

## Command Family Index

Use `electronicload-tool <command> --help` for exact argument syntax. This index
lists the implemented CLI surface, not a LIVE allowlist. See
[Supported Models](../core/supported-models.md#product-live-support) for permissions.

| Family | Commands | Reference |
| --- | --- | --- |
| Setup and diagnostics | `--help`, `--version`, `manifest`, `list-resources`, `identify`, `scpi-version`, `display-mode`, `error` | [Connection](#resource-discovery-and-live-connection), [System Queries](#system-queries) |
| Measurement and Average | `measure`, `measure-min-max`, `measure-discharge`, `set-average`, `average-status` | [Instrument behavior](#instrument-behavior), [Measurement Extensions](#measurement-extensions) |
| Input and Fixed | `input-state`, `input-on`, `input-off`, `set-cc`, `set-cv`, `set-cr`, `set-cw` | [Fixed Modes and Input Control](#fixed-modes-and-input-control), [Auto Range](#fixed-software-auto-range) |
| Readback | `readback`, `fixed-status`, `fixed-slew-status` | [Configuration readback](#configuration-readback) |
| Auxiliary controls | `set-von`, `von-status`, `short-on`, `short-off`, `short-status`, `set-cr-led`, `cr-led-status`, `current-slow-rise-enable`, `current-slow-rise-disable`, `current-slow-rise-status` | [Slew, VON and Short](#fixed-cc-slew-von-and-short), [CR-LED and Slow Rise](#cr-led-and-current-slow-rise) |
| Input Timer | `set-input-timer`, `input-timer-enable`, `input-timer-disable`, `input-timer-status` | [Input Timer](#input-timer) |
| TRACE | `trace-configure`, `trace-status`, `trace-start` | [TRACE](#trace) |
| Transient | `set-transient-cc`, `set-transient-cv`, `set-transient-cr`, `set-transient-cw`, `transient-enable`, `transient-disable`, `transient-trigger`, `transient-status` | [Transient Modes](#transient-modes) |
| Native LIST | `set-list-cc`, `list-save`, `list-recall`, `list-trigger`, `list-status`, `list-exit` | [Native LIST CC](#native-list-cc) |
| Trigger | `trigger-configure`, `trigger-status` | [Trigger Setup](#trigger-setup) |
| Protection | `protection-status`, `protection-set`, `protection-clear` | [Protection](#protection) |
| Sequence and workflows | `sequence`, `measure-log`, `measure-until`, `cc-timed-load`, `cc-discharge` | [Software Sequence](#software-sequence), [Basic Workflows](#basic-workflows) |
| Worker and clients | `worker`, `send-command`, `status`, `stop`, `wait-ready` | [Worker and Automation](#worker-and-automation) |
| Contributor only | `contributor-settings`, `trigger-observe` | [Controlled instrument validation](#controlled-instrument-validation) |

## Resource Discovery and Live Connection

### VISA resource discovery

`list-resources` lists all resource types returned by System VISA (`@ivi`), including ASRL INSTR,
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
To verify one operator-selected ASRL INSTR resource, use `identify` with the
[ASRL contributor validation scope](#asrl-contributor-validation).

### VISA connection behavior

Core validates VISA resource strings and owns session lifetime, identity checks,
SCPI communication, and reply parsing. IT8812C uses separate USB INSTR and
ASRL INSTR paths through System VISA (`@ivi`), with the same model identity
rules and read-only command implementations.

| Transport | VISA open timeout | Identity query timeout | Other I/O timeout | Framing |
| --- | --- | --- | --- | --- |
| USB INSTR | 5000 ms | 5000 ms | 5000 ms | LF read/write, 150 ms query delay |
| ASRL INSTR | 1000 ms | 1000 ms | 5000 ms | VISA/PyVISA settings with selective overrides |

The USB open timeout is passed to the VISA driver, which controls its
enforcement. ASRL open and `*IDN?` have separate 1000 ms deadlines; the
identity write and all response reads share the identity deadline. Core rejects
late results before using the session or issuing more queries. Native VISA
calls are synchronous, so Core cannot report an overrun or clean up until
the driver returns. After successful ASRL identification, other queries use
the normal I/O timeout. ASRL owns a separate native manager handle and closes
the device handle before the manager. Open, configuration, query, and cleanup
errors terminate the request; cleanup failures are reported alongside the
original error. Neither connection path relies on PyVISA automatic clear or retry.

Core gates LIVE access by canonical model ID, command, transport, VISA backend,
execution scope, and required feature. IT8812C Product and Worker LIVE require
USB INSTR and System VISA. The three basic System Queries are standalone-only,
subject to the exact command permissions in
[Supported Models](../core/supported-models.md#product-live-support).
ASRL INSTR is available only for separately authorized Standalone CLI
Contributor validation via `--validation-connection asrl`, with the relevant
command-specific scope and confirmation. Optional serial overrides affect ASRL
only. Passive discovery may list ASRL resources, but automatic identification
queries USB resources only. See [ASRL contributor validation](#asrl-contributor-validation)
for serial options and the controlled physical procedure.

## Results and exits

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
.\.venv\Scripts\electronicload-tool.exe input-state --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --backend system --json
.\.venv\Scripts\electronicload-tool.exe measure --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --backend system --json
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

Exit codes follow the [Common contract](../contracts/common-cli-jsonl-contract.md):
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

USB Product LIVE uses System VISA with a 5000 ms I/O timeout and
LF read/write termination. The VISA open call also receives 5000 ms, although
that parameter does not guarantee a connection deadline on every runtime.
The target runtime is Keysight IO Libraries Suite.
There is no backend fallback, retry, reconnect, error-queue read, reset,
protection clear, input change, or remote/local command. A not-ready VISA open
status is rejected without PyVISA's automatic device-clear recovery. Read-only queries do
not require input OFF. Closing the session and resource manager does not turn
input OFF. A failed query never proves OFF or produces a substitute zero value.

## System Queries

| CLI command | SCPI query after `*IDN?` | Result |
| --- | --- | --- |
| `scpi-version` | `SYST:VERS?` | `scpi_version`, a `YYYY.V` string for the SCPI specification |
| `display-mode` | `DISP:MODE?` | `display_mode`, normalized to `NORMAL` or `TEXT` |
| `error` | `SYST:ERR?` | One integer `error_code` and string `message` |

Each Error Queue read removes the returned entry. `error --all`
reads in the same session until it receives code `0` or reaches 20 reads.
`errors` contains only nonzero entries. `read_count` includes the zero reply
when present and `max_reads` is 20. A zero reply, including on the 20th read,
sets `empty_confirmed: true` and `limit_reached: false`. A nonzero 20th reply
sets `empty_confirmed: false` and `limit_reached: true`. A nonzero instrument
error code is successful query data. Communication and invalid-response
failures use the standard CLI error envelope and exit code 3. If a queue read
fails during `--all`, `error.details.errors` and `error.details.read_count`
contain the entries parsed before the failure and their count. Whether the
failed read consumed another entry is unknown. The System Query commands send
no configuration writes or queue-clear commands.

All three support `--simulate`, `--dry-run` and ordinary standalone CLI LIVE
for the exact IT8812C over USB INSTR / System VISA. A normal USB dry-run lists
`*IDN?` and the selected query, with `max_reads` for `--all` and
`live_access: "product"`. Neither a Contributor flag nor Input OFF is required
for normal diagnostics; the exact identity is verified before querying.
Worker LIVE remains blocked. ASRL requires explicit Contributor authorization.
Use `error` only when removing a queue entry is intended; `--all` explicitly
consumes a bounded number of entries. Do not consume another controller’s queue.

```powershell
electronicload-tool scpi-version --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --json
electronicload-tool display-mode --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --json
electronicload-tool error --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --json
electronicload-tool error --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --all --json
```

The separately approved [System Query validation procedure](#system-query-validation)
retains its own Contributor scope and physical Input OFF precheck.

[ITECH IT8800 Programming Guide V2.1, printed pp. 31–33](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf#page=41)

## Measurement Extensions

`measure-min-max`, `measure-discharge`, `set-average` and `average-status`
support `--simulate` and `--dry-run`. All four refuse Product and Worker LIVE
before opening VISA. The standalone CLI `average-status` also supports the
contributor scope described in [Configuration readback](#configuration-readback).
The standalone CLI also accepts `--hardware-validation measurement-extensions`
for the two native measurement commands and `set-average`. Use a separately
authorized USB or ASRL INSTR resource with System VISA. Average setup additionally
requires `--confirm` and verified Input OFF and leaves the requested setting in place.

```powershell
.\.venv\Scripts\electronicload-tool.exe measure-min-max --simulate --json
.\.venv\Scripts\electronicload-tool.exe measure-min-max --metric current-max --dry-run --json
.\.venv\Scripts\electronicload-tool.exe measure-discharge --simulate --json
.\.venv\Scripts\electronicload-tool.exe measure-discharge --metric time --simulate --json
.\.venv\Scripts\electronicload-tool.exe set-average --count 8 --simulate --json
.\.venv\Scripts\electronicload-tool.exe average-status --simulate --json
```

After the existing `*IDN?` identity check, the native measurement queries are:

| Command | `--metric` | Query | Result field |
| --- | --- | --- | --- |
| `measure-min-max` | `voltage-min` | `MEAS:VOLT:MIN?` | `voltage_min_v` |
| `measure-min-max` | `voltage-max` | `MEAS:VOLT:MAX?` | `voltage_max_v` |
| `measure-min-max` | `current-min` | `MEAS:CURR:MIN?` | `current_min_a` |
| `measure-min-max` | `current-max` | `MEAS:CURR:MAX?` | `current_max_a` |
| `measure-discharge` | `capacity` | `MEAS:CAP?` | `capacity_ah` |
| `measure-discharge` | `time` | `MEAS:TIME?` | `discharge_time_s` |

Omitting `--metric` queries all readings for that command in the table's order.
A selection sends only its corresponding measurement query and returns only
that field as a JSON number. Values use V, A, Ah and seconds as indicated by
their keys. These are sequential independent queries; Min/Max is not a single
SCPI command or a synchronized snapshot. The tool does not accumulate Min/Max,
capacity or time, infer reset windows, or initiate/reset a Battery Test.
Discharge readings have no Fixed/Transient/LIST mode restriction. Time means
native discharge time, separate from Input Timer and voltage rise/fall time.
No software statistics, automatic `FETC` fallback or retries are used. Invalid
metrics fail before instrument I/O; invalid/nonfinite responses or a failed
query fail the whole command without partial success.

`set-average --count` requires an integer from **2 through 16**. Both Average
commands return `{"average_count":8}` for a configured count of 8.
The Core models native `SENS:AVER:COUN <count>` and `SENS:AVER:COUN?`, verifies
the set readback, and rejects mismatches. Set confirms Input OFF before entering
remote mode and checks OFF again before writing. It never changes Input.
`average-status` only queries identity and count, including with Input ON;
it never enters remote mode. This is native measurement averaging, independent
of TRACE `TRAC:FILT`, without a software average algorithm.

The simulator supplies deterministic example measurement values and starts
Average at 2. One simulated Worker retains its count across jobs; standalone
CLI invocations each start fresh. Dry-run validates arguments without querying
or changing simulator state. Measurement previews list the selected queries
with `live_access: "blocked"`; Average previews contain `hardware_access: false`
and Set's validated `average_count`. The existing `measure` interface, fields,
LIVE permissions and `MEAS:VOLT?`, `MEAS:CURR?`, `FETC:POW?` order are unchanged.

## TRACE

`trace-configure` prepares the TRACE buffer while Input is OFF. Use
`--source VOLT|CURR|TWO`, `--points`, and `--interval-s`. Each source accepts
2–1000 points. TWO requires an even count and returns half as many voltage and
current pairs. The interval must be between 0.001 and 3600 seconds, inclusive.
The expected capture time is `(sample_count - 1) × interval_s`, up to 86400
seconds. Configure sets Delay and Filter to zero and verifies the settings.
Interval comparison uses the exact decimal values, so `1.000000` and `1E+0`
match, while `0.001` differs from a request for `0.0013`.

`trace-start` uses the current TRACE settings. It requires Feed idle, TBF
clear, Delay and Filter zero, Fixed CC/CV/CR/CW, Transient OFF, Short OFF,
and Input Timer disabled. These checks apply with Input ON or OFF. The
command confirms Feed NEXT before sending the trigger. The capture deadline
adds 20% to the expected time, with at least five seconds of margin.
After the trigger, once Feed reports NEVER, Start reads the batch once with a
separate 30-second VISA timeout and checks its length, format, and instrument
error status. Either TBF value can accompany a complete batch. The JSON
result includes the settings, an exact decimal string for `interval_s`, and
the complete sample array. Sample indexes start at one, and each sample
includes only the selected measurement fields.

Both commands support simulation and dry-run. Standalone simulation starts
fresh with an empty TRACE buffer and example TWO settings. The simulated
batch becomes available at the first Feed poll. To capture with settings
from a preceding Configure, submit both jobs to the same simulated Worker.
Dry-run validates arguments without touching the instrument, simulator state,
or files. TRACE Product LIVE is disabled.

```powershell
.\.venv\Scripts\electronicload-tool.exe trace-configure --source TWO --points 40 --interval-s 1 --dry-run --json
.\.venv\Scripts\electronicload-tool.exe trace-start --simulate --csv .tmp_tests/trace.csv --csv-format full --csv-time-columns elapsed,delta --json
```

CSV is optional. The default `data` format contains the shared batch read
timestamp in UTC+8, the sample index, and the selected measurements. The
`full` format adds `source`, `points`, `interval_s`, and `sample_count`.
The CLI creates missing parent directories and creates a new file with its
header before triggering. An existing file causes the command to fail.
Failed captures and write errors retain any newly created file and identify
it as incomplete in the error details.

`--csv-time-columns` accepts `elapsed`, `delta`, or `elapsed,delta`. The
optional estimated elapsed time is `(sample_index - 1) × interval_s`.
Estimated delta is blank for the first row and equals `interval_s` for later
rows. The batch timestamp records the host's reading time. The estimated
columns derive from the configured sample interval.

See [TRACE contributor validation](#contributor-only-trace-hardware-validation) for the separately authorized physical procedure.

## Fixed Modes and Input Control

Fixed CC uses a complete `set-cc` request with amperes and a required
`auto`, `low` (0–6 A) or `high` (0–60 A) range. Software Auto checks Low
first, then High, including programming resolution, before any hardware access.
It never changes the setpoint or switches range while Input is ON.
See [Fixed Software Auto Range](#fixed-software-auto-range) for all three Fixed modes.
Before LIVE configuration, Core checks the exact IT8812C identity. Product
LIVE checks `FUNC?` before and after `SYST:REM` and requires a fresh physical
Input OFF readback immediately before entering remote. Product LIVE currently
refuses any reported resistance function, including ordinary Fixed CR with
LED OFF. The approved queries cannot distinguish it from CR-LED or verify
the LED state in other modes.

An approved contributor trial can switch from Fixed CR with
`set-cc --hardware-validation cr-led`. It exercises the same Core CC setup
with the CR-LED interlock enabled. A future Product LIVE approval for this
transition requires model-specific physical evidence and a support-policy
update to allow that same Core path. A switch performed outside the tool
may leave LED state that the current Product LIVE guard cannot establish.
The controlled script plans a conditional contributor handoff before the CC
trial and the powered TRACE capture. It uses the handoff only if ordinary
`set-cc` reads a resistance function and refuses before entering remote.
The handoff confirms Input OFF and uses the Core LED OFF interlock before CC
setup. Other setup errors stop validation. The plan and report record the
conditional step and which path ran.

Product LIVE rechecks Input OFF before **each** of the four CC setup writes. After all four
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
Use `set-cv --voltage <V> --range auto|low|high` (Low 0.1–18 V, 1 mV;
High 0.1–120 V, 10 mV). Invalid values and unrepresentable resolutions
are rejected without rounding. The simulator supports CV configuration and
confirmed simulated Input ON/OFF; normal physical CV Input ON is blocked even
with ON confirmation. Controlled physical trials require the separate
[hardware-validation path](#controlled-instrument-validation).

Fixed CR is also available for **dry-run and simulation only**. Use
`set-cr --resistance <ohms> --range auto|low|high`: Low **0.05–10 Ω** and
High **10–7,500 Ω**, including 10 Ω in either explicitly selected range.
Auto prefers Low at the shared 10 Ω boundary. Explicit High remains High.
Core accepts finite numeric values within the selected range, without imposing decimal
places, a fixed step or rounding. The published
16-bit resolution is not interpreted as a decimal step. Offline validation
does not certify hardware precision or acceptance.

Plain CR setup explicitly switches LED OFF, confirms that state, verifies
range/setpoint readback and leaves Input OFF.
Simulated Input ON requires explicit confirmation; Input OFF remains explicit.
The simulator provides setting/state behavior, not a circuit or DUT model.

### CR-LED and Current Slow Rise

```powershell
electronicload-tool set-cr-led --resistance 50 --range auto --vdrop 1 --simulate --json
electronicload-tool cr-led-status --simulate --json
electronicload-tool current-slow-rise-enable --simulate --json
electronicload-tool current-slow-rise-disable --simulate --json
electronicload-tool current-slow-rise-status --simulate --json
```

`set-cr-led` requires Input OFF and uses the same resistance and Range
validation as `set-cr`. Vdrop accepts finite nonnegative values with their
decimal precision preserved. The contributor validation scope reads
`RES:VDR? MAX` before configuring Vdrop and reports the returned maximum.
`cr-led-status` reads `led_enabled`, `vdrop_v` (decimal string),
`resistance_ohm`, `range`, `range_readback_ohm` and current mode fields.
`fixed_cr` identifies the selected Fixed CR mode independently of the LED
flag. Leaving CR-LED in a supported mode switch requires confirmed LED OFF
before the next mode write. A failed write or readback stops setup.
The plain CR contributor trial requires confirmed LED OFF both before and
after entering remote for Input ON.

Current Slow Rise Enable requires Fixed CC and Input OFF. Disable requires
Input OFF in any mode. Status is read-only in any mode and returns `enabled`
even with Input ON. While enabled, selecting any non-Fixed-CC mode is
refused. Explicit Input OFF, Fixed CC setup and Worker Stop preserve the
enable state. The switch is independent of the paired Fixed CC slew rates.

Each standalone simulation starts with fresh state. Use one simulated Worker
to observe settings across commands. Dry-run previews validate arguments
without reading settings or opening VISA. Simulation provides setting and
state readback, with no LED curve or current ramp timing.

Both features are offline in Product and Worker. Controlled hardware
validation uses the dedicated CLI scopes `cr-led` and `current-slow-rise`
with explicit operator approval. The corresponding script modes keep
Input OFF and check configuration, readback, and explicit state transitions.
Slow Rise CC setup requires a starting function other than CR. Its trial
also verifies that Fixed CV setup is refused before any writes while
Slow Rise is enabled.

Product LIVE Input ON cannot confirm the Slow Rise state. Before resuming
ordinary powered CLI or Worker operation after a Slow Rise trial, require
confirmed Disable and Input OFF. If the controlled script reports unconfirmed
cleanup, use the independent operator safety procedure and confirm both states
before resuming.

```powershell
.\scripts\preflight-cli.ps1 -CrLedSlowRiseOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Modes cr-led,current-slow-rise -PlanOnly
```

The SCPI commands are defined in the [IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf).
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
.\.venv\Scripts\electronicload-tool.exe set-cc --simulate --current 2.5 --range auto --json
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

## Configuration readback

Use `readback` for the selected Fixed, Transient, or LIST mode, Input and
Short state. Results include `active_mode`, `function_mode`, the regulation
`function` (CC/CV/CR/CW), `transient_enabled`, and `configuration`. The Core
uses the function selector and the Transient enable flag together. A disabled
Transient leaves the active mode Fixed unless LIST is selected. The Transient
summary gives the waveform mode, applicable Range and levels A/B. Use
`transient-status` for widths and slew. The LIST summary gives Range, repeat
count, and step count. Use `list-status` for the step settings.

| Command | Settings returned |
| --- | --- |
| `fixed-status` | Fixed CC/CV/CR/CW setpoint and applicable Range. Returns `mode_not_fixed` for LIST or enabled Transient. |
| `fixed-slew-status` | Fixed CC positive and negative slew in A/µs as decimal strings. A fresh simulator reports `configured: false` with null rates. |
| `cr-led-status` / `current-slow-rise-status` | CR-LED, Vdrop, resistance and Range / Current Slow Rise enable. |
| `von-status` / `short-status` / `average-status` | VON threshold and Latch / Short state / averaging count. |
| `input-timer-status` / `trigger-status` | Timer seconds and enable / Trigger Source and Timer interval. |
| `transient-status` | Settings for the current CC, CV, CR or CW function, even with Transient disabled. Includes waveform mode, levels A/B, applicable widths and Range, CC slew, and the enable flag. |
| `list-status` | Current LIST working configuration, including each indexed level, slew and width. Physical slot identity and last trigger are null. Reading a stored slot requires an explicit Recall. |
| `trace-status` | Source, points, interval and delay in seconds, filter enable, and Feed. The query observes the current buffer configuration while capture is idle or active. |
| `protection-status` / `input-state` | Protection settings and condition / Input state. The read-and-clear Protection Event register requires the separate `--events` option. |

```powershell
.\.venv\Scripts\electronicload-tool.exe readback --simulate --json
.\.venv\Scripts\electronicload-tool.exe fixed-status --simulate --json
.\.venv\Scripts\electronicload-tool.exe trace-status --simulate --json
```

Each standalone simulation starts with fresh state. Use the same simulated
Worker for configuration and subsequent status jobs. `configured: true` in
Transient and LIST status means configuration values are available. The
instrument can return factory or previously stored settings. The simulator
returns `false` until configuration is available in that session.
New status results identify simulation as `state_source: "simulate"`. Dry-run
validates the request without opening VISA or reading configuration values.

Physical configuration readback is available in the separately approved
standalone CLI contributor scopes. Use `--hardware-validation readback` for
`readback`, `fixed-status`, `fixed-slew-status`, `von-status`, `short-status`,
`average-status`, `transient-status`, and `list-status`. Use
`--hardware-validation trace` for `trace-status`. CR-LED, Current Slow Rise,
Input Timer, Trigger, and Protection use their existing scopes. These requests
require the exact IT8812C identity and an explicit USB or ASRL INSTR Resource with
System VISA. The new contributor readbacks identify their source as
`instrument_readback`. Query failures and invalid replies return errors.
`fixed-slew-status` checks the positive and negative rates against the reported
CC Range. It stops at the first out-of-range rate and reports the query, observed
rate, range and bounds. Compare those details with the instrument's current
front-panel readings without changing settings. The programming guide for
`CURR:RANG` says a range change can affect immediate, transient and triggered
current settings as well as slew. Settings outside the new range are set to
its maximum. A test that changes the range requires a separately approved
Contributor procedure.
[Programming Guide, printed p. 47](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf#page=57)

For CV, CR and LIST, a valid numeric Range selector without a known label is
returned as its raw readback with `range: null`. The IT8812C has a single 250 W
CW range, so CW reports `range: null` and reads the power setpoint without a
range query. `list-status` checks each width against the IT8812C range of
0.00002–3600 seconds. An out-of-range slew or LIST step value is included in
the error details. [ITECH IT8811/12/12B/12C User Manual, §3.18 and IT8812C specifications](https://media.digikey.com/pdf/Data%20Sheets/ITech%20Elect%20PDFs/IT8800%20User%20Manual.pdf)

```powershell
.\.venv\Scripts\electronicload-tool.exe readback --hardware-validation readback --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --json
```

These snapshots use query-only operations. Diagnostics with destructive reads,
mode selection, Input control, LIST Recall and TRACE capture each require their
own explicit commands. Range replies include the observed numeric selector
where applicable. CV, CR and LIST report a null Range label when the numeric
selector does not identify a known range endpoint. LIST status describes the
working buffer, and Transient status describes configuration and enable state.
Waveform timing and completion require independent observation. TRACE status
can display native settings outside the Tool's capture limits.

[ITECH IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf),
printed pp. 42–46, 47–59, and 60–62.

## Fixed Software Auto Range

Fixed CC, CV and CR accept a required `auto`, `low` or `high` Range. CW has
no Range. Auto is a software choice made once during setup: Core checks Low
first, then High, using the original setpoint. If neither range can represent
it, the request fails before hardware access or simulated state changes.
There is no rounding, clamping, hardware Auto command or range switching
while Input is ON. Explicit Low/High keep their existing behavior and never
fall back to another range.

| Mode | Low | High | Auto examples |
| --- | --- | --- | --- |
| CC | 0–6 A, 0.0001 A step | 0–60 A, 0.001 A step | 0, 2, 6 → Low; 6.001, 10, 60 → High |
| CV | 0.1–18 V, 0.001 V step | 0.1–120 V, 0.01 V step | 0.1, 12, 18 → Low; 18.01, 24, 120 → High |
| CR | 0.05–10 Ω | 10–7500 Ω | 0.05, 10 → Low; 10.01, 100, 7500 → High |

CC 6.0001 A and CV 18.001 V fail: they exceed Low and do not match High's
resolution. Out-of-range values also fail. CR has no assumed decimal step;
the published 16-bit resolution does not define one. All modes require finite
numeric values. Worker values must be JSON numbers, not booleans or numeric
strings. Decimal tokens are preserved during CLI forwarding and Worker
validation so excess precision cannot be rounded into an accepted request.

```powershell
electronicload-tool set-cc --simulate --current 2.5 --range auto --json
electronicload-tool set-cv --dry-run --voltage 24 --range auto --json
electronicload-tool set-cr --simulate --resistance 10 --range auto --json
```

`--range` remains required, with no default. For a Worker's `set-cc`, arguments
`{"current": 2.5, "range": "auto"}` produce `current_a: 2.5` and `range: "low"`
in the existing result. Successful results always report the resolved Low/High,
including dry-run. Invalid Auto candidates return `no_matching_range` with
the failure code for each range in `details.ranges`. Invalid types/nonfinite
values retain their established errors. Invalid Worker arguments are rejected
before job creation; state-precondition failures occur in the accepted job.

A simulated Worker retains resolved settings between jobs. Standalone CLI
simulation starts fresh each time. Dry-run validates and reports a plan with
`hardware_access: false`, without inspecting or changing simulator state.
Manifest schema 2 advertises `range_options: ["auto", "low", "high"]` on
these three commands; their `kind: "async_job"` and Worker protocol 3 are
unchanged. Consumers may read this additive capability field; existing
manual requests and result/JSONL structures stay compatible.

Software Auto does not expand Product LIVE support. Fixed CC retains exact
identity/USB INSTR/System VISA checks, confirmed Input OFF before `SYST:REM`
and every setup write, numeric Low selector or High `MAX`, and final range,
setpoint and Input OFF readbacks. Input ON still needs explicit authorization;
Stop, process exit and VISA close never mean Input OFF. CV/CR/CW, Transient and
LIST remain Product LIVE blocked. Transient/LIST Auto rules are unchanged.

The separate CLI-only CV/CR hardware-validation path also resolves Auto before
planning commands or comparing expected settings. It retains its existing
authorization and safety gates, including `VOLT:RANG:AUTO OFF`; this is not
permission to execute a physical trial. See the [Worker contract](../contracts/electronicload-worker-contract.md)
and [controlled validation procedure](#controlled-instrument-validation).

`scripts/preflight-cli.ps1 -FixedAutoOnly` checks this feature offline; the
default preflight includes it. The unified `live-cli-check.ps1` prepares
additional Input OFF Auto setup/readback cases for CC, CV and CR using the
existing parameter values. `-Current` preserves the original decimal token
for Core validation rather than converting it to a binary float.
Expected ranges come from Core's dry-run plan and
are compared with results during an approved physical run. Manual cases and
ON/OFF counts stay unchanged. The CC ceiling stays 2 A; Auto High above 6 A
is checked offline. `-PlanOnly` never accesses hardware or marks planned cases
as physically passed.

## Fixed CC Slew, VON and Short

These capabilities support simulation and dry-run. Use one simulated Worker to
retain settings and Input state across commands.

```powershell
electronicload-tool set-cc --simulate --current 1 --range auto --rise-slew 0.1 --fall-slew 0.2 --json
electronicload-tool set-von --simulate --voltage 10 --latch on --json
electronicload-tool von-status --simulate --json
```

Supply both Slew rates in A/μs or omit both for the original Fixed CC operation.
When supplied, results include `rise_slew_a_per_us` and `fall_slew_a_per_us`
as exact decimal strings; omission preserves the existing result fields.
Auto selects the range from current first. Low accepts 0.0001–0.25 A/μs and
High accepts 0.001–2.5 A/μs. VON requires a threshold and Latch (`on` or `off`)
with confirmed Input OFF. The simulated threshold accepts 0–120 V without an
assumed step and persists across mode changes. The IT8812C manual describes VON
as ineffective in CV mode.

In a simulated Worker, `short-on` requires Fixed CC/CV/CR/CW, confirmed Input ON
and Short OFF. Read state with `short-status`. **After `short-off`, a DUT can
still be loaded by the previous Fixed setting if Input remains ON. Explicitly
issue `input-off` and confirm OFF to stop loading.**

See the [Worker contract](../contracts/electronicload-worker-contract.md#offline-fixed-cc-slew-von-and-short)
for argument and result fields. ITECH documents the command mappings as
`CURR:SLEW:POS` / `CURR:SLEW:NEG`, `VOLT:ON` / `VOLT:LATC` and `INP:SHOR`.
The offline VON bound uses the documented 0–MAX range and the rated 120 V
input limit. Physical VON limits and write/readback behavior require separate
validation. [ITECH Programming Guide](https://cdn.itechate.com/uploadfiles/%E7%94%A8%E6%88%B7%E6%89%8B%E5%86%8C/user%20manual/it8800/IT8800%20Programming%20Guide-EN.pdf) · [IT8812C specifications](https://www.itechate.com/uploadfiles/2019/06/201906211045484548.pdf) · [ITECH User Manual](https://media.digikey.com/pdf/Data%20Sheets/ITech%20Elect%20PDFs/IT8800%20User%20Manual.pdf)

## Input Timer

Input Timer supports Fixed CC/CV/CR/CW in Core, CLI, Simulator and Worker.
It accepts integer seconds **1–60000 inclusive**, without rounding or clamping.

| Command | CLI arguments | Simulated result |
| --- | --- | --- |
| `set-input-timer` | `--seconds <integer>` | `seconds`, `enabled` |
| `input-timer-enable` | None | `seconds`, `enabled` |
| `input-timer-disable` | None | `seconds`, `enabled` |
| `input-timer-status` | None | `seconds`, `enabled` |

Setting seconds, enabling and disabling require confirmed Input OFF. Setting
and enabling also require Fixed mode with Transient disabled. Enabling requires
Short OFF. While Timer is enabled, Short ON, Transient setup/enable and LIST
setup/recall are refused. Explicitly disable Timer before selecting those modes.
VON and Timer can coexist. Setting seconds preserves the current enabled state.

Status is read-only and may be queried with Input ON. Simulator defaults to
10 seconds, disabled; it stores settings only, without countdown, elapsed time,
or automatic Input OFF. Manual Input OFF preserves Timer settings and enable
state. Worker Stop remains I/O-free and changes neither Input nor Timer.
Standalone simulated CLI invocations each start fresh; use one Worker for a
multi-command cycle. Dry-run validates arguments without inspecting/changing state.

```powershell
.\.venv\Scripts\electronicload-tool.exe set-input-timer --seconds 60 --simulate --json
.\.venv\Scripts\electronicload-tool.exe input-timer-enable --dry-run --json
.\.venv\Scripts\electronicload-tool.exe input-timer-status --simulate --json
```

All four commands, including Status, are blocked before physical VISA access
on normal Product LIVE and Worker LIVE paths, including the legacy contributor
override. Input Timer does not expand Product LIVE support.

See [Input Timer contributor validation](#controlled-timer-validation) for the separately authorized physical procedure.

## Transient Modes

Core, CLI, Simulator and the Common v3 Worker support Continuous, Pulse and
Toggle for all four functions. Each setup replaces the complete inactive
Transient configuration. Use `set-transient-cc`, `set-transient-cv`,
`set-transient-cr` or `set-transient-cw`, followed by the shared controls.
Normal Product and Worker LIVE Transient requests are refused before VISA access.

### Setup arguments

| Function | Level A/B units and limits | Range | Slew |
| --- | --- | --- | --- |
| CC | Low 0–6 A / 0.0001 A; High 0–60 A / 0.001 A | Required `auto\|low\|high` | Required `rise_slew`, `fall_slew` in A/μs; Low 0.0001–0.25, High 0.001–2.5 |
| CV | Low 0.1–18 V / 0.001 V; High 0.1–120 V / 0.01 V | Required `auto\|low\|high` | Rejected |
| CR | Low 0.05–10 Ω; High 10–7500 Ω; no fixed decimal step | Required `auto\|low\|high` | Rejected |
| CW | 0–250 W / 0.01 W | Rejected | Rejected |

All commands require `transient_mode`, `level_a` and `level_b`. One range must
accept both original levels and their precision. Auto checks Low first, then
High; CR 10 Ω fits either explicit range and prefers Low in Auto. CC also checks
both slew values. Explicit ranges never fall back. Core rejects invalid or
nonfinite values without rounding or clamping. These model limits do not
establish a safe source/DUT operating envelope.

| Mode | Required width fields (seconds) | Rejected width fields |
| --- | --- | --- |
| `continuous` | `width_a`, `width_b` | `pulse_width` |
| `pulse` | `pulse_width` (Level A duration) | `width_a`, `width_b` |
| `toggle` | None | All |

All widths are 0.00002–3600 s inclusive. CC requires 0.000001 s steps.
CV/CR/CW accept exact finite widths within the bounds without imposing an
undocumented decimal step. No physical timing accuracy is implied.

```powershell
.\.venv\Scripts\electronicload-tool.exe set-transient-cv --simulate --transient-mode continuous --range auto --level-a 5 --level-b 6 --width-a 0.0000205 --width-b 0.002 --json
.\.venv\Scripts\electronicload-tool.exe set-transient-cr --dry-run --transient-mode pulse --range auto --level-a 10 --level-b 1000 --pulse-width 0.001 --json
.\.venv\Scripts\electronicload-tool.exe set-transient-cw --simulate --transient-mode toggle --level-a 1 --level-b 2 --json
```

CLI flags use hyphens; Worker JSON argument keys use underscores. Worker numeric
arguments must be JSON numbers, not strings or booleans. Original decimal tokens
are retained through HTTP and `send-command --arguments-json` until validation.
Dry-run validates and plans without hardware access or simulation mutation.

### Transient CC CLI Examples

```powershell
.\.venv\Scripts\electronicload-tool.exe set-transient-cc --simulate --transient-mode continuous --range auto --level-a 1 --level-b 2 --width-a 0.001 --width-b 0.002 --rise-slew 0.1 --fall-slew 0.1 --json
.\.venv\Scripts\electronicload-tool.exe set-transient-cc --dry-run --transient-mode pulse --range low --level-a 2 --level-b 1 --pulse-width 0.001 --rise-slew 0.1 --fall-slew 0.1 --json
.\.venv\Scripts\electronicload-tool.exe set-transient-cc --dry-run --transient-mode toggle --range auto --level-a 1 --level-b 2 --rise-slew 0.1 --fall-slew 0.1 --json
```

### Control and results

Standalone simulation starts fresh on every invocation. For a complete sequence,
use one simulated Worker and wait for each successful `job_finished`:

`set-transient-*` → `transient-enable` → confirmed `input-on` →
`transient-trigger` → `transient-status` → `input-off` → `transient-disable`.

Setup, enable and disable require Input OFF; setup also requires disabled
Transient. Trigger requires configured, enabled Transient and Input ON.
Simulated Input ON checks the configured function, levels, range, Short OFF and
Stop state, and requires `confirm_input_on: true`. LIST must be explicitly exited
before Transient mutation. Disable retains configuration and trigger history;
Input OFF leaves Transient enabled. Disable Transient before Fixed or LIST setup.
No operation implicitly changes Input or performs cleanup. Stop remains I/O-free.

Domain status adds `function: "CC"|"CV"|"CR"|"CW"`; `mode` remains
`continuous|pulse|toggle`. Before setup, both are null. CW `range` is null;
other ranges are resolved Low/High. Existing CC configuration names and JSON
value types remain unchanged. New configurations contain:

| Function | Level fields | Width fields |
| --- | --- | --- |
| CV | `level_a_v`, `level_b_v` (JSON numbers) | Applicable `width_a_s`, `width_b_s`, `pulse_width_s` as exact decimal strings |
| CR | `level_a_ohm`, `level_b_ohm` (exact decimal strings) | Same exact decimal strings |
| CW | `level_a_w`, `level_b_w` (JSON numbers) | Same exact decimal strings |

New configurations also retain `transient_mode` and resolved `range` (null for
CW), with no slew fields. Dry-run setup returns these fields at the top level
plus `function`, `command` and `hardware_access: false`. `GET /status` retains
its Worker/runtime shape; `transient-status` is a separate async domain command.
Manifest schema remains 2 and Worker protocol remains 3.

The simulator tracks settings and accepted software triggers only. Repeated
triggers are allowed under the same prerequisites. Trigger success does not
prove waveform shape, timing or completion. Explicitly command and confirm
Input OFF before requesting Worker Stop; neither Stop, exit nor VISA close
proves OFF.

See [Transient contributor validation](#contributor-transient-validation) for the separately authorized physical procedure.

## Native LIST CC

Core, CLI, Simulator and the Common v3 Worker support IT8812C native CC LIST
configuration in simulation and dry-run. Product LIVE LIST is blocked before
VISA opens, including with the contributor override. CV/CR/CW LIST and native
OCP/OPP/Battery tests are outside this interface.

### Configuration and CLI

Supply one complete JSON object to `set-list-cc`. Core and Worker use this same
object; CLI accepts it through either `--arguments-json` or `--file`:

```json
{
  "range": "auto",
  "repeat_count": 3,
  "steps": [
    {"current_a": 0.5, "slew_a_per_us": 0.1, "width_s": 1},
    {"current_a": 1, "slew_a_per_us": 0.2, "width_s": 2}
  ]
}
```

```powershell
.\.venv\Scripts\electronicload-tool.exe set-list-cc --simulate --file list.json --json
.\.venv\Scripts\electronicload-tool.exe set-list-cc --dry-run --arguments-json '{"range":"auto","repeat_count":1,"steps":[{"current_a":0.5,"slew_a_per_us":0.1,"width_s":1},{"current_a":1,"slew_a_per_us":0.2,"width_s":2}]}' --json
.\.venv\Scripts\electronicload-tool.exe list-save --dry-run --slot 1 --json
.\.venv\Scripts\electronicload-tool.exe list-recall --dry-run --slot 1 --json
.\.venv\Scripts\electronicload-tool.exe list-status --simulate --json
```

Every field shown is required; unknown or duplicate fields are rejected. Step
values must be finite JSON numbers, not booleans or numeric strings. Files are
UTF-8 (optional BOM), with the same schema as inline JSON; YAML/CSV are not
accepted. CLI file loading is local to the CLI. Worker arguments contain data,
never a client file path. A JSON file is separate from native Save/Recall slots.

| Setting | Offline validation |
| --- | --- |
| `range` | `auto`, `low`, `high` |
| `repeat_count` | Integer token 1–65536; 65536 means infinite and must be explicit |
| `steps` | 2–84 objects; each has one current, one slew and one width |
| Low current | 0–6 A, 0.0001 A steps |
| High current | 0–60 A, 0.001 A steps |
| Low / High slew | 0.0001–0.25 / 0.001–2.5 A/μs; no imposed decimal step |
| Width | 0.00002–3600 s; no imposed LIST decimal step |
| Save/Recall slot | Integer token 1–7; an explicit Save permits overwrite |

All Steps are validated before state changes. Auto tries Low first, then High,
using every current, resolution and slew value together. If no single range
accepts the complete LIST, the request fails. No rounding, clamping or dynamic
range switching occurs. Repeat count has no implicit infinite default.

The offline current resolution and slew bounds use the IT8812C current
specifications. The LIST sections establish width bounds but do not establish
a LIST-specific width or slew decimal step. The Dynamic T1/T2 1 μs resolution
is not applied to LIST. Accepted widths and slew values retain their exact
decimal values; status/preview returns these two fields as decimal **strings**.
Current fields remain JSON numbers. Offline acceptance does not certify
physical programming precision or timing.

### Explicit control flow

Standalone simulated CLI commands each start fresh. Use one simulated Worker
to retain active settings and saved slots across jobs. Slots exist only for that
simulator instance; they do not emulate persistent instrument NVRAM.

1. Confirm Input OFF with the existing `input-state` command. ON or UNKNOWN
   blocks setup; no implicit OFF occurs. Explicitly disable active Transient.
2. `set-list-cc` configures the complete LIST and selects CC + LIST.
   Alternatively, `list-recall` with `{"slot":1}` loads a saved configuration
   and selects CC + LIST. An empty slot fails without changing state.
3. Optional `list-save` with `{"slot":1}` stores the configuration. Save
   requires Input OFF and disabled Transient; it does not select a mode.
4. Send existing `input-on` with `{"confirm_input_on":true}`. Core requires
   valid selected LIST, Input OFF, Transient OFF, Short OFF and no Stop request.
5. `list-trigger` with `{}` records a software trigger while selected LIST,
   Input ON, Transient OFF, Short OFF and no Stop request are confirmed.
6. Explicitly send `input-off` and confirm OFF. LIST stays selected.
7. `list-exit` with `{}` requires confirmed OFF and selected CC LIST, and
   returns to FIXED mode while retaining LIST configuration and trigger record.

Setup/Recall clear the prior LIST trigger record and replace inactive Transient
setup. They never disable active Transient. Fixed setup and Transient mutations
are refused while LIST is selected: explicitly OFF and exit first. Setup and
Recall never enable Input or trigger. No operation guesses or restores previous
fixed settings. Save is also allowed after LIST exit when its configuration is
retained. Recall is the way to reselect a saved LIST without re-entering Steps.

`list-status` returns `state_source: "simulate"`, `configured`, `selected`,
`function_mode`, resolved `range`, `slot`, `configuration`, and `last_trigger`.
`slot` is the last saved/recalled slot for that configuration, or null after
fresh setup. Before setup, configuration/range/slot/trigger are null. Trigger
records contain `sequence` (starting at 1 after setup/recall), `accepted_at_utc`
and `source: "software"`. Results are independent snapshots. Input is queried
separately. Repeated trigger requests record control commands; they make no
claim about physical restart, abort or step advancement.

Successful Trigger and Worker `job_finished` establish command completion
only. There is no waveform simulation, timer, progress/completed state or
`wait_complete`. Input OFF does not establish internal LIST-engine abort.
The controlling Orchestrator must explicitly attempt and confirm Input OFF
before Worker Stop on normal, error and user-stop workflow exits. Stop,
process exit and VISA close do not change Input or establish OFF.

Dry-run validates request data without inspecting or mutating simulation state.
Common Worker protocol 3, Manifest schema 2, single-job admission, result
envelopes, `GET /status` and Stop retain their existing semantics. See the
[Worker contract](../contracts/electronicload-worker-contract.md).

### Instrument command reference

The [IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf)
explicitly includes IT8812C. Printed pp.20–21, 40, 46 and 60–61 document
`FUNC CURR`, `FUNC:MODE LIST` / `FIX`, `LIST:RANG`, `LIST:COUN`, `LIST:STEP`,
indexed `LIST:LEV`, `LIST:SLEW`, `LIST:WID`, and `LIST:SAV` / `LIST:RCL`.
Software triggering is documented through `TRIG:SOUR BUS` + `*TRG`, or
`TRIG:IMM` (including HOLD source). `FUNC:MODE?`, `TRAN?`, `INP:STAT?` and
`INP:SHOR:STAT?` describe control states, not LIST progress/completion.

The [IT8811/12/12B/12C User Manual V2.7](https://media.digikey.com/pdf/Data%20Sheets/ITech%20Elect%20PDFs/IT8800%20User%20Manual.pdf)
printed pp.31–32 describes LIST width and panel flow; pp.40–41 gives IT8812C
current/slew specifications. The programming guide's generic width discussion
also describes values above 16383 s, outside this model's 3600 s LIST limit.
This interface uses the model-specific limit. The guide's `LIST:SLEW?`
description and memory wording contain ambiguities; this implementation does
not infer query results, NVRAM durability, or physical behavior after partial
writes, repeated triggers, Input OFF or interrupted finite/infinite execution.
Physical LIST support is limited to the separately authorized
[Trigger validation procedure](#contributor-trigger-validation).

## Trigger Setup

Transient CC/CV/CR/CW and LIST CC use one Trigger Source and Timer interval.
Configure the source with `trigger-configure --source MANUAL|BUS|HOLD|TIMER|EXTERNAL`.
For TIMER, include `--interval-s` in seconds, from 0.01 through 999.99.
Core preserves decimal input. Contributor configuration requires exact numeric
readback before reporting success. Changing a non-TIMER source retains the
stored interval. Use `trigger-status` to read both fields.

In simulation, use the same Worker for setup and subsequent commands. A new
simulation starts in HOLD with a 0.01 s stored interval so existing software
trigger workflows can start immediately. The configuration requires confirmed
Input OFF. Readback includes `source`, `interval_s` as a decimal string, and
`state_source`. Dry-run validates and previews configuration without mutation.

`transient-trigger` and `list-trigger` accept software triggering in HOLD and
BUS, with the existing dynamic-mode, Input ON and Stop guards. The contributor
path uses `TRIG:IMM` for HOLD and `*TRG` for BUS. MANUAL uses the front panel,
EXTERNAL uses the rear Trigger Input, and TIMER uses the instrument oscillator.
Software trigger requests for those three sources fail. MANUAL powered
contributor LIVE validation remains blocked before VISA access; reading or
restoring MANUAL with confirmed Input OFF remains supported. A successful software
trigger establishes command acceptance only. Input OFF remains explicit and
Worker Stop preserves the stored Trigger Setup.

Source syntax, native trigger routing and the Timer range come from printed
pp.40–41 of the [IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf).

## Protection

Use `protection-status` to read Input, settings and the live Questionable
Condition register. Add `--events` to read and consume the Questionable Event
register. Both register objects have integer `raw` and an array of known
`flags`. The event register depends on the instrument's existing transition
masks. An empty event register can coexist with an observed trip.

Use `protection-set --confirm` with one or more of these options:

| Option | Value |
| --- | --- |
| `--software-current-enabled` | `on` or `off` |
| `--software-current-limit` | Amperes, 0 through 60 |
| `--software-current-delay` | Seconds, 0 through 60 |
| `--software-power-limit` | Watts, 0 through 260 |
| `--software-power-delay` | Seconds, 0 through 60 |
| `--hardware-power-limit` | Watts, 0 through 250 |
| `--restore-defaults` | `software-power` (instrument maximum and 0 s) or `hardware-power` (instrument maximum), each used on its own |

The IT8812C profile uses separate Software Power and Hardware Power bounds.
For each power default, the contributor command reads that setting's own
instrument maximum and verifies the value after applying it. The software
current limit uses a conservative 60 A application bound. The programming
guide specifies 0 through MAX for this setting without a model-specific
software current maximum.

Status returns settings with decimal values as strings and the software current
enable field as a boolean. Set reports the requested and verified fields in
`applied`. A partial write reports `confirmed`, `unconfirmed` and
`not_attempted` and stops the sequence. Input OFF is required before each write.
Use `protection-clear --confirm` to clear latched protection with Input OFF
after removing the cause. The load may briefly restore Input ON after Clear.
If the readback shows ON, the operation fails and attempts one bounded OFF
while communication is trusted. Check the returned Input state.

```powershell
electronicload-tool protection-status --simulate --events --json
electronicload-tool protection-set --dry-run --software-power-limit 200 --confirm --json
electronicload-tool protection-set --dry-run --restore-defaults hardware-power --confirm --json
electronicload-tool protection-clear --simulate --confirm --json
```

Use a single simulated Worker to retain settings between commands. Simulation
stores settings and register state. Physical trip timing and power limiting
require the [contributor procedure](#contributor-protection-validation).
Ordinary CLI and Worker requests support simulation and dry-run only.

## Software Sequence

Create a UTF-8 JSON file with `version: 1`, an integer `loop_count` from 1 to
255, and 1 to 255 steps. At most 10,000 steps may run after expansion.
Every step has exactly `action` and `arguments`.

```json
{
  "version": 1,
  "loop_count": 1,
  "steps": [
    {"action": "set-cc", "arguments": {"current": 0.1, "range": "auto"}},
    {"action": "input-on", "arguments": {}},
    {"action": "wait", "arguments": {"seconds": 0.25}},
    {"action": "measure", "arguments": {}},
    {"action": "input-off", "arguments": {}},
    {"action": "input-state", "arguments": {}}
  ]
}
```

| Action | Arguments |
| --- | --- |
| `set-cc` | `current` in A and `range`; optional paired `rise_slew` and `fall_slew` in A/µs for offline use |
| `set-cv` | `voltage` in V and `range` |
| `set-cr` | `resistance` in Ω and `range` |
| `set-cw` | `power` in W |
| `input-on`, `input-off`, `measure`, `input-state` | Empty object `{}` |
| `wait` | Finite decimal `seconds` from 0 to 3600, inclusive |

Range is `auto`, `low` or `high`. Write setpoints and Wait as JSON numbers.
Every execution starts with an Input OFF check. A Sequence never changes a
load that starts ON. Insert a fresh Fixed setup before **every** `input-on`,
including later in the same loop, and apply setup only with Input OFF. Each
loop must end OFF. Read-only steps may follow `input-off`, and a loop with no
ON needs no explicit OFF. The entire document is checked before starting.
`fresh_fixed_setup_required` reports which ON step needs a new setup.
Sequence requires Input Timer OFF before Input ON and Current Slow Rise OFF
before Input ON in CV, CR or CW. Fixed CR setup requires CR-LED OFF. Simulation
also checks that any requested CC slew pair still matches the setup before Input ON.

```powershell
electronicload-tool sequence --file sequence.json --dry-run
electronicload-tool sequence --file sequence.json --simulate --confirm
```

The preview requires no confirmation. Runs with any `input-on` require a
new confirmation each time. Results are JSON, with one V/I/P record per
`measure` and a final verified Input state. For Worker requests, use
`arguments.document` and `arguments.confirm_input_on` as described in the
[Worker contract](../contracts/electronicload-worker-contract.md#software-sequence).

See [Software Sequence contributor validation](#contributor-software-sequence-validation) for the separately authorized physical procedure.

## Basic Workflows

Each command runs independently in the CLI or as one Worker job. Use
`--simulate` to run or `--dry-run` to preview validated arguments without
waiting, reading the simulator, or creating files. CLI runs return one JSON
object. Press Ctrl+C to interrupt a CLI run. For a separately authorized run,
the standalone CLI can execute `measure-log` and `measure-until` with
`--hardware-validation workflow` and an explicit System VISA resource.
Both verify the instrument identity and confirm Input OFF in the same session
before read-only sampling. See the
[contributor extension selection](#contributor-extension-selection).
The simulator uses a fixed 12 V source, so a discharge simulation with a
3 V cutoff ends at the timeout, capacity cutoff, or sample limit.

| Command | Required options | Optional options |
| --- | --- | --- |
| `measure-log` | `--interval-s`, exactly one of `--count` or `--duration-s` | `--csv` |
| `measure-until` | `--interval-s`, `--metric`, `--operator`, `--threshold`, `--timeout-s` | `--csv` |
| `cc-timed-load` | `--current`, `--duration-s`, `--confirm` when simulating | `--range` |
| `cc-discharge` | `--current`, `--cutoff-voltage`, `--timeout-s`, `--confirm` when simulating | `--interval-s` (defaults to 1 s), `--cutoff-capacity-ah`, `--range`, `--csv` |

```powershell
electronicload-tool measure-log --simulate --interval-s 1 --count 60
electronicload-tool measure-log --simulate --interval-s 1 --duration-s 60
electronicload-tool measure-until --simulate --interval-s 1 --metric voltage --operator le --threshold 4.5 --timeout-s 6
electronicload-tool cc-timed-load --simulate --current 0.5 --duration-s 30 --confirm
electronicload-tool cc-discharge --dry-run --current 0.5 --cutoff-voltage 3 --cutoff-capacity-ah 1 --timeout-s 7300
```

- The measurement commands preserve Input state. `measure-log` reads voltage,
  current and power. `measure-until` reads one metric (`voltage`, `current`
  or `power`) using `lt`, `le`, `gt` or `ge`. The threshold uses V, A or W
  for the selected metric. The run ends when the condition is met.
- Sampling starts immediately and skips missed ticks. Intervals accept
  0.001 to 3600 seconds. Durations and timeouts accept positive, finite
  seconds with no fixed maximum. `measure-log --count` accepts 1 to 10,000
  and returns `completed` at the requested count. In duration mode it samples
  strictly before the deadline and returns `duration_elapsed` if the deadline
  comes first. A monitoring timeout returns `workflow_timeout` with the
  available summary. If a sampling workflow reaches 10,000 actual samples
  before another stop condition, it succeeds with `sample_limit_reached`.
  Discharge still runs Input OFF cleanup at the sample limit. Timed load
  waits for the requested duration without sampling.
- CC runs require initial Input OFF and verify Fixed CC settings before
  enabling Input. Current must be positive and valid for the selected range
  (`auto` by default, or `low` or `high`). Timed load waits the requested
  duration after ON confirmation. Discharge reads the initial voltage with
  Input OFF and ends without enabling Input if that voltage is at or below
  the cutoff. After ON confirmation, discharge reads voltage, current and
  the latest available power in sequence. It stops at or below the positive
  voltage cutoff in V, or at or above the optional positive capacity cutoff
  in Ah. The voltage cutoff ends sampling before the current and power
  queries. The capacity cutoff ends sampling before the power query. Ah is
  integrated between consecutive current readings using their own timestamps.
  The first sample starts at 0 Ah.
- CC runs verify Input OFF at the end. If OFF cannot be confirmed, the
  result instructs the operator to independently switch off the load.
  Timed load returns a summary. The three sampling commands also report
  `sample_count` and `last_measurement`. Discharge includes `capacity_ah`
  and the preflight `initial_voltage_v` when read. An initial cutoff has
  zero load samples and zero Ah. A cutoff sample includes only the readings
  collected for that sample in `last_measurement`.
- Only an explicit `--csv` creates a CSV. Relative paths resolve from the
  running process's working directory. Missing parent directories are created,
  and existing files are preserved. CSV uses `sample_index`, `elapsed_s`
  and the measured columns, plus `capacity_ah` for discharge. Unmeasured
  columns in a partial sample are blank. Discharge CSV rows are exported
  after the OFF cleanup path completes. If the run reports unconfirmed OFF
  cleanup, the file retains only its header and the result reports
  `csv_incomplete`. Other failures after creating the file also retain it
  and report `csv_incomplete`.

For Worker argument names, results and Stop behavior, see the
[Worker contract](../contracts/electronicload-worker-contract.md#basic-workflows).

## Worker and Automation

Standalone simulation commands each start with fresh state. To exercise a CV/CR/CW
setup followed by simulated ON/OFF, send the commands to the same simulated
Worker, with `confirm_input_on: true` on the ON request.

A Worker prints Common v3 JSONL on stdout and offers local `GET /status`,
`POST /command`, `POST /stop` and `POST /cancel` on numeric loopback only.
It runs one async job at a time and reports completion via `job_finished`
and `last_job`. Only `trace-start` supports job cancellation. Use
`input-off` for independent Input control. Active CC workflows own their
Input OFF cleanup. For domain argument shapes and
permissions, see [Electronic Load Worker Contract](../contracts/electronicload-worker-contract.md).


## Examples

For a hardware-free starting point, use simulation for results and dry-run for
query or configuration previews. Standalone simulated invocations do not share
state; use one simulated Worker for multi-command control sequences.

```powershell
electronicload-tool identify --simulate --json
electronicload-tool error --all --simulate --json
electronicload-tool set-cc --current 0.1 --range low --dry-run --json
```

For normal USB diagnostics, select the resource explicitly using the
[discovery workflow](#visa-resource-discovery), then use the [System Queries](#system-queries)
examples. For ASRL, see the [Contributor connection and preview](#asrl-contributor-validation).
Function-specific command examples stay with their reference sections so
parameters, interlocks and results can be read together.

## Test

Follow [Testing Guidelines](../testing-guidelines.md). Run the narrowest
relevant hardware-free tests from the repository root; the test harness blocks
real VISA. Do not use physical tests to replace failed offline checks. The
scripts below distinguish offline preflight, plan-only and separately approved
physical validation.

## CLI Validation Scripts

Run these PowerShell scripts from the repository root. Offline validation
does not authorize physical instrument operation.

| Script | Purpose | Real hardware |
| --- | --- | --- |
| `scripts/preflight-cli.ps1` | Offline CLI, simulator, refusal and Manifest checks. Focused switches include `-BaseOnly`, `-SystemQueryOnly`, `-ReadbackOnly`, `-ProtectionOnly` and `-SequenceOnly` | Never |
| `scripts/live-cli-check.ps1 -PlanOnly` | Validate command plans; reported physical cases remain `NOT RUN` | Never |
| `scripts/live-cli-check.ps1` | Separately approved interactive IT8812C validation, including deliberate Input ON/OFF | With operator approval |

### Available validation modes

`-Modes full` selects every mode in the table. Omitting `-Modes` also selects
FULL. A single name or a comma-separated list such as `cc,readback` selects
each named mode once. USB and ASRL use the same modes and acceptance checks.
`-InputTimerOnly` is a shortcut for `-Modes input-timer` and cannot be combined
with `-Modes`. Hardware selections first identify the instrument, confirm
Input OFF and measure V/I/P. Any selection containing TRACE, Trigger or legacy
Transient saves Trigger Source and Timer Interval before changes. TRACE verifies
HOLD; legacy Transient and Trigger retain their existing required sources.
The original settings are restored once after the selected connected trials,
before Average finalization or isolated settings trials, when safe. Selections
that do not need this shared lifecycle do not add these writes.

| `-Modes` | FULL coverage |
| --- | --- |
| `cc` | Fixed CC High, Auto, Low and one Input ON/OFF |
| `cv` | Fixed CV Low, Auto, High |
| `cr` | Fixed CR Low, Auto, High |
| `cw` | Fixed CW |
| `transient-cc` | Continuous, Pulse, Toggle with Low/High/Auto |
| `transient-cv` | Continuous Auto, Pulse Low, Toggle High |
| `transient-cr` | Continuous Auto, Pulse High, Toggle High |
| `transient-cw` | Continuous, Pulse, Toggle |
| `trace` | Five captures, including powered CC and Full CSV |
| `cr-led` | CR-LED, plain CR and return to CC with Input OFF |
| `current-slow-rise` | Fixed CC enable, retention, interlock and disable |
| `trigger` | HOLD, TIMER, BUS and EXTERNAL with Transient CC, finite LIST CC and setting restoration |
| `input-timer` | Fixed CC timer, expiry and disable |
| `protection` | Current and power trips, hardware power limit and defaults |
| `system-query` | SCPI version, display mode and one Error Queue entry |
| `readback` | Automatic Fixed CC/CV/CR/CW, CC Slew, Transient, LIST, VON, Short and Average settings/readback/restoration |
| `sequence` | Four bounded Fixed CC/CV/CR/CW trials |
| `workflow` | Contributor live measurement and bounded CC Timed Load/Discharge trials |
| `measurement-extensions` | Native Min/Max, Discharge and Average setting |
| `auxiliary-controls` | VON setting, Short ON/OFF interlocks and Fixed CC Slew |
| `list-cc` | LIST CC setup, Save, Recall and Exit |

The System Query case reads one Error Queue entry and records the numeric
code, including zero for an empty queue. Every selected mode uses its hardware
validation procedure. `-PlanOnly` validates the CLI requests without VISA
and leaves hardware cases `NOT RUN`; it does not execute simulation or pytest
as a substitute for physical validation. Use the separate offline CLI or
preflight/test commands for offline verification.

Without Expected parameters, Readback uses the isolated settings batch to
automatically select functions, exercise known settings, compare independent
readbacks and verify restoration. No panel values or repeated manual function
selection are required. For explicit read-only diagnostics with known panel
settings, use `-ExpectedRiseSlew` and `-ExpectedFallSlew` together (A/µs),
`-ExpectedVonThreshold` (V) and `-ExpectedVonLatch` (`on` or `off`) together,
`-ExpectedShort` (`on` or `off`), and `-ExpectedAverageCount` (2–16).
Incomplete baselines leave the case `NOT RUN` with observed values and the
overall result `OBSERVED ONLY`. Fixed, Transient, Trigger and TRACE also keep
their own post-setup comparisons.

### Contributor extension selection

Select Readback, any extension mode or `-Modes full` to request the
corresponding contributor hardware cases. Provide an explicit `-Resource`
and matching `-Connection`. Use `-PlanOnly` to review CLI operations and
the extension SCPI previews without opening VISA. Execution requires the
interactive physical validation procedure.
The hardware case results carry `execution: live`.

With Input OFF, `workflow` verifies two scheduled measurement samples and
completion of a voltage condition at 1 V or above. The two CC cases establish
Fixed CC Low with a dedicated 5 V source limited to 0.8 A and use a 0.1 A
setpoint. Timed Load requests OFF at the five-second boundary without adding queries after that boundary. Discharge samples every
0.5 seconds and must reach 0.00010 Ah. It stops on a voltage of 4.5 V or lower
or after 6 seconds if capacity is still below the target. Timing starts at
the SCPI `INP ON` write. Before the initial Enter, prepare an independent
hardware cutoff for both load trials so that load time, including OFF confirmation,
cannot exceed 10 seconds from each actual write. It must be ready or rearmed
automatically as needed for every trial without a repeated readiness prompt.
Cancel if this cannot be ensured. If communication is lost, use that
cutoff immediately. Skip physical execution if the site cannot enforce this
limit. Timed Load automatically samples instrument current and Input state
before the five-second OFF schedule and checks the current band (0.09–0.11 A),
sample coverage and integer-nanosecond OFF timestamps. Discharge checks current
during capacity sampling. Timed Load uses bounded functional evidence: consecutive current
and Input observations and the final current query start to OFF write must
span at most one second each, retaining the existing initial ON confirmation
allowance. A valid schedule and bounded samples pass functional validation,
not continuous loading calibration. Observed early loss or insufficient
coverage fails; no manual observation is requested.
Do not manually intervene or allow the independent cutoff to fire during
the expected observation except for safety. The CC trials leave Fixed CC at 0.1 A with
Input OFF.

`measurement-extensions` queries native Min/Max and Discharge values and
verifies Average set to 8. The Average case leaves that setting after operator
confirmation. When combined with source-connected trials, its setting case runs
after those trials (including Trigger), before the isolated settings batch.

For every selection containing source-connected trials, the script reads the
original measurement Average count privately after the common prechecks and
initial diagnostic readbacks. With Input OFF it sets and verifies Average 2
before those trials, regardless of the incoming count. The initial operator
authorization covers this setting. On successful completion of the
source-connected trials it restores and verifies the original count, unless
Measurement Extensions is selected: that mode's final Average 8 case retains
its existing leave-at-8 behavior. A failed or interrupted run does not open
another VISA session just to recover Average; independently confirm Input OFF
and safe communication before using the private recovery baseline. The
shareable plan uses an original-value placeholder, not the private count.
PlanOnly and noninteractive refusal perform no hardware I/O. Pure diagnostic
and isolation-only selections do not change Average except in their explicitly
selected Average test. The baseline does not change Product/Worker LIVE,
physical timing, current tolerances, safety interlocks or TRACE's own filter.

For `auxiliary-controls` and `list-cc`, physically disconnect the source and
DUT. Standalone trials require Fixed CC with Input, Short, Transient and Input
Timer OFF. They do not switch the starting function or mode; other starting
states are refused before settings writes.
VON and same-range Slew save the original values, verify test readback, then
restore and verify the original values. The Short trial checks ON and OFF
state with isolated terminals and verifies Input OFF. LIST uses different
first-step widths for A and B. For isolation-only runs, complete the physical
disconnection before the initial Enter. If LIST is selected, that Enter also
authorizes overwriting the displayed scratch slot; cancel if it must be
preserved. There is no separate typed authorization. The trial saves B to `-ListScratchSlot`,
changes the working buffer to A, and verifies Recall restores B. It then saves A,
changes the working buffer to B, and verifies Recall restores A. Both comparisons
cover the complete LIST configuration. No manually supplied prior slot width is
required. The slot retains A after a successful trial.

Before writing, the trial displays a private recovery baseline, including
the original working LIST and Fixed CC settings for LIST. Keep the terminal
record private. The standalone CLI sends the baseline to stderr and the
script keeps it out of shareable reports, including untouched values in successful automatic settings readbacks. The LIST trial returns to Fixed CC
and verifies the original working buffer. Configuration writes require a
restorable baseline. If restoration cannot be verified after a write, keep
the terminals isolated and use the recorded baseline for recovery at the
instrument.

The script runs source-connected trials (including Trigger and Protection)
before the isolated settings batch. Only mixed or Full selections with powered
trials prompt once at the actual transition to physical isolation. The operator
must disconnect both source and DUT before continuing; slot authorization
was already given at the initial Enter. Isolation-only selections proceed
without another prompt. Automatic Readback needs no panel values
or manual function selection. With Input OFF it exercises known values and
restores the original settings and function, including LIST without saving a
slot. An enabled Transient starting state is supported when its original
settings and HOLD source can be verified and restored; other unsafe or
unrestorable states are refused before writing. Only the automatic Readback
procedure authorizes that temporary Transient change, including its VON,
Slew and Short readbacks. Standalone Auxiliary Controls and LIST CC retain
the original Fixed CC and Transient OFF guards. Mixed runs must meet the
same precondition before standalone trials; Readback evidence may be reused
for the shared Auxiliary cases. When a preceding powered trial leaves a
non-CC Fixed function, the mixed plan includes an explicit isolated Fixed CC
handoff before the first standalone trial, without changing setpoints. It
requires Fixed mode and the existing interlocks OFF; it is not a standalone
mode-switch permission. Only the scratch-slot trial has a 300-second
command allowance; automatic LIST Readback retains the 30-second command limit.
Both LIST trials check the actual remaining command budget against the buffer
length, configured query delay, observed snapshot cost and restoration reserve
before writing. Large buffers or slow exchanges may be refused without settings
writes. A later unexpected transport stall still requires the existing manual
recovery procedure; time budgeting is not a hardware guarantee. Explicit
legacy Expected parameters request read-only diagnostics, not automatic
Readback acceptance.

### Script parameters

| Parameter | Required / default | Purpose |
| --- | --- | --- |
| `-Target` | Required: `itech-it8812c` | Exact registered model ID |
| `-Resource` | Required, including PlanOnly | Explicit USB INSTR or ASRL INSTR resource matching `-Connection` |
| `-Connection` | `usb` | `usb` for USB INSTR or `asrl` for RS-232 ASRL INSTR |
| `-Backend` | `system` | System VISA (`@ivi`) only |
| `-Modes` | `full` | FULL, a single mode or a comma-separated selection |
| `-ListScratchSlot` | 1 | LIST test slot (1–7), overwrite authorized at initial Enter |
| `-InputTimerOnly` | Off | Backward-compatible Timer-only shortcut, incompatible with `-Modes` |
| `-SourceVoltageLimitV` | **5 V** | Operator-declared external source voltage, override >0 to 18 V |
| `-SourceCurrentLimitA` | **0.8 A** | Operator-declared external source current limit, override >0 to 2 A |
| `-PlanOnly` | Off | No VISA or physical hardware access |
| `-DebugArtifacts` | Off | Additionally retain raw private diagnostics |
| `-Verbose` | Off | Print individual planned CLI steps and arguments |

Both Source parameters are optional. Protection, Sequence and Contributor
Workflow load trials require the standard values. The standard external supply setup
is **5 V with a 0.8 A current limit** (nominal 4 W envelope). The script
**does not set, query or verify** this supply. The operator must configure
and verify it, along with wiring and the DUT, before a physical run. A declared
source limit is not a measured value or a guarantee of DUT safety.

| Mode-specific parameter | Default | Units / use |
| --- | --- | --- |
| `-Current` | `0.1` | A; Fixed CC, powered TRACE, Timer, CR-LED exit and Current Slow Rise; positive, ≤2 A and ≤declared source current limit |
| `-Voltage` | `3` | V; Fixed CV |
| `-ResistanceLow` / `-ResistanceHigh` | `10` / `1000` | Ω; Fixed CR |
| `-Power` | `1` | W; Fixed CW |
| `-LevelA` / `-LevelB` | `0.1` / `0.2` | A; Transient CC, ≤2 A and ≤declared source current limit |
| `-VoltageLevelA` / `-VoltageLevelB` | `3` / `4` | V; Transient CV |
| `-ResistanceLevelA` / `-ResistanceLevelB` | `10` / `1000` | Ω; Transient CR |
| `-PowerLevelA` / `-PowerLevelB` | `1` / `2` | W; Transient CW |
| `-RiseSlew` / `-FallSlew` | `0.1` / `0.1` | A/µs, Transient CC; Auto High trial uses 0.5 A/µs |
| `-WidthA` / `-WidthB` | `0.001` / `0.002` | Seconds, Continuous |
| `-PulseWidth` | `0.001` | Seconds, Pulse |
| `-TimerSeconds` | `5` | Input Timer seconds (5 for LIVE, integer 3–10 for PlanOnly) |
| `-TriggerObservationSeconds` / `-TriggerMaxOnSeconds` | `15` / `60` | Seconds per Trigger trial; includes explicit OFF deadline |
| `-TriggerIntervalSeconds` | `5` | Seconds, Trigger TIMER source |
| `-TriggerListWidthA` / `-TriggerListWidthB` | `2` / `2` | Seconds, finite LIST dwell |
| `-TriggerListRepeat` | `1` | Finite LIST repetitions |
| `-TriggerExternalSetup` | Optional | Extra external trigger wiring notes reviewed before the initial Enter and at the EXTERNAL readiness confirmation |

These are **validation script defaults**, not changes to Core or Product LIVE
permissions. At 5 V, 10 Ω nominally implies 0.5 A, and 2 W implies 0.4 A;
actual load behavior depends on source limit, load mode, wiring and DUT.
Core still validates exact model range/resolution and resolves Auto range.
This procedure does not establish measurement or waveform accuracy.

### Validation quick start

Set a **fictional** USB resource for offline previews. For an approved
physical trial, replace it with the exact operator-selected USB INSTR
Resource. This environment variable is a PowerShell convenience; the script
only uses the explicit `-Resource` argument.

```powershell
$env:ELECTRONICLOAD_TOOL_RESOURCE = "USB0::...::INSTR"

# Preview the full matrix without opening VISA.
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -PlanOnly

# Preview selected modes.
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Modes cc,cv -PlanOnly

# Preview TRACE or Timer separately.
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Modes trace -PlanOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -InputTimerOnly -PlanOnly
```

After reviewing the plan and separately approving the physical setup, use
an interactive Windows console with the **real** Resource and remove
`-PlanOnly`. For example, Fixed CC only:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Connection usb -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Backend system -Modes cc
```

## ASRL contributor validation

Use `-Connection asrl`, an explicit `ASRL<number>::INSTR` resource and System
VISA (`@ivi`) with the same FULL or focused matrix as USB. Standalone contributor
commands use `--validation-connection asrl` alongside any command-specific
`--hardware-validation` scope. The original three read-only CLI commands also
accept their existing `--hardware-validation asrl-readonly` option.
The following serial overrides are optional. Omitted values come from VISA
and PyVISA. The shareable JSON records explicitly supplied values in
`serial_overrides` and redacts the VISA resource.

| CLI | PowerShell | Value |
| --- | --- | --- |
| `--serial-baud-rate` | `-SerialBaudRate` | Positive 32-bit unsigned integer |
| `--serial-data-bits` | `-SerialDataBits` | 5–8 |
| `--serial-parity` | `-SerialParity` | `none`, `odd`, `even`, `mark`, `space` |
| `--serial-stop-bits` | `-SerialStopBits` | `1`, `1.5`, `2` |
| `--serial-flow-control` | `-SerialFlowControl` | `none`, `xon_xoff`, `rts_cts`, `dtr_dsr` |
| `--serial-read-termination` | `-SerialReadTermination` | `CR`, `LF`, `CRLF` |
| `--serial-write-termination` | `-SerialWriteTermination` | `CR`, `LF`, `CRLF` |

Use a fictional resource for the offline preview. Both examples below use
System VISA and leave the serial settings as provided by the session.

```powershell
$resource = 'ASRL999::INSTR'
.\.venv\Scripts\electronicload-tool.exe identify --resource $resource --validation-connection asrl --dry-run --json
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Connection asrl -Resource $resource -Modes full -PlanOnly
```

### Physical validation procedure

1. Obtain separate authorization for the instrument, resource and bounded
   source/DUT. Verify RS-232 wiring and both ends' serial settings, confirm
   physical Input OFF, and prepare an independent manual OFF procedure.
2. Review FULL or focused cases in `-PlanOnly`. Use the exact authorized ASRL
   INSTR resource in an interactive console for the approved physical run.
   Apply the same scope-specific confirmations and acceptance rules as USB.
3. Review redacted reports under `.tmp_tests/`. ASRL Product and Worker LIVE
   require separate physical evidence for each scope being opened. Complete
   conditional Readback coverage with focused runs as needed. Record
   instrument evidence separately from simulated and planned cases.

See the [VISA connections and LIVE support](../../README.md#visa-connections-and-live-support).

## Controlled instrument validation

Before any live VISA operation, the script validates the selected CLI plans,
runs the applicable offline preflight checks, displays the selected Resource
for operator verification, and displays all selected-mode preparation and
authorization conditions before the **initial interactive Enter confirmation**
(Ctrl+C cancels). After that Enter, settings, readbacks, comparisons and
cleanup are automatic. Additional confirmations are limited to physical source
disconnection for a mixed isolated batch, Protection source isolation/reconnection,
and EXTERNAL Trigger equipment readiness with Input OFF immediately before its
trial. Apply the physical TTL pulse only after the capture-ready message.
Timer and Workflow require a cutoff ready at each actual ON write without
a repeated readiness prompt; cancel if the physical setup cannot enforce
the existing deadline. A selection containing only Workflow,
Measurement Extensions, Auxiliary Controls or LIST CC has no separate
preflight invocation; the report leaves that check `NOT RUN`. All selected
CLI plans are still validated. Noninteractive execution and redirected stdin
are blocked.

| Check before Enter | Operator action |
| --- | --- |
| Target, Resource, backend and modes | Correct instrument; no other process controlling the device |
| External source (**5 V / 0.8 A** by default, only when selected modes include source-connected trials) | For those trials, manually set and verify voltage and current limit |
| Load Input, wiring and DUT | Physically OFF and safe for every selected setting |
| Manual OFF contingency | Independent means of making the circuit safe if communications fail |

Use `-Verbose` or the report to review all setpoints before physical testing.
Powered Fixed and Transient trials configure with Input OFF, explicitly
command and confirm Input ON, measure, then command and independently verify OFF.
Transient also enables, triggers twice, checks status, then disables after
OFF. TRACE runs four Input OFF captures and one powered Fixed CC capture
with a Full CSV. Timer uses its own bounded CC case. Fixed Auto adds only
Input OFF configuration and readback. CR-LED and Current Slow Rise trials
keep Input OFF throughout. The Slow Rise trial requires a non-resistance
starting function and checks that the Fixed CV setup request is refused
while Slow Rise is enabled. An unexpected success stops the run.

Failures stop further normal cases. If ON may have been attempted, the script
makes bounded explicit OFF cleanup and confirms it only by physical readback.
Transient Disable requires confirmed OFF. An **untrusted TRACE VISA session**
must not be blindly reconnected for OFF; if OFF is unconfirmed, independent
operator action is required. After a Slow Rise Enable attempt without normal
OFF readback, the script attempts one bounded Disable and status check only
with trusted communication and a fresh Input OFF readback. It retains the
trial failure and reports when independent operator action is required.
Protection Clear and the single System Query Error Queue read belong to
their selected cases. VISA Close alone does not imply OFF.

| Existing deadline | Value |
| --- | --- |
| Each CLI command | 30 s |
| TRACE Start | 120 s |
| CC-only total I/O | 240 s |
| Any other mode total I/O | 900 s |
| Cleanup commands | Separate 30 s allowance |

FULL uses a 900-second total deadline. Slow hardware or operator observations may consume that budget. Select a smaller `-Modes` set if necessary. V/I/P are separate queries, with no guarantee
of power freshness, synchronization, waveform timing or accuracy. This
contributor-only CLI validation **does not** enable Worker LIVE or expand
Product LIVE support.

### System Query validation

For a separately approved physical validation, select `-Modes system-query`
or run each query manually with `--hardware-validation system-query`, an
operator-supplied USB or ASRL INSTR resource and System VISA. Confirm physical
Input OFF before querying. Use the explicit ASRL connection option for RS-232.
The CLI verifies IT8812C identity before sending any System Query. The script
reads one Error Queue entry; choose `--all` only if bounded consumption is intended.

```powershell
electronicload-tool scpi-version --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --backend system --hardware-validation system-query --json
```

The validation mode is retained independently of ordinary USB CLI permission.

### Contributor-only TRACE hardware validation

Normal TRACE Product LIVE and Worker LIVE remain **disabled**. For a separately
approved, interactive instrument trial, the existing CLI has a narrow
`--hardware-validation trace` scope on `trace-configure` and `trace-start`.
This explicit scope uses the exact operator-supplied USB or ASRL INSTR Resource with
System VISA, validates the exact IT8812C identity, and follows the same
Configure/Start prechecks, single trigger, timeout and CSV rules as Offline TRACE.
The result includes `validation_only: true`, `validation_scope: "trace"` and
VISA runtime evidence. The legacy LIVE override does not enable TRACE.
`--hardware-validation trace --dry-run` previews arguments without opening VISA
or creating CSV, and never counts as physical success. Do not use these CLI
commands as unattended or ordinary load-operation APIs.

The single controlled script `scripts/live-cli-check.ps1 -Modes trace` saves the
original Trigger Source and Timer Interval with Input OFF, sets and verifies
HOLD without changing the stored interval, and performs five short trials: VOLT 10 points / 1 s OFF, CURR 10 / 1 s OFF, TWO 40 / 1 s
OFF, VOLT 10 / 1 ms OFF, and TWO 10 / 1 s with explicitly confirmed bounded
Fixed CC Input ON. The last trial writes a new Full CSV with estimated elapsed
and delta columns, then explicitly commands and confirms Input OFF. This is
not waveform calibration, an exhaustive interval test, or Worker LIVE validation.
TRACE Start alone has a 120-second script process deadline; other commands
keep their existing 30-second budget and the total run stays bounded.
The script restores and verifies the original Trigger Source and Timer Interval
with trusted communication and confirmed Input OFF, including after a safely
recoverable trial failure. A failed run stays FAIL. An untrusted VISA Session,
unconfirmed Feed stop or uncertain Input state blocks Trigger restoration and
requires independent operator recovery. The script never reconnects to an
untrusted VISA Session to attempt blind Input OFF.

Use an explicit operator-selected real Resource only after separately approving
the bounded source/DUT, manual OFF contingency and interactive confirmation:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Modes trace -PlanOnly
# For a separately authorized interactive physical run, remove -PlanOnly.
```

`-Modes trace` selects only TRACE. Omitting `-Modes` selects the default validation modes, including TRACE.
TRACE-only runs use `preflight-cli.ps1 -TraceOnly` for focused, no-hardware
preflight. `-PlanOnly` produces cases marked NOT RUN, not physical PASS.
The existing redacted `shareable/report.json` records TRACE case settings,
sample counts, CSV completeness and unconfirmed safety flags. `summary.md`
provides concise per-case statuses and totals;
raw samples and the CSV are held only in the run's ignored `.tmp_tests/`
folder outside `shareable/`. The script requires the original interactive
operator gate and explicit Input OFF readback. If communications become
untrusted or a process deadline expires, the report marks safe shutdown
unconfirmed and requires an independent manual safety procedure.

### Controlled Timer validation

Use the existing script's `-InputTimerOnly` shortcut (without `-Modes`), or
select `-Modes input-timer`. Input Timer is included in the default all-mode
run. Preview with a fictional USB resource for offline checks; supply an
approved resource only for a separately authorized physical run:

```powershell
.\scripts\preflight-cli.ps1 -InputTimerOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -InputTimerOnly -Current 0.1 -TimerSeconds 5 -PlanOnly
```

PlanOnly accepts 3–10 seconds. The physical trial uses 5 seconds and positive
CC up to 2 A (default 0.1 A), valid for both CC ranges. The script defaults to an
operator-managed 5 V / 0.8 A source (overrides: >0 to 18 V / >0 to 2 A).
The operator must configure and verify the source before physical testing.
It retains the common approved setup/manual OFF contingency, interactive
confirmation, runtime checks, process deadlines, redaction and explicit OFF
cleanup requirements described below. Use an independently armed hardware
cutoff that limits actual loading to at most 10 seconds. Its clock must start
no later than the SCPI Input ON write, not the initial Enter. Prepare the
cutoff and confirm the approved current-limited source at the initial
confirmation; no additional Timer readiness prompt is needed. Cancel if
the cutoff cannot be ready at the actual write. The 10-second cutoff
applies to this 5-second trial under its approved conditions.

The planned physical trial confirms Input OFF, explicitly disables Timer,
sets low-range Fixed CC, configures/enables Timer, reads status while OFF,
then confirms Input ON through `--hardware-validation input-timer` with complete
expected `current`, `range` and integer `seconds`. Before and after remote
transition Core rechecks physical OFF, CC settings, Short/Transient OFF and Timer
readback. Core signals the SCPI ON write start before waiting for the instrument,
so the same six-second deadline covers the ON subprocess and observation.
Immediately after ON confirmation, a single read-only CLI process opens and
identifies one VISA session and collects Timer/Input snapshots until the first OFF
or the observation deadline. It does not reopen VISA or restart Python for each sample. The script requires a timed ON observation followed
by the first OFF within six seconds of the write start. The conservative
bracket from the ON query start to the OFF query completion must be at most
one second and must not end before the five-second boundary. A definite
early OFF, absent ON evidence or a wider bracket fails; later OFF queries
cannot replace the first OFF. This is bounded functional acceptance, not
exact timing calibration. Each poll respects the remaining
observation window. When the window closes without a valid observation, the
script fails and begins OFF cleanup. If the CLI process is still running at
the deadline, the script prompts you to use the independent hardware cutoff
immediately, before it waits for the process to exit. VISA cleanup requires that
the process has exited and the existing communication rules permit it. A
communication failure in a run that includes Trigger requires independent
operator action. The CLI records the write, ON confirmation and Input query
times. `-DebugArtifacts`
saves these observations privately. The script sends and verifies explicit
OFF, disables Timer separately and reads the final state. Acceptance is automatic
from the timed ON/OFF evidence and verified cleanup. The operator must ensure
the independent cutoff cannot intervene in the observation window and must not
intervene except for safety; sampled evidence does not independently prove causation.
A possible ON attempt gets one bounded OFF cleanup
attempt under the existing communication rules. Timer-disable cleanup follows
confirmed OFF.

The validation-only Core/CLI scope uses `INP:TIM:DEL <seconds>`, `INP:TIM 0|1`,
`INP:TIM:DEL?` and `INP:TIM?` from the
[IT8800 Programming Guide V2.1, printed pp.45–46](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf),
which lists IT8812C. The DELay syntax line omits `:DELay`; its example and query
include it. Readback must match. The trial reports the observed Enabled value
after expiry without assuming retention, and does not establish timing accuracy,
VON interaction, CV/CR/CW physical compatibility or Worker LIVE behavior.
PlanOnly leaves every physical case NOT RUN and cannot promote Product LIVE.

### Contributor Transient validation

The CLI-only `--hardware-validation transient-cc|transient-cv|transient-cr|transient-cw`
route follows the [controlled validation procedure](#controlled-instrument-validation).
It requires separate physical authorization, exact identity/USB/System VISA,
Input OFF checks, complete expected settings/readback and deliberate ON
confirmation. It grants no normal Product or Worker LIVE permission.

The existing `preflight-cli.ps1 -TransientOnly` covers all four functions offline.
`live-cli-check.ps1` defaults to FULL;
`-Modes` retains subset selection. `-PlanOnly` validates each request without
VISA and reports physical cases as NOT RUN. New Transient trial defaults are
`-VoltageLevelA 3`, `-VoltageLevelB 4` (V), `-ResistanceLevelA 10`,
`-ResistanceLevelB 1000` (Ω), `-PowerLevelA 1`, `-PowerLevelB 2` (W).
The existing width parameters are shared; CC keeps its separate levels/slew.
CV uses Continuous Auto / Pulse Low / Toggle High; CR uses Continuous Auto /
Pulse High / Toggle High; CW has all three without Range or slew.
Auto expected ranges come from Core. Real execution retains explicit Resource,
interactive confirmation, default 0.8 A / 5 V declared source limits,
manual OFF contingency and private/redacted reporting. Cleanup uses the
active Transient scope and verifies OFF before Disable. The unchanged 30-second
command and 900-second total budgets apply; the default 192 invocations allow
about 4.7 seconds each on average. Slow hardware may require selected subsets;
no larger deadline is assumed.

The [IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf)
includes IT8812C. Printed pages 19–20, 48 and 50–58 define Transient controls:
`FUNC:MODE FIX`, the selected `FUNC CURR|VOLT|RES|POW`, function-specific
`:TRAN:MODE`, `:TRAN:ALEV`, `:TRAN:BLEV`, `:TRAN:AWID/BWID`, separate `TRAN ON/OFF`,
`TRIG:SOUR HOLD` and `TRIG:IMM`. Pulse uses AWID; Toggle writes neither width.
CV disables hardware automatic range and selects 18/120 V; CR selects
10/7500 Ω. Unrecognized readback fails closed. CW writes no range or slew.
Manual syntax and offline/fake tests do not certify physical acceptance.

### Contributor Software Sequence validation

For separately authorized physical runs, select `-Modes sequence` or include
Sequence in a combined run with a dedicated 5 V / 0.8 A source. The script uses
`sequence --file <file> --hardware-validation sequence --resource <USB-INSTR> --backend system --confirm --json`
with the registered IT8812C over System VISA. For ASRL, use the explicit
[contributor transport option](#asrl-contributor-validation). Each trial uses one session.
Fixed setup and Input switching verify the applicable interlocks and settings.
Use an exclusive instrument and an independent manual OFF method. Ordinary CLI
and Worker LIVE are closed for Sequence.

The contributor scope accepts only the four fixed documents, for 0.1 A CC,
3 V CV, 1000 Ω CR and 0.10 W CW. Each has one ON, a 0.25 s Wait, measurement,
and verified OFF. The initial Enter authorizes the four-trial Sequence batch
without another operator prompt, retaining per-request CLI confirmation, a 30 s command deadline
and a 900 s total deadline. The Core handles
trusted cleanup on the existing session. The CLI directs the operator to
switch off the load independently if a Remote transition during setup or Input ON
ends without a fresh OFF readback, or if a run fails with Input observed ON
after the initial OFF check succeeded. Once the instrument is identified,
loss of trusted communication stops further commands. The CLI reports Input
`UNKNOWN`, records `session_untrusted` as the cleanup reason, preserves the
original error, and gives the same independent OFF instructions. The script
also gives independent OFF instructions if the process times out or Input OFF
is unconfirmed. Cleanup stays within the original session.

### Contributor Protection validation

Use the separately approved `live-cli-check.ps1 -Modes protection` procedure
with the exact IT8812C model, an operator-supplied USB or ASRL INSTR resource
and System VISA. Add `-PlanOnly` for the offline command plan. Start with
Input OFF, a dedicated source set at the source to 5 V with a 0.8 A current
limit, and an independent manual cutoff. The script checks current mode and
interlocks, saves and restores the original Fixed CC and protection settings,
and stops at the first failed trial. The temporary Fixed CC setpoint is 0.20 A.
The initial Enter authorizes the powered trials with the dedicated source
ready. The script keeps the individual CLI confirmation gates. After a
software trip requires physical source isolation, the operator reconnects
and energizes the same source at the next powered trial's prompt. No
additional confirmation is requested while the physical setup is unchanged.

The script reuses an available independent Readback snapshot only if no intervening mode-changing Workflow ran; otherwise it reads the current state. From Fixed CV
or CW it confirms Input and Short are both OFF, prepares Fixed CC at 0.20 A Low,
and independently verifies the settings with Input OFF before taking the first
Protection snapshot. The prepared Fixed CC settings become the trial restore
baseline. From Fixed CC the initial settings are the baseline. Setup writes are
skipped when the settings already match the trial. If starting in another mode,
select Fixed CC at the panel before beginning validation.

| Trial | Temporary setting | Physical acceptance |
| --- | --- | --- |
| Software current | Enabled, 0.10 A, 1 s | OC and PS with Input OFF |
| Software power | 0.50 W, 1 s | OP and PS with Input OFF, after exposure above 0.50 W |
| Hardware power | 0.50 W | With Input ON and no PS, compare the baseline at approximately 0.20 A with V×I at 0.45–0.55 W under the limit. Both observations require 4.75–5.25 V and explicit OFF. |

For Hardware Power, first measure the baseline with the saved protection settings.
The script requires 0.18–0.22 A at the load during the baseline to establish that
the source and wiring sustain the intended current. After confirming Input OFF,
the script applies the temporary 0.50 W limit. Keep the same source settings and
wiring; the limited observation proceeds automatically. The report records
V×I for each observation and includes the stored `FETC:POW?` reading for
reference. The stored reading does not trigger a fresh power measurement and
the load's power readback accuracy does not support the narrow window at 0.50 W.

The script verifies the software trip and Input OFF automatically. For each
software trip, turn OFF and isolate the external source when instructed and
confirm physical fault removal. With confirmed Input OFF and trusted communication,
the script restores the original protection thresholds and Fixed CC settings,
then automatically issues one Clear and polls the condition with repeated OFF
checks. It verifies the final restored settings and absence of protection faults.
The load can momentarily re-enable Input during Clear; unexpected ON remains a
failure even if the bounded OFF attempt succeeds. If state or communication
becomes uncertain, use the independent manual cutoff. The script separately checks
software and hardware power defaults and restores their saved values without
resetting the instrument. The shareable report records trip, register,
measurement, OFF and restoration results without the VISA resource string.
Failed Trigger and Workflow observations retain sampled evidence and timing;
isolated settings refusals retain selected numeric bounds and query/step
diagnostics. Software Trigger diagnostics distinguish an attempted write from a
confirmed write exchange, not waveform completion. A confirmed write exchange
can pass its dispatch case even when the separate waveform case fails. A
preflight or baseline failure before a write leaves dispatch NOT RUN; a
failed or unconfirmed write blocks the dependent waveform case.
LIST failures retain selected nested diagnostic fields, the failing Save/Recall
or automatic readback phase and first differing field, and the capture guard's
authorized LIST comparison. Private baseline or unrelated slot values and malformed reply text
are not published. Known session/identity failures before software dispatch
leave dispatch NOT RUN; unknown process termination remains uncertain.
Workflow discharge failures retain the final measured quantities and elapsed time.
A current or Input-state failure preserves the rejected sample separately from
accepted samples and integrated capacity. A late OFF
reply is recorded as a received-reply timestamp, not as accepted OFF
confirmation; the failure and manual cutoff requirement remain unchanged.
The isolated Low CC readback trial verifies the selected Low range and current
within the model programming resolution, without requiring the candidate numeric
selector to echo the setpoint. The original selector and setpoint must be restored exactly.
Arbitrary exception text,
private recovery snapshots and VISA identifiers are not included in shareable
error diagnostics. A failed bounded Clear retains its failure even if a later
trusted OFF status can verify the restored settings.
When the CLI returns partial write progress, the report's
`protection_write_progress` records confirmed setting values, unconfirmed
writes, and settings that were never attempted.
With `-DebugArtifacts`, Protection MAX replies that parse as numbers and
their bounds are recorded in the private command stderr. Invalid replies
stop the trial before a defaults write. A numeric MAX outside the allowed
bounds leaves the defaults case `NOT RUN` with a refusal reason, preserves
the trip results and performs no restore write for the unmodified group.

The contributor scope does not open Product or Worker LIVE.

The register meanings, Clear behavior and configuration syntax are in the
[IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf),
printed pp.15–16, 47, 49–50 and 58–59.
See also p.36 for stored power readback and the
[IT8800 User's Manual](https://www.itechate.com/uploadfiles/2019/10/20191015163409349.pdf)
for the IT8812C measurement specifications.

### Contributor Trigger validation

Select `live-cli-check.ps1 -Modes trigger` with the usual approved model,
explicit USB or ASRL INSTR Resource, source/DUT limits and independent manual OFF procedure.
Add `-PlanOnly` to check the command plan without VISA access. Trigger uses
built-in preset settings by default: 0.1/0.2 A levels, 0.1 A/µs slews,
2 s pulse, 5 s TIMER interval, 2/2 s LIST dwell, one LIST repeat,
15 s observation and a 60 s trial ON/OFF deadline. These are controlled
validation presets, not proof that a particular supply or DUT is safe.
The original Transient Pulse default remains 0.001 s; explicitly supplying
`-PulseWidth` overrides both. Standalone, mixed and full selections containing
legacy Transient, TRACE or Trigger save the original Trigger Source and Timer
Interval before any changes and restore both after the selected connected trials
when Input OFF is confirmed and communication trusted. TRACE explicitly verifies
HOLD before capture. If an earlier trial fails after a possible Source change,
restoration requires trusted communication, confirmed Input OFF and completed
applicable cleanup. An untrusted session, unconfirmed TRACE Feed stop or uncertain
safety state blocks automatic restoration and requires independent operator action;
the run stays FAIL.

| Parameters | Purpose |
| --- | --- |
| `-TriggerObservationSeconds`, `-TriggerMaxOnSeconds` | Whole seconds of observation and the larger per-trial deadline from before Input ON through OFF confirmation, within the 900 s total deadline. Capture is additionally limited to a single 30 s command minus the existing 500 ms return reserve. Plans must fit the required event and minimum query costs within the smaller window. Actual VISA latency may still limit evidence. The script reserves part of the difference for OFF and any process termination. |
| `-LevelA`, `-LevelB`, `-RiseSlew`, `-FallSlew`, `-PulseWidth` | Distinct bounded Low-range CC levels, slew rates in A/μs and the Transient pulse width in seconds. Both current levels must fit the declared source limit. |
| `-TriggerIntervalSeconds` | TIMER interval in seconds. The observation must accommodate two periods plus the pulse width. |
| `-TriggerListWidthA`, `-TriggerListWidthB`, `-TriggerListRepeat` | LIST CC dwell times in seconds and a finite repeat count. The LIST must fit the observation window. |
| `-TriggerExternalSetup` | Optional explanatory note shown before initial authorization and at the EXTERNAL equipment readiness confirmation. |

The Contributor CLI samples instrument current in one session, dispatching the
software Trigger only for HOLD or BUS after the baseline is observed and Input ON is rechecked. Plans with pulse or dwell too short for configured VISA query delays are rejected. It checks
ordered current plateaus and bounded time brackets; insufficient sample
resolution, missing pulses or wrong order fail the trial. Finite BUS/EXTERNAL
LIST additionally requires a clear-to-set-to-clear RUN condition (bit 7 of
the existing read-only `STAT:QUES:COND?`, IT8800 Programming Guide V2.1
printed pp.15,29), with the final dwell bracket ending at the first stopped
condition. A stuck final level without RUN completion fails, even if it
lasts the expected dwell. No assumption is made about terminal current. This is sampled
functional evidence, not independent pulse-width or amplitude calibration.
HOLD and TIMER use Transient CC Pulse (baseline B, pulse at A for the
configured width, then back to B). BUS and EXTERNAL use a two-step LIST CC,
with a zero fixed-current baseline before LIST selection. The test replaces
the active CC Transient and LIST configurations.
MANUAL remains implemented for offline Trigger Setup and as an original Source
to read or restore after validation, but it has no powered contributor trial;
no Local or front-panel Trigger action is required. For EXTERNAL, use the rear
TRIG terminal and its common negative terminal with a TTL low pulse longer than
10 μs. Verify the actual source voltage levels, common reference and wiring
against the instrument and signal equipment before use. Confirm external On/Off and analog programming are
disabled. [IT8800 User’s Manual V2.7, §§3.1, 3.4, 3.19.2](https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/5562/IT8800%20User%20Manual.pdf)

The script saves the original Trigger Source and interval while Input is OFF.
After the initial batch safety authorization, HOLD, BUS and TIMER run without
operator observation prompts. EXTERNAL equipment readiness requires a separate
Enter after successful setup/readback with Input OFF and before Input ON.
Keep the signal inactive until the capture-ready message, then apply the
physical pulse. The script attempts and verifies Input OFF before marking
waveform acceptance. The
existing 900 s total limit and per-trial ON/OFF budget remain. Missing
evidence, cancellation, deadlines or unsuccessful restoration fail validation;
there is no manual Yes/No override.
The script restores and reports readback for each original Trigger setting
after confirmed OFF and with trusted communication. If communication becomes
untrusted, use the independent manual OFF procedure immediately. The script
skips further VISA commands in that situation. Treat TIMER observation as a
functional test.

The standalone contributor scope `--hardware-validation trigger` is limited to
IT8812C USB/ASRL System VISA. In that scope, Enable, Input ON and software Trigger
compare the expected function, full dynamic configuration and Trigger Setup
with physical readback. Input ON also requires `--confirm`. When restoring a
snapshot, contributor `trigger-configure` can supply both Source and interval
for any source. The Product and Worker LIVE paths reject Trigger Setup.
The CLI-only `trigger-observe` command accepts the exact validation
configuration and a bounded observation deadline. It does not enable Input.

### Reports

| Artifact | Content |
| --- | --- |
| `shareable/report.json` | Resource-redacted PASS/FAIL/NOT RUN, selected settings, expected Auto ranges and safety findings |
| `shareable/summary.md` | Case statuses and totals |
| `private/` | Raw logs/Resource, only with `-DebugArtifacts`; inspect before sharing |

Reports remain in an ignored `.tmp_tests/readonly-live-*/` directory.
PlanOnly physical cases remain **NOT RUN**, never PASS. Normal runs do not
retain raw logs. Do not commit private instrument IDs, resources or lab
details. This script is not an unattended control API.

Follow [Testing Guidelines](../testing-guidelines.md),
[AGENTS.md](../../AGENTS.md) and the
[ITECH IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf)
before an independently authorized physical run.

## Safety Defaults

- [Exact LIVE support](../core/supported-models.md#product-live-support) is owned
  by Core; simulation and dry-run never authorize physical operations.
- No implicit Input ON. Use deliberate confirmation and the existing mode,
  range, Input OFF, protection and cleanup checks for control operations.
- Basic USB diagnostics never configure the load; Error Queue reads consume
  entries only on explicit request. Worker and ASRL permissions remain separate.
- A communication failure or VISA Close does not prove Input OFF. Use the
  independent cutoff and recovery requirements in
  [Controlled instrument validation](#controlled-instrument-validation).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Resource missing or stale | Use `list-resources`; explicit `--live-only` probes USB responders. Do not auto-select a resource. |
| `identity_mismatch` or unsupported model | Verify the actual instrument and expected model; do not force a driver. |
| VISA open or query failure | Check the selected System VISA runtime, resource, cable and framing. For ASRL use the approved contributor connection options. |
| `unsupported_live_scope` | Consult the exact model/transport/execution-scope matrix; use simulate or dry-run for unsupported features. |
| Instrument reports a nonzero error code | Use `error` explicitly when consuming the entry is intended. A valid nonzero code is query data, not a CLI transport failure. |
| Input OFF is unconfirmed | Follow the independent manual cutoff procedure; do not blindly reconnect or treat session close as proof of OFF. |

Share only redacted diagnostics; real resource strings, serials and private
settings must not be published.
