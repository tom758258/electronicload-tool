# Electronic Load Worker Contract

This document defines IT8812C-specific behavior under the existing
[Common Worker Protocol](common-worker-protocol.md) and
[Common CLI JSON / JSONL Contract](common-cli-jsonl-contract.md).
Common contracts remain authoritative for the HTTP and JSONL envelopes.

## Versions and lifecycle

- Manifest schema **2**, Worker protocol **3** (`v3-only`).
- `tool_id: "electronicload"`; `execution.model: "multi_job"`,
  `execution.context_binding: "per_command"`.
- `common.async_jobs` contract version 1, for all domain commands.
  Cancellation and streaming are not adopted.
- `electronicload-tool manifest --json` is static: no VISA, filesystem artifacts,
  instrument discovery, or Worker startup.
- Start the Worker using `electronicload-tool worker --mode simulate|live`.
  `--control-port 0` selects an OS-assigned local port; the `ready` event
  reports the chosen numeric-loopback control URLs.
- HTTP endpoints: `GET /status`, `POST /command`, `POST /stop`. Bind only to
  `127.0.0.1`. Do not use proxies or redirects in managed clients.
- `GET /status` observes in-memory state only; it never opens or queries VISA.
- A valid request is accepted as a single async job with HTTP **202**,
  `kind: "async_job"`, and `worker_job_id`. One job can be active at a time;
  another command receives HTTP **409 Busy**. No waiting queue exists.
- An accepted job emits one `job_finished` terminal with `state`, `ok`, `result`,
  `error`, fixed terminal time and job identity. `GET /status.last_job` retains
  the latest matching terminal; `active_job` tracks the current operation.
  Accepted **does not** mean succeeded. A Worker may continue after a failed job.
- The Worker keeps results in memory and emits JSONL to stdout. It creates no
  `request.json`, `result.json`, event log, artifact directory, or job history.
  Human diagnostics go to stderr.
- Stop acknowledges without performing VISA I/O in the HTTP handler, rejects
  further submissions, and prevents a not-yet-sent `INP ON`. Blocking VISA I/O
  already in progress is not forcibly interrupted. Worker Stop **does not**
  imply Input OFF or authorize sending an automatic OFF.
- Normal shutdown emits `summary` and terminates stdout (EOF) with an observed
  process exit. A stop acknowledgement is not shutdown confirmation.

## Commands and context

| Command | Arguments | Result |
| --- | --- | --- |
| `identify` | `{}` | Physical/simulated identity |
| `input-state` | `{}` | `input_state` |
| `measure` | `{}` | `voltage_v`, `current_a`, `power_w` |
| `set-cc` | `{"current": <number>, "range": "auto"\|"low"\|"high"}` | Four confirmed CC settings, physical input stays OFF |
| `set-cv` | `{"voltage": <number>, "range": "auto"\|"low"\|"high"}` | Offline-only CV setpoint simulation; LIVE refused |
| `set-cr` | `{"resistance": <number>, "range": "auto"\|"low"\|"high"}` | Offline-only CR setpoint/range simulation; LIVE refused |
| `set-cw` | `{"power": <number>}` | Offline-only Fixed CW setpoint simulation; LIVE refused |
| `set-transient-cc` | Complete mode-specific configuration below | Offline Transient control status |
| `set-transient-cv` | `transient_mode`, `range`, `level_a`, `level_b` (V), applicable widths (s) | Offline Transient CV status |
| `set-transient-cr` | `transient_mode`, `range`, `level_a`, `level_b` (Ω), applicable widths (s) | Offline Transient CR status |
| `set-transient-cw` | `transient_mode`, `level_a`, `level_b` (W), applicable widths (s); no Range | Offline Transient CW status |
| `transient-enable` | `{}` | Offline Transient control status |
| `transient-disable` | `{}` | Offline Transient control status |
| `transient-trigger` | `{}` | Status with latest accepted software trigger |
| `transient-status` | `{}` | Offline Transient control status |
| `set-list-cc` | `{"range":"auto"\|"low"\|"high","repeat_count":<integer>,"steps":[{"current_a":<number>,"slew_a_per_us":<number>,"width_s":<number>},...]}` | Offline LIST status; selects CC + LIST |
| `list-save` | `{"slot": <integer 1–7>}` | Save configured LIST; explicit overwrite allowed |
| `list-recall` | `{"slot": <integer 1–7>}` | Recall stored LIST and select CC + LIST |
| `list-trigger` | `{}` | LIST status with software-trigger record; no completion inference |
| `list-status` | `{}` | Known simulated LIST configuration/mode/slot/trigger |
| `list-exit` | `{}` | Confirmed Input OFF required; select FIXED, retain LIST data |
| `input-on` | `{"confirm_input_on": true}` | Confirmed `input_state: "ON"` or failure/UNKNOWN |
| `input-off` | `{}` | Confirmed `input_state: "OFF"` or failure/UNKNOWN |

