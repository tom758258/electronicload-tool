# Electronic Load Orchestrator Workflows

Manifest schema: `2`; Worker runtime schema: `3` (`v3-only`).
This document describes how an external process consumes the existing
Electronic Load CLI and Worker. It extends
[Common Orchestrator Workflows](common-orchestrator-workflows.md) with
Electronic Load-specific integration, not a new execution or hardware policy.
[Electronic Load Worker Contract](electronicload-worker-contract.md) owns
domain arguments, safety, admission and cleanup; the
[CLI JSON / JSONL Contract](electronicload-cli-jsonl-contract.md) owns output
shapes. Use the [CLI reference](../cli/README.md) for exact flags and
[Supported Models](../core/supported-models.md#product-live-support) for
permissions.

## Manifest and startup

1. Read `electronicload-tool manifest --json` from the actual executable.
   Validate tool ID `electronicload`, Manifest schema 2, v3-only Worker
   protocol, `multi_job`/`per_command`, command inventory and required
   features. An advertised command is not a LIVE allowlist.
2. Choose an explicit mode. The hardware-free example is:

   ```text
   electronicload-tool worker --mode simulate --control-port 0
   ```

   The Worker emits JSONL without a format flag. Port 0 selects an
   OS-assigned local port; never guess it.
3. Drain stdout and stderr continuously. Validate `ready` or the Common
   status fallback, adopted features, local control URLs and nonempty
   `run_id`. Use numeric loopback `127.0.0.1`, no proxy or redirects,
   and the Common same-origin rules. HTTP 200 alone is not readiness.
4. Use `GET /status` only for Worker/job observations. For actual
   Input state use the `input-state` job; for identity use `identify`.
   A ready Worker is not evidence of a connected, safe or identified device.

The built-in `wait-ready --url URL --json` helper polls an already known
status URL. It does not replace consumer validation of runtime features,
origin or run correlation.

## Context and command submission

Mode and model context travel with every domain request. Resource, backend
and the LIVE Input ON startup gate belong to the Worker launch configuration,
not the command arguments. Simulation is allowed on a simulated Worker;
LIVE requires a live Worker; dry-run is allowed on either. Dry-run and
simulation require the canonical planning model; a LIVE expected model is
an optional mismatch guard, never a driver override.

Example request to the simulated Worker's reported `command_url`:

```json
{
  "schema_version": 3,
  "command": "input-state",
  "arguments": {},
  "job_id": "load-state-1",
  "context": {"mode": "simulate", "planning_model_id": "itech-it8812c"}
}
```

An equivalent CLI forwarding invocation (URL is the reported command URL):

```text
electronicload-tool send-command --url URL --worker-command input-state --arguments-json "{}" --context-json '{"mode":"simulate","planning_model_id":"itech-it8812c"}' --job-id load-state-1 --json
```

For cross-platform subprocesses, pass an argv array with the JSON strings as
individual arguments rather than depending on a shell's quoting. Use exact
integer schema 3. Unknown or malformed request fields and invalid arguments
fail admission (HTTP 400); busy, stopping, required confirmation and
restricted recovery may reject with HTTP 409. See the
[Worker contract](electronicload-worker-contract.md#commands-and-context)
for individual validation/state rules.

## Job completion and results

- HTTP 202 means a job was accepted, not completed. A second submission
  while a job is active receives Busy; there is no waiting queue.
- Consume JSONL continuously and match `run_id`, `worker_job_id`, client
  `job_id` and `command`. A status terminal inherits the enclosing status
  `run_id`. Client `job_id` is correlation metadata, not idempotency or
  deduplication.
- A matching `job_finished` or complete matching `last_job` snapshot
  provides terminal evidence. Require successful state and `ok: true`
  before dependent steps; wait for empty `active_job` before submission.
  A job may finish before its HTTP response is received. Do not discard early
  events or substitute an unrelated later snapshot.
- A rejected or unreachable request is not a successful job. If admission
  is uncertain, reconcile observable state; do not blindly resubmit a
  command that may have affected Input or settings.
- Capture the terminal result/error and requested export evidence before
  starting the next job, because `last_job` stores only the latest one.
  Keep failure and cleanup evidence even after a later success.

These steps apply to every domain workflow below. A one-shot command instead
uses its own JSON `ok` and actual exit code; it does not share simulator
state with another one-shot process.

## Read-only and Fixed load workflows

Use one simulated Worker to practice stateful workflows. Standalone
simulation starts with fresh state each time. The following sequence is
simulation-only as written, with the context in the example above on each job.
Require each terminal to succeed before its dependent step.

| Order | Command | Arguments / completion check |
| --- | --- | --- |
| 1 | `identify` | `{}`; check identity against intended model. |
| 2 | `input-state` | `{}`; require confirmed OFF before setup. ON or unknown blocks setup. |
| 3 | `set-cc` | `{"current":0.1,"range":"auto"}`; verify returned setting and OFF. |
| 4 | `input-on` | `{"confirm_input_on":true}`; require confirmed ON. |
| 5 | `measure` | `{}`; consume V/I/P, without assuming synchronous or fresh power. |
| 6 | `input-off` | `{}`; require confirmed OFF. |

For offline CV/CR/CW substitute the corresponding complete Fixed setup
and follow [Fixed mode rules](../cli/README.md#fixed-modes-and-input-control).
If a state check fails, do not continue the normal sequence. Use the
[Stop and cleanup](#stop-and-cleanup) rules below. Input ON is never a
side effect of setup. Simulator results are not an electrical safety proof.

## Software Sequence

Use one `sequence` job for a complete version-1 Sequence document,
not a Worker command for each embedded step. Use the
[Sequence document](../cli/README.md#software-sequence) as the schema source.
Pass it inline as `arguments.document`. Preview using `dry_run`; a run
with any Input ON requires `confirm_input_on: true`. The document must
contain a fresh Fixed setup before each ON and end every loop OFF; the Worker
checks the actual starting Input state before execution. It does not change
a load that starts ON. A Stop interrupts the run cooperatively. Preserve
partial progress and cleanup evidence on failure; do not assume a rollback
or restart a failed Sequence. See [Sequence behavior](electronicload-worker-contract.md#software-sequence).
This command is offline-only in Worker.

## Basic measurement and CC workflows

Use the following as separate offline jobs, not overlapping commands:

| Command | Example arguments | Completion evidence |
| --- | --- | --- |
| `measure-log` | `{"interval_s":1,"count":3}` | Sample count/last measurement and stop reason. |
| `measure-until` | `{"interval_s":1,"metric":"voltage","operator":"ge","threshold":4.5,"timeout_s":5}` | Check condition_met versus other terminal reasons; timeout is failure. |
| `cc-timed-load` | `{"current":0.1,"duration_s":2,"confirm_input_on":true}` | Duration result, final Input state, cleanup. |
| `cc-discharge` | `{"current":0.1,"cutoff_voltage":3,"timeout_s":5,"confirm_input_on":true}` | Capacity, stop reason, sample summary, final Input and cleanup. |

The two measurement jobs preserve Input state. CC jobs own Input OFF
cleanup after their own ON attempt, require initial OFF and verify setup.
A sample limit is a successful terminal reason, not proof of reaching a
voltage or capacity target. Initial voltage cutoff can return zero load
samples. Inspect `stop_reason` and cleanup rather than merely
`ok`. For Stop, timeout or export failure, preserve partial evidence.
Unconfirmed cleanup requires independent manual action. See
[Basic Workflows](electronicload-worker-contract.md#basic-workflows) for
the authoritative result/cleanup/CSV semantics. No Worker LIVE permission
is granted by contributor CLI validation of these workflows.

## Transient, LIST and Trigger

The following are simulated Worker workflows only. Use the complete argument
schemas and interlocks in [Transient](electronicload-worker-contract.md#offline-transient-cc--cv--cr--cw),
[LIST](electronicload-worker-contract.md#offline-native-list-cc),
and [Trigger Setup](electronicload-worker-contract.md#trigger-setup).

- Transient: confirm OFF, configure the selected CC/CV/CR/CW waveform,
  enable Transient, explicitly confirm Input ON, issue the permitted
  software trigger (HOLD/BUS), explicitly turn Input OFF, then disable
  Transient if returning to Fixed setup. Verify each terminal.
- LIST: confirm OFF and disabled Transient; set the complete CC LIST
  or recall a known saved slot; optionally save with explicit overwrite
  intent. Confirm Input ON, issue the permitted software trigger,
  explicitly confirm OFF and exit LIST if returning to Fixed. Save/Recall
  and LIST Exit have their own OFF guards.
- Trigger source selection is a separate OFF-only configuration operation.
  Do not issue software trigger commands for MANUAL, TIMER or EXTERNAL
  source as if they were HOLD/BUS. The contributor physical observation
  procedure is not a Worker workflow.

A successful software trigger terminal means dispatch, not a completed
waveform, restart or finite LIST completion. No physical waveform simulation
or LIST progress event is provided. On normal, error and user-stop exits from
an owned LIST workflow, explicitly attempt and confirm Input OFF before
Worker Stop while communication is trusted and the Worker can admit the job.
If OFF cannot be confirmed, follow the failure rules in Stop and cleanup.
Input OFF does not prove LIST-engine abort.
Use the existing mode interlocks before changing modes. Do not infer state
restoration or extra trigger behavior.

## TRACE and cancellation

In one simulated Worker, confirm Input OFF for `trace-configure`.
For example use `{"source":"TWO","points":4,"interval_s":1}`.
Wait for its terminal, then submit `trace-start` with `{}` or
an explicit CSV request. Follow the [TRACE prechecks and result contract](electronicload-worker-contract.md#offline-trace)
for Feed, TBF, current mode, Input and buffer conditions. TRACE Start may
observe Input ON or OFF under those guards; do not turn Input ON just for
capture. Result contains the complete batch; optional CSV does not replace
terminal validation.

Only `trace-start` is cancellable. Send the exact active
`worker_job_id` to the reported `cancel_url` with schema 3:

```json
{"schema_version":3,"worker_job_id":"REPLACE_WITH_ACTIVE_TRACE_JOB_ID","reason":"operator request"}
```

HTTP 202 acknowledges cancellation, not terminal success. A terminal
`cancelled` with `ok: false` confirms cancellation; completion or
failure may win the race. Wait for matching terminal evidence and empty
active_job before proceeding. If `trace_safety` reports restricted
recovery, only the documented Input state/OFF and then TRACE configure
recovery operations are allowed. If communication is untrusted, no
additional device commands or reconnect attempts are allowed. See
[Cancel and recovery](electronicload-worker-contract.md#cancel-and-recovery).
Cancel is not Worker Stop and neither performs Input OFF.

## Protection and configuration readback

Use simulated `readback`, `fixed-status` and the relevant
mode-specific status command for configuration; GET status remains a
Worker-only observation. Use simulated `protection-status` to obtain
condition and settings; request `events: true` only when the
read-and-clear event register is intentionally consumed. For settings or
Clear, require confirmed Input OFF and explicit `confirm: true`, and
apply only the requested settings or one named Defaults group. Inspect
the verified settings and condition/cleanup fields. Do not repeatedly
clear or assume an empty event register proves absence of a fault.
See [Protection](electronicload-worker-contract.md#offline-protection).
Other offline settings (VON, Short, Current Slow Rise, Average, Input Timer)
follow their individual interlocks in the Worker contract. For example,
Short OFF does not turn Input OFF and Timer must be disabled before
incompatible mode selection.

## Result and CSV ownership

Read terminal result/error from JSONL or matching status. The Electronic
Load Worker is memory-based; no result directory, implicit per-job file,
event log, database or persistent history is required. TRACE returns its
complete batch. Measure Log, Measure Until and Discharge return summaries
and optionally one explicitly requested local CSV; Timed Load does not
export samples. No Common streaming feature is adopted. Use explicit
non-existing output paths when requesting CSV and check error details for
incomplete files. See the [result contract](electronicload-cli-jsonl-contract.md#csv-and-result-ownership).
Keep current-run results and diagnostics only as required by the consumer;
do not invent artifacts or infer ownership from client job IDs.

## LIVE boundary

Use [Supported Models](../core/supported-models.md#product-live-support)
as the exact authorization source. The current Worker LIVE scope is IT8812C,
explicit USB INSTR resource, System VISA and only identify, input-state,
measure, restricted Fixed CC, confirmed Input ON and explicit Input OFF.
Standalone USB System Queries are not Worker LIVE permissions. ASRL and
the other separately controlled contributor validation paths are not
available through Worker requests. Never guess, scan or rotate resources
inside a failed workflow, fall back to simulation, or pass contributor
options via context.

For separately authorized LIVE operation the launch mode must be live,
with an operator-selected resource and System backend. Input ON additionally
requires the Worker startup `--allow-input-on` and per-request
`confirm_input_on: true`. Do not automatically enable that startup
gate. Core checks actual physical identity, Fixed CC, range/current and
Input/Transient/Short interlocks. Range limits alone do not ensure DUT
safety. The operator must provide appropriate source/DUT limits and an
independent cutoff. No powered LIVE example is implied by the simulated
sequence above. Never rely on remembered setpoints, a simulator pass,
contributor CLI success or a Worker Manifest to authorize LIVE.

## Stop and cleanup

- On normal completion, error or user-stop of a workflow that owns deliberate
  loading, follow its domain cleanup contract. For independently submitted
  Input control, explicitly attempt `input-off` and confirm OFF before Stop
  when communication is trusted and the Worker can admit the job. Do not
  repeat an uncertain write or bypass recovery restrictions. Apply documented
  mode-specific cleanup/restore only under its required guards.
- If a workflow starts with Input ON and refuses, preserve its observed
  state and failure. Do not pretend the workflow owns an earlier load.
  If a job is active, do not assume another command can interrupt it:
  only TRACE Cancel and cooperative Worker Stop have the documented control
  semantics. CC workflows and Sequence own their bounded cleanup after
  their own ON attempt; observe terminal cleanup rather than overriding it.
- If Input OFF cannot be confirmed or VISA communication becomes
  untrusted, retain the failure and direct the operator to independent
  physical cutoff/manual verification. Do not blindly repeat writes,
  reconnect, or infer OFF from a failed ON, status, Stop or VISA close.
  Observe the exact [Safety](electronicload-worker-contract.md#safety),
  [TRACE recovery](electronicload-worker-contract.md#cancel-and-recovery)
  and [Basic Workflow cleanup](electronicload-worker-contract.md#basic-workflows)
  contracts.
- Use the reported `stop_url` with `POST /stop` and integer
  `schema_version: 3` (optional reason), or the CLI `stop --url URL --json`.
  Stop is I/O-free in its HTTP handler, rejects further work and prevents
  not-yet-sent Input ON. Do not expect it to interrupt blocking VISA I/O
  or automatically perform Input OFF for an independent Input operation.
- Continue draining JSONL, check matching terminal/cleanup evidence,
  `summary`, EOF and actual process exit independently. Preserve
  `trace_safety.safe_shutdown_unconfirmed` and
  `workflow_cleanup_failure` when present. A successful Stop or exit
  0 cannot erase job failure or establish physical OFF.

Use the Common lifecycle contract for deadlines and process control.
The consumer may impose an orchestration deadline, but must not change the
underlying VISA/trigger/cleanup timings or safety rules. Physical validation
requires the separate operator procedure, not these integration examples.
