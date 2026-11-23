# Core VISA connections and LIVE support / Core VISA 連線與 LIVE 支援

Core owns resource validation, VISA session lifetime, identity, SCPI and reply
parsing. USB and ASRL use System VISA (`@ivi`) with separate session paths.
Both use the same IT8812C identity rules and read-only commands.

Core 負責資源檢查、VISA session 生命週期、儀器識別、SCPI 與回應解析。
USB 與 ASRL 皆使用 System VISA（`@ivi`），各有獨立的 session 路徑，並共用
IT8812C 的身分規則與唯讀指令。

| Transport / 傳輸 | VISA open timeout / 開啟逾時 | IDN I/O timeout / 身分查詢逾時 | Other I/O timeout / 其餘 I/O 逾時 | Framing / 訊框設定 |
| --- | --- | --- | --- | --- |
| USB INSTR | 5000 ms | 5000 ms | 5000 ms | LF read/write, 150 ms query delay / 讀寫 LF，查詢延遲 150 ms |
| ASRL INSTR | 1000 ms | 1000 ms | 5000 ms | VISA/PyVISA settings with selective overrides / 沿用 VISA／PyVISA 設定，只覆寫指定項目 |

The open timeout is passed to VISA. The driver controls how it is enforced.
After a successful ASRL `*IDN?`, Core uses the normal I/O timeout for any further
queries in that session. The ASRL path owns its native manager handle and closes
the device handle before the manager. Open, configuration, query and cleanup
failures terminate the request. Cleanup failures are reported alongside the
primary error. Both session paths bypass PyVISA's automatic clear and retry.

開啟逾時由 Core 傳給 VISA，實際執行方式由驅動程式決定。ASRL 的 `*IDN?`
核對成功後，同一 session 的後續查詢使用一般 I/O 逾時。ASRL 各有原生 Manager
handle，依序關閉裝置與 Manager。開啟、設定、查詢或清理出錯都會結束請求。
清理失敗會與主要錯誤一併回報。兩條 session 路徑均略過 PyVISA 的自動清除與重試。

## LIVE support policy / LIVE 支援規則

Core checks the canonical model ID, command, transport, backend, and required
feature for each live scope. Product LIVE for IT8812C requires explicit USB
INSTR and System VISA. The standalone `asrl-readonly` contributor scope
requires an explicit ASRL INSTR resource and permits only `identify`,
`input-state` and `measure` for IT8812C. Serial overrides apply only to that
scope. Worker LIVE requires USB INSTR and does not accept contributor scopes.
Passive discovery can list ASRL resources. Only USB resources receive IDN
queries during automatic discovery.

Core 逐一檢查 LIVE 範圍的標準型號 ID、指令、傳輸、後端和必要功能。IT8812C 的
Product LIVE 要求明確指定 USB INSTR 並使用 System VISA。獨立 CLI 的貢獻者範圍
`asrl-readonly` 要求明確指定 ASRL INSTR 資源，只允許對 IT8812C 執行 `identify`、
`input-state` 與 `measure`。序列埠覆寫只適用於此範圍。Worker LIVE 要求 USB INSTR，
且不接受貢獻者範圍。被動探索可列出 ASRL 資源，自動探索只對 USB 資源送出 IDN 查詢。
