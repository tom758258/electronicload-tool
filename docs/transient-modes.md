# Offline Transient CC / CV / CR / CW

[繁體中文](transient-modes.zh-TW.md)

Core, CLI, Simulator and the Common v3 Worker support Continuous, Pulse and
Toggle for all four functions. Each setup replaces the complete inactive
Transient configuration. Use `set-transient-cc`, `set-transient-cv`,
`set-transient-cr` or `set-transient-cw`, followed by the shared controls.
Normal Product and Worker LIVE Transient requests are refused before VISA access.

## Setup arguments

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

## Control and results

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

## Contributor validation

The CLI-only `--hardware-validation transient-cc|transient-cv|transient-cr|transient-cw`
route follows the [controlled validation procedure](../README.md#controlled-instrument-validation).
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
write/ON opt-ins, interactive confirmation, source limits (>0 to 2 A / >0 to
18 V), manual OFF contingency and private/redacted reporting. Cleanup uses the
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
