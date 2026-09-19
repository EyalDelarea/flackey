"""Ed25519 over the raw bytes of the update zip.

Pure: no network, no filesystem, no clock. Everything that decides whether a downloaded bundle is
allowed to become the running app comes through `verify`, and it is a function of its arguments and
nothing else, which is what makes the failure table in the design doc testable at all.

Nothing in here raises. `verify` answers True or False for any input, including a malformed key and a
signature that is not a signature. The caller's job is to refuse on False; giving it a second,
exception-shaped way to fail is how a refusal turns into a 500 and a 500 turns into someone adding a
bare `except` that swallows it.
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
    """The 32 raw bytes of a public key -- the form `verify` takes and the form `key.py` stores as hex.
    Here rather than at the two call sites so the release tooling and the tests agree on the encoding
    by construction."""
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def baked_public_key() -> bytes | None:
    """The key this build carries, or None when the owner has not generated one yet.

    None for an empty constant *and* for a malformed one. A key that cannot be decoded is a mistake in
    the source, and the safe reading of a mistake there is "this build does not do seamless updates",
    not "every update fails to verify" -- the second wedges the Update button with an error that looks
    like a compromised release."""
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
    installer path -- deliberately *not* the refusal that a bad signature earns, because no key means
    nothing was claimed and nothing failed."""
    return baked_public_key() is not None


def decode_signature(asset: bytes) -> bytes | None:
    """The 64 raw bytes carried by a `.sig` release asset, or None when it is not one.

    The asset is hex text rather than raw bytes so that it survives a copy-paste, reads back in a
    terminal, and cannot be quietly truncated by something that treats it as a string. Trailing
    whitespace is what a shell redirect leaves behind.

    None rather than b"": an empty or unreadable asset has to reach the caller as "there is no
    signature here", which is refused for being missing. Letting it through as empty bytes would make
    it indistinguishable from a signature that merely did not verify, and the design doc requires a
    missing signature never to be treated as "no signature required"."""
    try:
        raw = binascii.unhexlify(asset.strip())
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == SIGNATURE_BYTES else None


def verify(payload: bytes, sig: bytes, public_key: bytes | None = None) -> bool:
    """Does `sig` sign `payload` under `public_key`? Defaults to the key baked into this build.

    False for every kind of no: wrong signature, wrong key, wrong length, no key at all. `cryptography`
    raises `InvalidSignature` for a mismatch and `ValueError` for a key or signature of the wrong size,
    and both mean the same thing to the one caller there is."""
    key = public_key if public_key is not None else baked_public_key()
    if key is None or len(key) != PUBLIC_KEY_BYTES or len(sig) != SIGNATURE_BYTES:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(sig, payload)
    except (InvalidSignature, ValueError):
        return False
    return True
