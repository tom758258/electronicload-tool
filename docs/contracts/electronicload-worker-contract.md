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
  `common.job_cancel` contract version 1 lists only `trace-start`.
- `electronicload-tool manifest --json` is static: no VISA, filesystem artifacts,
  instrument discovery, or Worker startup.
- Start the Worker using `electronicload-tool worker --mode simulate|live`.
  `--control-port 0` selects an OS-assigned local port; the `ready` event
  reports the chosen numeric-loopback control URLs.
- HTTP endpoints are `GET /status`, `POST /command`, `POST /stop` and
  `POST /cancel`. Bind only to
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
  TRACE and sampling workflows can each write one explicitly requested local
  CSV. Human diagnostics go to stderr.
- Stop acknowledges without performing VISA I/O in the HTTP handler, rejects
  further submissions, and prevents a not-yet-sent `INP ON`. Blocking VISA I/O
  already in progress is not forcibly interrupted. Worker Stop **does not**
  imply Input OFF. An active Software Sequence or CC workflow owns bounded
  OFF cleanup after attempting Input ON, subject to communication trust.
- Normal shutdown emits `summary` and terminates stdout (EOF) with an observed
  process exit. A stop acknowledgement is not shutdown confirmation.

## Commands and context

| Command | Arguments | Result |
| --- | --- | --- |
| `identify` | `{}` | Physical/simulated identity |
| `input-state` | `{}` | `input_state` |
| `readback` | `{}` | Active mode, Input, Short and active configuration summary; offline only |
| `fixed-status` | `{}` | Fixed setpoint and applicable Range; offline only |
| `fixed-slew-status` | `{}` | Fixed CC rising and falling slew; offline only |
| `measure` | `{}` | `voltage_v`, `current_a`, `power_w` |
| `sequence` | `{"document":{...},"confirm_input_on":true}` (confirmation when running with ON) | One async Software Sequence result, offline only |
| `measure-log` | `{"interval_s":1,"count":60}` | Periodic V/I/P summary, offline only |
| `measure-until` | `{"interval_s":1,"metric":"voltage","operator":"le","threshold":4.5,"timeout_s":600}` | Conditional measurement summary, offline only |
| `cc-timed-load` | `{"current":0.5,"duration_s":30,"confirm_input_on":true}` | Timed CC summary, offline only |
| `cc-discharge` | `{"current":0.5,"cutoff_voltage":3,"timeout_s":7300,"confirm_input_on":true}` | CC discharge summary with calculated Ah, offline only |
| `measure-min-max` | `{}` or `{"metric":"voltage-min"\|"voltage-max"\|"current-min"\|"current-max"}` | All four native extrema, or only the selected field; offline only |
| `measure-discharge` | `{}` or `{"metric":"capacity"\|"time"}` | `capacity_ah`, `discharge_time_s`, or only the selected field; offline only |
| `scpi-version` | `{}` | `scpi_version`, a `YYYY.V` string; offline only |
| `display-mode` | `{}` | `display_mode`, `NORMAL` or `TEXT`; offline only |
| `error` | `{}` or `{"all":<boolean>}` | One Error Queue entry or a bounded read summary; offline only |
| `set-average` | `{"count":<integer 2–16>}` | Integer `average_count`; confirmed Input OFF required; offline only |
| `average-status` | `{}` | Integer `average_count`, including with Input ON; offline only |
| `trace-configure` | `{"source":"VOLT"\|"CURR"\|"TWO","points":<integer>,"interval_s":<number>}` | Validated TRACE settings and sample count; offline only |
| `trace-status` | `{}` | Source, points, interval, delay, filter and Feed; offline only |
| `trace-start` | `{}` or `{"csv":"PATH","csv_format":"data"\|"full","csv_time_columns":"elapsed,delta"}` | Complete TRACE batch; offline only |
| `set-cc` | `{"current": <number>, "range": "auto"\|"low"\|"high"}`, with optional paired `rise_slew` and `fall_slew` (numbers, offline only) | Fixed CC settings with Input OFF, plus Slew fields when supplied |
| `set-cv` | `{"voltage": <number>, "range": "auto"\|"low"\|"high"}` | Offline-only CV setpoint simulation; LIVE refused |
| `set-cr` | `{"resistance": <number>, "range": "auto"\|"low"\|"high"}` | Fixed CR settings with LED confirmed OFF; offline only |
| `set-cr-led` | `{"resistance": <number>, "range": "auto"\|"low"\|"high", "vdrop": <number>}` | Fixed CR settings with LED confirmed ON and Vdrop readback; offline only |
| `cr-led-status` | `{}` | Read-only LED, Vdrop, resistance, Range and mode; offline only |
| `current-slow-rise-enable` | `{}` | `enabled: true`; Fixed CC and Input OFF required; offline only |
| `current-slow-rise-disable` | `{}` | `enabled: false`; Input OFF required; offline only |
| `current-slow-rise-status` | `{}` | Read-only `enabled` flag, available in any mode with Input ON or OFF; offline only |
| `set-cw` | `{"power": <number>}` | Offline-only Fixed CW setpoint simulation; LIVE refused |
| `set-von` | `{"voltage": <number>, "latch": "on"\|"off"}` | Offline VON settings, confirmed Input OFF required |
| `von-status` | `{}` | Read-only simulated VON settings |
| `short-on` | `{}` | Confirmed simulated Short ON, with Input ON and Fixed CC/CV/CR/CW required |
| `short-off` | `{}` | Confirmed simulated Short OFF, independent of Input |
| `short-status` | `{}` | Read-only simulated Short state |
| `set-input-timer` | `{"seconds": <integer 1–60000>}` | Offline Timer seconds and enabled state; confirmed Input OFF and Fixed mode required |
| `input-timer-enable` | `{}` | Offline Timer state; confirmed Input OFF, Fixed mode and Short OFF required |
| `input-timer-disable` | `{}` | Offline Timer state; confirmed Input OFF required |
| `input-timer-status` | `{}` | Read-only offline Timer state; allowed with Input ON |
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
| `trigger-configure` | `{"source":<source>}` or `{"source":"TIMER","interval_s":<number>}` | Offline Trigger Setup |
| `trigger-status` | `{}` | Stored Trigger Source and Timer interval |
| `protection-status` | `{}` or `{"events":true}` | Input, settings, condition register, optional read-and-clear event register |
| `protection-set` | `{"confirm":true,...}` | Verify and apply only the requested settings or one power defaults group |
| `protection-clear` | `{"confirm":true}` | Clear simulated latched faults with Input OFF |
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

