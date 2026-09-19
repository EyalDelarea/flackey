"""The public half of the update signing key.

A source constant rather than an asset beside `build.json`, and deliberately not a repository secret
injected at build time. It ships inside every copy of the app either way, so a secret would hide
nothing; committed, swapping it for another one takes a visible commit. That is the whole property
being bought here -- not secrecy, which an attacker already running as the owner does not need to
defeat anyway.

Empty until the owner generates the keypair, which is his step and not the build's::

    python -c 'from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; \
from cryptography.hazmat.primitives import serialization as s; k = K.generate(); \
print("private:", k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex()); \
print("public: ", k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex())'

The private half goes into the `FLACKEY_UPDATE_SIGNING_KEY` Actions secret and a password manager;
the public half replaces the empty string below. While it is empty the app updates by installer, the
way it did before -- an unset key is a build that cannot do seamless updates, which is a different
thing from a payload that failed to verify, and it must never be mistaken for one.
"""

from __future__ import annotations

# 64 hex characters: an Ed25519 public key is 32 raw bytes.
PUBLIC_KEY_HEX = ""
