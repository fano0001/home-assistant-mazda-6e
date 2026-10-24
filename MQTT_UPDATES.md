# MQTT updates: implementation and evidence

Branch: `feature/mqtt-push-updates`. Based on fano0001's `main` at
`9e7b7dc` (v1.5.3), fetched on 2026-10-18.

See [remaining and partial mappings](MQTT_REMAINING.md) for the current gaps,
including the different effect of a full refresh in EU and Asia.

## Data flow

1. Perform the existing first full refresh, so entity creation uses a snapshot.
2. Obtain `getConnConf` and `getAuthTokenByUserId` using the configured region.
   EU uses `ca-m.iov.changanauto.com.de`; Asia uses
   `cma-m.iov.changanauto.sg`. Broker addresses and topics come from the response.
   EU first obtains the car's `THU_Service` device ID from `appGetCarConfFunc`,
   then uses the documented `deviceType="1"`/car-device request. Asia retains
   its existing app-device-ID and car-ID request.
3. Open MQTT 5 over validated TLS, verify CONNACK and every SUBACK result, and
   complete Mazda's application login to obtain the service-encryption key.
4. Subscribe to the configured `properties/report` and `events/up` topics,
   together with login and status-read response topics. Do not subscribe to
   command or property-write topics, or publish vehicle control commands.
5. Decode report service envelopes and merge only the existing mapped fields
   into the affected vehicle's coordinator status. This path performs no HTTP
   request or MQTT property request. Unknown fields/enums are ignored.
6. Reconcile all vehicles every 900 seconds. Reconnection also requests a full
   refresh. EU retains the richer HTTPS snapshot; Asia reads through the same
   persistent MQTT session, avoiding another CONNECT using that client ID.
7. Fall back to the existing 60-second refresh cadence when disconnected or
   after a failed full refresh. Retry MQTT with exponential backoff from five
   seconds to five minutes. Renew bootstrap credentials on each connection attempt.

Vehicles sharing the same broker/login identity share one connection. Their
notification topics and snapshot configurations remain associated with their
individual vehicle IDs. Different identities use separate connections. A changed
vehicle list rebuilds the subscriptions. An unavailable subscription group causes
fallback for the entry rather than silently claiming all vehicles have push.

## State and lifecycle guarantees

