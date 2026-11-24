# Agent Instructions

These instructions define long-term, repository-specific boundaries for agents
working on Electronic Load Tool. Global agent rules already cover communication,
planning, simple and surgical changes, and text-file hygiene.

The initial repository may not yet contain all components or referenced documents
(CLI, Worker, WebUI, Desktop, Help, or validation scripts). The corresponding
rules apply when those components are introduced. Do not create placeholder
components or documents merely to satisfy a reference, and do not describe
future components as already implemented.

## 1. Project Context

- Read the affected code and the relevant documentation before changing
  behavior. Use the root `README.md` and `pyproject.toml`, when present, for
  tasks concerning installation, packaging, entry points, dependencies, or
  repository layout.
- Read the relevant files in `docs/contracts/`, when present, before changing
  CLI/WebUI adapter behavior, Worker or subprocess orchestration, JSON/JSONL
  schemas, or HTTP control/status contracts. Do not invent missing contracts.
- Preserve machine-mode stdout as structured JSON or JSONL only. Human-readable
  diagnostics belong in text mode or stderr; do not emit plain-text lifecycle
  output on machine-mode stdout.
- Get user confirmation before changing contract-defined queue admission or
  rejection semantics, Worker lifecycle or HTTP control behavior, process exit
  meanings, run correlation, or artifact path, privacy, redaction, publication,
  or ownership rules.
- Read `docs/webui/web-ui-change-rules.md` before changing WebUI static files or
  in-app UI behavior once that document exists. If the WebUI is present but
  its change rules are missing, do not assume or invent them; raise the gap.

## 2. Distribution And Import Boundaries

- The root `pyproject.toml`, when added, is the single distribution metadata
  boundary for `electronicload-tool`. Do not recreate component-local
  distributions or introduce an `electronicload_tool.*` namespace without
  explicit user approval.
- Get user confirmation before changing public packaging boundaries: package
  name or version, dependencies or optional dependency groups, console scripts
  or entry points, build system, or Core/CLI/WebUI component ownership. Tool
  configuration such as pytest, ruff, or mypy may be changed when the requested
  task clearly includes it.
- Use the import packages `electronicload_tool_core`, `electronicload_tool_cli`,
  and `electronicload_tool_webui` as the respective components are introduced.
  Preserve those names once established; do not require unimplemented
  components to exist at repository initialization.
- Core must not import CLI or WebUI. CLI and WebUI may depend on Core, but must
  not depend on each other.

## 3. Multi-Vendor Extension Boundary

- Keep the product identity and shared architecture vendor-neutral. ITECH
  IT8812C is the initial target model, not proof of live Product support.
  Implementation details and user documentation may remain vendor- or
  model-specific where accurate. Validation evidence may also remain vendor-
  or model-specific, but belongs in private or separately shared review
  artifacts, not tracked public documentation.
- For future brands or models, keep capability, identification, validation,
  support-policy, and instrument-command differences primarily in Core. Keep
  detected manufacturer/model identity, canonical model tokens, stable model
  IDs, and aliases distinct; do not infer vendor or support identity by
  splitting or formatting a model ID.
- CLI and WebUI must not copy or reimplement brand/model capabilities, safety
  limits, identification rules, or instrument-command branches. They must use
  Core profile, capability, validation, and support-policy results. Pure
  presentation may be derived from Core metadata.
- Apply fail-closed behavior to live hardware paths. Product LIVE support is
  exact-scoped by canonical model ID, command, transport, backend, and required
  categorical feature. Do not infer support across models, commands,
  transports, backends, connection scopes, or feature families. Unknown,
  unidentified, mismatched, missing, unsupported, unvalidated, or
  non-Product-open scopes must not run live. Dry-run and simulator paths may
  use an explicitly selected registered profile as allowed by the applicable
  contracts once defined.
- These rules constrain future changes. Do not pre-build abstractions for an
  unsupported second vendor or refactor reasonable current model-specific code
  without a concrete requirement.

## 4. Instrument Safety

- Treat changes that can affect a live electronic load or the connected device
  under test as high risk. Get user confirmation before changing SCPI/driver
  commands, load input-on/off behavior, CC/CV/CR/CW mode or setpoint application
  order, VISA timeouts, instrument protection or protection-clear behavior,
  LIST or sequence timing, trigger or wait strategies, remote/local behavior,
  or stop/cleanup behavior.
- Keep real load input OFF by default. Connection, identification, diagnostics,
  read-only queries, and configuration must not implicitly enable load input.
  Default tests must never enable real hardware input. Automated or unattended
  live flows may enable input only through the applicable contract-required
  configuration and per-request confirmation gates once defined; no such gates
  are presumed to exist during repository initialization.
