# Electronic Load Tool

Electronic Load Tool is a vendor-neutral Python project for electronic loads.
The current package provides importable Core and CLI packages and a command-line
interface for help and version information.

Instrument control and Product LIVE support are not available. ITECH IT8812C is
the initial target model, not a supported live instrument. PyVISA is a dependency;
the application does not initialize VISA, discover resources, or send SCPI
commands. No VISA backend is required for the current commands.

## Development environment

Python 3.10 or newer is required. From the repository root in PowerShell, install
uv if needed:

```powershell
py -m pip install --user uv
uv --version
```

Create or reuse the repository-local `.venv` and install the project and its
development dependencies from the committed lockfile:

```powershell
uv sync --all-extras --locked --link-mode=copy
```

This follows the same uv workflow as Meters Tool and Powers Tool. Share
`pyproject.toml` and `uv.lock` through Git; recreate `.venv` on each machine rather
than copying it. After pulling changes, run the same locked sync command. It
fails if the lockfile needs updating instead of silently changing dependencies.

When intentionally changing dependencies, update `pyproject.toml`, run the
following command, and review both files together:

```powershell
uv sync --all-extras --link-mode=copy
```

The project is installed in editable mode, so Python source edits are available
without reinstalling. The commands below use `.venv` directly and do not require
activation.

## CLI usage

```powershell
.\.venv\Scripts\electronicload-tool.exe --help
.\.venv\Scripts\electronicload-tool.exe --version
```

Both commands exit successfully. Running without arguments also shows help.
Unsupported arguments write a diagnostic to stderr and exit with code `2`.
Version information comes from the installed distribution metadata.

## Package layout

- `pyproject.toml` defines the single `electronicload-tool` distribution.
- `src/electronicload_tool_core/` owns Core functionality.
- `src/electronicload_tool_cli/` owns the CLI entry point.

Core must not import CLI. WebUI, Worker, and instrument drivers are not included.

## Checks and builds

Run checks from the repository root after syncing the development environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_foundation.py
.\.venv\Scripts\python.exe -m ruff check src tests/test_foundation.py
.\.venv\Scripts\python.exe -m build --outdir .tmp_tests/dist
```

See [Testing Guidelines](docs/testing-guidelines.md) for verification scope and
installed-wheel checks. Contributor changes follow [AGENTS.md](AGENTS.md).

## License

MIT. See [LICENSE](LICENSE).
