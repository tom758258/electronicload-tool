# Offline native LIST CC

[繁體中文](list-cc.zh-TW.md)

Core, CLI, Simulator and the Common v3 Worker support IT8812C native CC LIST
configuration in simulation and dry-run. Product LIVE LIST is blocked before
VISA opens, including with the contributor override. CV/CR/CW LIST and native
OCP/OPP/Battery tests are outside this interface.

## Configuration and CLI

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

## Explicit control flow

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
[Worker contract](contracts/electronicload-worker-contract.md).

## Instrument command reference

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
