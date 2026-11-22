# Electronic Load Tool CLI

The CLI and bundled Common v3 Worker use the shared Core for the registered ITECH
IT8812C. This document owns detailed CLI commands, offline mode behavior,
operator-facing safety guidance, and the controlled validation procedure.
Commands below run from the repository root unless stated otherwise.
For exact Worker JSON/HTTP/JSONL behavior, use the
[Electronic Load Worker Contract](../contracts/electronicload-worker-contract.md).
This documentation does not authorize physical tests or extend Product LIVE support.

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
resource and exact IT8812C identity. Set the operator-selected VISA resource
once in PowerShell and pass it explicitly to every LIVE command:

```powershell
$env:ELECTRONICLOAD_TOOL_RESOURCE = "USB0::...::INSTR"
```

PowerShell expands this documentation-only variable in the explicit `-Resource`
argument; the validation Script does not read it automatically. Operation
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

LIVE uses USB INSTR and System VISA only, with a 5000 ms I/O timeout and
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
| `scpi-error-next` | `SYST:ERR?` | One integer `error_code` and string `message` |

Each Error Queue read removes the returned entry. `scpi-error-next --all`
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

All three support `--simulate` and `--dry-run`. Dry-run lists `*IDN?` and the
selected query, with `max_reads` for `--all`. Product LIVE and Worker LIVE
are blocked.

For separately approved hardware validation, run each query manually using
the contributor CLI scope, an operator-supplied USB INSTR resource, and
System VISA. Confirm physical Input OFF before querying. The CLI verifies
the exact IT8812C identity before sending the query. Use `scpi-error-next`
only when removing the returned queue entry is intended, and choose `--all`
explicitly for a bounded read of the queue.

```powershell
electronicload-tool scpi-version --resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" --backend system --hardware-validation system-query --json
```

