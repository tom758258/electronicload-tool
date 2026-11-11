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

## Offline Measurement Extensions

`measure-min-max`, `measure-discharge`, `set-average` and `average-status`
support only `--simulate` and `--dry-run`. All four refuse LIVE before opening
VISA, including with the legacy contributor override. No hardware-validation
option opens these commands. Simulation and dry-run do not establish physical
SCPI support or measurement accuracy.

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

## Fixed Modes and Input Control

Fixed CC uses a complete `set-cc` request with amperes and a required
`auto`, `low` (0–6 A) or `high` (0–60 A) range. Software Auto checks Low
first, then High, including programming resolution, before any hardware access.
It never changes the setpoint or switches range while Input is ON.
See [Fixed Software Auto Range](#fixed-software-auto-range) for all three Fixed modes.
Before LIVE configuration, Core
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
Use `set-cv --voltage <V> --range auto|low|high` (Low 0.1–18 V, 1 mV;
High 0.1–120 V, 10 mV). Invalid values and unrepresentable resolutions
are rejected without rounding. The simulator supports CV configuration and
confirmed simulated Input ON/OFF; normal physical CV Input ON is blocked even
with ON confirmation. Controlled physical trials require the separate
[hardware-validation path](#controlled-instrument-validation). Fixed CC and previously supported LIVE commands
remain unchanged.

Fixed CR is also available for **dry-run and simulation only**. Use
`set-cr --resistance <ohms> --range auto|low|high`: Low **0.05–10 Ω** and
High **10–7,500 Ω**, including 10 Ω in either explicitly selected range.
Auto prefers Low at the shared 10 Ω boundary. Explicit High remains High.
Core accepts finite numeric values within the selected range, without imposing decimal
places, a fixed step or rounding. The published
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

Use the existing script's separate `-InputTimerOnly` switch, without `-Modes`.
It is excluded from the default eight-mode run. Preview with a fictional USB
resource for offline checks; supply an approved resource only for a separately
authorized physical run:

```powershell
.\scripts\preflight-cli.ps1 -InputTimerOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -InputTimerOnly -Current 0.1 -TimerSeconds 5 -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -PlanOnly
```

The Timer-only script accepts 3–10 seconds (default 5) and positive CC up to
2 A (default 0.1 A), valid for both CC ranges. Declared source limits must be
positive and at most 2 A / 18 V; the operator configures and verifies the source.
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
`-VoltageLevelA 5`, `-VoltageLevelB 6` (V), `-ResistanceLevelA 10`,
`-ResistanceLevelB 1000` (Ω), `-PowerLevelA 1`, `-PowerLevelB 2` (W).
The existing width parameters are shared; CC keeps its separate levels/slew.
CV uses Continuous Auto / Pulse Low / Toggle High; CR uses Continuous Auto /
Pulse High / Toggle High; CW has all three without Range or slew.
Auto expected ranges come from Core. Real execution retains explicit Resource,
interactive confirmation, source limits (>0 to 2 A / >0 to 18 V),
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
The offline implementation sends **no LIST SCPI**. Physical LIST control and
waveform/timing verification require separately authorized instrument work.

## Worker and Automation

Standalone simulation commands each start with fresh state. To exercise a CV/CR/CW
setup followed by simulated ON/OFF, send the commands to the same simulated
Worker, with `confirm_input_on: true` on the ON request.

A Worker prints Common v3 JSONL on stdout and offers local `GET /status`,
`POST /command`, and `POST /stop` on numeric loopback only. The Worker
runs one async job at a time, reports completion via `job_finished` and
`last_job`, and does not create request/result artifact files or a job
history. Stop does **not** imply Input OFF. For domain argument shapes and
permissions, see [Electronic Load Worker Contract](../contracts/electronicload-worker-contract.md).

Worker requests use the existing local Common v3 endpoints and one async job at
a time; see the [Worker Contract](../contracts/electronicload-worker-contract.md)
for exact argument types, admission timing, result fields, and JSONL events.

## CLI Validation Scripts

`scripts/live-cli-check.ps1` is the single hardware validation entry point.
Its default sequence covers all eight modes: CC High/Low, CV Low/High,
CR Low/High, CW and Transient CC/CV/CR/CW Continuous/Pulse/Toggle. Use `-Modes` to select
one or more modes; common read-only safety prechecks always apply.
CV/CR/CW/Transient use the explicit validation-only Core/CLI path.
`-PlanOnly` checks every selected CLI request without hardware; it does not
count planned cases as physical PASS.
See [Controlled instrument validation](#controlled-instrument-validation).

`scripts/preflight-cli.ps1` covers the offline CLI and ordinary LIVE refusal
behavior and Manifest exposure. Use `-TransientOnly` for focused Transient
checks, `-FixedSlewVonShortOnly` for the offline Slew, VON and Short cases,
or `-InputTimerOnly` for Timer.
Preparing hardware scripts and
executing hardware tests are separate activities: physical runs require the
operator gates below. Normal CI continues to run no-hardware checks.

## Controlled instrument validation

`scripts/live-cli-check.ps1` is the **one** CLI hardware verification entry
point. It defaults to all implemented modes, using supported LIVE commands and
the validation-only path where required. It is an explicit operator-
approved hardware validation workflow, **not** a general unattended control
command and not automatic Product LIVE promotion.

Preview the default **all-mode** plan without opening VISA or touching hardware.
For offline checks, set `ELECTRONICLOAD_TOOL_RESOURCE` to a **fictional** USB
resource. For an approved real run, set it to the exact operator-selected USB
INSTR resource (see the PowerShell setup above):

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -PlanOnly
```

`-Modes` is the sole mode selector and replaces `-IncludeValidationModes`.
Accepted values are `cc`, `cv`, `cr`, `cw`, `transient-cc`, `transient-cv`,
`transient-cr` and `transient-cw`; omit it for all eight. Select a single mode or a comma-separated list:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Modes cc -PlanOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Modes transient-cc -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -PlanOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Modes cc,cv -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -PlanOnly
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Modes cc,transient-cc -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12 -PlanOnly
```

The selected modes run once each in CC, CV, CR, CW, Transient CC, Transient CV,
Transient CR, Transient CW order. Unknown or empty mode names are rejected. Unselected modes have no
validation cases or PASS counts; planned but unexecuted cases remain NOT RUN.

Whenever CV, CR, CW or Transient is selected, including the default all-mode
plan, explicitly declare source limits greater than zero and no more than
**2 A / 18 V**, even for PlanOnly. Before a real run, configure and verify these
limits on the source and confirm the DUT tolerates every planned setting
shown with `-Verbose` or recorded in `report.json`.
The script does not control or verify the source. These script-only bounds do
not expand product support or substitute for a safe source/DUT setup.

The mode-specific trials use `-Voltage 5` for both CV ranges,
`-ResistanceLow 10`, `-ResistanceHigh 1000`, `-Power 1`, `-LevelA 0.1`,
`-LevelB 0.2`, `-RiseSlew 0.1`, `-FallSlew 0.1`, `-WidthA 0.001`,
`-WidthB 0.002` and `-PulseWidth 0.001` by default. Units are V, ohm, W, A,
A/us and seconds. Override these parameters for your approved setup; Core
checks exact model limits without rounding, and Transient CC levels also obey
the script's 2 A ceiling. A separate Auto High trial deliberately
uses **0.5 A/us** for both slew rates while retaining the selected levels.
New Transient defaults are `-VoltageLevelA 5`, `-VoltageLevelB 6` (V),
`-ResistanceLevelA 10`, `-ResistanceLevelB 1000` (Ω), `-PowerLevelA 1` and
`-PowerLevelB 2` (W), sharing the width parameters. CV runs Continuous Auto,
Pulse Low and Toggle High; CR runs Continuous Auto, Pulse High and Toggle High;
CW runs all three without Range. No CV/CR/CW slew is supplied. Auto selection
comes from Core and is recorded as `expected_range` for later readback checks.
An unsuitable parameter combination fails the offline plan before hardware I/O.
By default the Script prints a concise PlanOnly summary; use `-Verbose` to
inspect every planned CLI step and its arguments. Full case details remain in
the redacted `report.json` and `summary.md`.

The CV/CR/CW/Transient sequence covers CV Low/High, CR Low/High, CW, Continuous Auto,
Pulse Low, Toggle High, Continuous High and Pulse Auto High. Each trial
configures with Input OFF, explicitly confirms ON, measures, explicitly turns
OFF and independently checks OFF. Transient additionally enables before ON,
sends two software triggers, queries control status, and disables after OFF.
These checks do not measure waveform shape, timing, slew accuracy or completion;
such measurements require an appropriate external instrument and procedure.

Fixed Auto adds Input OFF setup/readback before the existing manual trials:
CC uses the same `-Current`, CV uses `-Voltage`, and CR uses both
`-ResistanceLow` and `-ResistanceHigh`. Each Auto result must match the range
resolved by Core's offline plan. PlanOnly reports this as `expected_range` while
the case remains NOT RUN. These checks add no Input ON/OFF cycles or higher
setpoints. The CC script ceiling remains 2 A, so Auto High selection above 6 A
is covered offline, without increasing physical loading for that test.

For a separately approved, safe IT8812C + bounded source/DUT setup, run once
in an interactive Windows console:

```powershell
.\scripts\live-cli-check.ps1 -Target itech-it8812c -Connection usb -Resource "$env:ELECTRONICLOAD_TOOL_RESOURCE" -Backend system -SourceCurrentLimitA 0.5 -SourceVoltageLimitV 12
```

For a selected-mode run, keep the desired `-Modes` and source limits from the
preview command and remove `-PlanOnly` after separate approval of the physical
setup. The Script itself supplies the CLI's per-command Input ON `--confirm`.
The same interactive confirmation and failure handling apply to every selection.

The CC trial defaults to 0.1 A, with a script-only maximum of 2 A.
Both low/high configurations must satisfy the model range and programming resolution.
`-Current` retains its original decimal token for Core validation, including
the script's positive/2 A bounds. The single value applies to both ranges; low is selected last for the
bounded ON/OFF trial. These limits do not alter normal product CC support.

The complete LIVE validation requires a user-supplied explicit USB INSTR
Resource, interactive operator confirmation, verified physical Input OFF,
a safe source/DUT and a manual OFF contingency. The Script blocks
noninteractive runs before any physical VISA connection. The opening banner
redacts Resource; the interactive LIVE check displays the selected Resource for
operator verification before any hardware access. Shareable reports keep it
redacted. After offline preflight, the operator presses **Enter once** to run;
Ctrl+C cancels. No magic words, Suite, Stage
or extra prompt are required.

Every selection starts with Identify (precheck), Input State (must be OFF),
and V/I/P measurement with Input OFF. When CC is selected, its steps are
CC High, CC Auto and CC Low setting/readback (with OFF checks before writes);
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
select `--hardware-validation cv|cr|cw|transient-cc|transient-cv|transient-cr|transient-cw` explicitly; the legacy
`--validation-allow-pending-live-support` does not enable these modes. Exact identity/USB/System VISA and Input ON
confirmation requirements remain unchanged; the 2 A bound is only for
the controlled validation script. System VISA library/manufacturer
is reported for diagnostics and cross-command consistency, not independent
operator verification. The script does not validate Worker LIVE lifecycle or
nonzero-load current/power accuracy.

Each CLI command has a **30-second** process deadline; the full run has a
**240-second** I/O budget for CC alone, or **900 seconds** when any other mode
is selected, beginning at Identify. Each explicit cleanup command has a
separate 30-second allowance. The all-eight plan contains 192 command invocations
(191 reported cases plus Identify), leaving an average of about 4.7 seconds per
invocation within 900 seconds. The 30-second per-command ceiling is not a
reservation for every command. Slow hardware may exhaust the unchanged total
budget; use an explicitly selected subset if needed. The script aborts further
normal operations on deadline and retains bounded cleanup allowances.
After a possibly attempted Transient enable, cleanup separately checks Input
OFF before attempting Transient Disable in the active selected scope. If OFF cannot be confirmed, disable
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
printed pages 40, 46, 48 and 50–58. Transient uses `FUNC:MODE FIX`,
`FUNC CURR|VOLT|RES|POW`, separate `TRAN ON/OFF`, `TRIG:SOUR HOLD` and `TRIG:IMM`.
Pulse width maps to the selected function’s `:TRAN:AWID`; Toggle writes neither width.
CV explicitly disables automatic range and selects 18/120 V; CR selects
10/7500 ohm. The validation path requires those corresponding numeric range
readbacks and exact requested setpoints before ON; unfamiliar responses fail
closed for investigation, with no guessed fallback or automatic adjustment.
These are validation requirements, not a claim of physical acceptance or
normal Product LIVE availability.

The existing report layout is retained under
`.tmp_tests/readonly-live-*/shareable/` with `report.json` and
`summary.md`. The `report.json` contains per-case PASS/FAIL/NOT RUN, planned arguments,
expected ranges and selected control readbacks. The `summary.md` lists case
names, statuses and overall counts. Both keep the Resource redacted. Planned and unexecuted cases remain NOT RUN. Default execution retains **no raw
CLI logs or private Resource file**. The existing `-DebugArtifacts` flag
additionally saves raw CLI stdout/stderr/exit information and Resource
details under `private/`. Raw files may contain lab or device identifiers:
review/redact them before sharing; do not commit validation artifacts.
`-DebugArtifacts` adds no instrument writes, no new permission and no
alternate validation flow.

Follow [Testing Guidelines](../testing-guidelines.md) and
[AGENTS.md](../../AGENTS.md).
