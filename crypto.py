"""Field-level encryption for Aadhaar / PAN / bank account numbers.

* Values are encrypted with Fernet (AES-128-CBC + HMAC) and stored as  enc1:<token>.
* A *blind index* (keyed HMAC of the normalised value) is stored next to it so we can enforce
  "unique per organization" and do exact-match search without decrypting anything.
* Keys come from config.FIELD_KEYS. The first key encrypts; every key can decrypt, which is how
  key rotation works (see manage.py rotate-key and the README).
"""
import hashlib
import hmac
import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy.types import String, TypeDecorator

import config

log = logging.getLogger("hrms.crypto")
PREFIX = "enc1:"


class DecryptionError(RuntimeError):
    pass


def _multi(keys=None):
    return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in (keys or config.FIELD_KEYS)])


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plain, keys=None):
    if plain is None or plain == "":
        return None
    if is_encrypted(plain):
        return plain
    return PREFIX + _multi(keys).encrypt(str(plain).encode()).decode()


def decrypt(value, keys=None):
    """Return plaintext. Legacy plaintext (no prefix) passes through so old rows keep working
    until the migration/`manage.py encrypt-existing` has converted them."""
    if value is None or not is_encrypted(value):
        return value
    try:
        return _multi(keys).decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        log.error("Cannot decrypt a stored value - wrong or missing HRMS_FIELD_KEY / data/field.key?")
        raise DecryptionError("A protected field could not be decrypted. The encryption key does not "
                              "match the data. See README: 'Recovering encrypted data'.") from e


def _index_key(keys=None):
    first = (keys or config.FIELD_KEYS)[0]
    return hashlib.sha256(b"hrms-blind-index:" + (first.encode() if isinstance(first, str) else first)).digest()


def blind_index(kind, value, keys=None):
    """Deterministic keyed hash of the normalised value (None for empty)."""
    if value is None or str(value).strip() == "":
        return None
    v = "".join(str(value).split()).upper()
    return hmac.new(_index_key(keys), f"{kind}:{v}".encode(), hashlib.sha256).hexdigest()[:32]


class EncryptedString(TypeDecorator):
    """Column type: encrypts on write, decrypts on read."""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value) if value not in (None, "") else None

    def process_result_value(self, value, dialect):
        return decrypt(value)
