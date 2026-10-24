# Missing and partial MQTT mappings

Checked against the local `normalize_status()` in `mqtt.py` on October 24, 2026.
This list describes what the integration applies to entities. Receiving a field
in an MQTT message does not mean that the field has a usable mapping.

## Fields without an MQTT mapping

| State | Internal field | What is missing |
| --- | --- | --- |
| Speed | `vehicleStatus.speed` | Source field conversion and units |
| Vehicle online status | `vehicleStatus.connectStatus` | `connectStatus` is not applied; connection-only reports must not make vehicle telemetry appear newer |
| Total charging current | `charge.chargeCurrent` | Source field and scaling; an observed `totalCurrent=10` corresponded to HTTPS `chargeCurrent=0` |
| Cockpit temperature | `hvac.consTempCockpit` | No established MQTT source; `constantTempCabinStatus` is not documented as a temperature |
| Location | `location` | Coordinate conversion, validity and coordinate system; usable coordinates have not been observed in the EU HTTPS responses either |
| Window opening percentage | `window.openDegree` | Units, scaling and position order |
| Battery-preheating plan: enabled state and time | `battery_preheating_plan` | No MQTT mapping; the plan is fetched through a separate HTTPS request |

## Partially mapped states

| State | What MQTT can update | What remains unresolved |
| --- | --- | --- |
| Lamps and turn signals | Raw code 0 maps to off | On and other codes |
| Tire warnings | Raw code 0 maps to status 0 | Warning codes; tire pressure itself is already mapped |
| Charging status | Raw `chargeStatus=3` maps to not charging | Charging, completed, paused and other codes |
| Charge connection | Raw DC code 0 maps to disconnected; overall disconnected requires both AC and DC to be 0 | Connected and other codes; AC=0 alone does not establish overall connection status |
| Power status | Raw `powerStatusFeedBack=0/2` maps to internal 0/2 | Code 1 and other codes |
| AC charging current | `BattACChrgInCurr` and its alias | No conversion from `obcChrgInpAcI`; only zero has been compared |

Window open/closed values are mapped, but the position order between MQTT and EU
HTTPS has not been verified with different states on individual windows.
Front defrost supports both off and on. Conflicting or unknown codes are ignored.
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

Seat heating and ventilation use levels 0-3 directly for all four positions. Seat
mode is heating when heat is active and ventilation when ventilation is active.
If both are active in the same report, their levels update without choosing a
combined mode. Setting the combined mode to off requires both heat and
ventilation to be 0 in the report. Unknown codes, including 6, are ignored.

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
