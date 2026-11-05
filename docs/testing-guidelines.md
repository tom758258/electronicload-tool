# Testing Guidelines

[AGENTS.md](../AGENTS.md) defines the repository boundaries. These guidelines
describe focused verification within those boundaries.

## Scope

Protect public behavior, component boundaries, and safety requirements at their
owning layer. Permanent tests cover CLI help/version, read-only Core policy,
identity, query parsing, session cleanup, and CLI output/exit integration.
Default tests replace VISA with a recording fake; an autouse fixture blocks
accidental creation of a real resource manager. Add focused tests for implemented
fixed-CC/Input switching, offline CV preview, and Common v3 Worker behavior. Do not add tests for
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

## Fixed-CC/Input/Worker focused checks

Scope these tests to the new Core, CLI and Worker behavior and the directly
affected CLI parser/help/version and read-only CLI contracts:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_control_core.py tests/test_control_cli.py tests/test_worker.py tests/test_foundation.py tests/test_readonly_cli.py
.\.venv\Scripts\python.exe -m ruff check src/electronicload_tool_core/control.py src/electronicload_tool_core/simulator.py src/electronicload_tool_cli/cli.py src/electronicload_tool_cli/worker.py src/electronicload_tool_cli/worker_client.py tests/test_control_core.py tests/test_control_cli.py tests/test_worker.py
.\scripts\preflight-cli.ps1
```

Worker integration uses a real local simulated subprocess, HTTP and JSONL,
but never physical hardware. The **single** `live-cli-check.ps1` now plans
read-only plus CC High/Low, Input ON, on-state measurement and Input OFF in
one fixed sequence. Test its `-PlanOnly` operation and unattended LIVE refusal
with a **fictional** USB Resource. Without `-ConfirmWrites` or
`-EnableInputOn`, and in noninteractive LIVE runs, verify it refuses before
opening any VISA session or sending SCPI. The script defaults to 0.1 A, with a 2 A script-only hard ceiling.
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
OFF before CC; high and low range are configured with the same bounded current
before one authorized ON/OFF trial. Reject further normal tests on any failure.
After a potentially attempted Input ON, make one explicit bounded OFF cleanup
attempt; if physical OFF cannot be confirmed, flag operator action. Validation
does not automatically promote Product LIVE support or demonstrate LIVE Worker
coverage. Because this change touches only PowerShell verification/docs, do
not rerun release packaging or unrelated pytest.

Dependency/lockfile changes require the focused packaging checks below.
Report unavailable PowerShell, Ruff, dependencies and hardware as BLOCKED
or NOT RUN, not PASS.

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
