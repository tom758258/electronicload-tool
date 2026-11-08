# Offline Fixed CC Slew, VON and Short

[繁體中文](fixed-slew-von-short.zh-TW.md)

These controls support dry-run and simulation with the IT8812C profile. An
individual simulated CLI invocation starts fresh. Use one simulated Worker to
keep settings and Input state across commands. Dry-run validates parameters
without reading or changing simulated state.

## Fixed CC Slew

```powershell
electronicload-tool set-cc --simulate --current 1 --range auto --rise-slew 0.1 --fall-slew 0.2 --json
```

Both rates are in A/μs and must be supplied together. Low accepts
0.0001–0.25 A/μs, and High accepts 0.001–2.5 A/μs. Auto first selects a range
from the current setpoint. If the selected range cannot accept both rates,
the request fails. The result preserves each rate as a decimal string.
Omit both options to use the original Fixed CC operation. That operation
sends no Slew writes. A physical range write may affect the device's Slew.

## VON Setup

```powershell
electronicload-tool set-von --simulate --voltage 10 --latch on --json
electronicload-tool von-status --simulate --json
```

Supply both the threshold and Latch (`on` or `off`). Setup requires confirmed
Input OFF. Status is read-only. The simulator accepts 0–120 V with no assumed
voltage step and retains the setting across mode changes. Its starting values
are 0 V and Latch on. The simulator models only the settings. The ITECH user
manual describes VON as ineffective in CV mode.

## Short

In one simulated Worker, configure Fixed CC, CV, CR or CW and explicitly
confirm Input ON using `input-on`. Then submit `short-on`, `short-status`
and `short-off` as separate jobs. Short ON requires Short OFF and is
unavailable in Transient and LIST. Short OFF also works with Input OFF.

**After `short-off`, the previous Fixed setting may still load the DUT if
Input remains ON. Explicitly issue `input-off` and confirm OFF to stop loading.**
Short state and Input state are separate. Worker Stop, process exit and VISA
close provide no confirmation of either state. The simulator tracks the Short
flag without modeling current or voltage during a short circuit.

## Instrument reference

The programming guide describes `CURR:SLEW:POS` / `CURR:SLEW:NEG`,
`VOLT:ON` / `VOLT:LATC`, and `INP:SHOR`. The VON simulation bound combines
the guide's 0–MAX range with the IT8812C rated 120 V input limit. The guide
specifies numeric query replies for Slew and threshold, and 0/1 replies for
Latch and Short. Physical VON bounds and precision, and device write/readback
behavior for these commands, require separate hardware validation. Product LIVE
supports the existing Fixed CC operation without the Slew pair.
[ITECH Programming Guide][programming] · [ITECH IT8812C specifications][specs] · [ITECH User Manual][manual]

[programming]: https://cdn.itechate.com/uploadfiles/%E7%94%A8%E6%88%B7%E6%89%8B%E5%86%8C/user%20manual/it8800/IT8800%20Programming%20Guide-EN.pdf
[specs]: https://www.itechate.com/uploadfiles/2019/06/201906211045484548.pdf
[manual]: https://media.digikey.com/pdf/Data%20Sheets/ITech%20Elect%20PDFs/IT8800%20User%20Manual.pdf
