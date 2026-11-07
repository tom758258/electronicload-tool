# Fixed 模式軟體 Auto Range

[English](fixed-auto-range.md)

Fixed CC、CV、CR 的 Range 必填，可選 `auto`、`low` 或 `high`；CW 沒有
Range。Auto 只在設定階段由 Core 依原始設定值先檢查 Low，再檢查 High。
若兩者都無法表示該值，會在硬體存取或模擬狀態變更前拒絕設定。
不四捨五入、不限制到邊界值、不發送硬體 Auto 指令，也不在 Input ON 期間
動態切換 Range。手動 Low／High 沿用原有行為，不自動改選另一個 Range。

| 模式 | Low | High | Auto 範例 |
| --- | --- | --- | --- |
| CC | 0～6 A，步進 0.0001 A | 0～60 A，步進 0.001 A | 0、2、6 → Low；6.001、10、60 → High |
| CV | 0.1～18 V，步進 0.001 V | 0.1～120 V，步進 0.01 V | 0.1、12、18 → Low；18.01、24、120 → High |
| CR | 0.05～10 Ω | 10～7500 Ω | 0.05、10 → Low；10.01、100、7500 → High |

CC 6.0001 A 與 CV 18.001 V 會被拒絕：超過 Low 上限，且不符合 High 解析度。
超出所有 Range 的值也會被拒絕。CR 不假設固定小數步進，文件中的 16-bit
解析度不代表已確認的小數步進。各模式都需要有限數值；Worker 必須使用 JSON
數字，不接受布林值或數字字串。CLI 轉送與 Worker 驗證保留原始小數，避免
過多精度在驗證前被四捨五入成合法值。

```powershell
electronicload-tool set-cc --simulate --current 2.5 --range auto --json
electronicload-tool set-cv --dry-run --voltage 24 --range auto --json
electronicload-tool set-cr --simulate --resistance 10 --range auto --json
```

`--range` 維持必填，沒有預設值。Worker 的 `set-cc` 參數
`{"current": 2.5, "range": "auto"}` 會在既有結果中回傳 `current_a: 2.5`
與 `range: "low"`。成功結果一律回傳解析後的 Low／High，dry-run 也相同。
Auto 找不到合適 Range 時回傳 `no_matching_range`，並在 `details.ranges`
列出各 Range 的失敗代碼。無效型別與非有限數值保留既有錯誤。Worker 參數
錯誤會在建立 Job 前拒絕；狀態條件錯誤則在已接受的 Job 執行時回報。

同一個模擬 Worker 會在不同 Job 之間保存解析後的設定；獨立 CLI 模擬每次
從新狀態開始。dry-run 驗證後回傳 `hardware_access: false` 的計畫，不讀取
或修改模擬狀態。Manifest Schema 2 在這三個命令新增
`range_options: ["auto", "low", "high"]`，原有 `kind: "async_job"` 與
Worker Protocol 3 不變。使用端可讀取此新增能力欄位；既有手動請求、結果
與 JSONL 結構保持相容。

軟體 Auto 不擴大 Product LIVE 支援。Fixed CC 保留精確身分、USB INSTR、
System VISA 檢查；在 `SYST:REM` 與每次設定寫入前確認 Input OFF，沿用
Low 數值選擇指令或 High `MAX`，最後驗證 Range、設定值與 Input OFF。
Input ON 仍需明確授權；Stop、程序結束、VISA 關閉都不代表 Input OFF。
CV／CR／CW、Transient、LIST 維持 Product LIVE 封鎖；Transient／LIST
的 Auto 規則不變。

獨立的 CLI 專用 CV／CR 硬體驗證路徑也會先解析 Auto，再產生指令計畫或
比較預期設定。原有授權與安全檢查不變，包括 `VOLT:RANG:AUTO OFF`；這
不代表已獲准執行實機測試。詳見 [Worker 契約](contracts/electronicload-worker-contract.md)
與[受控驗證流程](../README.md#controlled-instrument-validation)。

`scripts/preflight-cli.ps1 -FixedAutoOnly` 可只檢查這項離線功能；預設
preflight 也包含這些檢查。統一的 `live-cli-check.ps1` 使用原有參數值，
為 CC、CV、CR 增加 Input OFF 下的 Auto 設定與 Readback 案例。`-Current`
保留原始小數字串交給 Core 驗證，不先轉成二進位浮點數。預期 Range
來自 Core 的 dry-run 計畫，另經核准的實機執行時會比較回傳結果。手動案例
與 ON／OFF 次數不變；CC 上限維持 2 A，超過 6 A 的 Auto High 選擇只在
離線檢查。`-PlanOnly` 不存取硬體，也不會將計畫中的案例標成實機通過。
