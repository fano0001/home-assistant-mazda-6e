# MQTT updates

Europe and Asia use a persistent MQTT connection for vehicle status reports.
Mapped reports update Home Assistant entities directly. A local MQTT broker is
not required.

## Connection and refresh

The integration loads a full snapshot before creating entities, then obtains
broker credentials and subscriptions from Mazda. EU uses the car's THU device
configuration through `ca-m.iov.changanauto.com.de`. Asia keeps its app-device
bootstrap through `cma-m.iov.changanauto.sg`.

The MQTT 5 connection uses validated TLS and subscribes to the configured
`properties/report`, `events/up`, login and status-response topics. It does not
send vehicle-control commands. Vehicles sharing a broker/login identity share
one session, with reports routed by their configured vehicle topics.

- Full reconciliation runs every 15 minutes, independently of push updates.
- EU full snapshots use HTTPS. Asia reads through the persistent MQTT session,
  avoiding a second connection with the same client ID.
- Reconnection requests a catch-up refresh. Failures retry from 5 seconds up to
  5 minutes, with fresh bootstrap credentials on each attempt.
- Disconnection or a failed full refresh enables 60-second fallback polling.
- Disabling polling stops scheduled and reconnect refreshes; push remains active.
- Unload and shutdown cancel owned tasks, remove timers and close connections.

## State handling

Partial reports preserve omitted/null values and unchanged array positions.
Unknown enum codes do not replace existing values. Reports with timestamps older
than the current vehicle snapshot are ignored. Retained reports are acknowledged
without being applied as new state changes.

Dated MQTT values are retained per field until a full snapshot reaches the same
or a newer timestamp. Reports arriving during a full fetch are also replayed
against its result. Undated reports follow arrival order. A connection-only push
updates online status without advancing the timestamp of the vehicle telemetry.

EU and Asia use the same normalizer. EU HTTPS snapshots can supply fields absent
from MQTT reports; an Asia MQTT snapshot cannot fill a translation missing from
the shared parser. The battery-preheating plan keeps its separate HTTPS request.
Existing vehicle-control transport and its status confirmation are unchanged.

## Mappings and diagnostics

[MQTT_REMAINING.md](MQTT_REMAINING.md) lists unresolved fields, partial mappings
and enabled interpretations that still need confirmation. A supported mapping
does not mean the vehicle sends that field after every change.

Diagnostics contain connection/fallback state, refresh intervals, report counts
and timing per subscription. Counters reset when the connection is rebuilt.
The integration does not keep MQTT payload histories or write research files.

Tire-pressure sensors expose a readable `status` attribute (0: OK, 2: low
pressure, other/missing codes: unknown), following the HA server language.
The raw value remains in `status_code`; automations comparing numeric codes
should use that attribute. Sensor pressure values are unchanged.

## Tire warning notifications

Each vehicle/wheel has a stable persistent-notification ID. Tire status 1 or 2
creates a warning in Home Assistant's notification panel. Code 2 is shown as low
tire pressure; code 1 has a generic warning because its meaning is unresolved.
The title uses the HA device name, preferring a user-assigned name.
The message includes the last reported pressure when available. English, Danish
and German follow the HA server language; other languages use English.

Both MQTT reports and reconciled full snapshots update these notifications.
Status 0 dismisses the wheel's warning. Missing/unknown data, stale MQTT reports,
connection errors and integration unload do not dismiss it. Pressure changes
update the same notification; unchanged reports do not recreate it. No pressure
threshold or phone-push service is used.

## Validation

Run `python -m pytest tests -q` and
`python -m compileall -q custom_components/mazda_6e`.
Tests use synthetic values and an in-memory MQTT peer. They cover encryption,
regional bootstrap, direct reports, shared connections, partial/stale updates,
reconciliation, heartbeat failure, reconnect, polling settings and cleanup.
HA-dependent tests use facades; this is not a complete Home Assistant runtime test.

EU observations include driving, climate changes and AC charging. Asia has been
reported working by the user. The unresolved codes still need field-specific
evidence; the EU observations do not establish every code in Asia.
