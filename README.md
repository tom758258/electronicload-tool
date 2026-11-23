# Electronic Load Tool

Electronic Load Tool provides read-only identification and measurements,
plus fixed-CC configuration, offline CV/CR/CW/Transient/LIST CC and deliberate Input ON/OFF through a shared Core,
Standalone CLI and bundled Common v3 Worker. The initial registered model is
ITECH IT8812C (`itech-it8812c`).

Product LIVE supports `identify`, `input-state`, `measure`, `set-cc`,
`input-on` and `input-off` only for the exact IT8812C identity with an
operator-supplied USB INSTR Resource and System VISA (target: Keysight IO
Libraries Suite). Core enforces model/range limits without any optional Safety Config. Input ON still requires
deliberate confirmation. Simulation and dry-run never grant hardware authorization.

## Features

- Read-only identification, Input state and measurements for the exact supported IT8812C Product LIVE scope.
- [RS-232 contributor validation](docs/cli/README.md#asrl-contributor-validation--asrl-貢獻者驗證) for the shared IT8812C validation matrix over an explicit ASRL INSTR resource.
- Fixed CC and deliberate Input ON/OFF with the existing hardware safety gates.
- Offline Fixed CV/CR/CW and software Auto Range for Fixed CC/CV/CR.
- Offline Fixed CC paired optional Slew, standalone VON setup/status, and Fixed-mode Short ON/OFF/status.
- Offline Fixed Input Timer setup/enable/disable/status, with Short and dynamic-mode exclusions.
- Offline Transient CC/CV/CR/CW (Continuous, Pulse, Toggle) and native LIST CC.
- Offline protection status, settings and clear, with separately approved contributor validation.
- [Configuration readback](docs/cli/README.md#configuration-readback) for the active mode and dedicated status commands.
- Hardware-free simulation and dry-run; Common v3 Worker with JSON/JSONL for automation.
- Fail-closed model, command, transport and backend permissions; no implicit Input ON.
- Offline Software Sequence with JSON preview, simulator and one Worker job per run. See the [CLI guide](docs/cli/README.md#software-sequence).
- [Offline workflows](docs/cli/README.md#basic-workflows) for periodic or conditional measurement, timed CC load and CC discharge through the CLI or Worker.

## Project Structure

- `pyproject.toml` defines the single `electronicload-tool` distribution.
- `src/electronicload_tool_core/` owns Core functionality.
- `src/electronicload_tool_cli/` owns the CLI entry point.

Core owns model limits, identity, support policy, queries, parsing, VISA lifetime,
and simulation. CLI handles arguments and presentation. Core must not import
CLI. WebUI is not implemented. The bundled Worker is part of the CLI package and uses the same Core.

### Adding instrument models / 擴充儀器型號

Introduce the Vendor Registry, Model Registry, Driver Families and Capability
Profiles in Core when a second actual model is selected. Base the design on its
`*IDN?` reply, programming guide and specifications.

- **Identity** — Match both manufacturer and model. The Vendor Registry maps
  manufacturer aliases to a canonical manufacturer and stable vendor ID. Each
  Model Registry entry links a stable model ID and vendor ID to the canonical
  model token, model aliases, Driver Family and Capability Profile. Keep the
  detected values distinct from the canonical tokens and IDs.
- **Driver Family** — Group models by compatible SCPI and instrument behavior.
  The driver owns command formatting, parameter conversion, reply parsing and
  instrument-specific operations. Handle small differences within a family and
  use a separate driver when syntax or behavior differs substantially.
- **Capability Profile** — Describe each model's electrical limits, supported
  modes and features, and parameter limits. Models can share a profile when
  these match.
- **LIVE policy** — Require an explicit authorization for the exact model ID,
  command or workflow, transport, VISA backend, execution scope and any required
  feature. Product, Worker and Contributor authorizations are distinct. Deny LIVE
  for new models by default and reject unidentified or mismatched instruments
  and unlisted combinations. Core policy evaluates authorization independently
  of driver or profile reuse. Keep validation evidence separate from authorization.

Keep hardware differences in Core. CLI uses Core results, the Worker retains
Common v3, and the Orchestrator uses the existing interfaces. See the current
[LIVE support policy](docs/visa-connections.md#live-support-policy--live-支援規則).

選定第二個實際型號後，再於 Core 導入品牌登錄、型號登錄、Driver Family 與
Capability Profile。以該型號的 `*IDN?` 回應、程式設計手冊及規格為設計依據。

- **身分識別** — 同時核對品牌與型號。品牌登錄將製造商別名對應至標準製造商名稱
  與穩定的品牌 ID。每筆型號登錄記錄穩定的型號 ID、品牌 ID、標準型號名稱、
  型號別名、Driver Family 和 Capability Profile。儀器實際回傳值、標準名稱與 ID
  各自保留。
- **Driver Family** — 依 SCPI 語法與儀器行為的相容性分組。Driver 負責命令格式、
  參數轉換、回應解析與儀器特有的操作。小幅差異在同一家族內處理，語法或行為
  差異大時使用獨立的 Driver。
- **Capability Profile** — 描述各型號的電氣規格、支援的模式與功能，以及參數限制。
  這些條件一致的型號可以共用 Profile。
- **LIVE 授權** — 依確切的型號 ID、命令或工作流程、傳輸、VISA 後端、執行範圍與
  必要功能逐項授權。Product、Worker 和 Contributor 各有自己的授權範圍。新型號
  預設拒絕 LIVE，無法識別或身分不符的儀器，以及未列出的組合一律拒絕。共用
  Driver 或 Profile 時，仍須由 Core 的授權政策各自判定。驗證證據與授權分開記錄。

硬體差異由 Core 處理。CLI 使用 Core 的結果，Worker 維持 Common v3，Orchestrator
使用既有介面。
現行範圍見 [LIVE 支援規則](docs/visa-connections.md#live-support-policy--live-支援規則)。

## Key Dependencies

| Component | Purpose |
| --- | --- |
| Python 3.10+ | Runtime |
| [PyVISA](https://pyvisa.readthedocs.io/) | VISA communication |
| System VISA (PyVISA `@ivi`) | Required external runtime for authorized USB INSTR LIVE commands and ASRL INSTR contributor validation |
| [uv](https://docs.astral.sh/uv/) | Locked local development environment |
| pytest / Ruff / build | Optional development tools declared in `pyproject.toml` |

The single Python distribution and its dependencies are defined in
[pyproject.toml](pyproject.toml) and [uv.lock](uv.lock).
There is no implemented WebUI or Desktop component.

## Install

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

## Quick Start

From the repository root, run safe no-hardware commands:

```powershell
.\.venv\Scripts\electronicload-tool.exe --help
.\.venv\Scripts\electronicload-tool.exe identify --simulate --json
.\.venv\Scripts\electronicload-tool.exe set-cc --simulate --current 1 --range auto --json
.\.venv\Scripts\electronicload-tool.exe set-cc --dry-run --current 1 --range low --rise-slew 0.1 --fall-slew 0.2 --json
```

Each standalone simulation starts with fresh state. Use one simulated Worker
for multi-command sequences such as setup, confirmed Input ON, Short ON/OFF,
and explicit Input OFF. See the [CLI README](docs/cli/README.md) for detailed
commands, limits, mode sequences and safe usage.

## Test

Follow [Testing Guidelines](docs/testing-guidelines.md) and [AGENTS.md](AGENTS.md).
From the repository root, use the narrowest no-hardware checks for the files or
behavior actually changed. Prepare the ignored test directory first:

```powershell
New-Item -ItemType Directory -Force .tmp_tests | Out-Null
.\.venv\Scripts\python.exe -m pytest -q tests/test_foundation.py
```

The focused CLI preflight is available as
`scripts/preflight-cli.ps1 -FixedSlewVonShortOnly`. It does not authorize
LIVE operation. See [CLI validation scripts](docs/cli/README.md#cli-validation-scripts)
and [controlled instrument validation](docs/cli/README.md#controlled-instrument-validation)
for procedures and limitations. No routine full local suite or real-device test
is implied by a documentation change.

## Documentation

- [CLI README — detailed commands and validation](docs/cli/README.md)
- [Core VISA transport and LIVE support policy](docs/visa-connections.md)
- [Electronic Load Worker Contract](docs/contracts/electronicload-worker-contract.md)
- [Common Worker Protocol](docs/contracts/common-worker-protocol.md)
- [Common CLI JSON / JSONL Contract](docs/contracts/common-cli-jsonl-contract.md)
- [Testing Guidelines](docs/testing-guidelines.md)
- [AGENTS.md](AGENTS.md) — contribution and safety boundaries

## License and Disclaimer

MIT. See [LICENSE](LICENSE). This is an independent, unofficial software tool
and is not affiliated with or endorsed by ITECH. Hardware operation requires
appropriate instrument, source/DUT safety precautions and explicit authorization.
Offline results do not prove physical hardware support.
