# 離線 Transient CC／CV／CR／CW

[English](transient-modes.md)

Core、CLI、Simulator 與 Common v3 Worker 均支援四種功能的 Continuous、Pulse
及 Toggle。每次 setup 完整取代未啟用的 Transient 設定。使用
`set-transient-cc`、`set-transient-cv`、`set-transient-cr` 或
`set-transient-cw`，再使用共用控制命令。一般 Product 與 Worker LIVE Transient
請求會在存取 VISA 前被拒絕。

## Setup 參數

| 功能 | Level A/B 單位與限制 | Range | Slew |
| --- | --- | --- | --- |
| CC | Low 0–6 A／0.0001 A；High 0–60 A／0.001 A | 必填 `auto\|low\|high` | 必填 `rise_slew`、`fall_slew`，單位 A/μs；Low 0.0001–0.25、High 0.001–2.5 |
| CV | Low 0.1–18 V／0.001 V；High 0.1–120 V／0.01 V | 必填 `auto\|low\|high` | 拒絕 |
| CR | Low 0.05–10 Ω；High 10–7500 Ω；無固定十進位步進 | 必填 `auto\|low\|high` | 拒絕 |
| CW | 0–250 W／0.01 W | 拒絕 | 拒絕 |

所有命令必填 `transient_mode`、`level_a` 與 `level_b`。單一 Range 必須同時接受
A/B 原始數值與精度。Auto 先檢查 Low，再檢查 High；CR 10 Ω 可用於任一手動
Range，Auto 優先選 Low。CC 同時檢查兩個 slew。手動 Range 不會自動改選。
Core 拒絕無效或非有限數值，不四捨五入、不截限。型號限制不代表 source／DUT
的安全操作範圍。

| 模式 | 必填 Width 欄位（秒） | 拒絕的 Width 欄位 |
| --- | --- | --- |
| `continuous` | `width_a`、`width_b` | `pulse_width` |
| `pulse` | `pulse_width`（Level A 持續時間） | `width_a`、`width_b` |
| `toggle` | 無 | 全部 |

所有 Width 均為 0.00002–3600 s（含邊界）。CC 要求 0.000001 s 步進。
CV／CR／CW 接受範圍內精確且有限的 Width，不施加未記載的十進位步進。
這不代表實體時序精度。

```powershell
.\.venv\Scripts\electronicload-tool.exe set-transient-cv --simulate --transient-mode continuous --range auto --level-a 5 --level-b 6 --width-a 0.0000205 --width-b 0.002 --json
.\.venv\Scripts\electronicload-tool.exe set-transient-cr --dry-run --transient-mode pulse --range auto --level-a 10 --level-b 1000 --pulse-width 0.001 --json
.\.venv\Scripts\electronicload-tool.exe set-transient-cw --simulate --transient-mode toggle --level-a 1 --level-b 2 --json
```

CLI 旗標使用連字號，Worker JSON 參數鍵使用底線。Worker 數值參數必須是 JSON
number，不接受字串或布林值。HTTP 與 `send-command --arguments-json` 保留原始
十進位 token 至驗證完成。Dry-run 僅驗證及規劃，不存取硬體、不改變模擬狀態。

## 控制與結果

每次獨立 CLI 模擬皆從新狀態開始。完整流程請使用同一個模擬 Worker，並等待
每個命令的 `job_finished` 成功後再繼續：

`set-transient-*` → `transient-enable` → 明確確認的 `input-on` →
`transient-trigger` → `transient-status` → `input-off` → `transient-disable`。

Setup、Enable 及 Disable 都要求 Input OFF；Setup 另要求 Transient 停用。
Trigger 要求已設定且啟用的 Transient，以及 Input ON。模擬 Input ON 檢查已設定
功能、level、range、Short OFF 與 Stop 狀態，並要求 `confirm_input_on: true`。
必須明確退出 LIST 才能變更 Transient。Disable 保留設定及 trigger 紀錄；
Input OFF 不會停用 Transient。Fixed 或 LIST setup 前須先停用 Transient。
所有操作皆無隱含 Input 切換或清理；Stop 維持不執行 I/O。

