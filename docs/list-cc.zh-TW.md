# 離線原生 LIST CC

[English](list-cc.md)

Core、CLI、Simulator 與 Common v3 Worker 支援 IT8812C 原生 CC LIST 的模擬與
dry-run 設定。Product LIVE LIST 在開啟 VISA 前即拒絕，既有 contributor override
也不能開放。此介面不包含 CV/CR/CW LIST 或原生 OCP/OPP/Battery 測試。

## 設定與 CLI

`set-list-cc` 接收一份完整 JSON。Core 與 Worker 使用相同物件；CLI 可透過
`--arguments-json` 或 `--file` 二擇一輸入：

```json
{
  "range": "auto",
  "repeat_count": 3,
  "steps": [
    {"current_a": 0.5, "slew_a_per_us": 0.1, "width_s": 1},
    {"current_a": 1, "slew_a_per_us": 0.2, "width_s": 2}
  ]
}
```

```powershell
.\.venv\Scripts\electronicload-tool.exe set-list-cc --simulate --file list.json --json
.\.venv\Scripts\electronicload-tool.exe set-list-cc --dry-run --arguments-json '{"range":"auto","repeat_count":1,"steps":[{"current_a":0.5,"slew_a_per_us":0.1,"width_s":1},{"current_a":1,"slew_a_per_us":0.2,"width_s":2}]}' --json
.\.venv\Scripts\electronicload-tool.exe list-save --dry-run --slot 1 --json
.\.venv\Scripts\electronicload-tool.exe list-recall --dry-run --slot 1 --json
.\.venv\Scripts\electronicload-tool.exe list-status --simulate --json
```

範例中的所有欄位皆必填；未知或重複欄位會被拒絕。Step 數值必須是有限 JSON
數字，不能是布林值或數字字串。檔案採 UTF-8（允許 BOM），格式與直接輸入相同，
不接受 YAML/CSV。只有 CLI 讀取本機檔案；Worker 接收資料，不接受客戶端檔案路徑。
JSON 檔案與儀器原生 Save/Recall 儲存槽是兩件事。

| 設定 | 離線驗證規則 |
| --- | --- |
| `range` | `auto`、`low`、`high` |
| `repeat_count` | 整數 token 1～65536；65536 明確表示無限循環 |
| `steps` | 2～84 個物件；每個只有一個 Current、一個 Slew、一個 Width |
| Low Current | 0～6 A，步進 0.0001 A |
| High Current | 0～60 A，步進 0.001 A |
| Low／High Slew | 0.0001～0.25／0.001～2.5 A/μs；不另加小數步進 |
| Width | 0.00002～3600 秒；不另加 LIST 小數步進 |
| Save/Recall Slot | 整數 token 1～7；明確指定 Save 即允許覆蓋 |

所有 Steps 驗證完成才更新狀態。Auto 先嘗試 Low，再嘗試 High，會一起檢查整份
LIST 的 Current、解析度與 Slew。沒有單一 Range 能接受全部 Steps 時即拒絕。
不四捨五入、不截斷，也不在執行中動態換 Range。Repeat Count 沒有隱含的無限預設值。

離線 Current 解析度與 Slew 上下限依 IT8812C 電流規格。LIST 章節記載 Width 範圍，
但沒有確立 LIST 專屬 Width 或 Slew 小數步進，因此不套用 Dynamic T1/T2 的 1 μs
解析度。接受的 Width 與 Slew 保留完整十進位值，狀態與預覽以十進位**字串**回傳
這兩個欄位，Current 仍為 JSON 數字。離線接受不代表實體儀器的精度或時序保證。

## 明確控制流程

每次獨立 CLI 模擬命令皆為全新狀態；需要連續操作時，使用同一個模擬 Worker，
讓設定與儲存槽跨 Job 保留。儲存槽只存在該 Simulator 實例中，不模擬斷電保存。

1. 用既有 `input-state` 確認 Input OFF。ON 或 UNKNOWN 都阻擋設定，不會自動 OFF。
   如 Transient 已啟用，必須先明確停用。
2. `set-list-cc` 設定完整 LIST 並選擇 CC + LIST；或以 `list-recall` 搭配
   `{"slot":1}` 載入儲存設定並選擇 CC + LIST。空槽會失敗，狀態不變。
3. 可用 `list-save` 搭配 `{"slot":1}` 儲存設定。Save 要求 Input OFF、Transient
   停用；Save 本身不選擇模式。
