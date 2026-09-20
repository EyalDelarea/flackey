#!/usr/bin/env python
"""Sign an update archive with the Ed25519 release key.

    FLACKEY_UPDATE_SIGNING_KEY=<64 hex chars> packaging/sign_archive.py dist/Flackey-0.1.7.zip

Writes `<archive>.sig`: the detached signature as hex text, which is what the app downloads beside the
archive and checks before it unpacks anything.

The private key comes from the environment and is never written anywhere. Losing it does not break
installed copies of Flackey; published updates stop verifying until a new public key is committed, and
people install from the pkg in the meantime.

Prints and exits 0 without writing anything when the secret is unset, so a release built before the
owner has generated the keypair still publishes its installer instead of failing the job.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ENV_VAR = "FLACKEY_UPDATE_SIGNING_KEY"
KEY_BYTES = 32


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0] if argv else 'sign_archive.py'} <archive>", file=sys.stderr)
        return 2
    archive = Path(argv[1])
    if not archive.is_file():
        print(f"no such archive: {archive}", file=sys.stderr)
        return 1

    secret = os.environ.get(ENV_VAR, "").strip()
    if not secret:
        print(f"{ENV_VAR} is not set: publishing {archive.name} unsigned, so updates use the installer.")
        return 0

    try:
        raw = bytes.fromhex(secret)
    except ValueError:
        # Deliberately does not echo the value, even the part that parsed.
        print(f"::error title=Bad signing key::{ENV_VAR} is not hex.", file=sys.stderr)
        return 1
    if len(raw) != KEY_BYTES:
        print(f"::error title=Bad signing key::{ENV_VAR} is {len(raw)} bytes, expected {KEY_BYTES}.",
              file=sys.stderr)
        return 1

    private = Ed25519PrivateKey.from_private_bytes(raw)
    signature = private.sign(archive.read_bytes())
    out = archive.with_name(archive.name + ".sig")
    out.write_text(signature.hex() + "\n")

    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    # Printed so a mismatch with the key committed in `selfupdate/key.py` shows up in the release log
    # rather than only as "this update could not be verified" on somebody's Mac.
    print(f"signed {archive.name} -> {out.name}")
    print(f"public key: {public.hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
