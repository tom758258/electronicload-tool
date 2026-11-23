# Core VISA connections and LIVE support / Core VISA 連線與 LIVE 支援

Core owns resource validation, VISA session lifetime, identity, SCPI and reply
parsing. USB and ASRL use System VISA (`@ivi`) with separate session paths.
Both use the same IT8812C identity rules and read-only commands.

Core 負責資源檢查、VISA session 生命週期、儀器識別、SCPI 與回應解析。
USB 與 ASRL 皆使用 System VISA（`@ivi`），各有獨立的 session 路徑，並共用
IT8812C 的身分規則與唯讀指令。

| Transport / 傳輸 | VISA open / 開啟逾時 | IDN / 身分查詢逾時 | Other I/O timeout / 其餘 I/O 逾時 | Framing / 訊框設定 |
| --- | --- | --- | --- | --- |
| USB INSTR | 5000 ms | 5000 ms | 5000 ms | LF read/write, 150 ms query delay / 讀寫 LF，查詢延遲 150 ms |
| ASRL INSTR | 1000 ms | 1000 ms | 5000 ms | VISA/PyVISA settings with selective overrides / 沿用 VISA／PyVISA 設定，只覆寫指定項目 |

The USB open timeout is passed to VISA and its driver controls how it is enforced.
Core measures ASRL open and `*IDN?` against independent 1000 ms deadlines. The
IDN write and all response reads share its budget. Core rejects a late result
before using the session or issuing further queries. Native VISA calls are
synchronous, so a driver that overruns its deadline must return before Core can
report the timeout and clean up. After a successful ASRL identity check, further
queries use the normal I/O timeout. The ASRL path owns its native manager handle
and closes the device handle before the manager. Open, configuration, query and
cleanup failures terminate the request. Cleanup failures are reported alongside
the primary error. Both session paths bypass PyVISA's automatic clear and retry.

USB 開啟逾時由 Core 傳給 VISA，實際執行方式由驅動程式決定。
Core 分別以 1000 ms 為 ASRL 開啟與 `*IDN?` 的期限。IDN 的寫入和所有回應讀取
共用這個預算。若結果逾期，Core 會拒絕使用該 session 或繼續查詢。原生 VISA 呼叫
採同步執行，驅動程式逾期時，Core 必須等它返回才能回報逾時並清理。ASRL 身分核對
成功後，後續查詢使用一般 I/O 逾時。ASRL 各有原生 Manager handle，依序關閉裝置
與 Manager。開啟、設定、查詢或清理出錯都會結束請求。清理失敗會與主要錯誤一併
回報。兩條 session 路徑均略過 PyVISA 的自動清除與重試。

## LIVE support policy / LIVE 支援規則

Core checks the canonical model ID, command, transport, backend, and required
feature for each live scope. Product and Worker LIVE for IT8812C require USB
INSTR and System VISA. Standalone CLI contributors can explicitly select
ASRL INSTR with `--validation-connection asrl` for the validation matrix.
Command-specific validation scopes and confirmations still apply. Serial
overrides apply to ASRL only. Passive discovery can list ASRL resources.
Automatic identification queries USB resources only.

Core 逐一檢查 LIVE 範圍的標準型號 ID、指令、傳輸、後端和必要功能。IT8812C 的
Product 與 Worker LIVE 要求 USB INSTR 和 System VISA。獨立 CLI 可使用
`--validation-connection asrl` 明確選擇 ASRL INSTR 進行貢獻者驗證，仍須遵守
各命令的驗證範圍與確認條件。序列埠覆寫僅用於 ASRL。被動探索可列出 ASRL 資源，
自動識別只會查詢 USB 資源。
