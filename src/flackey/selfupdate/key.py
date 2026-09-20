"""The public half of the update signing key.

Committed rather than injected from a repository secret: it ships inside every copy of the app either
way, so a secret would hide nothing, and committed it takes a visible commit to swap.

The private half lives in the `FLACKEY_UPDATE_SIGNING_KEY` Actions secret and nowhere else in the
repository. Losing it does not break installed copies: published updates stop verifying until a new
public key is committed, and people install from the pkg in the meantime.

Rotating means generating a new pair, replacing the secret, and replacing the constant below::

    python -c 'from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; \
from cryptography.hazmat.primitives import serialization as s; k = K.generate(); \
print("private:", k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex()); \
print("public: ", k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex())'
"""

from __future__ import annotations

# 64 hex characters: an Ed25519 public key is 32 raw bytes.
PUBLIC_KEY_HEX = "69eb3b6e92ae54944ff48e0b7c5d01de8d6e95a8201bcfb3c3eacb5defb4c7ba"
