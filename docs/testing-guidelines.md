# Testing Guidelines

[AGENTS.md](../AGENTS.md) defines the repository boundaries. These guidelines
describe focused verification within those boundaries.

## Scope

Protect public behavior, component boundaries, and safety requirements at their
owning layer. Permanent tests cover CLI help/version, read-only Core policy,
identity, query parsing, session cleanup, and CLI output/exit integration.
Default tests replace VISA with a recording fake; an autouse fixture blocks
accidental creation of a real resource manager. Add focused tests for implemented
fixed-CC/Input switching, offline CV/CR/CW/Transient/LIST CC, and Common v3 Worker behavior. Do not add tests for
unimplemented WebUI or unsupported modes.

Avoid tests tied to private helpers, source text, exact help prose, or document
formatting. Packaging and installation are checked with actual build artifacts;
do not duplicate those checks in a second permanent test suite.

## CI test boundaries

CI uses the same Linux/Windows Python-version names as the other Tool projects.
The fast no-hardware matrix runs Core, CLI, Worker and Fake VISA contracts on
Linux Python 3.10–3.13 and Windows Python 3.10, 3.12 and 3.13. It excludes
`tests/test_live_validation_script.py`, which exercises real PowerShell
processes and is not part of routine Python-version compatibility testing.

Changes to validation scripts, their direct CLI/Core boundaries or their
script tests run the PowerShell PlanOnly, refusal and report contracts once on
Windows Python 3.13. Windows offline preflight runs separately on the same
affected boundary. Python Core/CLI/Worker tests own command, sequencing and
failure behavior; CI does not duplicate these as Linux pseudo-terminal operator
simulations. Unrelated changes skip the PowerShell checks; packaging changes
retain the wheel/sdist installation smoke. Skipping a CI job does not imply
real-instrument validation. Run focused tests from the repository root.
No CI job is authorized to access physical VISA.

## Local checks

Sync the development environment as described in the [README](../README.md).
Run pytest from the repository root, starting with the affected tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_foundation.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_readonly_core.py tests/test_readonly_cli.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_discovery.py tests/test_discovery_cli.py
.\.venv\Scripts\python.exe -m ruff check src tests
.\scripts\preflight-cli.ps1
```

Default tests require no hardware and must not discover or open physical VISA
resources. Put intentional verification artifacts under `.tmp_tests/`; it is
ignored by Git. `Local/` is reserved for private notes, not test output.

Preflight exercises offline CLI modes, JSON, and basic refusal behavior; it does
not repeat pytest or run VISA discovery. Discovery tests use an injected fake
VISA manager and must never call real `list-resources`. Check
`live-cli-check.ps1 -PlanOnly` and noninteractive refusal without hardware.
Use an explicitly fictional USB INSTR or ASRL INSTR resource for offline script verification. Real runs follow
the authorization, interaction, runtime-review, and deadline requirements in
the [README](cli/README.md#controlled-instrument-validation).

Changing the parser warrants the existing help/version tests. Read-only changes
do not by themselves require wheel/release acceptance or tests in other Tool
repositories. Fake VISA, simulator, dry-run, and plan reports are not evidence
of instrument support or permission to execute LIVE.

## Fixed-CC/offline-CV/CR/Input/Worker focused checks

Scope these tests to the new Core, CLI and Worker behavior and the directly
affected CLI parser/help/version and read-only CLI contracts:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_control_core.py tests/test_control_cli.py tests/test_worker.py tests/test_foundation.py tests/test_readonly_cli.py
.\.venv\Scripts\python.exe -m ruff check src/electronicload_tool_core/control.py src/electronicload_tool_core/simulator.py src/electronicload_tool_cli/cli.py src/electronicload_tool_cli/worker.py src/electronicload_tool_cli/worker_client.py tests/test_control_core.py tests/test_control_cli.py tests/test_worker.py
.\scripts\preflight-cli.ps1
```