`POST /command` uses exactly the Common v3 top-level fields:
`schema_version: 3`, `command`, optional `arguments`/`job_id`, and required
`context`. The Worker rejects unknown fields, invalid JSON primitive types,
wrong schema tokens, malformed context and unsupported arguments before it
creates a job or performs device I/O. `job_id` is for correlation, not deduplication.

A live request uses `context: {"mode": "live"}` with optional matching
`expected_model_id`. Simulation/dry-run uses the canonical
`planning_model_id: "itech-it8812c"`; the Worker startup mode constrains the
request mode. Worker does **not** allow the contributor-validation override.

## Fixed Software Auto Range

Fixed `set-cc`, `set-cv` and `set-cr` require `range: "auto"|"low"|"high"`;
omission is still HTTP 400. Core tries Low, then High, using the original
setpoint and the existing mode's bounds/resolution. If neither candidate matches,
`no_matching_range` includes a `details.ranges` error code for each candidate,
before job creation. Invalid numeric types/nonfinite values retain their
existing validation errors. Explicit Low/High never fall back to another range.
Fixed CW still rejects any Range argument.

Successful results, including `dry_run`, contain the resolved `range: "low"|"high"`.
Decimal numeric tokens are preserved through `send-command` and HTTP validation;
values are never rounded or clamped. Software Auto selects once during setup,
before VISA access, and never switches range during Input ON. The same
simulated Worker retains the resolved settings across jobs; dry-run does not
inspect or mutate those settings. See [bounds and examples](../fixed-auto-range.md)
([繁體中文](../fixed-auto-range.zh-TW.md)).

Manifest schema 2 adds the tool-specific `range_options: ["auto", "low", "high"]`
to these three command entries beside the unchanged `kind: "async_job"`.
Consumers may use this additive capability field for selection; it is not a
LIVE support declaration. Common Worker protocol 3, HTTP admission/state error
timing, result fields, JSONL envelopes, lifecycle and exits remain unchanged.
CV/CR/CW, Transient and LIST remain Product LIVE blocked. Auto uses the existing
Fixed CC commands, readbacks and Input OFF/ON gates.

## Offline Transient CC / CV / CR / CW

The [Transient interface](../transient-modes.md) ([繁體中文](../transient-modes.zh-TW.md))
defines all four setup commands, limits and units. CV/CR/CW use the same waveform
modes and width-field requirements as CC, with no slew arguments. CV/CR require
`auto|low|high` for both levels together, using their Fixed limits/resolution;
CW rejects Range and uses 0–250 W / 0.01 W. CV/CR/CW widths are finite values
within 0.00002–3600 s, without a fixed decimal step. Original numeric tokens
survive CLI/HTTP validation. Invalid/missing/extra fields and non-number values
receive HTTP 400 before creating a job. State and LIVE refusals retain the
existing HTTP 202 admission followed by a failed terminal.


`set-transient-cc` requires `transient_mode` (`continuous`, `pulse`, `toggle`),
`range` (`auto`, `low`, `high`), `level_a`, `level_b` in A, and `rise_slew`,
`fall_slew` in A/μs. Continuous additionally requires `width_a` and `width_b`;
Pulse requires `pulse_width` (Level A duration); Toggle accepts no width fields.
Widths are in seconds. Every numeric argument must be a finite JSON number,
not a boolean or numeric string. Missing, unknown or inapplicable fields are
rejected with HTTP 400 before job creation.

For example, submit this request to one simulated Worker's `POST /command`:

```json
{
  "schema_version": 3,
  "command": "set-transient-cc",
  "arguments": {
    "transient_mode": "continuous", "range": "auto",
    "level_a": 1, "level_b": 2,
    "width_a": 0.001, "width_b": 0.002,
    "rise_slew": 0.1, "fall_slew": 0.1
  },
  "context": {"mode": "simulate", "planning_model_id": "itech-it8812c"}
}
```

