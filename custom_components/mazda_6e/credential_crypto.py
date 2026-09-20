"""Android 1.2.3 RSAUtils.encrypt; se research/LOGIN_EVIDENCE.md."""

import base64
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
class MazdaECryptoError(ValueError):
    """Invalid credential encryption input; never includes secrets."""

SERVER_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkyhr43cBPTJ3jLiYsmbUwUp74cMJIOju5vqVzgtuK63Q99qV6iVT8wN5cXlyMtWI2mfOmhIao/fUN821im69MfOHsWXdqQEo5e9v654GPw+bju0pCphEPtD1I0VcyS34QkAu04urSun2U1q3Dr2OICLVWSnLa+01ioKxkaB0D209zXcls2eFQpvRAWm7xxVsoqzSwqp+neu5quOpn+eO/bW0TxcSQ8VZcDEUvadRTLSR0eOWgRuHIBiD2RGqPIPzKCm5A14q1qhxUZ8U0pmYe0Sx7eMy4RVe2iW7fnjc6pxTUMBkercSL26mevYouuCKqyie+LVQAtGa29RMl/lyiwIDAQAB'


def encrypt_credential(value: str, public_key_b64: str = SERVER_PUBLIC_KEY) -> str:
    """Uændret UTF-8, 245-byte blokke, PKCS#1 v1.5, Android Base64.DEFAULT."""
    try:
        der = base64.b64decode("".join(public_key_b64.split()), validate=True)
        key = serialization.load_der_public_key(der)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size != 2048:
            raise MazdaECryptoError("Der kræves en 2048-bit RSA-public key.")
        plain = value.encode("utf-8")
        encrypted = b"".join(key.encrypt(plain[i:i + 245], padding.PKCS1v15())
                             for i in range(0, len(plain), 245))
        return base64.encodebytes(encrypted).decode("ascii")
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise MazdaECryptoError("Credential-kryptering mislykkedes; kontrollér nøgle og input.") from None