Worker integration uses a real local simulated subprocess, HTTP and JSONL,
but never physical hardware. The **single** `live-cli-check.ps1` defaults to
FULL for USB and ASRL. See the [CLI validation modes](cli/README.md#available-validation-modes)
for the complete matrix. Use `-Modes` to focus checks on affected behavior.
Hardware modes retain the common read-only safety prechecks and any explicit
Input ON/OFF flow. Test `-PlanOnly` and unattended LIVE refusal with a fictional
resource matching the selected connection. Verify noninteractive hardware
requests are refused before opening VISA or sending SCPI. Workflow cases run
offline in dry-run and simulation, including when a valid connection is
supplied to a Workflow-only invocation. The script supplies the CLI's per-command
`input-on --confirm` during approved interactive execution. CC defaults to 0.1 A, with a 2 A script-only hard ceiling and a
default operator-declared source limit of 0.8 A at 5 V.
Verify redacted shareable case totals and normal mode's lack of private raw
logs; `-DebugArtifacts` uses the existing private/raw location without
changing authorization. No hardware is permitted in automated checks.

Fake VISA for control must emulate Local mode ignoring setup writes until
`SYST:REM`, numeric `CURR:RANG?` responses (including zero for low),
nonzero current readback mismatches, and Worker Stop/unsafe-state changes
between remote transition and Input ON. Read-only commands must not send
`SYST:REM` or other setup writes.

Run the **single** `live-cli-check.ps1` full LIVE command only with
separately approved model/Resource, bounded DUT/source, explicit interactive
operator confirmation, CLI Input ON confirmation, and a manual OFF contingency. Check physical Input
OFF before any selected mode; CC high and low range use the same bounded current
before one authorized ON/OFF trial. Reject further normal tests on any failure.
After a potentially attempted Input ON, make one explicit bounded OFF cleanup
attempt; if physical OFF cannot be confirmed, flag operator action. Validation
does not automatically promote Product LIVE support or demonstrate LIVE Worker
coverage. Script-only changes do not require unrelated local pytest or
release packaging.

Dependency/lockfile changes require the focused packaging checks below.
Report unavailable PowerShell, Ruff, dependencies and hardware as BLOCKED
or NOT RUN, not PASS.

For offline Transient CC/CV/CR/CW, run only its Core/CLI tests and selected Worker cases:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_transient_core.py tests/test_transient_cli.py tests/test_worker.py -k transient
.\scripts\preflight-cli.ps1 -TransientOnly
```

Cover complete mode-specific setup, both levels, CC-only slew rates, exact resolution,
Auto Low preference and High fallback for both levels together, CW without Range,
CV/CR/CW without slew, exact width preservation without an invented step,
no rounding, atomic refusal, state guards,
repeated software triggers, status snapshots and side-effect-free dry-run.
Verify one simulated Worker's HTTP/JSONL flow, Manifest entries, decimal-token
preservation, failed-job terminals, unchanged `GET /status`, function/waveform separation, CW null range, and LIVE refusal
before VISA even with the CLI override. Trigger terminal success never proves
waveform completion; Stop never changes Input state.

Select existing Input/Fixed CC regression cases only when those shared paths
change, and existing help/version or client/dispatch cases when those adapters
change. Run Ruff only on modified Python files. `-TransientOnly` runs the new
CLI cases and Manifest checks without unrelated preflight suites; the default
preflight includes all existing cases plus Transient. Use a fictional USB
resource solely for offline checks. Preparing the separately authorized
validation-only Core/CLI and hardware script is allowed without executing
hardware. Ordinary offline work does not require full local pytest,
release/packaging acceptance or physical validation.

For offline Fixed CR changes, start with CR cases in `test_control_core.py`,
`test_control_cli.py` and `test_worker.py`. Cover explicit Low/High bounds
(including 10 Ω in both), finite values without fixed decimal steps or rounding,
simulated readback, OFF-before-setup, confirmed simulated ON and explicit OFF.
Verify CLI JSON/help, Worker argument rejection/async results/`dry_run` dispatch,
and Manifest exposure. LIVE CR must fail before VISA access; fake physical CR
Input ON must fail before writes, including with the contributor override.
Run existing CC/CV/Input cases only where their shared paths are affected.

The offline preflight includes CR Low/High simulation and dry-run, explicit
range/out-of-range refusal, LIVE refusal using a fictional resource, and Manifest
checks. CV/CR physical trials require the dedicated validation-only opt-in
and separately approved hardware setup. Offline CR work alone does not warrant
the full test suite, release acceptance, packaging checks or hardware validation.

For offline Fixed CW, select CW cases in the same Core, CLI and Worker test
files first. Cover 0/250 W bounds, exact 0.01 W steps without rounding, invalid
values, absence of a range argument, simulated readback, Input OFF before setup,
confirmed simulated ON/explicit OFF, and dry-run without state changes. Verify
CLI JSON/help, Manifest exposure and Worker argument/async result integration.
LIVE CW must fail before VISA opens, and fake physical CW Input ON must fail
before writes, including with the contributor override. Then run only directly
affected shared Input/parser/dispatch cases and Ruff on modified Python files.
Do not run unrelated CC/CV/CR suites, release/packaging acceptance or hardware
validation for offline CW. Prepared CW hardware trials require the dedicated
validation-only opt-in and separately approved hardware setup.

When changing CW CLI preflight coverage, run `scripts/preflight-cli.ps1` with
the repository's installed CLI. It checks 0/250 W and a fractional CW setpoint
in simulation/dry-run, Fixed CW/Input OFF readback, no hardware access in
dry-run, required power, unsupported Range, invalid/bounds/resolution refusal
without rounding, LIVE refusal with/without the contributor override using a
fictional resource, and Manifest exposure. Script/documentation-only changes
do not require rerunning unrelated pytest or packaging checks locally. The
existing Windows CI job runs this same preflight script.

## Validation-only Core/CLI and hardware script preparation

Preparing an executable hardware procedure does not authorize running it.
Use recording Fake VISA tests for the dedicated `--hardware-validation` path:
exact identity/transport, complete expected settings, readback mismatch,
OFF-before-setup, guards before/after remote transition, explicit confirmation,
uncertain writes without retry, separate OFF/disable, and unchanged normal
CLI/Worker LIVE refusal. Physical range/trigger semantics remain subject to
real-instrument acceptance; Fake VISA is not physical evidence.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_hardware_validation.py tests/test_live_validation_script.py
```

The script tests run the real PowerShell PlanOnly and noninteractive gates with
fictional resources and a child-process VISA blocker. PowerShell must be installed;
unavailable PowerShell is reported as SKIP, not a successful script check.
For mode-selection changes alone, run only `tests/test_live_validation_script.py`
and Ruff on that modified Python file. Verify default all-mode, every single
mode, comma-separated/PowerShell-array selections, invalid-mode refusal,
interactive confirmation and default 5 V / 0.8 A source declarations, allowed
source overrides, selected case totals, and NOT RUN
for planned cases. Unselected modes must have no cases or PASS counts.
Keep every existing Transient mode/range, repeated trigger, status and OFF/disable
step. Check CV/CR/CW Continuous/Pulse/Toggle plans, Core-resolved Auto range,
mode-specific numeric tokens and no slew/Range where inapplicable. Preserve
the 30-second command and 900-second total deadlines; more cases do not
authorize a larger budget. Verify direct CC/Transient CC current setpoints cannot exceed the declared
source current limit, and the default CV/Transient CV plans use 3 V / 3–4 V.
Verify cleanup retains the active Transient scope. No unrelated Core/CLI/Worker,
local full-suite, release or packaging checks
are required for this script-only behavior change.
Exercise failure cleanup with a fake CLI/VISA process as appropriate: failure
after a possible ON must attempt explicit OFF, an enabled Transient is disabled
only after verified OFF, and failures retain FAIL with remaining cases NOT RUN.
Check redaction and that normal runs do not retain private raw artifacts.
The fake-CLI cleanup integration uses a POSIX pseudo-terminal to pass the real
interactive gates without hardware; Windows skips that specific check. It
stages the unchanged script with a preflight stub to avoid rerunning unrelated
preflight cases.
Never bypass interactive gates to test an actual instrument automatically.

Run directly affected existing Input/Fixed, CLI parser and Transient tests when
those paths change, and Ruff only on modified Python files. Local focused checks
do not replace the repository's normal CI: do not add CI-skip commit markers or
change workflow filters to suppress checks. CI retains its configured offline
suites, Windows preflight and applicable packaging smoke checks. Physical
validation is separate and must be reported NOT RUN until explicitly performed.

## Fixed Software Auto Range focused checks

For Fixed CC/CV/CR Range changes, run `test_control_core.py`,
`test_control_cli.py`, `test_foundation.py` and `test_readonly_cli.py` from
the repository root. Select Worker Fixed Auto, Fixed setup, argument rejection,
dry-run, client and Stop cases. For the dedicated CV/CR validation path, use
`test_hardware_validation.py` with recording fakes only.

Cover Low-first choice, High fallback, CR 10 Ω overlap, exact bounds and
resolution without rounding, required Range, unchanged manual selection,
resolved result/Manifest metadata, original decimal tokens through CLI/HTTP,
pre-admission refusal and simulator state across jobs. Verify dry-run leaves
active settings unchanged, Input ON blocks setup, and Fake VISA preserves CC
remote/write/OFF/readback ordering and failure handling. Select Transient/LIST
Auto, precision, atomic-validation and shared mode-guard cases when their
validators or Fixed setup paths are affected. Run Ruff on modified Python files.
Keep local evidence in `.tmp_tests/`; no full local suite, packaging acceptance
or physical trial is required for this feature. Normal CI remains unchanged.

For script coverage, run `scripts/preflight-cli.ps1 -FixedAutoOnly` and
`tests/test_live_validation_script.py`. The script tests execute the real
PowerShell preflight, PlanOnly and unattended-refusal paths with child-process
VISA access blocked. Check Core-resolved `expected_range`, original decimal
arguments, preserved ON/OFF counts, redacted reports and NOT RUN for planned
hardware cases. Fixed Auto adds only OFF setup/readback to the existing
bounded manual trials; never increase CC above the script ceiling to exercise
Auto High physically. Missing PowerShell must be reported, not treated as PASS.

## Offline LIST CC focused checks

Start with only LIST Core/CLI tests and LIST Worker cases:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_list_core.py tests/test_list_cli.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_worker.py -k list
```

Cover complete JSON and nested Step types, exact integer repeat/slots, 2–84
Steps, whole-LIST Auto range selection, no rounding, explicit mode exclusion,
OFF-before-setup/recall/save/exit, confirmed ON, Short/Stop guards, snapshot and
saved-slot isolation, overwrite, empty Recall and retained data after exit.
Do not infer a LIST width decimal step from Transient T1/T2. Preserve exact
width/slew decimal tokens through inline/file input, CLI forwarding and HTTP.
Verify one real simulated Worker's HTTP/JSONL flow, failed-job terminals,
unchanged `GET /status`, Manifest 2 / protocol 3, side-effect-free dry-run and
LIVE refusal before VISA, including contributor override refusal. Trigger job
success never means LIST completion or Input OFF; Stop never changes Input.

Run existing Input/Fixed, Transient, CLI parser/help/version and Worker
client/dispatch checks only when their shared paths change. Ruff is limited to
modified Python files. LIST-only offline work does not require all-mode
PowerShell preflight, full local pytest, release acceptance, packaging checks
or physical tests. Keep the normal configured CI intact. Physical LIST work
requires separate authorization and is not established by simulation.

## Input Timer focused checks

Run `tests/test_input_timer.py` and select `input_timer or input-timer` cases in
`tests/test_worker.py` and `tests/test_live_validation_script.py`. Cover strict
1–60000 integer seconds, atomic refusal, OFF-before-mutation, status with Input
ON, Fixed mode restrictions, Short mutual exclusion, Transient/LIST selection
blocking, VON coexistence, retained Timer on Input OFF/Stop, no simulated
countdown, and state-free dry-run. Core owns validation and safety rules.

Verify the four async Manifest entries, unchanged protocol/schema and runtime
status, HTTP argument rejection before jobs, accepted-job failures, CLI forwarding
and LIVE refusal before VISA even with the legacy override. Record physical
Timer SCPI with Fake VISA only; verify readback, remote-transition safety
rechecks, Stop, uncertain ON, and no implicit retry/OFF.

Run `scripts/preflight-cli.ps1 -InputTimerOnly` and Timer-only script PlanOnly /
noninteractive refusals using a fictional USB resource with VISA
blocked in child processes. Planned physical cases remain NOT RUN. Verify the
bounded Fixed CC trial, redacted reports and explicit OFF cleanup. Select
existing Input/Short/Transient/LIST guard tests and parser/help/version / Worker
client tests only where shared paths are affected. Run Ruff on modified Python
files. No unrelated full pytest, default all-mode preflight, release acceptance,
packaging or physical tests are required; leave normal CI configured checks intact.

## Offline TRACE focused checks

Run the TRACE tests and directly affected Worker and CLI contract checks.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_trace.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_worker.py -k "trace or worker_multi_job_terminal or worker_invalid_request or stop_waits_for_http_reply or local_lifecycle_client"
.\.venv\Scripts\python.exe -m pytest -q tests/test_control_cli.py tests/test_foundation.py -k "manifest or help or version"
```

Cover exact decimal readback, numeric formats, model bounds, Input and
trigger safety including unconfirmed arming over stale data, Feed cleanup,
independent capture and data-read deadlines,
one batch read and source-specific parsing. Use a fake clock for long captures
and recording Fake VISA for I/O errors and out-of-sync replies. Check CSV
exclusive creation, headers before triggering, both formats, optional time
columns and preservation following failure. Exercise Common v3 cancellation,
Stop during I/O, terminal and status snapshots, restricted recovery after
Cancel, deadline cleanup or Configure, and Core and Worker session isolation.
Verify that LIVE fails before opening VISA and that dry-run
leaves simulator state and the filesystem untouched. Use Ruff on modified
Python files. The configured CI runs its normal offline checks.

## Contributor TRACE CLI hardware-validation preparation

The existing `live-cli-check.ps1` is the sole instrument Script. Adding
`-Modes trace` and CLI-only `--hardware-validation trace` does not authorize
a physical run or promote Product/Worker LIVE. Verify only the directly
changed contributor boundaries:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_trace.py -k "hardware_validation"
.\.venv\Scripts\python.exe -m pytest -q tests/test_live_validation_script.py -k "trace"
.\.venv\Scripts\python.exe -m pytest -q tests/test_foundation.py -k "help or version"
.\scripts\preflight-cli.ps1 -TraceOnly
```

Use a recording Fake VISA for the explicit-scope Core dispatcher, actual native
Configure/Start sequencing, original Product LIVE refusal, untrusted Session
rejection, exact identity and USB/System connection requirements. For the
PowerShell Script, check PlanOnly, noninteractive refusal and report contracts.
Check Input OFF and failure handling primarily at the owning Core/CLI Fake VISA
layer rather than simulating operator keystrokes in a pseudo-terminal.
Use only fictional Resources in automated tests. Fake clock checks may replace
real long waits. PowerShell unavailable is SKIP, never PASS. Ruff checks only
modified Python files, and `git diff --check` checks the patch.

The script defaults to the established validation modes, including TRACE. Keep TRACE-only checks
focused; do not add unrelated Fixed/Transient/LIST/Worker or release acceptance
tests. Ordinary CI retains its repository-configured offline checks, irrespective of local
focused selection. Contributor physical TRACE remains NOT RUN until a
separately approved, bounded interactive run using an operator-supplied Resource,
safe DUT/source, and independent manual Input OFF contingency.

## Offline CR-LED and Current Slow Rise focused checks

Select CR-LED, Slow Rise and directly affected Fixed/Transient/LIST cases in
`test_control_core.py`, `test_hardware_validation.py`, `test_transient_core.py`
and `test_list_core.py`. Use Fake VISA to check write order, LED OFF before
leaving CR, failed readback without further writes, and protection of Product
LIVE `set-cc` from LED and Slow Rise SCPI. Check CLI parser/help and Manifest,
and select related Worker validation, state and LIVE-refusal cases.

Use `scripts/preflight-cli.ps1 -CrLedSlowRiseOnly` and the corresponding
script tests for PlanOnly and noninteractive refusal with a fictional USB
resource and child-process VISA blocking. Physical CR-LED and Slow Rise
trials require separate approval and keep Input OFF. For changes to the
Slow Rise interlock case, verify with recording Fake VISA that the planned
CV setup is refused before any control write and that the report recognizes
only the expected refusal. Run Ruff on modified Python files. Configured
CI retains its normal checks.

## Trigger Setup focused checks

Exercise `test_trigger.py` and the related Worker and live validation script
cases. Select affected Transient and LIST tests, the existing contributor
Transient paths, and parser help/version. Run Ruff on modified Python files.
Use `preflight-cli.ps1 -TriggerOnly` and an explicitly fictional Resource for
PlanOnly and noninteractive refusal. Default PlanOnly includes every supported
validation mode and captures Trigger Source/Timer before legacy Transient writes;
`-Modes trigger` remains selectable. Cover the compatible `-Modes input-timer`
and `-InputTimerOnly` entries, standard Trigger presets and focused default
PlanOnly selection without physical VISA. For mixed legacy Transient/Trigger
plans, confirm snapshot-before-write order. Test trusted Input OFF and
restore behavior at the owning Core/CLI boundary; uncertain communication
requires manual action rather than blind VISA writes.

Use recording Fake VISA for Input OFF guards, exact Source and Timer readback,
HOLD/BUS dispatch, finite LIST and communication failures. Script tests cover
PlanOnly and noninteractive refusal without simulating operator keystrokes.
Physical Trigger validation requires separate approval, the operator-supplied
resource and settings, and manual observation of HOLD, TIMER, BUS and EXTERNAL
only. The MANUAL physical Input ON path remains unvalidated and blocked; its
offline setup and Input-OFF-safe original-setting restoration remain supported.
Check the four-source PlanOnly presets (2 s pulse, 5 s Timer, 2/2 s LIST,
15 s observation), the MANUAL pre-VISA Input ON refusal, and restoration of an
original MANUAL Source using existing focused tests. Do not extend unrelated
Transient timing tests. Follow the
[contributor Trigger procedure](cli/README.md#contributor-trigger-validation).

## Protection focused checks

Run `tests/test_protection.py`, Protection cases in `tests/test_worker.py`
and `tests/test_live_validation_script.py`, and the affected help/version
checks. Run Ruff on modified Python files. Recording Fake VISA covers the
Input OFF guards, identity, exact readback, partial or uncertain writes and
Clear's one bounded OFF attempt. The PowerShell tests exercise
`preflight-cli.ps1 -ProtectionOnly` and `live-cli-check.ps1 -Modes protection`
in PlanOnly and noninteractive mode, with a fictional Resource and a VISA
blocker. The shareable report leaves physical cases `NOT RUN`.

For Fixed CC preparation, also check a Fixed CW starting state and verify
that Input ON, an unsupported mode or a readback mismatch stops the trial
before a Protection snapshot. Use Fake VISA for ordinary and MAX power
readback, including out-of-range and malformed replies. Check that diagnostic
values stay in private output.

Only the separately approved, operator-confirmed
[Protection procedure](cli/README.md#contributor-protection-validation)
can establish physical trip, power limit, Clear, defaults and restoration.
For focused checks select Protection by itself.

## Configuration readback focused checks

Run the affected Core, CLI and Worker cases from the repository root.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_readonly_core.py tests/test_hardware_validation.py tests/test_trace.py tests/test_readonly_cli.py tests/test_worker.py tests/test_list_core.py tests/test_transient_core.py -k configuration_readback
.\scripts\preflight-cli.ps1 -ReadbackOnly
```

Use recording Fake VISA for mode selection, the bounded LIST step count, and
status replies. Check the compact Transient summary, known Range conflicts,
and the model-specific LIST width limits. Verify query-only operation, session
closure on errors, and LIVE refusal before VISA. Use simulated status
mismatches for LIST and Transient write verification.
Select existing help/version tests when changing CLI arguments. Exercise
`-Modes readback -PlanOnly` and noninteractive refusal, and the directly
affected Fixed, Transient, TRACE and Trigger script plans. Verify that the
readback plan records known expected settings and distinguishes any missing
baseline. Use a fictional USB Resource and block VISA in child processes.
Real-instrument validation requires the separate interactive approval and
reports physical cases as `NOT RUN` in PlanOnly.

## Packaging checks

For packaging, dependency, or entry-point changes:

1. Run `uv sync --all-extras --locked --link-mode=copy` to verify the lockfile.
2. Build the sdist and wheel with
   `.\.venv\Scripts\python.exe -m build --outdir .tmp_tests/dist`.
3. Inspect wheel metadata and contents for the approved distribution name,
   version, Python requirement, license, dependencies, entry point, and packages.
4. Create a separate virtual environment under `.tmp_tests/` and install the
   actual wheel with its runtime dependencies. Do not use an editable install or
   repository `PYTHONPATH` as a substitute.
5. From outside the source tree, import Core and CLI and run that environment's
   `electronicload-tool` executable with `--help`, `--version`, and an unsupported
   argument. Confirm success exits for help/version and stderr with exit `2` for
   the unsupported argument.
6. Review the source import direction, Git status, ignored artifacts, and
   documentation consistency.

Focused packaging checks do not imply full release acceptance or authorization
for real-instrument validation.

## Reporting

Report each planned check as `PASS`, `FAIL`, `SKIP`, `BLOCKED`, or `NOT RUN`, with
the command and any limitation. Dependency download or installation failures
must be reported; a partial check without dependencies is not a full install
pass. Keep run-specific evidence in ignored or separately shared artifacts,
not tracked public documentation.

## Software Sequence focused checks

Run `tests/test_sequence.py` and select Sequence cases in `test_control_cli.py`,
`test_worker.py`, `test_hardware_validation.py` and
`test_live_validation_script.py`. Cover original decimal tokens, complete
preflight, fresh Fixed setup before each ON, loop end OFF, shared simulator state,
interruptible Wait, one-job admission, partial JSON results, and TRACE recovery
restrictions. Use recording Fake VISA for the CLI-only validation scope,
single-session Fixed setup and readback, Stop cleanup, uncertain replies, and
refusal before VISA in ordinary CLI/Worker LIVE.

Use `scripts/preflight-cli.ps1 -SequenceOnly`, and exercise
`live-cli-check.ps1 -Modes sequence` with `-PlanOnly` and unattended refusal
using a fictional resource and blocked VISA. Verify the four fixed plans,
deadline and report redaction. Run existing help/version, Input/Fixed, and
read-only checks affected by shared execution paths and Ruff on changed
Python files. Hardware validation requires separate authorization and the
operator procedure in the [CLI guide](cli/README.md#contributor-software-sequence-validation).

## Basic Workflow focused checks

Run `tests/test_workflows.py` and select Workflow tests in
`tests/test_worker.py`. Include the affected CLI help/version, shared
measurements, Fixed CC/Input and Sequence checks when those paths are touched.
Run Ruff on modified Python files. Use a controllable clock and simulated
readbacks for count and duration cadence, timeout, the initial voltage cutoff,
trapezoidal Ah accumulation at the cutoff boundary, and OFF cleanup before
CSV row export. Check the Common v3 HTTP/JSONL boundary with the simulated
Worker, including retention of cleanup failure evidence across subsequent jobs.
Verify LIVE refusal before VISA with the recording fake.
The basic workflows run offline and require no release or physical-device
acceptance.

## USB and ASRL validation matrix focused checks

Run `tests/test_asrl.py`, `tests/test_readonly_core.py`,
`tests/test_readonly_cli.py`, `tests/test_discovery.py`,
`tests/test_discovery_cli.py` and `tests/test_foundation.py` from the repository
root as applicable to the change. Compare USB and ASRL FULL plans, and
exercise mixed mode selection, report redaction and unattended refusal in
`tests/test_live_validation_script.py`. Use fictional resources and block
both PyVISA library and resource-manager access in offline plans.
Use `preflight-cli.ps1 -SystemQueryOnly` for scoped System Query and ASRL checks.
Run Ruff on changed Python files.

Verify the independent ASRL lifetime, omitted and explicit settings, timeout
transition, identity, cleanup errors, USB isolation, and refusal before VISA
for unapproved scopes. Reserve physical validation for the separately
authorized [ASRL procedure](cli/README.md#physical-validation-procedure).

## CLI validation extensions and Timer expiry

Select the affected PowerShell cases in `tests/test_live_validation_script.py`.
Use fictional resources with VISA blocked to verify the extension modes alone,
duplicate selections, mixed noninteractive refusal and Full PlanOnly for USB
and ASRL. The extension modes run focused Core and CLI tests for native
measurements and Average, VON/Short/CC Slew, or LIST CC setup/Save/Recall/Exit.
Check `-LiveExtensions` for explicit single, mixed and Full selection and
verify that it never falls back to the offline cases. Exercise the contributor
read-only sampling and measurements and the Input OFF guards for Workflow and
Average with recording Fake VISA. For the CC trials, verify the write-start
clock, capacity/voltage outcomes, confirmed OFF within the 10-second limit,
Stop cleanup and the independent-cutoff notice when communication fails.
Model an early drop during Timed Load and require the operator's current-trace
confirmation for script PASS. For isolated settings, cover refusal before
an unrestorable baseline, display of the private recovery baseline before
writes, and no blind writes after communication is lost. Cover Short without
`INP ON`, LIST baseline B established by Recall from a different working value,
then Save A and Recall from B, including ignored Save or Recall without a
manually supplied prior slot width. In PlanOnly, check the safety queries,
conditional cleanup writes and separate isolated run for a source-connected
or mode-changing selection. For Timer expiry, use
a controlled clock for early OFF refusal and
operator confirmation after cleanup. Use a delayed Fake VISA ON reply to verify
that the SCPI write-start deadline already applies while the ON subprocess is
still running. With a process substitute that stays running after termination is
requested, verify that the operator gets the cutoff prompt at the observation
deadline and that the script opens no new VISA session during cleanup.
Select affected Fake VISA cases in `tests/test_input_timer.py`. Physical expiry
remains a separately authorized check with an independent hardware cutoff.

離線檢查使用虛構 Resource 並阻擋實體 VISA。以 Fake VISA 驗證 Workflow
和 Average 的 Input OFF 前置。CC 案例核對 SCPI 寫入起點、容量及電壓截止、
10 秒內確認 OFF、Stop 的清理及通訊失信時的獨立停損提示。Timed Load 模擬
提前掉載，核對腳本須由操作者確認電流記錄才判為 PASS。隔離設定核對無法還原原值
時拒絕寫入、寫入前顯示私有復原基準，以及通訊失信後停止寫入。Short 不開啟
Input。LIST 核對從不同工作值 Recall B 建立基準，再 Save A 並從 B 召回 A；
不需人工提供舊寬度，涵蓋 Save 或 Recall 未生效。
PlanOnly 核對安全查詢及條件式清理。混選含需接電源或切換模式的試驗時，隔離
案例須分開執行。Timer 使用可控制的
時鐘驗證過早 OFF 拒絕與清理完畢後由操作者確認。以 Fake VISA 模擬延遲的
ON 回覆，驗證 ON 子程序尚在執行時已受 SCPI 寫入起點的期限約束。用收到
終止要求後仍在執行的程序替身，確認提示在觀察期限到達時發出，且清理時
未另開 VISA Session。實機到期驗證仍需另行授權及獨立的硬體停損。
