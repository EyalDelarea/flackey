"""The public half of the update signing key.

Committed rather than injected from a repository secret: it ships inside every copy of the app either
way, so a secret would hide nothing, and committed it takes a visible commit to swap.

Empty until the owner generates the keypair::

    python -c 'from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; \
from cryptography.hazmat.primitives import serialization as s; k = K.generate(); \
print("private:", k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex()); \
print("public: ", k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex())'

The private half goes into the `FLACKEY_UPDATE_SIGNING_KEY` Actions secret; the public half replaces
the empty string below. While it is empty the app updates by installer, the way it did before.
"""

from __future__ import annotations

# 64 hex characters: an Ed25519 public key is 32 raw bytes.
PUBLIC_KEY_HEX = ""
