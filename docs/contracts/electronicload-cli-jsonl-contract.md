# Electronic Load CLI JSON / JSONL Contract

Manifest schema: `2`; Worker runtime schema: `3` (`v3-only`).
Ordinary standalone CLI JSON has no top-level `schema_version` today.
The independent Sequence document uses `version: 1`.

This document defines the machine-readable Electronic Load result surfaces.
[Common CLI JSON / JSONL Contract](common-cli-jsonl-contract.md) owns the
runtime event envelopes; [Common Worker Protocol](common-worker-protocol.md)
owns HTTP and Common Error. The [Electronic Load Worker Contract](electronicload-worker-contract.md)
owns admission, command arguments, state, safety and recovery. For consumer
ordering, see [Electronic Load Orchestrator Workflows](electronicload-orchestrator-workflows.md).
The [CLI reference](../cli/README.md) owns flag syntax and contributor-only
procedures. Where a result is inseparable from its domain behavior, this
document links to that authoritative definition instead of maintaining a
second independent schema.

## Single-response JSON

Use `--json` for a standalone instrument or discovery command. Sequence
and the four basic workflows produce JSON results without requiring that flag.
Request help/version separately. Standalone JSON emits one object, not a stream.

```json
{"ok":true,"command":"measure","mode":"simulate","model_id":"itech-it8812c","result":{"voltage_v":12.0,"current_a":0.5,"power_w":6.0}}
```

```json
{"ok":false,"command":"measure","mode":"live","error":{"code":"missing_resource","message":"LIVE requests require an explicit --resource."}}
```

| Field | Contract |
| --- | --- |
| `ok` | Boolean; success contains `result`, failure contains `error`. |
| `command` | Command string; can be null on an error before command recognition. |
| `mode` | `live`, `simulate` or `dry-run` (hyphen). |
| `model_id` | Requested model ID on successful instrument commands; absent from discovery and the ordinary error envelope. |
| `result` | Command-specific object on success; see Result payloads. |
| `error` | `code` and `message` strings, optional `details` object; no Worker Common Error category is added to this standalone envelope. |

There is no ordinary CLI schema-version negotiation. Do not require a Worker
envelope or infer schema 2 from another Tool. Manifest and forwarded Worker
responses are separate surfaces. The CLI emits its final result after cleanup;
human-readable diagnostics do not belong on machine-mode stdout.
Authorized ASRL contributor results carry `transport: "asrl"` and
`validation_only: true` inside the result when using the contributor
ASRL connection. Neither is permission for Worker LIVE.

## CLI command inventory

