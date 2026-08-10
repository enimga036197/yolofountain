"""
yolofountain.crypto — optional password encryption.

Applied to the whole payload *before* the fountain layer, so frames carry only
opaque ciphertext: catching or holding the stream tells an eavesdropper nothing
without the password. Authenticated (AEAD), so a wrong password or tampered data
fails cleanly with an error rather than yielding plausible garbage.

Requires the ``cryptography`` package (``pip install yolofountain[crypto]``). The rest
of yolofountain works without it; only encrypt/decrypt need it.

Envelope layout (this is the encrypted payload the fountain then carries):
    b"BEC1" | salt[16] | nonce[12] | ChaCha20-Poly1305(ciphertext+tag)
Key = scrypt(password, salt, n=2**15, r=8, p=1, dklen=32).
"""
import os
import hashlib

ENC_MAGIC = b"BEC1"
_SALT = 16
_NONCE = 12


class YoloCryptoError(Exception):
    pass


def _need_cryptography():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        return ChaCha20Poly1305
    except Exception as e:  # pragma: no cover
        raise YoloCryptoError(
            "password encryption needs the 'cryptography' package "
            "(pip install yolofountain[crypto])") from e


_SCRYPT_N = 2 ** 15
_SCRYPT_MAXMEM = 128 * _SCRYPT_N * 8 + (1 << 20)   # OpenSSL needs maxmem > 128*N*r


def derive_key(password, salt):
    if isinstance(password, str):
        password = password.encode("utf-8")
    return hashlib.scrypt(password, salt=salt, n=_SCRYPT_N, r=8, p=1, dklen=32,
                          maxmem=_SCRYPT_MAXMEM)


def encrypt(data, password):
    """bytes + password -> self-describing encrypted envelope (bytes)."""
    ChaCha20Poly1305 = _need_cryptography()
    salt = os.urandom(_SALT)
    nonce = os.urandom(_NONCE)
    key = derive_key(password, salt)
    ct = ChaCha20Poly1305(key).encrypt(nonce, bytes(data), None)
    return ENC_MAGIC + salt + nonce + ct


def decrypt(blob, password):
    """envelope + password -> plaintext bytes. Raises YoloCryptoError on wrong
    password or tampering."""
    ChaCha20Poly1305 = _need_cryptography()
    if blob[:4] != ENC_MAGIC:
        raise YoloCryptoError("not a yolofountain encrypted envelope")
    salt = blob[4:4 + _SALT]
    nonce = blob[4 + _SALT:4 + _SALT + _NONCE]
    ct = blob[4 + _SALT + _NONCE:]
    key = derive_key(password, salt)
    try:
        return ChaCha20Poly1305(key).decrypt(nonce, ct, None)
    except Exception as e:
        raise YoloCryptoError("wrong password or corrupt data") from e


def is_encrypted(blob):
    return blob[:4] == ENC_MAGIC
