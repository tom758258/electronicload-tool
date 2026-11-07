# Fixed Software Auto Range

[繁體中文](fixed-auto-range.zh-TW.md)

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
permission to execute a physical trial. See the [Worker contract](contracts/electronicload-worker-contract.md)
and [controlled validation procedure](../README.md#controlled-instrument-validation).

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
