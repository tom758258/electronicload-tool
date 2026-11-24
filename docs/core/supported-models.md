# Supported Models and Multi-Model Architecture / 支援型號與多型號架構

## Supported Models / 支援型號

| Manufacturer / 製造商 | Canonical model / 標準型號 | Model ID / 型號 ID |
| --- | --- | --- |
| ITECH | IT8812C | `itech-it8812c` |

## Current Implementation / 目前實作

Core uses a single-model implementation. Identity matching, SCPI behavior and
model limits are implemented directly for IT8812C. The registries, Driver
Families and Capability Profiles below describe the design for future models.

Core 目前採單型號實作，身分核對、SCPI 行為與型號限制都直接針對 IT8812C。
下文的品牌與型號登錄、Driver Family 和 Capability Profile 是未來擴充的設計。

## Multi-Model Architecture / 多型號架構

| Component / 元件 | Responsibility | 職責 |
| --- | --- | --- |
| Vendor Registry / 品牌登錄 | Record a stable vendor ID, canonical manufacturer name and manufacturer aliases. | 記錄穩定的品牌 ID、標準製造商名稱與製造商別名。 |
| Model Registry / 型號登錄 | Link the stable model ID to the vendor ID, canonical model token, model aliases, Driver Family and Capability Profile. | 將穩定的型號 ID 對應至品牌 ID、標準型號名稱、型號別名、Driver Family 與 Capability Profile。 |
| Driver Family | Group models by compatible SCPI and instrument behavior. Own command formatting, parameter conversion, reply parsing and instrument-specific operations. | 依相容的 SCPI 語法與儀器行為分組，負責命令格式、參數轉換、回應解析與儀器特有操作。 |
| Capability Profile | Describe each model's electrical limits, supported modes and features, and parameter limits. | 描述各型號的電氣限制、支援模式與功能，以及參數限制。 |

Resolve identity by matching both manufacturer and model against the registry
entries. Keep detected values, canonical tokens, aliases and stable IDs distinct.

識別時依登錄資料同時核對製造商與型號。儀器回傳值、標準名稱、別名與穩定 ID 各自保留。

## Extension Rules / 擴充規則

- Introduce the multi-model components in Core when a second physical model is
  selected. Keep hardware differences in Core. CLI uses Core results, the Worker
  retains Common v3, and the Orchestrator uses the existing interfaces.

  選定第二個實際型號後，再於 Core 導入多型號元件。硬體差異由 Core 處理。
  CLI 使用 Core 的結果，Worker 維持 Common v3，Orchestrator 使用既有介面。
- Handle small driver differences within a family. Use a separate family for
  substantial SCPI or behavior differences. Share a Capability Profile when the
  models' limits, modes and features match.

  小幅 Driver 差異可在同一家族處理。SCPI 語法或儀器行為差異較大時，使用獨立家族。
  型號的限制、模式與功能一致時，可以共用 Capability Profile。
- Authorize LIVE independently of any driver or profile reuse. Require an explicit
  match for the model ID, command or workflow, transport, VISA backend, execution
  scope and required feature. Evaluate Product, Worker and Contributor separately.
  Deny LIVE by default for new models and reject unknown or mismatched identities
  and missing or unapproved scope combinations.

  LIVE 授權獨立於 Driver 或 Profile 的共用。依型號 ID、命令或工作流程、傳輸、
  VISA 後端、執行範圍與必要功能逐項明確授權。Product、Worker 與 Contributor
  分開判定。新型號預設拒絕 LIVE，身分未知或不符、組合缺少授權或未獲核准時一律拒絕。

## Product LIVE Support / Product LIVE 支援範圍

| Model ID / 型號 ID | Connection / 連線 | Commands / 命令 |
| --- | --- | --- |
| `itech-it8812c` | USB INSTR + System VISA (`@ivi`) | `identify`, `input-state`, `measure`, `set-cc`, `input-on`, `input-off` |

The operator supplies the USB resource and Core verifies the exact identity.
Fixed CC controls apply to `set-cc` and `input-on`. Input ON requires explicit
confirmation. ASRL is available for controlled Contributor validation. See the
[Core VISA connections and LIVE support policy](../visa-connections.md#live-support-policy--live-支援規則).

USB 資源由操作者提供，Core 會核對確切身分。`set-cc` 與 `input-on` 受 Fixed CC
規則限制。Input ON 需要明確確認。ASRL 可用於受控的貢獻者驗證。詳見
[Core VISA 連線與 LIVE 支援規則](../visa-connections.md#live-support-policy--live-支援規則)。

## Future Model Onboarding / 新型號導入流程

1. Collect the manufacturer and model, `*IDN?` reply, SCPI programming guide,
   supported transports, and electrical and feature limits.

   蒐集製造商與型號、`*IDN?` 回應、SCPI 程式設計手冊、支援的傳輸方式、電氣與功能限制。
2. Register the identity, select or implement a Driver Family, and assign a
   Capability Profile based on the model's specifications and behavior.

   登錄身分，依型號規格與實際行為選用或實作 Driver Family，並指定 Capability Profile。
3. Verify identity resolution, driver and profile selection, model limits, and
   unsupported-scope rejection offline. Use Fake VISA for affected command and
   reply paths and run focused IT8812C regression checks.

   離線驗證身分解析、Driver 與 Profile 選擇、型號限制，以及不支援範圍的拒絕行為。
   使用 Fake VISA 驗證受影響的命令與回應，並執行相關的 IT8812C 回歸測試。
4. Obtain separate approval for physical validation of each proposed LIVE scope.
   Authorize only the exact scope after the required validation and approval.
   Keep validation evidence separate from the authorization and tracked docs.

   針對擬開放的各 LIVE 範圍另外取得實機驗證授權。完成必要驗證並取得核准後，才授權
   該確切組合。驗證證據與授權分開記錄，並存放於版本庫文件之外。
