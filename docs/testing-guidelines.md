# Testing Guidelines

[AGENTS.md](../AGENTS.md) defines the repository boundaries. These guidelines
describe focused verification within those boundaries.

## Scope

Protect public behavior, component boundaries, and safety requirements at their
owning layer. The current permanent tests cover CLI help, version information,
and rejection of unsupported arguments. Do not add tests for unimplemented
instrument, Worker, or WebUI behavior.

Avoid tests tied to private helpers, source text, exact help prose, or document
formatting. Packaging and installation are checked with actual build artifacts;
do not duplicate those checks in a second permanent test suite.

## Local checks

Sync the development environment as described in the [README](../README.md).
Run pytest from the repository root, starting with the affected tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_foundation.py
.\.venv\Scripts\python.exe -m ruff check src tests/test_foundation.py
```

Default tests require no hardware and must not discover or open physical VISA
resources. Put intentional verification artifacts under `.tmp_tests/`; it is
ignored by Git. `Local/` is reserved for private notes, not test output.

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