4. 以既有 `input-on` 搭配 `{"confirm_input_on":true}` 開啟 Input。Core 要求有效且
   已選擇的 LIST、Input OFF、Transient OFF、Short OFF，而且 Worker 尚未 Stop。
5. `list-trigger` 搭配 `{}` 記錄軟體 Trigger，要求有效且已選擇的 LIST、Input ON、
   Transient OFF、Short OFF，而且 Worker 尚未 Stop。
6. 明確執行 `input-off` 並確認 OFF；LIST 仍維持選取狀態。
7. `list-exit` 搭配 `{}` 要求已確認 OFF 且已選擇 CC LIST，再回到 FIXED 模式，
   保留 LIST 設定與 Trigger 紀錄。

設定／Recall 清除上一份 LIST Trigger 紀錄並取代未啟用的 Transient 設定，絕不自動
停用已啟用的 Transient。LIST 選取中會拒絕 Fixed 設定與 Transient 狀態變更，必須
先明確 OFF 並退出 LIST。設定與 Recall 不開啟 Input、不觸發。任何操作都不猜測或
還原先前 Fixed 設定。退出 LIST 後，保留的設定仍可 Save；已儲存的 LIST 可透過
Recall 重新選取，無須重輸 Steps。

`list-status` 回傳 `state_source: "simulate"`、`configured`、`selected`、
`function_mode`、已解析的 `range`、`slot`、`configuration` 與 `last_trigger`。
`slot` 是該份設定最後 Save／Recall 的位置，新設定後為 null。設定前
configuration／range／slot／trigger 都是 null。Trigger 紀錄包含 `sequence`
（設定／Recall 後從 1 起算）、`accepted_at_utc` 與 `source: "software"`。
回傳值是獨立快照；Input 另外查詢。重複 Trigger 只記錄控制命令，不推定實體重新
開始、中止或前進 Step 的行為。

Trigger 成功或 Worker `job_finished` 只表示命令結束。沒有類比波形模擬、計時器、
進度／完成狀態或 `wait_complete`。Input OFF 不代表已中止內部 LIST 引擎。
控制端 Orchestrator 在正常、錯誤或使用者停止 Workflow 時，皆須先明確嘗試並確認
Input OFF，再 Stop Worker。Stop、程序退出與 VISA 關閉不改變 Input，也不證明 OFF。

Dry-run 只驗證輸入資料，不檢查或更動模擬狀態。Common Worker protocol 3、Manifest
schema 2、單一 Job 接納方式、結果封裝、`GET /status` 與 Stop 語意維持既有規範。
請參閱 [Worker 契約](contracts/electronicload-worker-contract.md)。

## 儀器命令參考

[IT8800 Programming Guide V2.1](https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf)
明列適用 IT8812C，印刷頁碼 20～21、40、46、60～61 記載 `FUNC CURR`、
`FUNC:MODE LIST`／`FIX`、`LIST:RANG`、`LIST:COUN`、`LIST:STEP`、帶索引的
`LIST:LEV`／`LIST:SLEW`／`LIST:WID`，以及 `LIST:SAV`／`LIST:RCL`。
軟體觸發介面記載 `TRIG:SOUR BUS` + `*TRG`，或 `TRIG:IMM`（含 HOLD 來源）。
`FUNC:MODE?`、`TRAN?`、`INP:STAT?`、`INP:SHOR:STAT?` 是控制狀態，不是 LIST
執行進度或完成查詢。

[IT8811/12/12B/12C User Manual V2.7](https://media.digikey.com/pdf/Data%20Sheets/ITech%20Elect%20PDFs/IT8800%20User%20Manual.pdf)
印刷頁碼 31～32 說明 LIST Width 與面板流程，40～41 提供 IT8812C 電流／Slew
規格。Programming Guide 的通用 Width 說明也提到大於 16383 秒的行為，超出此型號
LIST 的 3600 秒上限；此介面採型號上限。Guide 的 `LIST:SLEW?` 敘述與記憶體文字
存在歧義，因此本實作不推定查詢回傳、斷電保存能力，或部分寫入失敗、重複 Trigger、
Input OFF、有限／無限執行中斷後的實體行為。離線實作**不發送 LIST SCPI**。
實體 LIST 控制、波形與時序驗證需另外授權的儀器工作。
