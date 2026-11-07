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
Use only an explicitly fictional USB resource for offline script verification, and never reuse it for real validation. Real runs follow
the authorization, interaction, runtime-review, and deadline requirements in
the [README](../README.md#controlled-instrument-validation).

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
all five modes: CC, CV, CR, CW and Transient CC. `-Modes` selects one or more
modes while retaining common read-only safety prechecks and each selected
mode's explicit Input ON/OFF flow. Test its `-PlanOnly` operation and unattended
LIVE refusal with a **fictional** USB Resource. Without `-ConfirmWrites` or
`-EnableInputOn`, and in noninteractive LIVE runs, verify it refuses before
opening any VISA session or sending SCPI. CC defaults to 0.1 A, with a
2 A script-only hard ceiling.
Verify redacted shareable case totals and normal mode's lack of private raw
logs; `-DebugArtifacts` uses the existing private/raw location without
changing authorization. No hardware is permitted in automated checks.

Fake VISA for control must emulate Local mode ignoring setup writes until
`SYST:REM`, numeric `CURR:RANG?` responses (including zero for low),
nonzero current readback mismatches, and Worker Stop/unsafe-state changes
between remote transition and Input ON. Read-only commands must not send
`SYST:REM` or other setup writes.

Run the **single** `live-cli-check.ps1` full LIVE command only with
separately approved model/Resource, bounded DUT/source, explicit two-layer
write/Input ON opt-in, and a manual OFF contingency. Check physical Input
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

For offline Transient CC, run only its Core/CLI tests and selected Worker cases:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_transient_core.py tests/test_transient_cli.py tests/test_worker.py -k transient
.\scripts\preflight-cli.ps1 -TransientOnly
```

Cover complete mode-specific setup, both levels/slew rates, exact resolution,
Auto Low preference and High fallback, no rounding, atomic refusal, state guards,
repeated software triggers, status snapshots and side-effect-free dry-run.
Verify one simulated Worker's HTTP/JSONL flow, Manifest entries, decimal-token
preservation, failed-job terminals, unchanged `GET /status`, and LIVE refusal
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
unchanged authorization and source-limit gates, selected case totals, and NOT RUN
for planned cases. Unselected modes must have no cases or PASS counts.
Keep every existing Transient mode/range, repeated trigger, status and OFF/disable
step. No unrelated Core/CLI/Worker, local full-suite, release or packaging checks
are required for this script-only behavior change.
Exercise failure cleanup with a fake CLI/VISA process as appropriate: failure
after a possible ON must attempt explicit OFF, an enabled Transient is disabled
only after verified OFF, and failures retain FAIL with remaining cases NOT RUN.
Check redaction and that normal runs do not retain private raw artifacts.
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