- Examples or validation flows that enable real load input must be explicit,
  opt-in, bounded to model- and setup-appropriate safe operating conditions,
  and include a deliberate input-off cleanup path. Do not introduce automatic
  high-current or high-power loading without an explicit safety design and
  user approval. Do not assume mode switching or changing setpoints while
  input is ON is safe without model-appropriate verification.
- Once a Worker engine is introduced, keep stop-state signaling separate from
  instrument I/O: `engine.stop()` should set stop state/events only; VISA I/O
  belongs on the Worker or cleanup path. Establish and document its cleanup
  sequence before live operation. Preserve the implemented order thereafter
  unless explicitly approved; do not assume closing a VISA session or returning
  to local automatically turns load input OFF.
- Keep concrete SCPI/driver commands and query/wait semantics in Core,
  contracts, or supported-model documentation. Verify model-specific SCPI,
  load mode and setpoint syntax, timing, protection behavior, and LIST,
  sequence, and trigger behavior against the relevant programming guide.
  Do not change or generalize them without model-appropriate validation and
  explicit approval.
- Keep VISA resource strings configurable. Never commit real resource strings,
  instrument serial numbers, or private lab addresses.

## 5. Testing And Validation

- Follow [Testing Guidelines](docs/testing-guidelines.md) once available.
  Until then, follow the applicable rules in this section; do not create an
  empty guideline solely to satisfy this reference. Default tests must run
  without hardware; use simulators or fake instruments for command,
  validation, trigger-routing, and error-path coverage.
- Prioritize tests that protect important system behavior, public contracts,
  cross-component integration, and safety boundaries. Test each contract
  primarily at its owning layer; avoid redundant cross-layer coverage unless
  needed to verify actual integration behavior.
- Do not automatically add permanent regression tests for every minor bug fix.
  Prefer improving or consolidating existing coverage. Avoid tests coupled to
  private implementation details, source text, incidental execution order, or
  non-contractual wording unless they protect an explicit contract or safety
  requirement. Keep test complexity and maintenance cost proportional to risk.
- Run pytest from the repository root when pytest tests exist. Run the narrowest
  relevant checks first, then broader no-hardware tests when practical.
- Match verification depth to the changed boundary. Routine documentation,
  presentation-only, test-only, and hardware-independent refactors should
  normally use focused or affected no-hardware checks; do not run full release
  acceptance merely because a change was made.
- Reserve `scripts/release-acceptance.ps1`, if adopted, for release, packaging,
  distribution, entry-point, comparable release-gate work, and formal release
  candidates. Keep release acceptance separate from real-instrument validation.
- Escalate to approved real-instrument validation when a change materially
  affects SCPI/driver behavior, load input or setpoint sequencing,
  VISA/backend/transport behavior, trigger/wait or LIST/sequence timing,
  protection/restore safety, completion pulses (if applicable), or
  stop/release/local/cleanup behavior. Documentation-only and
  hardware-independent refactors do not normally require a hardware rerun.
- Use `.tmp_tests/` for intentional test and validation artifacts.
- Real-instrument validation must be explicit, opt-in, bounded, and use a VISA
  resource supplied by the user. Never infer, scan for, or guess a resource for
  unattended live validation. Do not describe dry-run, simulator, mocked, or
  plan-only results as real-instrument validation.
- If the full test suite is blocked by environment permissions, report the
  limitation and the focused checks that ran. Live validation is not a
  substitute and should run only when live behavior is in scope and approved.
- Report every failed, skipped, blocked, or unexecuted verification step.

## 6. Documentation Boundary

- Keep tracked documentation durable, public, and free of temporary planning,
  transient validation, review, or promotion status, private operator context,
  and run-specific validation results, records, evidence, or artifacts.
- Keep `USER_GUIDE.md` files, when introduced, operator-facing. Keep setup,
  build, maintainer, validation workflow and requirements, and detailed
  engineering material in `README.md` or focused contributor documentation.
  Include in `USER_GUIDE.md` only the minimum information required for normal
  user operation.
- Keep English and Traditional Chinese documentation semantically aligned and
  update both together. For bundled offline Help, once implemented, follow
  `docs/help/README.md` and the established generator; synchronize only the
  generated assets owned by the affected runtime surface(s). Generated Help
  HTML must not be manually maintained as a second documentation source.
- Do not place personal filesystem paths, real VISA resources, instrument
  serial numbers, private lab addresses, or link-local/private network
  addresses in tracked public documentation.
- Operator-facing and product-support documentation must not include internal
  phase names, candidate evidence, unperformed validation, review or promotion
  status or plans, or temporary laboratory-specific context.
