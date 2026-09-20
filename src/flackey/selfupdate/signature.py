"""Ed25519 over `b"flackey-update-v1\n" + version + b"\n" + sha256(zip)`. Pure, nothing here raises.

The version is inside the signed message so a validly signed older archive cannot be re-published
under a newer tag: the app asks for the version it was offered, and that signature is for another.
"""

from __future__ import annotations

import binascii
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .key import PUBLIC_KEY_HEX

PUBLIC_KEY_BYTES = 32
SIGNATURE_BYTES = 64
DOMAIN = b"flackey-update-v1"


def signing_message(version: str, archive: bytes) -> bytes:
    """What the release signs and the app checks: defined once so the two cannot drift apart."""
    digest = hashlib.sha256(archive).digest()
    return DOMAIN + b"\n" + normalise_version(version).encode() + b"\n" + digest


def normalise_version(version: str) -> str:
    """Tag `v0.1.7` and archive `Flackey-0.1.7.zip` name the same release."""
    return version.strip().removeprefix("v")


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


def verify_archive(version: str, archive: bytes, sig: bytes,
                   public_key: bytes | None = None) -> bool:
    """False for every kind of no: wrong version, wrong signature, wrong key, no key at all."""
    key = public_key if public_key is not None else baked_public_key()
    if key is None or len(key) != PUBLIC_KEY_BYTES or len(sig) != SIGNATURE_BYTES:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(sig, signing_message(version, archive))
    except (InvalidSignature, ValueError):
        return False
    return True
