# Missing and partial MQTT mappings

Checked against `normalize_status()` in `mqtt.py` on November 7, 2026.
This list describes what the integration applies to entities. Receiving a field
in an MQTT message does not mean that the field has a usable mapping.

## Fields without an MQTT mapping

| State | Internal field | What is missing |
| --- | --- | --- |
| Cockpit field | `hvac.consTempCockpit` | Meaning and MQTT source are unresolved; its existing temperature label is not established by observations |
| Battery-preheating plan: enabled state and time | `battery_preheating_plan` | No MQTT mapping; the plan is fetched through a separate HTTPS request |

## Partially mapped states

| State | What MQTT can update | What remains unresolved |
| --- | --- | --- |
| Tire warnings | Raw code 0 maps to status 0 | Warning codes; tire pressure itself is already mapped |
| Charging status | `batteryChargeStatus` codes 0/1/4 map to HTTPS 0/6/1; without that field, `chargeStatus` codes 1/4 map to 6/1 | Completed, paused and other codes; code 1 uses the inferred waiting label |
| Charge connection | AC or DC code 1 maps to connected; overall disconnected requires both to be 0; DC 0/1 maps directly | DC-connected code is inferred; other codes remain unresolved |
| Power status | Raw `powerStatusFeedBack=0/2` maps to internal 0/2 | Code 1 and other codes |
| AC charging current | `BattACChrgInCurr` and its alias | No conversion from `obcChrgInpAcI`; only zero has been compared |

Window open/closed values are mapped, but the position order between MQTT and EU
HTTPS has not been verified with different states on individual windows.
Front defrost supports both off and on. Conflicting or unknown codes are ignored.
Low beam, high beam, position lamps and both turn signals map codes 0/1 directly to off/on.
Low beam and position lamps each have five on-state comparisons with identical
MQTT/HTTPS report timestamps. The left turn signal has an on-state comparison
with timestamps one second apart.
High beam has 21 on-state comparisons with identical MQTT/HTTPS report
timestamps across October 26, 27, 29 and 30.
Steering-wheel heating maps raw code 1 to off and raw code 2 to on.
Driver and passenger door locks map 0 to locked and 1 to unlocked. Rear-door
lock codes are not applied.

Vehicle status maps `carStatus` 1 and 2 directly to the same internal codes.
Code 1 has also been observed during remote climate operation, so it does not
establish physical movement by itself.

DC current accepts `totalCurrent` after the existing `BattDCChrgInCurr` aliases.
It matched HTTPS `charge.dcChargeCurrent` in 32 comparisons with identical
report timestamps, covering 29 different values with float rounding differences.
It is not copied to total charging current, which remained zero in those samples.
The October 25 drive added 36 matching comparisons, covering 28 different values,
including a negative value. The largest float rounding difference was below 0.00001 A.
This establishes equivalence with the HTTPS field, not that the value represents
charging through a DC connector. Nonzero values were observed while unplugged
and driving. The physical meaning and sign convention remain to be established.
The week-long capture added broader evidence: 737 matching comparisons against
changed HTTPS DC-current values and three differences, despite identical report
timestamps. A report timestamp does not guarantee simultaneous measurements of
every field.

The right-rear window has one open-state match at `window.windows[3]` with
identical report timestamps. The driver window also has two open-state matches at `window.windows[0]`
with identical report timestamps. The passenger window has one nearby match
at index 1; the left-rear window was never observed open.

Seat heating and ventilation use levels 0-3 directly for all four positions. Seat
mode is heating when heat is active and ventilation when ventilation is active.
If both are active in the same report, their levels update without choosing a
combined mode. Setting the combined mode to off requires both heat and
ventilation to be 0 in the report. Unknown codes, including 6, are ignored.

## November charging observations

The November 3-4 AC charging session established `chargeCurrentVal` as the
source for `charge.chargeCurrent`: 302 nonzero equal comparisons at 43-45 A
and two comparisons differing by 1 A, all with identical report timestamps.
It does not populate the separate AC-current field, which stayed at zero.
AC connector code 1 matched HTTPS connected status in 101 comparisons.

`batteryChargeStatus` takes priority: raw 0 maps to HTTPS 0 (800 comparisons),
raw 1 to HTTPS 6 (53), and raw 4 to HTTPS 1 (48). HTTPS code 1 occurred while
connected before charging and is now labelled waiting. This label is inferred
from the session rather than a documented enum. Completed and paused were not observed.
Without `batteryChargeStatus`, raw `chargeStatus` 1/4 maps to HTTPS 6/1.
Raw `chargeStatus=3` is no longer translated: it matched both HTTPS 0 and 1.
Unknown primary codes and ambiguous partial reports preserve previous values.

## Inferred mappings enabled on November 7

The user approved the following interpretations pending further observations:

- The four window-degree fields populate `window.openDegree` directly in
  driver/passenger/left-rear/right-rear order. Values 0 and 10 have matching
  samples; other values and positions use the same encoding by inference.
  Values are passed through without scaling; their physical unit is unresolved.
- `connectStatus` 0/1 maps to offline/online. Only 1 has been compared against
  HTTPS. A connection-only push updates that field without advancing the
  vehicle telemetry timestamp. Broker connection loss does not set the car offline.
- DC connector code 1 means connected, matching the observed AC 0/1 encoding.
  Overall disconnected still requires both connector states to be 0.
- HTTPS charging status 1 is labelled waiting, based on the connected interval
  with zero charging current before charging began. MQTT `batteryChargeStatus=4`
  maps to that value; raw code 4 must not be mistaken for HTTPS completed code 4.

## Unavailable API data: outside the current mapping work

Usable speed and location data are currently unavailable from the API. These
are not active investigation items. Revisit them only if the API starts
providing usable values; future availability is unknown.

## EU and Asia

Both regions use the same MQTT normalizer. EU still receives a full HTTPS status
every 15 minutes, which can update missing fields when the endpoint supplies
them. Asia reads its full status through MQTT and the same parser: a new
snapshot does not add support for fields or codes that the parser does not map.
The battery-preheating plan retains its separate HTTPS request.

Unknown codes in partial reports preserve the existing value. A supported
mapping does not guarantee that the vehicle sends a spontaneous report for
every change. Local MQTT/HTTPS comparisons are from EU. The user confirmed on
October 24, 2026 that Asia has been tested and works; that does not establish
the meaning of the unresolved status codes listed above.
