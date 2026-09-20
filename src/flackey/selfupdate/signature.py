"""Ed25519 over the raw bytes of the update zip.

Pure: no network, no filesystem, no clock. Nothing here raises -- `verify` answers True or False for
any input, including a malformed key and a signature that is not a signature, so a refusal cannot turn
into a 500 that somebody then swallows with a bare `except`.
"""

from __future__ import annotations

import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .key import PUBLIC_KEY_HEX

PUBLIC_KEY_BYTES = 32
SIGNATURE_BYTES = 64


def encode_public_key(public_key: Ed25519PublicKey) -> bytes:
    """The 32 raw bytes of a public key -- the form `verify` takes and the form `key.py` stores as hex."""
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def baked_public_key() -> bytes | None:
    """The key this build carries, or None when the owner has not generated one yet.

    None for a malformed constant too: the safe reading of a mistake in the source is "this build does
    not do seamless updates", not "every update fails to verify", which looks like a compromised
    release."""
    text = PUBLIC_KEY_HEX.strip()
    if not text:
        return None
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return None
    return raw if len(raw) == PUBLIC_KEY_BYTES else None


def seamless_updates_configured() -> bool:
    """Whether this build can verify an update payload at all. False sends the Update button down the
    installer path -- deliberately not the refusal a bad signature earns, because nothing failed."""
    return baked_public_key() is not None


def decode_signature(asset: bytes) -> bytes | None:
    """The 64 raw bytes carried by a `.sig` release asset, or None when it is not one.

    Hex text rather than raw bytes so the asset survives a copy-paste and cannot be quietly truncated
    by something that treats it as a string. None rather than b"" so a missing signature reaches the
    caller as missing, which is refused, rather than as one that merely did not verify."""
    try:
        raw = binascii.unhexlify(asset.strip())
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == SIGNATURE_BYTES else None


def verify(payload: bytes, sig: bytes, public_key: bytes | None = None) -> bool:
    """Does `sig` sign `payload` under `public_key`? Defaults to the key baked into this build.

    False for every kind of no: wrong signature, wrong key, wrong length, no key at all."""
    key = public_key if public_key is not None else baked_public_key()
    if key is None or len(key) != PUBLIC_KEY_BYTES or len(sig) != SIGNATURE_BYTES:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(sig, payload)
    except (InvalidSignature, ValueError):
        return False
    return True