- Reports update coordinator data directly through `async_set_updated_data`.
  Independent full/fallback timers are necessary because that method resets the
  coordinator's built-in polling countdown. See the
  [Home Assistant fetching-data documentation](https://contributors.home-assistant.io/core/integrations/fetching-data/).
- Partial reports preserve omitted/null fields, other vehicles, configuration,
  preheating plans and unchanged elements of door/window arrays. Full snapshots
  remain authoritative except for fields with a newer dated MQTT value.
- Each mapped MQTT field keeps its own timestamp, including individual array
  members. An older full response cannot roll back that field, even when the
  MQTT report arrived before the refresh started. Other snapshot fields still
  update. A snapshot at or after the field timestamp retires the saved value;
  equal-timestamp snapshots are authoritative. Removed vehicles are discarded.
  The cache holds one value per field, not an accumulating report history.
- Reports with comparable timestamps older than the current snapshot are ignored.
  Reports received during a full fetch are replayed in order against its result,
  preserving newer values without replaying older timestamped patches. Ordering
  cannot be established from a report without a usable timestamp.
  An undated change retires the previous dated cache value for the same field.
  A full snapshot without a usable timestamp clears the cache; updates received
  during that request are still replayed in arrival order.
- Retained reports are acknowledged but do not overwrite a fresh snapshot after
  connection. Normal duplicate QoS 1 deliveries are safe to merge again.
- PINGREQ/PINGRESP, QoS 1 acknowledgements, subscription rejection, read-response
  correlation, connection loss and cancellation are handled by one reader per
  connection. Heartbeats never cancel a partially received packet. MQTT packet
  and heartbeat semantics follow the
  [MQTT 5 specification](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html).
- Background tasks belong to the config entry. Unload/shutdown removes timers,
  cancels listeners and closes TLS transports. Home Assistant's disable-polling
  option is respected; direct MQTT state updates remain enabled.
- Vehicle-control command transport is unchanged. Its existing bounded status
  confirmation still runs after a command; the 15-minute period is not a delay
  imposed on user actions.

## Evidence and remaining validation

### Additional field mappings

The European MQTT `properties/get/res` response was compared with the HTTPS
status snapshot on 2026-10-18. The shared EU/Asia parser now also maps:

| MQTT field | HTTPS status field | Conversion |
| --- | --- | --- |
| `chargeSoc` | `charge.maxSocPercent` | Numeric; separate from actual SOC |
| `lfTyrePressure` | `tire.leftFront.pressure` | Truncate to whole kPa |
| `rfTyrePressure` | `tire.rightFront.pressure` | Truncate to whole kPa |
| `lrTyrePressure` | `tire.leftBack.pressure` | Truncate to whole kPa |
| `rrTyrePressure` | `tire.rightBack.pressure` | Truncate to whole kPa |
| `innerHumidity` | `hvac.insideHumidity` | Truncate to whole percent |
| `pm25` | `hvac.insidePm25` | Numeric |

The user changed the charge limit in the Mazda app and confirmed the new value;
both MQTT and HTTPS returned that limit. These mappings apply to snapshots and
partial notifications through the same parser. Nested empty tire dictionaries
are removed from patches, so a missing tire cannot clear an existing reading or
make a timestamp-only report count as telemetry. Tire warning states are kept.
Tests use synthetic measurements and cover precision, partial tire updates,
invalid values and preservation of fields with unverified enum mappings.

This is not evidence that every push contains all these fields or that the
same values have been verified on a live Asia vehicle. Spontaneous European
reports have included telemetry field names as well as connectivity-only
reports; the user observed immediate climate changes in the app and HA.

### Enum translations

The user enabled and disabled front defrost in the app. Spontaneous MQTT reports
and subsequent HTTPS snapshots showed both transitions. The latest off report
and HTTPS snapshot had the same vehicle timestamp. The shared parser translates:

| MQTT field | Observed MQTT value | HTTPS field | HTTPS value |
| --- | --- | --- | --- |
| `frontDefrostStatus` | 1 / 2 | `hvac.defrostStatus` | 0 / 1 |
| `frontDefrosterSwitch` | 0 / 1 | `hvac.defrostStatus` | 0 / 1 |
| `powerStatusFeedBack` | 0 / 2 | `vehicleStatus.powerStatus` | 0 / 2 |
| `steeringWheelHeating` | 1 | `vehicleStatus.steeringWheelHeater` | 0 |
| `chargeStatus` | 3 | `charge.chargeStatus` | 0 |
| `driverDoorLock` | 0 | `door.driverLock` | 0 |
| `passengerDoorLock` | 0 | `door.passengerLock` | 0 |

Defrost uses the switch field when present, with a status-field fallback. If
both fields decode to different states, the patch does not update defrost.
Other enum codes are not extrapolated from the observed codes. In particular,
steering heat on, charging/complete/paused, unlocked and rear-lock codes remain
unverified and do not overwrite cached HTTPS values. The 15-minute full refresh
is still needed. Asia uses these same mappings but has not been tested live.

The available Android 1.2.3 DEX extracts contain empty implementations for the
relevant `CarConditionManager` methods and native refresh handlers. They do not
establish the missing conversions or which transport a manual app refresh uses.

Further static inspection located the exact conversion entry point:
`com.changan.mobile.oversea.common.carcontrol.entity.MqttCarDataExtKt`
`.toCarConditionEntity(CarData, CarConditionEntity)`. Its DEX code item reserves
1180 instruction units, but contains only `const/4 v0, 0`, `return-object v0`
and NOPs in all five available extracts. The decompiler's failed method body is
therefore not usable conversion code. A search of 1005 saved memory mappings
for nearby references to the relevant CarData getters found no candidate body;
this search does not establish that no recoverable implementation exists.

Other implementations were checked, not treated as Mazda protocol specifications:

- [Sunek0's MQTT normalizer](https://github.com/Sunek0/ha-mazda-6e-cx6/blob/195928b2eed8/mazda_sdk/mazda/mqtt.py)
  copies `frontDefrostStatus` directly and divides seat heat values by two.
  The former conflicts with the observed EU 1/2-to-0/1 translation; applying the
  latter to the saved rear-seat value would conflict with the HTTPS off state.
- [BeauGiles' Deepal normalizer](https://github.com/BeauGiles/ha-deepal-cloud/blob/main/custom_components/deepal/api.py)
  also copies front defrost and steering-wheel heat values directly. These do
  not reproduce the observed EU off states. They are not used to fill missing
  Mazda enum codes.

`chargDeltMins=8191` is now preserved as `charge.remainChargeTime=8191`, matching
HTTPS. The existing sensor validator displays that sentinel as no estimate.
Previously normalization changed it to null and partial-patch filtering omitted
it, leaving an old estimate visible. Null/missing fields still preserve values.

### Observed locally on 2026-10-18

- The saved European server response includes `properties/report` and
  `events/up` subscriptions, alongside property-read response topics. These
  paths were not constructed from guessed device IDs.
- A read-only connection to the configured European broker accepted MQTT
  authentication, all selected subscriptions and Mazda's application login.
- A status read succeeded over that persistent session.
- A second 75-second observation using the final transport implementation
  confirmed three PINGRESP replies and a still-connected session. No spontaneous
  mapped report arrived. The returned snapshot included mapped SOC, range,
  odometer, trunk and HVAC fields.
- No spontaneous report arrived during a 45-second observation. This does not
  establish whether the car reports while parked, which changes cause reports,
  their latency, or whether all relevant services require extra activation.
- A later attempt to validate a fresh EU HTTP bootstrap encountered the expired
  local research HTTP session. Its token-refresh attempt failed and the library
  retained its uncertainty marker; that research session requires login before
  further HTTP testing. No Home Assistant config entry was accessed or changed.
- Android app models declare `r`, `rs` and `sers` in `MqttEventData`, with both
  `params` and `data` in `MqttPropertiesCarConditionRes`. Protected method bodies
  are not treated as executed-code evidence. The report decoder is tested against
  these model shapes and the existing snapshot encryption implementation; a
  spontaneous encrypted report has not yet been captured to validate it end to end.
- The branch was copied over Samba to a live Home Assistant 2026.10.2 installation
  after archiving the original component. SHA-256 checks verified the deployed
  files. Configuration checks passed before restarting HA.
- Live startup exposed an incorrect reuse of Asia's bootstrap host/request for
  Europe. EU now uses the documented `ca-m` gateway and the car's configured THU
  device; a regression test checks the exact host and request body. Asia keeps
  its existing regional format.
- Following that correction, the European entry loaded successfully. Integration
  diagnostics reported `connected=true`, `full_refresh_interval_seconds=900`,
  `fallback_interval_seconds=60` and `fallback_active=false`. All 50 registered
  Mazda entities loaded with none unavailable. Report delivery remains unverified.

The saved connection/auth responses and Android research files remain outside
this integration repository. Credentials, vehicle IDs, VIN and raw captures are
not included in tests or this document.

### Offline verification

Run `python -m pytest tests -q` and
`python -m compileall -q custom_components/mazda_6e` with the existing Python
test dependencies. Tests use synthetic credentials and a scripted in-memory MQTT
peer, not Mazda servers. They cover direct report delivery, encryption, partial
state merging, stale/in-flight reports, subscription rejection, heartbeat loss,
reconnection, timer independence, per-vehicle routing, shared connections,
regional bootstrap endpoints and unload cleanup.

Integration diagnostics include MQTT connectivity, received report count, age of
the last mapped report, full/fallback intervals and fallback status. This lets a
live trial distinguish an authenticated connection from actual incoming states.
Control credentials are redacted along with the other config-entry credentials.

Per-connection subscription diagnostics also count all non-retained reports,
including reports with no mapped fields. They expose received field names,
mapped output paths and the interval between the last two messages on each
subscription. Credentials and device/topic IDs are not retained.
These counters reset when a connection is rebuilt. The measured interval is
MQTT message timing, not a measurement of the Mazda app's polling interval.

Each subscription also keeps its last 32 reports containing selected numeric
enum fields for defrost, steering-wheel heating, door locks, charging and power.
`enum_reports` exposes these values, the vehicle timestamp and report age, so
control transitions can be compared with HTTPS without storing arbitrary
payloads. Non-numeric values are excluded, and unverified states are not applied
to entities. This history resets on reconnect and is bounded in memory.
Already-subscribed `properties/get/res` messages are also observed, including
responses with no matching HA request. These are reported separately from
`properties/report` and `events/up` and are not applied as unsolicited state
updates. No additional status requests are sent for this measurement.

The first field-level observation contained only `latestDate` and `connectStatus`.
Such a connectivity report previously passed the timestamp mapping and inflated
the mapped-report count. Timestamp-only patches are now ignored, so they cannot
make older vehicle telemetry appear fresh. Received-message diagnostics still
count them and list their field names. The earlier mapped-report count by itself
is therefore not evidence of climate, charging or lock telemetry delivery.

### Further observed states

The saved EU MQTT response and HTTPS status also establish the following codes.
These translations are shared by EU and Asia; they have not been observed on an
Asia vehicle. Codes outside this table are still omitted from patches.

| MQTT | HTTPS | Observed translation |
| --- | --- | --- |
| `lowBeam`, `highBeam`, `positionLamp` | Same names under `lamp` | 0 → 0 |
| `turnLndicatorLeft`, `turnLndicatorRight` | `lamp.leftTurn`, `lamp.rightTurn` | 0 → 0 |
| `driverSeatHeatStatus`, `passengerSeatHeatStatus` | Front-seat `heatStatus` | 0 → 0 |
| `driverSeatAirStatus`, `passengerSeatAirStatus` | Front-seat `ventStatus` | 0 → 0 |
| `lf/rf/lr/rrPressureWarning` | Corresponding `tire.*.status` | 0 → 0 |
| `carStatus` | `vehicleStatus.status` | 2 → 2 (parked) |
| `dcDhargeGunConnectionState` | `charge.dcChargeGunConnectStatus` | 0 → 0 |
| Both AC and DC connection states | `charge.chargeConStatus` | Both 0 → 0 |

A front seat's mode is set to off only when the report supplies both heat and
ventilation as off. Missing or unknown values do not establish a combined mode.
An AC-disconnected report alone cannot establish that the car is unplugged: the
DC state must also be present and known to be disconnected.

The rear-seat value 6 is not interpreted as level 3. The saved MQTT response
contains 6 for rear heat and ventilation while HTTPS contains 0. Likewise,
`totalCurrent=10` is not copied to `chargeCurrent`, which was 0 in HTTPS.
Speed units, coordinate validity, active seat levels, lamp-on codes, charging
states and warning codes still require conversion evidence.

Numeric report history now includes seat, lamp and tire-warning codes, car
status, speed and the candidate current fields. It records ordinary reports;
it sends no commands or extra property requests. Tests cover resetting old
HTTPS states, incomplete reports and preservation when a code is unknown.

Tests use explicit minimal Home Assistant facades. The Windows test environment
does not include a running Home Assistant instance. Pure utility tests bypass
the HA entry-point import when HA is not installed. Existing lock/login facade
fixtures were updated to reflect upstream's region field and refresh-until
methods; the unchanged upstream baseline had one failure and four import errors.

### Required before calling live push verified

Update, 2026-10-24: the user reports that Asia has been tested and works.
The outstanding checks below concern field coverage and specific scenarios;
the EU observations do not establish every enum code in either region.

- Capture spontaneous state-bearing reports for a real EU vehicle and a real
  Asia vehicle; verify decoding, field semantics, report timestamps and actual
  entity changes without snapshot requests.
- Test integration setup, controls, reconnect, reload and HA shutdown in Home
  Assistant for Asia, and continue EU control/reconnect/reload testing, including
  multiple vehicles where available. EU setup and restart have been tested.
- Measure reporting behavior while driving, charging, parked and asleep. No
  fixed push latency, wake-up behavior or server rate limit is claimed.

EU's HTTPS snapshot can refresh fields without an established MQTT mapping.
Asia's full snapshot uses the same MQTT parser, so missing mappings remain
missing there. A healthy connection alone is not evidence that the car reports
every state change.