All Worker domain commands are also standalone CLI commands; see the
[command inventory and argument examples](electronicload-worker-contract.md#commands-and-context)
and the [CLI command family index](../cli/README.md#command-family-index).
The following are additional CLI surfaces, not Worker domain commands:

| Surface | Output |
| --- | --- |
| `manifest --json` | Static Manifest object (below). |
| `list-resources --json` | Ordinary success/error envelope; result includes `backend`, `live_only`, `count`, `resources`. Each row has `resource`; eligible live-only IDN results add `idn` and `model_id` (possibly null). |
| `worker` | Common v3 JSONL runtime, not a one-shot result. |
| `send-command`, `status`, `stop`, `wait-ready` | One HTTP response or CLI-owned error object; see Worker client JSON. |
| `contributor-settings`, `trigger-observe` | Standalone contributor-only interfaces; see the [controlled validation procedure](../cli/README.md#controlled-instrument-validation). Not integration commands. |

Discovery is an explicit separate operation, not a fallback from a failed LIVE
request. Live-only discovery queries eligible USB resources. Resource strings,
raw IDN and VISA library paths can contain private information; do not publish
them. Help/version are human-readable and are not JSON APIs.

## Tool Manifest

Read `electronicload-tool manifest --json` from the executable that will
be launched. It does not start a Worker, discover hardware or access VISA.

| Field | Current declaration |
| --- | --- |
| `event` | `tool_manifest` |
| `schema_version` | Integer 2 |
| `tool_id` | `electronicload` |
| `tool_version` | Installed distribution version |
| `worker_protocol` | `{"compatibility_policy":"v3-only","schema_versions":[3]}` |
| `execution` | `{"model":"multi_job","context_binding":"per_command"}` |
| `features.common.async_jobs` | `{"contract_version":1}` |
| `features.common.job_cancel` | `{"contract_version":1,"commands":["trace-start"]}` |
| `commands` | Object keyed by domain command; each entry has `kind: "async_job"`. |

`set-cc`, `set-cv`, `set-cr` and `set-cr-led` also declare
`range_options: ["auto", "low", "high"]`. No Common Streaming feature
is declared. Manifest command availability is not LIVE authorization; consult
[Supported Models](../core/supported-models.md#product-live-support).

## Runtime JSONL and status

Each runtime line is one JSON object with Common integer `schema_version: 3`,
`event`, `timestamp_utc` and `run_id`. Use the Common envelopes
without a v2 fallback. Electronic Load uses the following result surfaces:

| Event or response | Domain-specific content / interpretation |
| --- | --- |
| `ready` | `service: "electronicload-tool"`, adopted `features`, `status_url`, `command_url`, `stop_url`, `cancel_url`. No physical readiness assertion. |
| `job_accepted`, `job_started` | `worker_job_id`, `job_id` (null if omitted), `command`; neither establishes success. |
| `job_finished` | Same identity, fixed terminal timestamp, `state`, `ok`, `result`, `error`. Successful result object with null error; failed/cancelled terminal has null result and Common Error. |
| `GET /status` | Common status with `service`, features, control URLs, `active_job`, `last_job`, and `trace_safety` (object or null). No instrument query. |
| `summary` | Common process summary; normal shutdown also includes `last_job` (possibly null); safety evidence below. |

`last_job` holds only the latest terminal, not history. The matching
terminal has the same result and timestamp as JSONL. Bind the `run_id` from
the enclosing status response and correlate `worker_job_id`, client `job_id`
and `command` with the accepted job. A successful process summary is not proof
that every job succeeded. Common `error` events require `scope` and `fatal`;
reject malformed events rather than inferring those fields from the error
category. Retain independent failure and safety evidence.

`trace_safety` and optional summary `trace_safety.safe_shutdown_unconfirmed`
are defined in [TRACE Cancel and recovery](electronicload-worker-contract.md#cancel-and-recovery).
Optional `summary.workflow_cleanup_failure` retains the latest matching
unconfirmed CC workflow cleanup terminal within the run, even if a later job
succeeds; see [Basic Workflows](electronicload-worker-contract.md#basic-workflows).
Neither field changes Common process exit meanings.

## Worker client JSON and exit codes

The installed CLI clients use the exact endpoint URL reported by the Worker:
`send-command --url URL --worker-command NAME --arguments-json JSON --context-json JSON`,
optional `--job-id`; `status --url URL`; `stop --url URL`; and
`wait-ready --url URL --wait-timeout-ms N`. Append `--json` when using
these in automation. `wait-ready` defaults to 10000 ms and accepts
1–120000 ms. It returns the status object; full Common validation and
correlation remain consumer responsibilities.

Successful clients print the Worker response directly, preserving schema 3.
HTTP errors with JSON bodies are likewise forwarded without wrapping.
A local argument or transport failure uses the CLI-owned error shape above.
There is no standalone cancel helper; send `POST /cancel` directly.

| Surface | Exit 0 | Exit 2 | Exit 3 |
| --- | --- | --- | --- |
| Standalone instrument CLI | Successful command | Argument, identity, validation or support-policy refusal | Communication/execution or cleanup failure |
| Forwarding HTTP client | HTTP 2xx (admission or control acknowledgement, not job success) | HTTP 400, 409, 429; local validation error | Other HTTP failures, transport/response failure |
| `wait-ready` | Ready status observed | Local validation error | Readiness timeout or transport/response failure |
| Worker runtime | Normal shutdown without fatal error | Startup validation refusal before runtime | Fatal runtime or bind failure |

These are current Electronic Load exit mappings, not another Tool's mappings.
Use the Common completion rules for Worker summary, EOF and observed exit;
do not translate an accepted response to a completed result.

## Result payloads

Standalone instrument commands return their domain object under `result`.
Worker domain commands return it under the successful terminal's `result`
(or matching `last_job.result`). The data family index below covers every
Manifest command; detailed mixed result/behavior clauses remain authoritative
at the linked source. Fields may differ by mode, and conditional fields must
not be fabricated. Preserve documented decimal strings rather than coercing
all values to binary floats.

| Commands | Main result fields and detailed contract |
| --- | --- |
| `identify` | `manufacturer`, detected `model`, `canonical_model`, `firmware`; [ordinary results](../cli/README.md#results-and-exits). |
| `input-state`, `input-on`, `input-off` | `input_state` is `ON` or `OFF` on confirmed success; unconfirmed errors may report `UNKNOWN`. [Safety](electronicload-worker-contract.md#safety). |
| `measure` | Finite numeric `voltage_v`, `current_a`, `power_w`; latest power may not be fresh or synchronized with V/I. |
| `readback`, `fixed-status`, `fixed-slew-status` | [Configuration readback results](#configuration-readback-results). |
| `scpi-version`, `display-mode`, `error` | `scpi_version`, `display_mode`, or Error Queue result; [System Queries](electronicload-worker-contract.md#offline-system-queries). Nonzero instrument error code is query data, not by itself a failed query. |
| `measure-min-max`, `measure-discharge`, `set-average`, `average-status` | Numeric extrema/selected metric, `capacity_ah`, `discharge_time_s`, integer `average_count`; [Measurement Extensions](electronicload-worker-contract.md#offline-measurement-extensions). |
| `set-cc`, `set-cv`, `set-cr`, `set-cw` | `current_a`, `voltage_v`, `resistance_ohm`, `power_w` respectively; applicable resolved Range, optional CC slew pair and mode-specific readback fields. [Fixed setup](../cli/README.md#fixed-modes-and-input-control), [Auto Range](electronicload-worker-contract.md#fixed-software-auto-range), [Safety](electronicload-worker-contract.md#safety). |
| `set-cr-led`, `cr-led-status`, `current-slow-rise-enable`, `current-slow-rise-disable`, `current-slow-rise-status` | LED state, exact decimal `vdrop_v`, CR state and resolved Range, or Slow Rise `enabled`; [CR-LED and Current Slow Rise](electronicload-worker-contract.md#offline-cr-led-and-current-slow-rise). |
| `set-von`, `von-status`, `short-on`, `short-off`, `short-status` | Decimal string `threshold_v`, `latch`, or `short_state`; [Slew, VON and Short](electronicload-worker-contract.md#offline-fixed-cc-slew-von-and-short). |
| `set-input-timer`, `input-timer-enable`, `input-timer-disable`, `input-timer-status` | Integer `seconds` and boolean `enabled`; [Input Timer](electronicload-worker-contract.md#offline-input-timer). |
| `set-transient-cc`, `set-transient-cv`, `set-transient-cr`, `set-transient-cw`, `transient-enable`, `transient-disable`, `transient-trigger`, `transient-status` | [Transient results](#transient-results). |
| `set-list-cc`, `list-save`, `list-recall`, `list-trigger`, `list-status`, `list-exit` | LIST status snapshot including configuration and trigger record; [LIST status and control flow](../cli/README.md#explicit-control-flow), [LIST behavior](electronicload-worker-contract.md#offline-native-list-cc). |
| `trigger-configure`, `trigger-status` | `source`, decimal string `interval_s`, `state_source`; [Trigger Setup](electronicload-worker-contract.md#trigger-setup). |
| `protection-status`, `protection-set`, `protection-clear` | Settings, applied settings or condition, Input state and optional consumed event register as applicable; [Protection](electronicload-worker-contract.md#offline-protection). |
| `trace-configure`, `trace-status`, `trace-start` | TRACE settings/status or complete indexed sample batch; [TRACE](electronicload-worker-contract.md#offline-trace), [status](electronicload-worker-contract.md#configuration-readback). |
| `sequence` | Loop/step progress, measurement and Input-state arrays, final Input and cleanup; [Software Sequence](electronicload-worker-contract.md#software-sequence). |
| `measure-log`, `measure-until`, `cc-timed-load`, `cc-discharge` | `elapsed_s`, `stop_reason`, sample/last-measurement summaries and CC cleanup/capacity fields as applicable; [Basic Workflows](electronicload-worker-contract.md#basic-workflows). |

LIVE results may include `visa.library` and `visa.manufacturer`
runtime information; it is not instrument identity or proof of calibration.
Simulator values describe the simulator, not an electrical DUT model.

### Configuration readback results

The simulated readback commands report `state_source: "simulate"`; dry-run
and LIVE behavior remain in the [Worker contract](electronicload-worker-contract.md#configuration-readback).

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

For Fixed setpoints, the unit-bearing field is `current_a`, `voltage_v`,
`resistance_ohm` or `power_w` according to function. For TRACE status
and contributor-only physical readback fields, follow the
[Worker readback contract](electronicload-worker-contract.md#configuration-readback)
and [CLI readback reference](../cli/README.md#configuration-readback).

### Transient results

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

The [Worker Transient contract](electronicload-worker-contract.md#offline-transient-cc--cv--cr--cw)
owns all preconditions, state transitions and trigger limitations. A trigger
record is dispatch evidence, never a measured waveform or completion signal.

## Dry-run previews

Standalone uses `--dry-run` and returns `mode: "dry-run"`; Worker
uses `context.mode: "dry_run"` with `planning_model_id`.
Plans return domain fields including `hardware_access: false`; they
validate without simulator/physical I/O or file creation. The exact plan
fields are command-specific: see [readback](electronicload-worker-contract.md#configuration-readback),
[Sequence](electronicload-worker-contract.md#software-sequence),
[basic workflows](electronicload-worker-contract.md#basic-workflows),
[TRACE](electronicload-worker-contract.md#offline-trace) and the
[CLI reference](../cli/README.md#quick-start). Decimal strings in a plan
are output representations, not permission to use numeric strings as
Worker arguments. A valid plan neither proves a safe current physical
state nor expands LIVE scope.

## Error and cleanup payloads

Standalone errors and Common Worker errors are distinct envelopes. Worker
terminal errors carry `category`, `code`, `message`, `details`;
a rejected request does not create a job. Preserve structured error details
and original errors; do not treat a previous result as a successful retry.
The domain contracts specify conditional details, including:

- Unconfirmed Input state and exact physical readback evidence:
  [Safety](electronicload-worker-contract.md#safety).
- Auto Range candidate errors: [Auto Range](electronicload-worker-contract.md#fixed-software-auto-range).
- Sequence progress, `failed_step`, `original_error` and cleanup:
  [Software Sequence](electronicload-worker-contract.md#software-sequence).
- CC workflow progress, `manual_action_required`, cleanup and export failure:
  [Basic Workflows](electronicload-worker-contract.md#basic-workflows).
- TRACE cancel and recovery: [Cancel and recovery](electronicload-worker-contract.md#cancel-and-recovery).
- Protection partial setting application: [CLI Protection reference](../cli/README.md#protection).

Do not infer Input OFF from an error, worker termination or session close.
Never publish raw private resource/identity diagnostics.

## CSV and result ownership

The Worker normally holds results in memory, with no request/result JSON
files, event log, artifact directory or persistent job history. TRACE
and the three sampling workflows can write one explicitly requested local
CSV. No native Common streaming or per-sample JSONL is advertised.

| Export | Existing columns and result |
| --- | --- |
| TRACE `data` | `read_timestamp_utc_plus_8`, `sample_index`, selected measurement columns. |
| TRACE `full` | Data columns plus `source`, `points`, `interval_s`, `sample_count`. |
| TRACE optional time columns | `estimated_elapsed_s`, `estimated_delta_t_s`; estimated delta blank for first sample. |
| Measure Log / Until / Discharge | `sample_index`, `elapsed_s`, measured columns, plus `capacity_ah` for discharge. Partial sample leaves unmeasured columns blank. |

See [TRACE CSV](../cli/README.md#trace) and [Basic Workflows CSV](../cli/README.md#basic-workflows)
for exact selection and creation rules. Relative paths use the running process
working directory. Existing files are preserved. Incomplete files are not
successful results; consult `error.details.csv_incomplete` and the
domain-specific cleanup rules. TRACE returns the full sample batch even when
no CSV is requested; sampling workflows return summaries, not sample arrays.
Consumers should retain the results needed for the current operation without
requiring a database, implicit job history or a new artifact system.