[ITECH IT8800 Programming Guide V2.1, printed pp. 31–33](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf#page=41)

## Offline Measurement Extensions

`measure-min-max`, `measure-discharge`, `set-average` and `average-status`
support `--simulate` and `--dry-run`. All four refuse Product and Worker LIVE
before opening VISA. The standalone CLI `average-status` also supports the
contributor scope described in [Configuration readback](#configuration-readback).

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

## Offline TRACE

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

### Contributor-only TRACE hardware validation

Normal TRACE Product LIVE and Worker LIVE remain **disabled**. For a separately
approved, interactive instrument trial, the existing CLI has a narrow
`--hardware-validation trace` scope on `trace-configure` and `trace-start`.
Only this explicit scope opens the exact user-supplied USB INSTR Resource with
System VISA, validates the exact IT8812C identity, and follows the same
Configure/Start prechecks, single trigger, timeout and CSV rules as Offline TRACE.
The result includes `validation_only: true`, `validation_scope: "trace"` and
VISA runtime evidence. The legacy LIVE override does not enable TRACE.
`--hardware-validation trace --dry-run` previews arguments without opening VISA
or creating CSV, and never counts as physical success. Do not use these CLI
commands as unattended or ordinary load-operation APIs.

The single controlled script `scripts/live-cli-check.ps1 -Modes trace` performs
five short trials: VOLT 10 points / 1 s OFF, CURR 10 / 1 s OFF, TWO 40 / 1 s
OFF, VOLT 10 / 1 ms OFF, and TWO 10 / 1 s with explicitly confirmed bounded
Fixed CC Input ON. The last trial writes a new Full CSV with estimated elapsed
and delta columns, then explicitly commands and confirms Input OFF. This is
not waveform calibration, an exhaustive interval test, or Worker LIVE validation.
TRACE Start alone has a 120-second script process deadline; other commands
keep their existing 30-second budget and the total run stays bounded.
Unexpected communication failures halt remaining cases; the script never
reconnects to an untrusted VISA Session to attempt blind Input OFF.

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

### Offline CR-LED and Current Slow Rise

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
require the exact IT8812C identity and an explicit USB INSTR Resource with
System VISA. The new contributor readbacks identify their source as
`instrument_readback`. Query failures and invalid replies return errors.
`fixed-slew-status` checks both rates against the current CC Range.
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

## Offline Fixed CC Slew, VON and Short

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

## Offline Input Timer

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

### Controlled Timer validation

Use the existing script's `-InputTimerOnly` shortcut (without `-Modes`), or
select `-Modes input-timer`. Input Timer is included in the default all-mode
run. Preview with a fictional USB resource for offline checks; supply an
approved resource only for a separately authorized physical run:

```powershell
.\scripts\preflight-cli.ps1 -InputTimerOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -InputTimerOnly -Current 0.1 -TimerSeconds 5 -PlanOnly
```

The Timer-only script accepts 3–10 seconds (default 5) and positive CC up to
2 A (default 0.1 A), valid for both CC ranges. The script defaults to an
operator-managed 5 V / 0.8 A source (overrides: >0 to 18 V / >0 to 2 A).
The operator must configure and verify the source before physical testing.
It retains the common approved setup/manual OFF contingency, interactive
confirmation, runtime checks, process deadlines, redaction and explicit OFF
cleanup requirements described below.

The planned physical trial confirms Input OFF, explicitly disables Timer,
sets low-range Fixed CC, configures/enables Timer, reads status while OFF,
then confirms Input ON through `--hardware-validation input-timer` with complete
expected `current`, `range` and integer `seconds`. Before and after remote
transition Core rechecks physical OFF, CC settings, Short/Transient OFF and Timer
readback. After the bounded wait the script queries actual Timer/Input state;
only observed Input OFF passes expiry. It then explicitly sends/verifies Input
OFF, separately disables Timer and reads final Timer/Input state. Failure stops
further trials; a possible ON attempt gets one bounded OFF cleanup attempt.
Timer-disable cleanup is a separate operation after confirmed OFF.

The validation-only Core/CLI scope uses `INP:TIM:DEL <seconds>`, `INP:TIM 0|1`,
`INP:TIM:DEL?` and `INP:TIM?` from the
[IT8800 Programming Guide V2.1, printed pp.45–46](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf),
which lists IT8812C. The DELay syntax line omits `:DELay`; its example and query
include it. Readback must match. The trial reports the observed Enabled value
after expiry without assuming retention, and does not establish timing accuracy,
VON interaction, CV/CR/CW physical compatibility or Worker LIVE behavior.
PlanOnly leaves every physical case NOT RUN and cannot promote Product LIVE.

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

### Contributor validation

The CLI-only `--hardware-validation transient-cc|transient-cv|transient-cr|transient-cw`
route follows the [controlled validation procedure](#controlled-instrument-validation).
It requires separate physical authorization, exact identity/USB/System VISA,
Input OFF checks, complete expected settings/readback and deliberate ON
confirmation. It grants no normal Product or Worker LIVE permission.

The existing `preflight-cli.ps1 -TransientOnly` covers all four functions offline.
`live-cli-check.ps1` defaults to `cc,cv,cr,cw,transient-cc,transient-cv,transient-cr,transient-cw`;
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

## Offline Protection

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

### Software Sequence（繁體中文）

建立 UTF-8 JSON 檔，包含 `version: 1`、1 至 255 的整數 `loop_count`
及 1 至 255 個步驟。展開後最多執行 10,000 個步驟，每步只包含
`action` 和 `arguments`。範例、參數與單位如上表。Range 可選
`auto`、`low` 或 `high`，Setpoint 與 Wait 必須使用 JSON 數字。Wait
接受 0 至 3600 秒的有限小數，包含端點。CC 的上升和下降 Slew 必須成對提供，
限離線使用。

正式執行前會確認 Input OFF。若起始已 ON，會拒絕執行並保留原狀態。
**每次** `input-on` 前都要在 Input OFF 時重新設定 Fixed Setpoint，
同一輪再次 ON 也要重新設定。每輪結束時必須 OFF。`input-off` 後可以
保留只讀步驟，未使用 ON 的輪次可以省略 OFF 步驟。整份文件會在第一步前預檢，
`fresh_fixed_setup_required` 會指出須補上新設定的 ON 步驟。
Sequence 要求 Input Timer OFF 才能啟用 Input。CV、CR、CW 模式也要求
Current Slow Rise OFF，設定 Fixed CR 前則要求 CR-LED OFF。模擬執行時，
Input ON 前也會確認指定的 CC 上升及下降 Slew 仍與設定相符。

使用上方的 `--dry-run` 指令預檢，不需確認。每次正式執行若含
`input-on`，都要重新提交確認。CLI 以 `--file` 讀取 JSON，執行結果為
JSON，每個 `measure` 回傳一筆 V／I／P，並包含讀回確認的最終 Input 狀態。
Worker 使用 `arguments.document` 與 `arguments.confirm_input_on`。
[Worker 契約](../contracts/electronicload-worker-contract.md#software-sequence)

### Contributor Software Sequence validation / 貢獻者實機驗證

For separately authorized physical runs, select `-Modes sequence` on its own
with a dedicated 5 V / 0.8 A source. The script uses the contributor CLI command
`sequence --file <file> --hardware-validation sequence --resource <USB-INSTR> --backend system --confirm --json`
with the registered IT8812C over USB/System VISA. Each trial uses one session.
Fixed setup and Input switching verify the applicable interlocks and settings.
Use an exclusive instrument and an independent manual OFF method. Ordinary CLI
and Worker LIVE are closed for Sequence.

The contributor scope accepts only the four fixed documents, for 0.1 A CC,
3 V CV, 1000 Ω CR and 0.10 W CW. Each has one ON, a 0.25 s Wait, measurement,
and verified OFF. The script requires operator confirmation before each trial
and enforces a 30 s command deadline and 900 s total deadline. The Core handles
trusted cleanup on the existing session. The CLI directs the operator to
switch off the load independently if a Remote transition during setup or Input ON
ends without a fresh OFF readback, or if a run fails with Input observed ON
after the initial OFF check succeeded. Once the instrument is identified,
loss of trusted communication stops further commands. The CLI reports Input
`UNKNOWN`, records `session_untrusted` as the cleanup reason, preserves the
original error, and gives the same independent OFF instructions. The script
also gives independent OFF instructions if the process times out or Input OFF
is unconfirmed. Cleanup stays within the original session.

另行授權實機驗證後，單獨選擇 `-Modes sequence` 並使用專用 5 V／0.8 A 電源。
Script 使用上方 CLI 貢獻者指令，以 USB／System VISA 連接已註冊的 IT8812C。
每次試驗使用一個 Session，確認 Fixed 設定與 Input 切換的適用安全條件。
實機操作需獨占儀器，並備妥獨立手動 OFF 手段。一般 CLI 與 Worker 維持拒絕
Sequence LIVE。

貢獻者驗證範圍只接受四份固定文件，分別驗證 0.1 A CC、3 V CV、1000 Ω CR、
0.10 W CW。每份只有一次 ON、0.25 秒 Wait、量測及確認 OFF。Script 在每次
試驗前要求操作員確認，並執行單一命令 30 秒與總時限 900 秒的限制。通訊可信時
由 Core 在原 Session 處理清理。若設定或啟用 Input 時切換 Remote，卻無法重新
讀回確認 OFF，或起始已確認 OFF、執行失敗時仍觀測到 ON，CLI 會要求操作員獨立
手動 OFF。識別儀器後若失去可信通訊，便停止發送命令。CLI 將 Input
回報為 `UNKNOWN`、記錄清理原因 `session_untrusted`、保留原始錯誤，並提供相同的獨立
手動 OFF 指引。程序逾時或無法確認 Input OFF 時，Script 也會提供獨立手動 OFF 指引。
清理僅使用原 Session。

## Basic Workflows

Each command runs independently in the CLI or as one Worker job. Use
`--simulate` to run or `--dry-run` to preview validated arguments without
waiting, reading the simulator, or creating files. CLI runs return one JSON
object. These commands are offline only. The simulator uses a fixed 12 V
source, so a discharge simulation at a 3 V cutoff needs a time or capacity
limit to stop.

| Command | Required options | Optional options |
| --- | --- | --- |
| `measure-log` | `--interval-s`, `--count` | `--csv` |
| `measure-until` | `--interval-s`, `--metric`, `--operator`, `--threshold`, `--timeout-s` | `--csv` |
| `cc-timed-load` | `--current`, `--duration-s`, `--confirm` when simulating | `--range` |
| `cc-discharge` | `--current`, `--cutoff-voltage`, `--timeout-s`, `--confirm` when simulating | `--interval-s` (defaults to 1 s), `--cutoff-capacity-ah`, `--range`, `--csv` |

```powershell
electronicload-tool measure-log --simulate --interval-s 1 --count 60
electronicload-tool measure-until --simulate --interval-s 1 --metric voltage --operator le --threshold 4.5 --timeout-s 6
electronicload-tool cc-timed-load --simulate --current 0.5 --duration-s 30 --confirm
electronicload-tool cc-discharge --dry-run --current 0.5 --cutoff-voltage 3 --cutoff-capacity-ah 1 --timeout-s 7300
```

- The measurement commands preserve Input state. `measure-log` reads voltage,
  current and power. `measure-until` reads one metric (`voltage`, `current`
  or `power`) using `lt`, `le`, `gt` or `ge`. The threshold uses V, A or W
  for the selected metric. The run ends when the condition is met.
- Sampling starts immediately and skips missed ticks. Intervals accept
  0.001 to 3600 seconds, and durations and timeouts accept 0.001 to 86,400
  seconds. `measure-log` accepts 1 to 10,000 samples with the last scheduled
  before 86,400 seconds. A monitoring timeout
  may span up to 10,000 intervals. A timeout returns `workflow_timeout`
  with the available summary.
- CC runs require initial Input OFF and verify Fixed CC settings before
  enabling Input. Current must be positive and valid for the selected range
  (`auto` by default, or `low` or `high`). Timed load waits the requested
  duration after ON confirmation. Discharge monitors voltage and current
  starting at that point. It stops at or below the positive voltage cutoff
  in V, or at or above the optional positive capacity cutoff in Ah. Voltage
  takes precedence if both cutoffs are reached together. Ah is integrated
  between consecutive measured currents and their timestamps. The first
  sample starts at 0 Ah.
- CC runs verify Input OFF at the end. If OFF cannot be confirmed, the
  result instructs the operator to independently switch off the load.
  Timed load returns a summary. The three sampling commands also report
  `sample_count` and `last_measurement`. Discharge includes `capacity_ah`.
- Only an explicit `--csv` creates a CSV. Relative paths resolve from the
  running process's working directory. Missing parent directories are created,
  and existing files are preserved. CSV uses `sample_index`, `elapsed_s`
  and the measured columns, plus `capacity_ah` for discharge. A run that
  fails after creating the file retains it and reports `csv_incomplete`.

For Worker argument names, results and Stop behavior, see the
[Worker contract](../contracts/electronicload-worker-contract.md#basic-workflows).

### 基礎工作流程（繁體中文）

每條命令可獨立在 CLI 執行，也可提交為一個 Worker 工作。使用
`--simulate` 執行，或用 `--dry-run` 預覽已驗證的參數。預覽不等待、
不讀取模擬器，也不建立檔案。CLI 回傳單一 JSON 物件。這些指令僅供
離線使用。模擬電壓固定為 12 V，因此用 3 V 截止模擬放電時，會由
時間或容量限制結束。

| 指令 | 必填選項 | 選填選項 |
| --- | --- | --- |
| `measure-log` | `--interval-s`、`--count` | `--csv` |
| `measure-until` | `--interval-s`、`--metric`、`--operator`、`--threshold`、`--timeout-s` | `--csv` |
| `cc-timed-load` | `--current`、`--duration-s`，模擬時需 `--confirm` | `--range` |
| `cc-discharge` | `--current`、`--cutoff-voltage`、`--timeout-s`，模擬時需 `--confirm` | `--interval-s`（預設 1 秒）、`--cutoff-capacity-ah`、`--range`、`--csv` |

- 量測指令維持原本的 Input 狀態。`measure-log` 讀取電壓、電流與功率。
  `measure-until` 只讀取所選項目（`voltage`、`current` 或 `power`），以
  `lt`、`le`、`gt` 或 `ge` 比較門檻，達成條件即結束。門檻使用對應的
  V、A 或 W。
- 取樣立即開始，錯過的取樣時點直接略過。間隔接受 0.001 至 3600 秒，
  持續時間和超時接受 0.001 至 86,400 秒。`measure-log` 接受 1 至 10,000
  筆，最後一筆須排在 86,400 秒前。
  監控超時最多涵蓋 10,000 個取樣間隔。超時回報 `workflow_timeout` 與已有摘要。
- CC 指令確認初始 Input OFF，並在確認固定 CC 設定後才開啟 Input。電流
  必須為正值且符合所選檔位，預設 `auto`，也可選 `low` 或 `high`。定時負載
  自確認 ON 後開始計時。放電自同一時點開始監控電壓與電流，電壓小於
  或等於正值截止電壓（V），或容量大於或等於選填的正值截止容量（Ah），
  就結束放電。同時達成時以電壓截止為優先。容量依相鄰實測電流與取樣
  時間差積分，首筆為 0 Ah。
- CC 指令結束時確認 Input OFF。若無法確認，結果會要求操作人員獨立關閉
  負載。定時負載回傳執行摘要。三條取樣指令另回傳 `sample_count` 與
  `last_measurement`，放電也回傳 `capacity_ah`。
- 只有明確指定 `--csv` 才建立 CSV。相對路徑以執行程序的工作目錄為準。
  缺少的上層目錄會建立，既有檔案會保留。欄位包含 `sample_index`、
  `elapsed_s` 和量測欄位，放電另有 `capacity_ah`。若建檔後執行失敗，
  會保留檔案並回報 `csv_incomplete`。

Worker 的參數、結果與 Stop 行為見 [Worker 契約](../contracts/electronicload-worker-contract.md#basic-workflows)。

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

Worker requests use the existing local Common v3 endpoints and one async job at
a time; see the [Worker Contract](../contracts/electronicload-worker-contract.md)
for exact argument types, admission timing, result fields, and JSONL events.

## CLI Validation Scripts

Run these PowerShell scripts from the repository root. Offline validation
does not authorize physical instrument operation.

| Script | Purpose | Real hardware |
| --- | --- | --- |
| `scripts/preflight-cli.ps1` | Offline CLI, simulator, refusal and Manifest checks; focused switches: `-TransientOnly`, `-FixedAutoOnly`, `-FixedSlewVonShortOnly`, `-InputTimerOnly`, `-TraceOnly`, `-CrLedSlowRiseOnly`, `-TriggerOnly`, `-ProtectionOnly`, `-ReadbackOnly` | Never |
| `scripts/live-cli-check.ps1 -PlanOnly` | Validate command plans; reported physical cases remain `NOT RUN` | Never |
| `scripts/live-cli-check.ps1` | Separately approved interactive IT8812C validation, including deliberate Input ON/OFF | With operator approval |

### Available validation modes

`-Modes` accepts one mode or a comma-separated list. If omitted, it executes
every default mode once in the established order. Unselected modes
produce no test cases or PASS counts. The normal default includes the
formerly separate Input Timer trial; `-Modes trigger` remains available
for focused verification.

| `-Modes` value | Coverage | Default |
| --- | --- | --- |
| `cc` | Fixed CC High, Auto, Low and one Input ON/OFF | Yes |
| `cv` | Fixed CV Low, Auto, High | Yes |
| `cr` | Fixed CR Low, Auto, High | Yes |
| `cw` | Fixed CW | Yes |
| `transient-cc` | Continuous, Pulse, Toggle; Low/High/Auto | Yes |
| `transient-cv` | Continuous Auto, Pulse Low, Toggle High | Yes |
| `transient-cr` | Continuous Auto, Pulse High, Toggle High | Yes |
| `transient-cw` | Continuous, Pulse, Toggle; no Range | Yes |
| `trace` | Five bounded TRACE captures, including powered CC and Full CSV | Yes |
| `cr-led` | Fixed CR-LED at 50 Ω and 1 V Vdrop, plain CR and exit to CC, all with Input OFF | Yes |
| `current-slow-rise` | Fixed CC enable, retention after explicit OFF and CC setup, disable and readback | Yes |
| `trigger` | Four physical sources (HOLD, TIMER, BUS, EXTERNAL), using Transient CC Pulse and finite LIST CC with observation and Trigger Setup restoration; MANUAL powered validation is excluded | Yes |
| `input-timer` | Bounded Fixed CC Input Timer, expiry and explicit disable | Yes |
| `protection` | Software current trip, software power trip, hardware power limiting and separate defaults | Explicit selection only |
| `readback` | Fixed CC, Slew, VON, Short and Average with Input OFF and existing settings | Explicit selection only |

`-InputTimerOnly` remains a compatible shortcut for the single
`-Modes input-timer` trial and **cannot** be combined with `-Modes`.
When Trigger and legacy Transient are both selected, the original Trigger
Source and Timer Interval are captured before any mutating mode command and
restored after Trigger validation where safe. All selections keep Identify,
physical Input OFF and V/I/P prechecks. Invalid or empty mode
names are rejected.

Select `-Modes readback` on its own and start in Fixed CC with Input OFF.
Supply known front-panel settings for the cases you want to verify. Use
`-ExpectedRiseSlew` and `-ExpectedFallSlew` together (A/µs),
`-ExpectedVonThreshold` (V) and `-ExpectedVonLatch` (`on` or `off`) together,
`-ExpectedShort` (`on` or `off`), and `-ExpectedAverageCount` (2–16).
The report records expected and observed values. A comparison passes when
they match. Without a complete baseline for a command, its comparison is
`NOT RUN` and the report retains the observed values. A successful run with
any observation-only case has overall status `OBSERVED ONLY`. Fixed,
Transient, Trigger and TRACE selections compare independent status queries
with the settings of their existing trials.

### Script parameters

| Parameter | Required / default | Purpose |
| --- | --- | --- |
| `-Target` | Required: `itech-it8812c` | Exact registered model ID |
| `-Resource` | Required | Explicit USB INSTR VISA Resource, also for PlanOnly; never guessed |
| `-Connection` | `usb` | USB only |
| `-Backend` | `system` | System VISA (`@ivi`) only |
| `-Modes` | All default modes | Single or comma-separated selection from above. Select Protection separately. |
| `-InputTimerOnly` | Off | Backward-compatible Timer-only shortcut, incompatible with `-Modes` |
| `-SourceVoltageLimitV` | **5 V** | Operator-declared external source voltage, override >0 to 18 V |
| `-SourceCurrentLimitA` | **0.8 A** | Operator-declared external source current limit, override >0 to 2 A |
| `-PlanOnly` | Off | No VISA or physical hardware access |
| `-DebugArtifacts` | Off | Additionally retain raw private diagnostics |
| `-Verbose` | Off | Print individual planned CLI steps and arguments |

**Both Source parameters are optional.** The standard external supply setup
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
| `-TimerSeconds` | `5` | Seconds, Input Timer mode (allowed integer 3–10) |
| `-TriggerObservationSeconds` / `-TriggerMaxOnSeconds` | `15` / `60` | Seconds per Trigger trial; includes explicit OFF deadline |
| `-TriggerIntervalSeconds` | `5` | Seconds, Trigger TIMER source |
| `-TriggerListWidthA` / `-TriggerListWidthB` | `2` / `2` | Seconds, finite LIST dwell |
| `-TriggerListRepeat` | `1` | Finite LIST repetitions |
| `-TriggerExternalSetup` | Optional | Extra external trigger wiring notes; operator still confirms equipment and safety |

These are **validation script defaults**, not changes to Core or Product LIVE
permissions. At 5 V, 10 Ω nominally implies 0.5 A, and 2 W implies 0.4 A;
actual load behavior depends on source limit, load mode, wiring and DUT.
Core still validates exact model range/resolution and resolves Auto range.
This procedure does not establish measurement or waveform accuracy.

### Quick start

Set a **fictional** USB resource for offline previews. For an approved
physical trial, replace it with the exact operator-selected USB INSTR
Resource. This environment variable is a PowerShell convenience; the script
only uses the explicit `-Resource` argument.

```powershell
$env:ELECTRONICLOAD_TOOL_RESOURCE = "USB0::...::INSTR"

# Preview the default modes without opening VISA.
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

## Controlled instrument validation

Before any live VISA operation, the script runs offline preflight, displays
the selected Resource for operator verification, and requires a **single
interactive Enter confirmation** (Ctrl+C cancels). Noninteractive execution
and redirected stdin are blocked.

| Check before Enter | Operator action |
| --- | --- |
| Target, Resource, backend and modes | Correct instrument; no other process controlling the device |
| External source (**5 V / 0.8 A** by default) | Manually set and verify voltage and current limit |
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
No automatic Reset, `SYST:LOC`, protection clear,
error-queue read or reconnect is added. VISA Close alone does not imply OFF.

| Existing deadline | Value |
| --- | --- |
| Each CLI command | 30 s |
| TRACE Start | 120 s |
| CC-only total I/O | 240 s |
| Any other mode total I/O | 900 s |
| Cleanup commands | Separate 30 s allowance |

The default plan includes the modes listed as default above. It retains the existing
900-second total deadline; slow hardware or operator observations may exhaust
that budget. Select a smaller `-Modes` set if necessary. V/I/P are separate queries, with no guarantee
of power freshness, synchronization, waveform timing or accuracy. This
contributor-only CLI validation **does not** enable Worker LIVE or expand
Product LIVE support.

### Contributor Protection validation

Use the separately approved `live-cli-check.ps1 -Modes protection` procedure
with the exact IT8812C model, an operator-supplied USB INSTR resource and System
VISA. Add `-PlanOnly` for the offline command plan. Start in Fixed CC with
Input OFF, a dedicated source set at the source to 5 V with a 0.8 A current
limit, and an independent manual cutoff. The script checks current mode and
interlocks, saves and restores the original Fixed CC and protection settings,
and stops at the first failed trial. The temporary Fixed CC setpoint is 0.20 A.
The operator confirms each Input ON with a fresh Enter.

| Trial | Temporary setting | Physical acceptance |
| --- | --- | --- |
| Software current | Enabled, 0.10 A, 1 s | OC and PS with Input OFF |
| Software power | 0.50 W, 1 s | OP and PS with Input OFF, after exposure above 0.50 W |
| Hardware power | 0.50 W | With Input ON and no PS, compare the baseline at approximately 0.20 A with V×I at 0.45–0.55 W under the limit. Both observations require 4.75–5.25 V and explicit OFF. |

For Hardware Power, first measure the baseline with the saved protection settings.
The script requires 0.18–0.22 A at the load during the baseline to establish that
the source and wiring sustain the intended current. After confirming Input OFF,
the script applies the temporary 0.50 W limit. Keep the same source settings and
wiring, and press Enter again for the limited observation. The report records
V×I for each observation and includes the stored `FETC:POW?` reading for
reference. The stored reading does not trigger a fresh power measurement and
the load's power readback accuracy does not support the narrow window at 0.50 W.

For each software trip, verify the physical trip and Input OFF, isolate the
external source, and confirm again before Clear. The load can momentarily
re-enable Input during Clear. If its state or communication becomes uncertain,
use the independent manual cutoff. Restoration requires confirmed Input OFF
and trusted communication. The script separately checks
software and hardware power defaults and restores their saved values without
resetting the instrument. The shareable report records trip, register,
measurement, OFF and restoration results without the VISA resource string.
When the CLI returns partial write progress, the report's
`protection_write_progress` records confirmed setting values, unconfirmed
writes, and settings that were never attempted.
The contributor scope does not open Product or Worker LIVE.

The register meanings, Clear behavior and configuration syntax are in the
[IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf),
printed pp.15–16, 47, 49–50 and 58–59.
See also p.36 for stored power readback and the
[IT8800 User's Manual](https://www.itechate.com/uploadfiles/2019/10/20191015163409349.pdf)
for the IT8812C measurement specifications.

### Contributor Trigger validation

Select `live-cli-check.ps1 -Modes trigger` with the usual approved model,
explicit USB Resource, source/DUT limits and independent manual OFF procedure.
Add `-PlanOnly` to check the command plan without VISA access. Trigger uses
built-in preset settings by default: 0.1/0.2 A levels, 0.1 A/µs slews,
2 s pulse, 5 s TIMER interval, 2/2 s LIST dwell, one LIST repeat,
15 s observation and a 60 s trial ON/OFF deadline. These are controlled
validation presets, not proof that a particular supply or DUT is safe.
The original Transient Pulse default remains 0.001 s; explicitly supplying
`-PulseWidth` overrides both. A full-mode run saves original Trigger Source
and Timer before any legacy Transient writes HOLD, then restores them after
the Trigger trials when Input OFF is confirmed and communication trusted.
If an earlier selected mode fails after legacy Transient may have changed
Source, it attempts the same restore only after trusted Input OFF confirmation.
An untrusted session or unconfirmed OFF blocks further VISA writes and requires
independent operator action; the run stays FAIL.

| Parameters | Purpose |
| --- | --- |
| `-TriggerObservationSeconds`, `-TriggerMaxOnSeconds` | Whole seconds of observation and the larger per-trial deadline from before Input ON through OFF confirmation, within the 900 s total deadline. Allow time for VISA setup, command readbacks and cleanup. The script reserves part of the difference for OFF and any process termination. |
| `-LevelA`, `-LevelB`, `-RiseSlew`, `-FallSlew`, `-PulseWidth` | Distinct bounded Low-range CC levels, slew rates in A/μs and the Transient pulse width in seconds. Both current levels must fit the declared source limit. |
| `-TriggerIntervalSeconds` | TIMER interval in seconds. The observation must accommodate two periods plus the pulse width. |
| `-TriggerListWidthA`, `-TriggerListWidthB`, `-TriggerListRepeat` | LIST CC dwell times in seconds and a finite repeat count. The LIST must fit the observation window. |
| `-TriggerExternalSetup` | Optional explanatory note; the console still requires physical equipment and wiring confirmation. |

Use a current observation method that can resolve the chosen levels and pulse
or dwell times. The IT8812C panel display may suffice for qualitative 2-second
changes; if no change can be reliably observed, answer No rather than inferring
PASS from command acceptance. This is not a pulse-width or amplitude calibration.
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
Press Enter before each trial to confirm the setup and then perform any
requested physical trigger during the bounded observation. The script
explicitly attempts and verifies Input OFF before requesting a Yes/No answer.
The readiness and observation prompts count toward the existing 900 s
limit, including default all-mode runs. If the complete selection cannot
finish within that deadline, the run fails rather than silently skipping
a mode or removing the bound. A missed
trial or run deadline fails validation even if bounded emergency cleanup is
still possible. Yes confirms the expected observable effect. Insufficient
observation, cancellation and unsuccessful restoration fail validation.
The script restores and reports readback for each original Trigger setting
after confirmed OFF and with trusted communication. If communication becomes
untrusted, use the independent manual OFF procedure immediately. The script
skips further VISA commands in that situation. Treat TIMER observation as a
functional test.

The standalone contributor scope `--hardware-validation trigger` is limited to
IT8812C USB/System VISA. In that scope, Enable, Input ON and software Trigger
compare the expected function, full dynamic configuration and Trigger Setup
with physical readback. Input ON also requires `--confirm`. When restoring a
snapshot, contributor `trigger-configure` can supply both Source and interval
for any source. The Product and Worker LIVE paths reject Trigger Setup.

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
