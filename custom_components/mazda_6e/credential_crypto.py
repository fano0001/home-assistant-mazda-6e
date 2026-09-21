"""Credential encryption matching Android app 1.2.3 RSAUtils.encrypt."""

import base64
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class MazdaECryptoError(ValueError):
    """Invalid credential encryption input; never includes secrets."""

SERVER_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkyhr43cBPTJ3jLiYsmbUwUp74cMJIOju5vqVzgtuK63Q99qV6iVT8wN5cXlyMtWI2mfOmhIao/fUN821im69MfOHsWXdqQEo5e9v654GPw+bju0pCphEPtD1I0VcyS34QkAu04urSun2U1q3Dr2OICLVWSnLa+01ioKxkaB0D209zXcls2eFQpvRAWm7xxVsoqzSwqp+neu5quOpn+eO/bW0TxcSQ8VZcDEUvadRTLSR0eOWgRuHIBiD2RGqPIPzKCm5A14q1qhxUZ8U0pmYe0Sx7eMy4RVe2iW7fnjc6pxTUMBkercSL26mevYouuCKqyie+LVQAtGa29RMl/lyiwIDAQAB'


def encrypt_credential(value: str, public_key_b64: str = SERVER_PUBLIC_KEY) -> str:
    """Use unmodified UTF-8, 245-byte chunks, PKCS#1 v1.5 and Android Base64.DEFAULT."""
    try:
        der = base64.b64decode("".join(public_key_b64.split()), validate=True)
        key = serialization.load_der_public_key(der)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size != 2048:
            raise MazdaECryptoError("A 2048-bit RSA public key is required.")
        plain = value.encode("utf-8")
        encrypted = b"".join(key.encrypt(plain[i:i + 245], padding.PKCS1v15())
                             for i in range(0, len(plain), 245))
        return base64.encodebytes(encrypted).decode("ascii")
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise MazdaECryptoError("Credential encryption failed; check the key and input.") from None


def generate_control_key_pair() -> tuple[str, str]:
    """Generate the RSA key pair used to sign vehicle-control commands."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_der = private.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return base64.encodebytes(public_der).decode(), base64.encodebytes(private_der).decode()


def decrypt_control_serial(value_b64: str, private_key_b64: str) -> str:
    """Decrypt a vehicle-control serial number with the registered private key."""
    try:
        key = serialization.load_der_private_key(
            base64.b64decode("".join(private_key_b64.split()), validate=True), None,
        )
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
            raise MazdaECryptoError("A 2048-bit RSA private key is required.")
        return key.decrypt(
            base64.b64decode("".join(value_b64.split()), validate=True),
            padding.PKCS1v15(),
        ).decode()
    except (ValueError, TypeError, UnicodeError, UnsupportedAlgorithm):
        raise MazdaECryptoError("Control serial decryption failed.") from None


def sign_door_control(open_doors: bool, rc_token: str, serial_no: str,
                      vehicle_id: int, private_key_b64: str) -> str:
    """Sign a door-control request using Mazda's canonical field order."""
    source = (
        f"open={'true' if open_doors else 'false'}&rcToken={rc_token}"
        f"&seriralNo={serial_no}&vehicleId={vehicle_id}"
    )
    try:
        key = serialization.load_der_private_key(
            base64.b64decode("".join(private_key_b64.split()), validate=True), None,
        )
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
            raise MazdaECryptoError("A 2048-bit RSA private key is required.")
        return base64.encodebytes(
            key.sign(source.encode(), padding.PKCS1v15(), hashes.SHA256())
        ).decode()
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise MazdaECryptoError("Door-control signing failed.") from None
