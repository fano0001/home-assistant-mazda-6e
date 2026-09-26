from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .const import SIGNER_MODE_RSA_PKCS1V15_SHA256


def _canonical_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def canonicalize_payload(payload: dict[str, Any], omit_keys: set[str] | None = None) -> str:
    """Build canonical key=value payload string sorted by key."""
    omit = {"sign"}
    if omit_keys:
        omit.update(omit_keys)

    pairs: list[str] = []
    for key in sorted(payload):
        if key in omit:
            continue
        pairs.append(f"{key}={_canonical_value(payload[key])}")

    return "&".join(pairs)


def build_rsa_pkcs1v15_sha256_signer(
    private_key_pem: str,
    *,
    omit_keys: set[str] | None = None,
) -> Callable[[dict[str, Any]], str]:
    """Return a signer implementing RSA PKCS1v15 SHA-256 over canonical key/value payload."""
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)

    def _sign(payload: dict[str, Any]) -> str:
        canonical = canonicalize_payload(payload, omit_keys=omit_keys)
        signature = private_key.sign(
            canonical.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode()

    return _sign


def build_command_signer(mode: str, private_key_pem: str) -> Callable[[dict[str, Any]], str]:
    """Create a command signer by mode name."""
    if mode == SIGNER_MODE_RSA_PKCS1V15_SHA256:
        return build_rsa_pkcs1v15_sha256_signer(private_key_pem)

    raise ValueError(f"Unsupported command signer mode: {mode}")