領域狀態新增 `function: "CC"|"CV"|"CR"|"CW"`；`mode` 維持
`continuous|pulse|toggle`。設定前兩者均為 null。CW 的 `range` 為 null；其他功能
回傳已解析的 Low／High。既有 CC 設定欄位名稱與 JSON 型別保持相容。新設定包含：

| 功能 | Level 欄位 | Width 欄位 |
| --- | --- | --- |
| CV | `level_a_v`、`level_b_v`（JSON number） | 適用的 `width_a_s`、`width_b_s`、`pulse_width_s`，以精確十進位字串回傳 |
| CR | `level_a_ohm`、`level_b_ohm`（精確十進位字串） | 同樣使用精確十進位字串 |
| CW | `level_a_w`、`level_b_w`（JSON number） | 同樣使用精確十進位字串 |

新設定亦保留 `transient_mode` 與已解析的 `range`（CW 為 null），不含 slew。
Dry-run setup 將這些設定置於結果頂層，另含 `function`、`command` 及
`hardware_access: false`。`GET /status` 維持 Worker／runtime 結構；
`transient-status` 是獨立的非同步領域命令。Manifest schema 維持 2，Worker
protocol 維持 3。

Simulator 僅記錄設定與已接受的軟體 trigger。同樣前提下可重複觸發。
Trigger 成功不代表波形形狀、時序或完成。要求 Worker Stop 前，須明確執行並
確認 Input OFF；Stop、程序結束及 VISA 關閉均不能證明 OFF。

## 貢獻者驗證

CLI 專用 `--hardware-validation transient-cc|transient-cv|transient-cr|transient-cw`
路徑遵循[受控驗證程序](../README.md#controlled-instrument-validation)。
實體執行要求獨立授權、精確型號／USB／System VISA、Input OFF 檢查、完整預期
設定與讀回比對，以及明確 ON 確認。此路徑不授予一般 Product 或 Worker LIVE 權限。

既有 `preflight-cli.ps1 -TransientOnly` 涵蓋四種功能的離線檢查。
`live-cli-check.ps1` 預設執行模式為
`cc,cv,cr,cw,transient-cc,transient-cv,transient-cr,transient-cw`；`-Modes` 保留子集
選擇。`-PlanOnly` 不存取 VISA，逐一驗證請求，實體案例標示為 NOT RUN。
新增 Transient 試驗預設參數為 `-VoltageLevelA 5`、`-VoltageLevelB 6`（V）、
`-ResistanceLevelA 10`、`-ResistanceLevelB 1000`（Ω）、`-PowerLevelA 1`、
`-PowerLevelB 2`（W）。共用既有 Width 參數；CC 保留獨立的 level／slew。
CV 使用 Continuous Auto／Pulse Low／Toggle High；CR 使用 Continuous Auto／
Pulse High／Toggle High；CW 三種模式皆無 Range 或 slew。Auto 預期 Range 由
Core 決定。實體執行保留明確 Resource、寫入／ON 雙重開關、互動確認、source
限制（>0 至 2 A／>0 至 18 V）、手動 OFF 應變及私密／遮蔽報告規則。清理使用
當前 Transient scope，且確認 OFF 後才 Disable。單次 30 秒與總計 900 秒期限維持
不變；預設 192 次命令平均每次約有 4.7 秒。慢速實機可能需要選擇子集，不能假設
有更長期限。

[IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf)
涵蓋 IT8812C。印刷頁 19–20、48、50–58 定義 Transient 控制：
`FUNC:MODE FIX`、所選的 `FUNC CURR|VOLT|RES|POW`、各功能的 `:TRAN:MODE`、
`:TRAN:ALEV`、`:TRAN:BLEV`、`:TRAN:AWID/BWID`，以及獨立的 `TRAN ON/OFF`、
`TRIG:SOUR HOLD` 與 `TRIG:IMM`。Pulse 使用 AWID；Toggle 不寫入 Width。
CV 停用硬體自動 Range 並選擇 18／120 V；CR 選擇 10／7500 Ω。無法辨識的讀回
會拒絕繼續執行。CW 不寫入 Range 或 slew。手冊語法與離線／Fake 測試無法證明
實機接受情形。
