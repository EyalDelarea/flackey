#!/usr/bin/env python
"""Sign an update archive with the Ed25519 release key.

    FLACKEY_UPDATE_SIGNING_KEY=<64 hex chars> packaging/sign_archive.py dist/Flackey-0.1.7.zip

Signs `signing_message(version, archive)`, not the bare zip, so the signature only ever names the
release it was made for. The version comes from the `Flackey-<version>.zip` filename, or a second
argument. Writes `<archive>.sig`, the detached signature as hex. Exits 0 without writing when the
secret is unset, so a release built before the key existed still publishes its installer.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# The signed format has to come from the app's own module; run from a checkout, uninstalled.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flackey.selfupdate.signature import signing_message

ENV_VAR = "FLACKEY_UPDATE_SIGNING_KEY"
KEY_BYTES = 32
ARCHIVE_NAME = re.compile(r"Flackey-(.+)\.zip")


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 3:
        print(f"usage: {argv[0] if argv else 'sign_archive.py'} <archive> [version]", file=sys.stderr)
        return 2
    archive = Path(argv[1])
    if not archive.is_file():
        print(f"no such archive: {archive}", file=sys.stderr)
        return 1

    if len(argv) == 3:
        version = argv[2]
    else:
        named = ARCHIVE_NAME.fullmatch(archive.name)
        if not named:
            print(f"::error title=Bad archive name::{archive.name} is not Flackey-<version>.zip, so "
                  "there is no version to sign. Pass one as a second argument.", file=sys.stderr)
            return 1
        version = named.group(1)

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
    signature = private.sign(signing_message(version, archive.read_bytes()))
    out = archive.with_name(archive.name + ".sig")
    out.write_text(signature.hex() + "\n")

    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    # Printed so a mismatch with `selfupdate/key.py` shows up in the release log.
    print(f"signed {archive.name} as version {version} -> {out.name}")
    print(f"public key: {public.hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
