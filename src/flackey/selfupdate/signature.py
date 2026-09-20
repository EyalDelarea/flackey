"""Ed25519 over the raw bytes of the update zip. Pure, and nothing here raises."""

from __future__ import annotations

import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .key import PUBLIC_KEY_HEX

PUBLIC_KEY_BYTES = 32
SIGNATURE_BYTES = 64


def encode_public_key(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def baked_public_key() -> bytes | None:
    """None for a malformed constant too: a mistake in the source means "no seamless updates", not
    "every update fails to verify", which looks like a compromised release."""
    text = PUBLIC_KEY_HEX.strip()
    if not text:
        return None
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return None
    return raw if len(raw) == PUBLIC_KEY_BYTES else None


def seamless_updates_configured() -> bool:
    return baked_public_key() is not None


def decode_signature(asset: bytes) -> bytes | None:
    """The 64 bytes carried by a `.sig` asset, or None when it is not one.

    None rather than b"", so a missing signature is refused as missing rather than as one that
    merely did not verify."""
    try:
        raw = binascii.unhexlify(asset.strip())
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == SIGNATURE_BYTES else None


def verify(payload: bytes, sig: bytes, public_key: bytes | None = None) -> bool:
    """False for every kind of no: wrong signature, wrong key, wrong length, no key at all."""
    key = public_key if public_key is not None else baked_public_key()
    if key is None or len(key) != PUBLIC_KEY_BYTES or len(sig) != SIGNATURE_BYTES:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(sig, payload)
    except (InvalidSignature, ValueError):
        return False
    return True
