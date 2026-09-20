# Introduction

This component has been created to be used with Home Assistant.

Mazda 6e presents a possibility to connect your Mazda 6e vehicle to Home Assistant.
This local branch accepts your ordinary email and password and encrypts them internally. A device ID is generated automatically and retained for reauthentication. Enter the verification code sent by email when requested. Existing entries remain compatible; no migration or manual device ID is needed. Plaintext credentials and passwords are not saved in the config entry.

# Installation

## With HACS

1. Add this repository as a custom repository in HACS.
2. Download the integration.
3. Restart Home Assistant

## Manual

Copy the `mazda_6e` directory, from `custom_components` in this repository,
and place it inside your Home Assistant Core installation's `custom_components` directory. Restart Home Assistant prior to moving on to the `Setup` section.

`Note`: If installing manually, in order to be alerted about new releases, you will need to subscribe to releases from this repository.

## Local login patch

The credential routine uses the Android 1.2.3 server RSA key, UTF-8, 245-byte chunks, PKCS#1 v1.5 and Android-compatible Base64 line breaks. It is copied from the evidence-backed pymazda-e implementation; the request pubKey is a separate client key. pymazda-e itself remains an independent library. This branch is not published upstream.

For testing, copy the complete custom_components/mazda_6e folder over your existing installation, restart Home Assistant, and add or reauthenticate Mazda 6e. Home Assistant installs the declared cryptography dependency. HACS updates can overwrite this local patch.

Offline unit tests cover encryption/chunking, login and email verification, retry, distinct generated IDs, and retaining an existing ID during reauthentication. Run `python -m pytest tests -q --disable-socket --allow-hosts=127.0.0.1,::1` with pytest, pytest-socket, voluptuous and cryptography installed. These use a minimal Home Assistant facade; they do not replace testing the flow in a running Home Assistant installation. No production server is contacted by the tests.