Core validates the complete request before mutation. Low permits 0–6 A in
0.0001 A steps and slew 0.0001–0.25 A/μs. High permits 0–60 A in 0.001 A
steps and slew 0.001–2.5 A/μs. Widths permit 0.00002–3600 s in 0.000001 s
steps. Auto tries Low, then High, considering both current levels, their
resolution and both slew values together. Explicit ranges are never changed.
No rounding, clamping or undocumented slew step is applied. The HTTP parser and
`send-command --arguments-json` preserve decimal numeric tokens for validation.
See the [source manuals and CLI usage](../../README.md#offline-transient-cc-dynamic-mode).

| Operation | Preconditions and effects |
| --- | --- |
| Setup | Input OFF, Transient disabled; replace the complete configuration and clear the last trigger |
| Enable | Configured and Input OFF; enable Transient without changing Input |
| Input ON | Existing per-request confirmation, valid enabled Transient configuration, Input OFF, Short OFF and Worker not stopping |
| Trigger | Configured, enabled, Input ON and Worker not stopping; record a software trigger |
| Input OFF | Existing explicit operation; leaves Transient enabled |
| Disable | Input OFF; clear enabled only, retaining configuration and last trigger |
| Fixed setup | Explicitly disable Transient first; successful Fixed setup invalidates Transient configuration and trigger history |

State-precondition failures occur during the accepted job and produce a failed
`job_finished`; admission alone is not execution. Trigger completion means the
software trigger was executed, not that any waveform ended. Repeated requests
are accepted whenever the same prerequisites hold. No timer, waveform, restart,
phase alignment or physical activity/completion state is inferred. No command
implicitly enables/disables Input or performs rollback. Stop remains a signal,
never an implicit Input OFF; it blocks new Transient mutations and Input ON.

All eight Transient commands return the same simulated domain status shape:

```json
{
  "configured": true,
  "function": "CC",
  "mode": "continuous",
  "range": "low",
  "enabled": true,
  "last_trigger": {
    "sequence": 1,
    "accepted_at_utc": "2026-01-01T00:00:00+00:00",
    "source": "software"
  },
  "configuration": {
    "transient_mode": "continuous", "range": "low",
    "level_a_a": 1, "level_b_a": 2,
    "width_a_s": 0.001, "width_b_s": 0.002,
    "rise_slew_a_per_us": "0.1", "fall_slew_a_per_us": "0.1"
  }
}
```

`configuration` includes only the selected mode's width fields (`pulse_width_s`
for Pulse, none for Toggle). CC levels/widths remain JSON numbers and its slew
fields remain exact decimal strings. CV/CW levels use `level_a_v`/`level_b_v`
and `level_a_w`/`level_b_w` as JSON numbers; CR uses `level_a_ohm`/`level_b_ohm`
as exact decimal strings. New modes' widths are exact decimal strings with the
same `_s` suffixes. There are no CV/CR/CW slew fields. `function` is CC/CV/CR/CW;
`mode` remains the waveform mode. Range resolves to `low`/`high`, or null for
CW (also in `configuration`). Before setup, `configured` and `enabled` are false,
and `function`, `mode`, `range`, `configuration`, `last_trigger` are null.
Before a trigger, `last_trigger` is null; its sequence starts at 1 after setup.
Status queries and later triggers do not mutate earlier result snapshots.

`transient-status` is an ordinary async domain command. `GET /status` keeps its
existing Worker/runtime/job shape and makes no instrument queries. Use the
separate `input-state` command for Input. All state lives in the simulated Worker;
standalone CLI simulation is fresh for each invocation.

Common `context.mode: "dry_run"` maps to Core `dry-run`: results contain
`command` and `hardware_access: false`, plus the validated configuration fields
at the top level for setup, including `function`. A plan neither inspects nor mutates simulator state.
A valid LIVE request keeps existing HTTP 202 admission, then terminates with
`unsupported_live_scope` before VISA access. The CLI contributor override cannot
enable physical Transient support. Existing physical Fixed CC/Input behavior
and Common v3 envelopes, lifecycle and single-job admission remain unchanged.

At normal workflow completion, the Orchestrator must explicitly attempt
`input-off` and confirm a successful OFF result before requesting Worker Stop.
Trigger completion, Transient Disable, Stop acknowledgement, process exit and
VISA close are not Input OFF confirmation.

## Offline native LIST CC

The complete LIST JSON, limits, status fields and mode guards are defined in
the [LIST interface](../list-cc.md) ([繁體中文](../list-cc.zh-TW.md)). Worker
arguments are that JSON object directly, without a wrapping file/configuration
field. Only CLI loads files. Unknown/missing/duplicate fields, nonfinite or
non-numeric Step values, invalid slots/counts and invalid complete-range
requests fail with HTTP 400 before job creation. Integer fields require integer
tokens; `1.0`, booleans and numeric strings are rejected. Decimal tokens survive
CLI forwarding and HTTP parsing without binary-float rounding.

