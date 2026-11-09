# Remaining MQTT mappings

EU and Asia use the same normalizer. Unknown codes in partial reports preserve
existing state. EU HTTPS reconciliation can fill missing fields when the
endpoint supplies them. Asia snapshots use MQTT and cannot fill parser gaps.

## Missing and partial mappings

| Field | Current handling | Remaining work |
| --- | --- | --- |
| `hvac.consTempCockpit` | No MQTT mapping | Meaning and source; its existing temperature label is not established by observations |
| Battery-preheating plan | Separate HTTPS request | No MQTT source identified |
| Tire warnings | Raw 0/1/2 maps directly for all four wheels | Other warning codes |
| Charging status | `batteryChargeStatus` 0/1/4 maps to HTTPS 0/6/1; without that field, `chargeStatus` 1/4 maps to 6/1 | Completed, paused and other codes |
| Power status | Raw 0/2 maps directly | Other codes, including the single observed code 3 |
| AC current | `BattACChrgInCurr` and its lowercase-initial alias | Nonzero evidence for the alternative `obcChrgInpAcI` source |

Raw `chargeStatus=3` is not translated: it corresponded to both HTTPS 0 and 1.
An unknown `batteryChargeStatus` does not fall back to the secondary field.

## Enabled interpretations needing confirmation

| Mapping | Basis and limits |
| --- | --- |
| Window degrees passed directly in driver/passenger/left-rear/right-rear order | Some positions have matching 0/10 samples; the full scale and all positions are not verified. No scaling or physical unit is assumed. |
| `connectStatus` 0/1 means offline/online | Repeated comparisons establish 1; 0 is inferred. Connection-only pushes do not advance vehicle telemetry timestamps. Broker loss does not set the car offline. |
| DC connector code 1 means connected | Uses the observed AC 0/1 encoding; DC-connected was not captured. Overall disconnected requires both connector states to be 0. |
| HTTPS charging status 1 is labelled waiting | Observed with the AC connector attached and no charging current before charging began. The exact backend enum meaning is undocumented. |

Tire warning code 2 means low pressure. Code 1 passes through with its meaning
unresolved. All four wheels use the same mapping.

## Field conversions

- `chargeCurrentVal` populates total charging current, not the separate AC-current
  field.
- `totalCurrent` populates the HTTPS DC-current field. It also changes while
  unplugged and driving, so this correspondence does not establish DC connector
  charging or the physical meaning of its sign.
- A shared report timestamp does not guarantee simultaneous sampling of each field.
- Seat heat and ventilation use levels 0-3 for all four positions. Active levels
  were observed at the driver seat; other positions reuse that encoding. Unknown
  code 6 is ignored. Conflicting active modes do not choose a combined mode.
- Front defrost uses status codes 1/2 or switch codes 0/1. Conflicting values are
  ignored. Steering heat uses 1/2 for off/on. Lamps, turn signals and front-door
  locks use 0/1 directly. Rear-door lock codes are not applied.
- Vehicle status codes 1/2 map directly. Code 1 also occurs during remote climate
  operation and does not establish physical movement by itself.
- Charge limit is distinct from actual SOC. The remaining-charge-time sentinel
  8191 clears an old estimate through the existing sensor validator.

Usable speed and location data are unavailable from the API and are outside this
mapping work. Revisit them only if the API starts supplying usable values.

To contribute a mapping, include the raw MQTT field/code, corresponding status
field/value and both report timestamps. Note whether the response came from EU
HTTPS or Asia MQTT; the latter is not an independent check of this normalizer.
