# Introduction

This component has been created to be used with Home Assistant.

Mazda 6e presents a possibility to connect your Mazda 6e vehicle to Home Assistant.
This integration accepts your email and password and encrypts them internally. A device ID is generated automatically and retained for reauthentication. Enter the verification code sent by email when requested. Plaintext credentials and passwords are not saved in the config entry.

The integration exposes a lock entity for the vehicle doors. Mazda's six-digit Control Passcode can be entered during setup or later through **Reconfigure**. The lock entity remains unavailable until the passcode and control key have been configured. Normal Home Assistant lock and unlock actions do not ask for a code.

## Vehicle updates on this branch

This branch adds persistent MQTT subscriptions for **Europe and Asia**. Mapped
status values in Mazda's `properties/report` and `events/up` messages update
entities directly; a report does not trigger another HTTP or MQTT status read.
Only subscription topics returned by Mazda are used. Your own MQTT broker and
Home Assistant's MQTT integration are not required.

A full refresh runs every **15 minutes**, independently of incoming reports.
It also runs after a successful MQTT connection/reconnection to recover changes
missed while offline. Europe uses the existing HTTPS status endpoint for full
refreshes; Asia reuses the open MQTT connection. If MQTT is unavailable, or a
full refresh fails, automatic refresh falls back to **60 seconds**. MQTT reconnects
with increasing delays, up to five minutes. Manual refresh and updates after
vehicle controls continue to work. Disabling polling in the integration's Home
Assistant system options disables the periodic, fallback and reconnect refreshes;
MQTT reports still update entities.

MQTT maps battery percentage,
remaining range, odometer, individual door/window states, trunk, cabin/target
temperature, air-conditioning state, AC/DC current from the supported current
fields and remaining charge time,
plus update time when supplied. It also maps charge limit, all four tire pressures,
cabin humidity and cabin PM2.5 to the HTTPS status fields. Tire pressure and
humidity are truncated to whole numbers to match the observed HTTPS response.
Omitted or null fields in a partial report keep
their previous values. Fields without an established MQTT mapping, including
unverified lock, steering-wheel heating and charging-status codes, can still be
updated by EU's HTTPS refresh. Asia's full refresh uses the same MQTT parser and
does not fill gaps in that parser. Front defrost maps MQTT status 1/2 to HTTPS 0/1; its alternate
switch field uses 0/1 directly. Conflicting defrost fields are ignored. Observed
off/locked codes and power states 0/2 are also translated; other codes are kept
out of entity updates until verified. The same MQTT parser serves both regions;
the new measurements
have been compared with a European HTTPS snapshot, not a live Asia vehicle.

Full refreshes preserve newer dated MQTT values per field, including changes
received before the refresh started. Other snapshot fields still update. Once
the snapshot timestamp catches up, its values take precedence. Reports without
usable timestamps cannot provide the same ordering guarantee.

**Validation status:** The branch is installed in Home Assistant 2026.10.2 for a
European account. Regional bootstrap and persistent MQTT login succeeded;
diagnostics show a connected session, 900-second reconciliation and inactive
fallback. Spontaneous telemetry reports have been observed, and the user observed
instant climate changes. The user has also reported Asia tested and working.
The unresolved field mappings remain listed separately. Direct updates are also covered by offline
tests. See
[MQTT implementation and evidence](MQTT_UPDATES.md) and the
[list of missing and partial mappings](MQTT_REMAINING.md).

# Installation

## With HACS

1. Add this repository as a custom repository in HACS.
2. Download the integration.
3. Restart Home Assistant

## Manual

Copy the `mazda_6e` directory, from `custom_components` in this repository,
and place it inside your Home Assistant Core installation's `custom_components` directory. Restart Home Assistant prior to moving on to the `Setup` section.

`Note`: If installing manually, in order to be alerted about new releases, you will need to subscribe to releases from this repository

# Cloud-control passcode

The Control Passcode is required by Mazda for cloud vehicle controls. Bluetooth control in the Mazda app does not request it, so disable Bluetooth on the phone or move outside Bluetooth range before looking for the prompt:

1. Open the Mazda app with Bluetooth disabled or while outside Bluetooth range of the vehicle.
2. Start a cloud vehicle-control action, such as locking or unlocking the doors.
3. Enter the requested six-digit Control Passcode. If it is unknown, use **Forgot PWD** in the Mazda app to reset it.
4. In Home Assistant, open the Mazda 6e integration and select **Reconfigure**.
5. Enter the Mazda account credentials and the same six-digit Control Passcode.

Reconfiguration signs in again and registers the control key required for signed cloud commands. The passcode is stored in the Home Assistant config entry as an integration credential. It is encrypted before being sent to Mazda and is never exposed as a code field on the lock entity.