LIST uses ordinary async jobs, Manifest schema 2 and Worker protocol 3.
State-precondition failures yield failed `job_finished` events after HTTP 202
admission. A valid LIVE LIST request is also admitted, then fails with
`unsupported_live_scope` before opening VISA. Neither CLI contributor override
nor Worker Input ON authorization grants physical LIST support.

Input ON/OFF remain the shared commands. Setup/Recall/Save/Exit require
confirmed OFF and disabled Transient. Setup/Recall select CC + LIST; they
replace inactive Transient setup without disabling an active one. Fixed setup
and Transient mutations require explicit LIST exit first. Save may operate on
retained configuration after exit. Empty Recall fails without mutation.
Trigger requires configured selected CC LIST, Input ON, Transient OFF, Short
OFF and Worker not stopping. Every LIST mutation checks Stop. Status is
read-only and returns independent in-memory snapshots with
`state_source: "simulate"`; `GET /status` keeps its Worker/job shape.

Dry-run validates data only, with `hardware_access: false`, and leaves active
simulation untouched. The simulator has no timer/waveform or completion state.
`job_finished` for Trigger never proves LIST completion, including finite
repeat; 65536 means infinite. Input OFF and LIST exit are explicit and separate.
Stop is I/O-free and does not change Input. The Orchestrator must attempt and
confirm explicit Input OFF before Worker Stop on normal/error/user-stop exits;
Input OFF does not prove the internal LIST engine aborted.

## Safety

Core checks the IT8812C current range for LIVE `set-cc` and `input-on`;
`set-cc` also validates programming resolution. There is no operator-supplied
Safety Config or extra DUT current ceiling. Model range/current checks do not
guarantee DUT safety or enforce the full voltage/power operating envelope.

Fixed CV can be planned and simulated, with published IT8812C Low
0.1–18 V (1 mV) / High 0.1–120 V (10 mV) validation and required
Auto/Low/High selection. Physical CV range SCPI and readback semantics remain unverified.
LIVE `set-cv` and LIVE CV `input-on` are blocked, including when an
operator uses the contributor-validation CLI override; the simulated CV
readback does not certify hardware support.

Fixed CR supports `simulate` and `dry_run` with Auto, Low **0.05–10 Ω**
or High **10–7,500 Ω**. Both ranges include 10 Ω; Auto prefers Low. `resistance` must be
a finite JSON number (not a boolean or numeric string); no decimal-place or
fixed-step restriction or rounding is applied. The
published 16-bit resolution does not define a confirmed decimal step.

The result contains `resistance_ohm` and `range`. Simulation also reports
`function_mode: "FIX"`, `function: "RES"`, `range_readback_ohm` and
`input_state: "OFF"`. The numeric range readback is a simulator convention,
not physical RES/range evidence. Dry-run returns a plan with
`hardware_access: false` and does not mutate simulated state. The Worker maps
Common `dry_run` to Core `dry-run` using the same control-command path.

In one simulated Worker, `set-cr` can be followed by `input-on` with
`confirm_input_on: true`, then an explicit `input-off`. Setup while Input is ON
is refused. No circuit/DUT model or resistance-derived V/I/P accuracy is claimed.
LIVE `set-cr` fails in Core before opening VISA, and physical CR Input ON is
refused before any write, even with the CLI contributor-validation override.
A valid LIVE `set-cr` request retains the existing async admission behavior:
HTTP 202 means accepted; its terminal reports `unsupported_live_scope`.
Invalid CR arguments are rejected with HTTP 400 before creating a job.

Fixed CW supports `simulate` and `dry_run` with **0–250 W inclusive** in
**0.01 W** steps, including 0 W. The required `power` is a finite JSON number,
not a boolean or numeric string. Missing power, invalid types, out-of-range
values, non-step-aligned values and extra arguments (including `range`) are
rejected with HTTP 400 before job creation. Core owns these offline model
limits; no rounding, clamping or range selection is applied.

CW results contain `power_w`. Simulation also returns `function_mode: "FIX"`,
`function: "POW"` and `input_state: "OFF"`, after verifying simulated readback.
The internal simulated power readback is not a physical POW/POW:RANG SCPI
contract. Dry-run returns `command: "set-cw"`, `hardware_access: false` and
`input_on_requires_confirmation: false`; it does not change simulated state.
No Fixed CW result contains a range. These offline settings do not establish a
circuit/DUT model or power-derived V/I/P measurement accuracy.

