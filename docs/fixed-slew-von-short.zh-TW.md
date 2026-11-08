# 離線 Fixed CC Slew、VON 與 Short

[English](fixed-slew-von-short.md)

這些功能可用 IT8812C 設定檔執行 dry-run 或模擬。獨立 CLI 每次模擬皆從
初始狀態開始。使用同一個模擬 Worker 可在指令之間保留設定及 Input 狀態。
Dry-run 驗證參數，不讀取或變更模擬狀態。

## Fixed CC Slew

```powershell
electronicload-tool set-cc --simulate --current 1 --range auto --rise-slew 0.1 --fall-slew 0.2 --json
```

兩個斜率的單位都是 A/μs，必須同時提供。Low 接受 0.0001～0.25 A/μs，
High 接受 0.001～2.5 A/μs。Auto 先依電流設定值選量程。若選定的量程無法
接受任一斜率，就拒絕請求。結果以十進位字串保留各斜率。省略兩個參數即可
使用原有 Fixed CC 操作。該操作不寫入斜率，實機的量程寫入仍可能影響斜率。

## VON 設定

```powershell
electronicload-tool set-von --simulate --voltage 10 --latch on --json
electronicload-tool von-status --simulate --json
```

Threshold 與 Latch（`on` 或 `off`）皆必填，設定前須確認 Input OFF。
狀態查詢為唯讀。模擬接受 0～120 V，不假設電壓步進，跨模式切換仍保留設定。
模擬初始值為 0 V、Latch on，僅保存設定。ITECH 使用手冊說明 VON 在 CV
模式下不起作用。

## Short

在同一個模擬 Worker 中設定 Fixed CC、CV、CR 或 CW，透過 `input-on` 明確
確認開啟 Input。再依序提交 `short-on`、`short-status` 與 `short-off`。
開啟 Short 前須確認 Short OFF，Transient 與 LIST 不可開啟 Short。
Input OFF 時仍可執行 `short-off`。

**執行 `short-off` 後，若 Input 仍為 ON，先前的 Fixed 設定仍可能對 DUT 帶載。
要停止帶載，請明確執行 `input-off` 並確認 OFF。** Short 與 Input 是兩個
獨立狀態。Worker Stop、程序結束和關閉 VISA 都無法確認兩者的狀態。
模擬只追蹤 Short 狀態，不模擬短路時的電流或電壓。

## 儀器參考資料

程式指南記載 `CURR:SLEW:POS`／`CURR:SLEW:NEG`、
`VOLT:ON`／`VOLT:LATC` 及 `INP:SHOR`。VON 模擬範圍依指南的 0～MAX 與
IT8812C 的 120 V 額定輸入上限設定。指南定義斜率及 Threshold 查詢回傳數字，
Latch 與 Short 查詢回傳 0 或 1。實機 VON 範圍、精度及這些指令的寫入與回讀
行為需另行驗證。Product LIVE 支援省略斜率參數的既有 Fixed CC 操作。
[ITECH 程式指南][programming] · [ITECH IT8812C 規格][specs] · [ITECH 使用手冊][manual]

[programming]: https://cdn.itechate.com/uploadfiles/%E7%94%A8%E6%88%B7%E6%89%8B%E5%86%8C/user%20manual/it8800/IT8800%20Programming%20Guide-EN.pdf
[specs]: https://www.itechate.com/uploadfiles/2019/06/201906211045484548.pdf
[manual]: https://media.digikey.com/pdf/Data%20Sheets/ITech%20Elect%20PDFs/IT8800%20User%20Manual.pdf
