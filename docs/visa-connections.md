# Core VISA connections and LIVE support

Core owns resource validation, VISA session lifetime, identity, SCPI and reply
parsing. USB and ASRL use System VISA (`@ivi`) with separate session paths.
Both use the same IT8812C identity rules and read-only commands.

| Transport | VISA open timeout | Identity query timeout | Other I/O timeout | Framing |
| --- | --- | --- | --- | --- |
| USB INSTR | 5000 ms | 5000 ms | 5000 ms | LF read/write, 150 ms query delay |
| ASRL INSTR | 1000 ms | 1000 ms | 5000 ms | VISA/PyVISA settings with selective overrides |

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

## LIVE support policy

Core checks the canonical model ID, command, transport, backend, and required
feature for each live scope. Product and Worker LIVE for IT8812C require USB
INSTR and System VISA. Standalone CLI contributors can explicitly select
ASRL INSTR with `--validation-connection asrl` for the validation matrix.
Command-specific validation scopes and confirmations still apply. Serial
overrides apply to ASRL only. Passive discovery can list ASRL resources.
Automatic identification queries USB resources only.