Within the same simulated Worker, CW uses the existing confirmed `input-on`
and explicit `input-off` commands. Setup while Input is ON is refused. A valid
LIVE `set-cw` retains HTTP 202 async admission, then fails with
`unsupported_live_scope` before opening VISA. Physical CW Input ON is refused
before any control write or power query, even with the CLI contributor override.
CW support does not change Worker Stop, cleanup or existing Product LIVE scope.

LIVE Input ON has two gates: the Worker starts with `--allow-input-on`, and the
single `input-on` request carries `confirm_input_on: true`. Neither gate
replaces Core's physical checks. Before LIVE ON, Core requires physical input OFF,
CC+FIX, valid current/range within the model range, Transient OFF and Short
OFF. It does not compare against any previous `set-cc` command. An existing ON
is rejected. Readback failure following an ON attempt returns UNKNOWN, not
success, and does not trigger automatic OFF.

After exact identity verification, LIVE `set-cc` checks physical
`INP:STAT?` OFF before sending `SYST:REM` (required by the IT8800
Programming Guide). It then performs `FUNC:MODE FIX`, `FUNC CURR`, range
and current writes, each immediately preceded by a **fresh** `INP:STAT?`
OFF confirmation. Four setting readbacks and a final Input OFF query must
all succeed before reporting success. The instrument is not switched back
to local automatically (`SYST:LOC` is not sent).

For low-range `set-cc`, Core sends `CURR:RANG <requested current>` before
`CURR <requested current>`. It does not use `CURR:RANG MIN`: on IT8812C,
that selector can leave the effective range at zero and cause a nonzero
current command to be rejected. The high-range path remains `CURR:RANG MAX`.
Both paths require verified setpoint and range readback and physical Input OFF.

`CURR:RANG?` returns a numeric range-selection value rather than a required
6/60 ceiling. IT8812C selects its highest-resolution low range for selector
values 0 through 6 A, and high range for greater than 6 through 60 A.
Out-of-bounds or nonnumeric replies are rejected. Even if the range reply is
zero, a requested nonzero `CURR` must read back correctly, or the whole
command fails; diagnostics include the finite numerical readbacks.
Readback failures stop without rollback, Input OFF or automatic retries.

LIVE `input-on` checks all physical safety prerequisites **before and after**
`SYST:REM`, including the Worker stop gate, before `INP ON` is permitted.
`input-off` is an independent explicitly requested operation; it sends
`SYST:REM` followed by `INP OFF` and verifies physical OFF. After exactly
one `INP ON` or `INP OFF` write, Core polls only `INP:STAT?` for at most
2 seconds (approximately 100 ms between reads) to confirm the target state;
there is no repeat write. The existing per-query VISA timeout remains
unchanged and can make an in-flight query exceed the polling budget.
A query error, invalid reply or expired confirmation budget fails closed
with an unconfirmed `input_state: UNKNOWN`, never an assumed ON/OFF. An
unsuccessful `input-on` does not implicitly send OFF. Worker Stop remains
separate from instrument I/O. The initial read-only commands never switch
remote mode. Closing the session never means Input OFF or local mode.
Neither `SYST:RWL` nor `SYST:LOC` is sent.

Product LIVE is open only for the exact IT8812C identity over USB INSTR /
System VISA: `identify`, `input-state`, `measure`, `set-cc`, `input-on`
and `input-off` (input-on is physical fixed CC only). The control commands retain Core safety checks, explicit
Resource requirements, model limits and Input ON confirmation gates.
The Worker uses this same Core support policy and its own startup/per-request
Input ON authorization; its end-to-end physical LIVE behavior is not implied
to be hardware-validated by CLI acceptance. The hidden contributor-validation
CLI option remains for compatibility, is not used by the Worker, and cannot
bypass identity or connection restrictions. Dry-run/simulation do not certify
real hardware behavior.


## Separate CLI hardware validation

The standalone CLI offers an explicit `--hardware-validation` option for
controlled contributor CV/CR/CW/Transient trials. It is not a Worker command,
argument, context, feature, or LIVE promotion. The Worker does not forward this
option or its `validation_settings` and continues to reject extra arguments.
All LIVE refusal and admission/terminal behavior described above remains in
force, including for the legacy contributor override. The dedicated CLI path
requires complete expected settings for Enable, Input ON and Trigger and
compares physical readback; its status describes control state, not waveform
completion. See the README's controlled instrument validation procedure.
