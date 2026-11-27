# Supported Models and Multi-Model Architecture

## Supported Models

| Manufacturer | Canonical model | Model ID |
| --- | --- | --- |
| ITECH | IT8812C | `itech-it8812c` |

## Current Implementation

Core uses a single-model implementation. Identity matching, SCPI behavior and
model limits are implemented directly for IT8812C. The registries, Driver
Families and Capability Profiles below describe the design for future models.

## Multi-Model Architecture

| Component | Responsibility |
| --- | --- |
| Vendor Registry | Record a stable vendor ID, canonical manufacturer name and manufacturer aliases. |
| Model Registry | Link the stable model ID to the vendor ID, canonical model token, model aliases, Driver Family and Capability Profile. |
| Driver Family | Group models by compatible SCPI and instrument behavior. Own command formatting, parameter conversion, reply parsing and instrument-specific operations. |
| Capability Profile | Describe each model's electrical limits, supported modes and features, and parameter limits. |

Resolve identity by matching both manufacturer and model against the registry
entries. Keep detected values, canonical tokens, aliases and stable IDs distinct.

## Extension Rules

- Introduce the multi-model components in Core when a second physical model is
  selected. Keep hardware differences in Core. CLI uses Core results, the Worker
  retains Common v3, and the Orchestrator uses the existing interfaces.
- For future multi-model LIVE execution, resolve the Driver Family from
  the manufacturer and model actually detected through `*IDN?`. An expected
  model ID is a safety mismatch guard only; it must never override the detected
  identity or select a different driver. Offline dry-run and simulation may
  use an explicitly selected registered planning model profile without
  opening physical hardware.
- Handle small driver differences within a family. Use a separate family for
  substantial SCPI or behavior differences. Share a Capability Profile when the
  models' limits, modes and features match.
- Authorize LIVE independently of any driver or profile reuse. Require an explicit
  match for the model ID, command or workflow, transport, VISA backend, execution
  scope and required feature. Evaluate Product, Worker and Contributor separately.
  Deny LIVE by default for new models and reject unknown or mismatched identities
  and missing or unapproved scope combinations.

## Product LIVE Support

| Model ID | Connection | Execution scope | Commands |
| --- | --- | --- | --- |
| `itech-it8812c` | USB INSTR + System VISA (`@ivi`) | Standalone CLI and Worker | `identify`, `input-state`, `measure`, `set-cc`, `input-on`, `input-off` |
| `itech-it8812c` | USB INSTR + System VISA (`@ivi`) | Standalone CLI only | `scpi-version`, `display-mode`, `error` |

The operator supplies the USB resource and Core verifies the exact identity.
Fixed CC controls apply to `set-cc` and `input-on`. Input ON requires explicit
confirmation. The three standalone basic diagnostics need no Contributor scope;
`error` explicitly consumes queue entries (one by default, at most 20 with
`--all`). They do not change the load configuration or Input state. This is an
explicit diagnostic authorization, not a claim of physical validation or an
extension of Worker LIVE. ASRL remains available only for controlled
Contributor validation. See the [CLI connection reference](../cli/README.md#visa-connection-behavior).

## Future Model Onboarding

1. Collect the manufacturer and model, `*IDN?` reply, SCPI programming guide,
   supported transports, and electrical and feature limits.
2. Register the identity, select or implement a Driver Family, and assign a
   Capability Profile based on the model's specifications and behavior.
3. Verify identity resolution, driver and profile selection, model limits, and
   unsupported-scope rejection offline. Use Fake VISA for affected command and
   reply paths and run focused IT8812C regression checks.
4. Obtain separate approval for physical validation of each proposed LIVE scope.
   Authorize only the exact scope after the required validation and approval.
   The explicitly authorized basic standalone diagnostics above are the narrow
   exception to the physical feature-validation prerequisite; do not generalize
   that permission to other models, transports, Worker or functional commands.
   Keep validation evidence separate from the authorization and tracked docs.
