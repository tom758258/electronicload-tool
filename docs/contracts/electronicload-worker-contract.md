# Electronic Load Worker Contract

This document defines IT8812C-specific behavior under the existing
[Common Worker Protocol](common-worker-protocol.md) and
[Common CLI JSON / JSONL Contract](common-cli-jsonl-contract.md).
Common contracts remain authoritative for the HTTP and JSONL envelopes.

## Versions and lifecycle

- Manifest schema **2**, Worker protocol **3** (`v3-only`).
- `tool_id: "electronicload"`; `execution.model: "multi_job"`,
  `execution.context_binding: "per_command"`.
- `common.async_jobs` contract version 1, for all six domain commands.
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
| `set-cc` | `{"current": <number>, "range": "low"\|"high"}` | Four confirmed CC settings, physical input stays OFF |
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

## Safety

LIVE `set-cc` and `input-on` may run without a Safety Config. Core always
checks the IT8812C current range; `set-cc` also validates programming
resolution. When a TOML Safety Config is supplied, its `[safety] max_current` adds a stricter current
ceiling. An invalid supplied file is refused. The live Worker loads a provided
file once at startup; changes require a restart. Standalone CLI reads a
provided file on each invocation. Range/current checks do not guarantee DUT
safety or enforce the entire voltage/power operating envelope.

LIVE Input ON has two gates: the Worker starts with `--allow-input-on`, and the
single `input-on` request carries `confirm_input_on: true`. Neither gate
replaces Core's physical checks. Before ON, Core requires physical input OFF,
CC+FIX, valid current/range within the safety limit, Transient OFF and Short
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
and `input-off`. The control commands retain Core safety checks, explicit
Resource requirements, optional Safety Config and Input ON confirmation gates.
The Worker uses this same Core support policy and its own startup/per-request
Input ON authorization; its end-to-end physical LIVE behavior is not implied
to be hardware-validated by CLI acceptance. The hidden contributor-validation
CLI option remains for compatibility, is not used by the Worker, and cannot
bypass identity or connection restrictions. Dry-run/simulation do not certify
real hardware behavior.