## Software Sequence

`sequence` takes a version 1 JSON document in `arguments.document`. The
document and argument schema are described in the [CLI guide](../cli/README.md#software-sequence).
The HTTP request limit remains 65,536 bytes. Duplicate JSON keys, nonfinite
numbers and invalid documents return HTTP 400 without creating a job. Fixed
setpoints and Wait use original decimal tokens. Step validation failures report
`sequence_precheck_failed` with one-based `loop_index`, `step_index`, `action`
and a machine-readable `reason`. The repeated document is checked from the
same OFF/no-setup state, so static step violations report the first loop.

In a run containing `input-on`, missing or false `confirm_input_on` returns
HTTP 409. The boolean is optional in `dry_run`, whose plan gives the resolved
steps, loop and expanded step counts, `hardware_access: false` and
`input_on_requires_confirmation`. Decimal step arguments in the plan are
strings. Current simulator state is neither read nor modified in a dry run.

Accepted requests receive HTTP 202 and run as one serial job. Simulated steps
share the Worker's existing simulator. A valid LIVE request on a live Worker
fails with `unsupported_live_scope` before VISA. TRACE recovery restrictions
apply to Sequence, and only `trace-start` supports Cancel.

Execution checks Input OFF before step one and at the end of each loop. If
Input starts ON, the terminal reports `input_not_off` and leaves it ON.
A successful result includes `loop_count`, `completed_loops`, `completed_steps`,
`measurements`, `input_states`, final `input_state` and `cleanup`. Each reading
includes its one-based loop and step and the action, plus V/I/P or Input state.
`cleanup` reports `required`, `attempted` and `off_confirmed`.

An interrupted Wait or other Stop failure produces a `failed` terminal.
With trusted communication, the execution thread attempts bounded OFF if
the Sequence may have turned Input ON and OFF remains unconfirmed. An
already attempted explicit OFF is never resent after that Step fails.
Untrusted communication stops further I/O. A failed job has `result: null`;
its `error.details` carries the progress fields above, `failed_step` (null
outside a step), and `original_error`. When Input OFF remains unconfirmed
after attempting to switch Input or entering Remote during setup or Input ON,
the terminal uses `sequence_cleanup_unconfirmed` and directs the operator to
independently switch off the load. Once the initial OFF check succeeds, a
failure with Input still observed ON gives the same instruction, reports
`input_state: "ON"` and `cleanup.reason: "input_not_off"`, and preserves the
original failure. It reports `"UNKNOWN"` when the Input state cannot be
determined. If communication is lost after the latest Input readback observed
ON, the terminal gives the same manual OFF instruction and preserves the
communication error in `original_error`. The Worker emits no per-step events
and creates no Sequence artifacts.

## Basic Workflows

The four commands are independent Common v3 async jobs. The command table
shows example arguments. `measure-log` requires `interval_s` and exactly one
of `count` or `duration_s`. The three sampling commands also accept an
optional local `csv` path. Both CC commands accept optional `range`, with
`auto` as the default. CC discharge also accepts `interval_s` (defaults to
1 second) and `cutoff_capacity_ah`. The [CLI guide](../cli/README.md#basic-workflows)
defines the bounds, units, operators, and CSV columns.

Worker arguments use finite JSON numbers, integer `count` (1 to 10,000),
and boolean `confirm_input_on`. `duration_s` and `timeout_s` must be positive
and have no fixed maximum. `interval_s` accepts 0.001 to 3600 seconds.
Malformed arguments, duplicate JSON fields, and unknown
fields fail before admission with HTTP 400. The simulated CC commands require
`confirm_input_on: true`; omission or false returns HTTP 409. Valid LIVE
requests on a live Worker are admitted and finish with
`unsupported_live_scope` before VISA access. Valid `dry_run` requests
return `hardware_access: false`, `input_on_requires_confirmation`,
`csv_requested`, and the resolved `arguments` with decimal numbers as
strings. Dry-run omits the confirmation flag and local CSV path from the plan
and performs no simulator I/O or file creation.

Simulated jobs share the Worker's existing simulator. A successful result
has `elapsed_s` and `stop_reason` (`completed`, `condition_met`,
`duration_elapsed`, `voltage_cutoff`, `capacity_cutoff`, or
`sample_limit_reached`). Sampling results also include `sample_count` and
`last_measurement` (null before the first sample). Sampling stops after at
most 10,000 actual samples. `measure-log` with `count` returns `completed`
at the requested count. If the sample limit is reached before another stop
condition, the job returns `sample_limit_reached`; CC discharge runs its
Input OFF cleanup before reporting the result. CC results include
`current_a`, resolved `range`, `input_state`,
and `cleanup` with `required`, `attempted`, and `off_confirmed`.
Timed load includes `duration_s` and does not sample. Discharge samples `voltage_v`, `current_a`
and `power_w` in sequence. It checks voltage before querying current and
integrates `capacity_ah` using consecutive current-reading timestamps
before querying power. Either cutoff ends sampling immediately. A partial
sample counts toward `sample_count`, and `last_measurement` contains only
the fields collected for that sample. When preflight reads
`initial_voltage_v`, the result includes it separately from the load
samples. If the initial voltage is at or below the cutoff, the job succeeds
with `voltage_cutoff`, zero samples, zero Ah and confirmed Input OFF.
`csv_written: true` confirms completion of the requested export.
There are no per-sample events or sample arrays in results.

The measurement jobs preserve Input state. The CC jobs require Input OFF at
entry, verify Fixed CC before Input ON, and attempt OFF once and confirm it
when communication remains trusted and the workflow attempted ON. If the
initial Input is ON, the job fails with `input_not_off` and leaves it in its
observed state. If communication becomes untrusted, the job ceases
instrument I/O. If OFF remains unconfirmed when the workflow might have
changed Input state, or communication becomes untrusted in a CC run, the job
reports `workflow_cleanup_unconfirmed`, `manual_action_required: true`, and
the known state (`UNKNOWN` if it cannot be determined). The error tells the
operator to independently switch off the load. When this error occurs, the
Worker includes the most recent such job terminal in the optional
`summary.workflow_cleanup_failure` field, even after a subsequent successful
job. This records historical evidence for the current Worker run. Consumers
should retain terminal and cleanup evidence and independently confirm the
Input state. The field leaves `last_job`, Common summary and process exit
meanings intact.

Stop interrupts the wait between samples or during timed load. A Stop or
timeout produces a failed terminal (normally `stopping` or
`workflow_timeout`), with progress and cleanup evidence in
`error.details` and `result: null`. After CSV creation, an execution or
export failure retains the file with `csv_incomplete: true`. The CSV is
created exclusively before sampling or enabling Input, following the
existing local-path rules. Discharge buffers the bounded sample rows and
exports them once the OFF cleanup path has finished, including after Stop or
timeout. Unmeasured fields in a partial sample are blank in the CSV. A
`workflow_cleanup_unconfirmed` failure skips row export and leaves the
header-only CSV marked incomplete. A second failure exporting the rows
preserves the original execution error and adds
`error.details.csv_export_error`. The TRACE recovery restrictions also
apply to these commands. Cancel is available only for `trace-start`.

## Configuration readback

`readback`, `fixed-status`, `fixed-slew-status` and `trace-status` are
`async_job` commands with empty arguments. They read the current simulated
Worker session state and return `state_source: "simulate"`. Use
`context.mode: "dry_run"` for a preview with `hardware_access: false` and
an empty `planned_writes` array. The new commands decline LIVE requests
with `unsupported_live_scope` before opening VISA.

`readback` returns `active_mode` (`fixed`, `transient`, or `list`),
`function_mode`, `function`, `transient_enabled`, `input_state`,
`short_state`, and mode-specific `configuration`. Disabled Transient reports
`active_mode: "fixed"` and `function_mode: "FIX"` unless LIST is selected.
The Transient summary contains waveform mode, applicable Range and levels
A/B. The LIST summary contains Range, repeat count and step count. `fixed-status`
returns the Fixed setpoint in its unit-specific field and the applicable
Range. `fixed-slew-status` requires Fixed CC and reports `configured` and
the two slew rates as decimal strings or null before simulated configuration.
An incompatible mode fails the job with `mode_not_fixed`.

`trace-status` returns `source`, `points`, `interval_s`, `delay_s`,
`filter_enabled`, and `feed`. Interval and delay are decimal strings, and
Feed is `NEXT` or `NEVER`. The job only observes TRACE configuration. Use
`trace-start` for acquisition and its separate cancellation contract.

Existing dedicated status commands retain their simulated result shapes.
The standalone CLI contributor scopes are described in the
[CLI readback reference](../cli/README.md#configuration-readback).
`GET /status` continues to expose Worker and job lifecycle data.

## Offline System Queries

The three commands are async jobs. Simulation retains the Error Queue across
jobs in the same Worker and each Error Queue read consumes one FIFO entry.
`error` with omitted or false `all` returns one integer `error_code`
and string `message`. A zero code represents an empty queue. With `all: true`,
it returns `errors` (nonzero entries in order), `read_count` (including any zero
reply), `max_reads: 20`, `empty_confirmed` and `limit_reached`. A zero on any
read, including the 20th, confirms empty. A nonzero 20th read reaches the limit
with the remaining queue state unknown. Nonzero instrument codes are successful
query data. Dry-run returns the query plan with no queue consumption.

Malformed arguments receive HTTP 400. Valid LIVE requests on a live Worker
receive HTTP 202 and finish with `unsupported_live_scope` before opening VISA.
The standalone CLI separately permits these three basic diagnostics for normal
IT8812C USB/System VISA requests. Its contributor System Query scope remains
CLI-only, including ASRL. Neither CLI path grants Worker LIVE permission.
[CLI queries and contributor procedure](../cli/README.md#system-queries)

## Offline Protection

The three Protection commands are async jobs. Status accepts an optional
boolean `events`, default false. It returns `input_state`, `settings`,
`condition` and, if requested, `events`. Each register has `raw` (integer)
and `flags` (array of known mnemonics). Explicit event reads consume the
simulated event register. Dry-run previews the request without changing state.

Set accepts `confirm: true` and one or more of `software_current_enabled`
(boolean), `software_current_limit` (A), `software_current_delay` (s),
`software_power_limit` (W), `software_power_delay` (s) and
`hardware_power_limit` (W). Limits and delays are JSON numbers. Alternatively,
use `restore_defaults: "software-power"` or `"hardware-power"` with
`confirm: true`. Individual settings and defaults are mutually exclusive.
Status and Set preserve decimals as strings in `settings` and `applied`,
respectively. Set and Clear require Input OFF and fail without changing state
if Input is ON. Clear returns `input_state` and the resulting `condition`.

Malformed arguments return HTTP 400. Missing confirmation follows the existing
HTTP 409 confirmation rejection. Legal LIVE requests on a live Worker receive
HTTP 202 and finish with `unsupported_live_scope` before opening VISA.
Stop, Cancel and `GET /status` retain their Common v3 behavior.

## Offline CR-LED and Current Slow Rise

The five commands are async jobs in Manifest schema 2 and Worker Protocol 3.
`set-cr-led` advertises the same Range choices as `set-cr`. The Worker
retains numeric tokens through argument validation and returns `vdrop_v` as a
decimal string. CR setup and status return `led_enabled`, `fixed_cr`,
`function_mode`, `function`, `resistance_ohm`, `range` and `range_readback_ohm`.
CR setup and Slow Rise mutations additionally return confirmed
`input_state: "OFF"`. Invalid arguments fail at admission (HTTP 400).
Valid LIVE jobs end with `unsupported_live_scope` before opening VISA.
The CLI contributor validation scope is unavailable through the Worker.

A simulated Worker retains configuration across jobs. Plain CR explicitly
turns LED OFF and verifies readback. Selecting another load mode from
CR-LED requires confirmed LED OFF before the switch. Enabled Current Slow
Rise blocks non-Fixed-CC selection. Fixed CC setup and Input OFF preserve
Slow Rise. Dry-run validates the request without touching simulator state.
Worker Stop remains I/O-free. Product LIVE Fixed CC refuses every reported
resistance function, including ordinary Fixed CR with LED OFF. Verifying
the LED state requires a separately authorized scope.
The Worker cannot confirm the instrument's Slow Rise state on LIVE Input ON.
Before returning an instrument from Slow Rise contributor validation to normal
Worker operation, explicitly disable Slow Rise with Input OFF and confirm the
disabled state through the contributor CLI.
[CLI operation and validation](../cli/README.md#cr-led-and-current-slow-rise)

## Offline Measurement Extensions

The four measurement extension commands are ordinary async jobs in Manifest
schema 2 / Worker protocol 3. They use the shared Core for SCPI selection,
numeric parsing, validation and permissions. The Worker read path uses the
same Core execution entry as the CLI, including its Product LIVE allowlist.
Valid LIVE requests retain HTTP 202 admission, then fail with
`unsupported_live_scope` before opening VISA. Worker Input ON authorization,
the legacy CLI override and CLI hardware-validation scopes do not open them.

`measure-min-max` with `{}` issues `MEAS:VOLT:MIN?`, `MEAS:VOLT:MAX?`,
`MEAS:CURR:MIN?`, `MEAS:CURR:MAX?` in order, returning `voltage_min_v`,
`voltage_max_v`, `current_min_a`, `current_max_a`. `measure-discharge` with `{}`
issues `MEAS:CAP?`, then `MEAS:TIME?`, returning `capacity_ah` and
`discharge_time_s`. Both first perform the existing `*IDN?` identity check.
`metric` selects just one measurement query and result field, using the
[CLI metric mapping](../cli/README.md#measurement-extensions).
Omission selects all; null, non-string, unknown, empty and extra arguments
are HTTP 400 before job creation. Measurement fields are finite JSON numbers.
A failed query fails the job with no fabricated partial success, automatic
`FETC` fallback or retry. Independent readings are sequential, without host
accumulation, synchronization, mode restrictions or Battery Test/reset control.

`count` requires a JSON integer token 2–16; missing, boolean, string,
fractional/exponent-form, nonfinite, out-of-range and extra arguments are
HTTP 400 before job creation. Set requires confirmed Input OFF before remote
transition and again before the native count write, then verifies readback.
Status only reads identity/count, including with Input ON. Neither changes
Input, load mode or TRACE filtering. Stop blocks Set and performs no I/O;
Status remains read-only. State failures occur during the accepted job.

One simulated Worker retains Average count (initially 2) across jobs, including
Input OFF and mode changes. It returns deterministic example Min/Max and
discharge readings, without simulated measurement history or elapsed-time
progression. Common `dry_run` maps to Core `dry-run`, validating arguments
without inspecting or changing simulated state. Previews and standalone CLI
behavior are described in the CLI reference above. `GET /status`, HTTP/JSONL
envelopes, single-job admission, Stop and the existing `measure` behavior and
LIVE scope retain their contracts. Offline results do not prove hardware support.

## Offline TRACE

Both commands are Common v3 async jobs. Configure requires `source`, a JSON
integer `points` and a finite JSON number `interval_s`. Start accepts an empty
object or a non-empty `csv` path with optional `csv_format` and
`csv_time_columns` strings. The latter accepts an empty string, either column
token, or both comma-separated. Invalid arguments fail before job creation
(HTTP 400). State failures and valid LIVE refusals retain HTTP 202 admission
and a failed terminal. LIVE requests fail before opening VISA. Dry-run
validates arguments without reading state or creating directories or files.

Configure requires confirmed Input OFF. It stops Feed, applies Source/Points/
Interval, sets Delay and Filter to zero, clears the buffer, and checks the
readbacks. The requested and observed intervals must have the same decimal
value. A difference fails with `trace_interval_mismatch`, including
`requested_interval_s` and `actual_interval_s` in error details.

Start checks the current instrument configuration, Feed idle, TBF clear,
Input state, Fixed CC/CV/CR/CW, Transient OFF, Short OFF and Input Timer
disabled. This applies with Input ON or OFF. Start arms once and requires
Feed NEXT before triggering once. If the arm readback still reports NEVER,
Start fails with `trace_arm_unconfirmed` before triggering or reading data.
After the trigger, it waits for Feed NEVER and issues one `TRAC:DATA?` with
its own 30-second VISA timeout. The success result contains `source`, `points`,
exact decimal string `interval_s`, `sample_count`,
`read_timestamp_utc_plus_8`, and all `samples`. Sample indexes start at
one. Configure returns the four setting and count fields. Bounds, timing and
CSV columns are defined in the [CLI reference](../cli/README.md#trace).

For a requested CSV, the Worker creates any missing parents and exclusively
creates and flushes the CSV header after prechecks and before arming. An
existing file or a creation failure prevents the trigger. A failure after
creation retains the file and reports `error.details.csv_incomplete: true`.
CSV writing failures fail the job even when the batch was acquired. The full
batch is available in JSONL and status for every successful Start.

### Cancel and recovery

Ready and status publish `cancel_url`. `POST /cancel` accepts required
`schema_version: 3` and the exact `worker_job_id` of the active `trace-start`,
with an optional string `reason`. It returns HTTP 202 on acceptance, 400 for
malformed requests, and 409 for a missing, terminal, or non-cancellable job.
Cancel and Stop only signal the job in their HTTP handlers. They wake waits
and prevent further normal precheck queries after the current I/O returns.
The job thread can attempt one Feed stop and verify it when communication
remains reliable.

Successful cancellation finishes with `state: "cancelled"` and `ok: false`.
Worker Stop aborts an incomplete Start as `failed`. Completion or failure
can win a race with cancellation. Before sending a subsequent command, wait
for the matching terminal or its equivalent `last_job` snapshot and an empty
`active_job`. Send `input-off` explicitly and confirm its result before
stopping the Worker. Neither request performs Input OFF.

`GET /status.trace_safety` is null during ordinary operation. If TRACE tries
to stop Feed and a reliable readback still reports NEXT, the job fails and
status reports `state: "restricted_recovery"`, `code`, `feed`, and
`input_state`. This covers Cancel, Start's deadline or Stop cleanup, and
Configure's Feed stop. A deadline failure retains `trace_timeout` and a
Configure Feed stop failure retains `trace_feed_active`. Terminal error
details report `feed_stop_unconfirmed: true`. Only `input-state` and
explicit `input-off` are admitted until OFF is confirmed. After that,
`trace-configure` is also admitted for recovery. Only successful
configuration clears the restriction. Other commands receive HTTP 409
with `trace_recovery_required`.

When TRACE loses reliable VISA communication, the job fails and Worker status
reports `worker_state: "failed"` and a non-null `fatal_error`.
`trace_safety.state` is `session_untrusted` with unknown Input and Feed
states. All domain commands are rejected with HTTP 409 and
`trace_session_untrusted`. Status and explicit Worker Stop remain available
without device I/O. The Worker never attempts to reconnect or send cleanup
on that session. Core also rejects reuse of the untrusted communication
object. If TRACE safety restrictions remain at Worker shutdown, the summary
preserves `trace_safety` and sets `trace_safety.safe_shutdown_unconfirmed`
to `true`. Common Worker summary and exit code semantics still apply.

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
inspect or mutate those settings. See [bounds and examples](../cli/README.md#fixed-software-auto-range).

Manifest schema 2 adds the tool-specific `range_options: ["auto", "low", "high"]`
to these three command entries beside the unchanged `kind: "async_job"`.
Consumers may use this additive capability field for selection; it is not a
LIVE support declaration. Common Worker protocol 3, HTTP admission/state error
timing, result fields, JSONL envelopes, lifecycle and exits remain unchanged.
CV/CR/CW, Transient and LIST remain Product LIVE blocked. Auto uses the existing
Fixed CC commands, readbacks and Input OFF/ON gates.

## Offline Fixed CC Slew, VON and Short

For Fixed CC, supply both `rise_slew` and `fall_slew` or omit both. Auto resolves
the range using current before checking Slew. Low accepts 0.0001–0.25 A/μs and
High accepts 0.001–2.5 A/μs. Results with the pair add
`rise_slew_a_per_us` and `fall_slew_a_per_us` as decimal strings. Omission keeps
the existing result and LIVE write sequence.

VON configuration and status return `threshold_v` as a decimal string and
`latch` as `"on"|"off"`. The offline IT8812C profile accepts 0–120 V without
an assumed programming step. The simulated setting persists across mode changes.
Short operations return `short_state` as `"ON"|"OFF"`. Short ON also requires
Short OFF and a Worker that is not stopping. An explicit `input-off` remains
available regardless of Short state. Short OFF leaves Input unchanged, so the
previous Fixed setting can still load the DUT until explicit Input OFF.

HTTP and CLI forwarding preserve the original JSON numeric tokens for these
setups. Invalid arguments are rejected before job creation. Simulator state
checks and refusals of valid LIVE requests occur during the accepted job.
Dry-run validates arguments and returns `hardware_access: false` without
checking or changing simulated state. The five new commands appear as
`kind: "async_job"` in Manifest schema 2. All five and Fixed CC with Slew are
blocked before VISA, including on contributor validation paths. See the
[offline CLI usage](../cli/README.md#fixed-cc-slew-von-and-short).

## Offline Input Timer

The four Timer commands are ordinary async jobs in Manifest schema 2 / Worker
protocol 3. `seconds` requires a JSON integer token from 1 through 60000;
missing, fractional (including `1.0` or `1e0`), boolean, string, nonfinite,
out-of-range and extra arguments fail before job creation (HTTP 400).
State failures retain HTTP 202 followed by a failed `job_finished`.

Simulation returns `{"seconds": 60, "enabled": true}`. The setting persists
across jobs and Fixed mode changes. The simulator starts at 10 seconds, disabled,
and never counts down or turns Input OFF automatically. Status is read-only,
including with Input ON; `GET /status` retains its Worker/job shape without
device queries. Dry-run returns `command`, `hardware_access: false`, and
validated `seconds` for Set, without checking or changing simulated state.

Set, Enable and Disable require confirmed Input OFF. Set/Enable require Fixed
CC/CV/CR/CW with Transient disabled. Enable requires Short OFF. Enabled Timer
blocks Short ON, Transient setup/enable, LIST setup and LIST recall. Disable
Timer explicitly before selecting those modes. VON can coexist. Setting seconds
preserves enabled state; Input OFF and Stop do not disable Timer. Stop blocks
Timer mutations, while status remains readable; Stop performs no device I/O.

All four commands, including Status, remain Product/Worker LIVE blocked before
VISA access. Valid LIVE jobs are admitted and then fail with
`unsupported_live_scope`. Neither legacy override nor Input ON authorization
opens Timer LIVE. The separate CLI-only `input-timer` hardware-validation scope
and expected settings cannot be selected through Worker arguments/context.
See [Timer CLI and validation procedure](../cli/README.md#input-timer).

## Offline Transient CC / CV / CR / CW

The [Transient interface](../cli/README.md#transient-modes)
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
See the [source manuals and CLI usage](../cli/README.md#transient-modes).

| Operation | Preconditions and effects |
| --- | --- |
| Setup | Input OFF, Transient disabled; replace the complete configuration and clear the last trigger |
| Enable | Configured and Input OFF; enable Transient without changing Input |
| Input ON | Existing per-request confirmation, valid enabled Transient configuration, Input OFF, Short OFF and Worker not stopping |
| Trigger | Configured, enabled, Input ON, Short OFF, Source HOLD or BUS, Worker not stopping |
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
the [LIST interface](../cli/README.md#native-list-cc). Worker
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
OFF, Source HOLD or BUS and Worker not stopping. Every LIST mutation checks Stop. Status is
read-only and returns independent in-memory snapshots with
`state_source: "simulate"`; `GET /status` keeps its Worker/job shape.

Dry-run validates data only, with `hardware_access: false`, and leaves active
simulation untouched. The simulator has no timer/waveform or completion state.
`job_finished` for Trigger never proves LIST completion, including finite
repeat; 65536 means infinite. Input OFF and LIST exit are explicit and separate.
Stop is I/O-free and does not change Input. The Orchestrator must attempt and
confirm explicit Input OFF before Worker Stop on normal/error/user-stop exits;
Input OFF does not prove the internal LIST engine aborted.

## Trigger Setup

Source is one of `MANUAL`, `BUS`, `HOLD`, `TIMER`, or `EXTERNAL`. TIMER requires
a JSON number `interval_s` between 0.01 and 999.99 seconds. Non-TIMER sources
omit `interval_s` and retain the stored interval. The CLI client and Worker
preserve exact numeric tokens. Invalid, duplicate, or extra fields are rejected
with HTTP 400 before job creation.

Simulation starts in HOLD with a stored interval of 0.01 s. Configuration
requires Input OFF and a running Worker. Both commands return `source`,
`interval_s` as a decimal string, and `state_source: "simulate"`. Dry-run
previews supplied settings with `hardware_access: false` and preserves state.
Trigger commands accept HOLD and BUS. Requests to actively trigger MANUAL,
EXTERNAL, or TIMER are admitted and fail with `unsafe_trigger_source` when
other trigger guards are satisfied. Input OFF and Stop preserve Trigger Setup.

These commands use the existing Common v3 async jobs and Manifest schema 2.
Valid LIVE requests are admitted and fail with `unsupported_live_scope`
before VISA access. The CLI-only Trigger validation option is unavailable
through Worker arguments and context. Contributor MANUAL powered Input ON is
not physically validated and is refused before opening VISA; read-only Source
status and Input-OFF-safe MANUAL restoration remain available in that scope. See [Trigger Setup](../cli/README.md#trigger-setup).

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
The Worker uses the Core Worker scope (not the standalone CLI diagnostic
exception) and its own startup/per-request
Input ON authorization; its end-to-end physical LIVE behavior is not implied
to be hardware-validated by CLI acceptance. The hidden contributor-validation
CLI option remains for compatibility, is not used by the Worker, and cannot
bypass identity or connection restrictions. Dry-run/simulation do not certify
real hardware behavior.


## Separate CLI hardware validation

The standalone CLI offers an explicit `--hardware-validation` option for
controlled contributor CV/CR/CW/Transient and Trigger trials. It is not a Worker command,
argument, context, feature, or LIVE promotion. The Worker does not forward this
option or its `validation_settings` and continues to reject extra arguments.
All LIVE refusal and admission/terminal behavior described above remains in
force, including for the legacy contributor override. The dedicated CLI path
requires complete expected settings for Enable, Input ON and Trigger and
compares physical readback; its status describes control state, not waveform
completion. See the [CLI controlled instrument validation procedure](../cli/README.md#controlled-instrument-validation).
