"""The release side of the signature: what `packaging/sign_archive.py` writes must verify in the app."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flackey.selfupdate import signature

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "sign_archive.py"


def load():
    """Not importable as a module: `packaging/` is a build directory, not a package."""
    spec = importlib.util.spec_from_file_location("sign_archive", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sign_archive():
    return load()


@pytest.fixture
def keypair():
    private = Ed25519PrivateKey.generate()
    return private, signature.encode_public_key(private.public_key())


@pytest.fixture
def archive(tmp_path) -> Path:
    out = tmp_path / "Flackey-1.2.3.zip"
    out.write_bytes(b"PK\x03\x04pretend this is a bundle" * 64)
    return out


def test_the_signature_it_writes_verifies_for_the_version_in_the_name(sign_archive, archive, keypair,
                                                                      monkeypatch):
    private, public = keypair
    monkeypatch.setenv(sign_archive.ENV_VAR, private.private_bytes_raw().hex())

    assert sign_archive.main(["sign_archive.py", str(archive)]) == 0

    sig = signature.decode_signature(archive.with_suffix(".zip.sig").read_bytes())
    assert signature.verify_archive("1.2.3", archive.read_bytes(), sig, public) is True
    assert signature.verify_archive("1.2.4", archive.read_bytes(), sig, public) is False


def test_the_version_can_be_given_instead_of_read_from_the_name(sign_archive, tmp_path, keypair,
                                                                monkeypatch):
    private, public = keypair
    monkeypatch.setenv(sign_archive.ENV_VAR, private.private_bytes_raw().hex())
    odd = tmp_path / "build.zip"
    odd.write_bytes(b"bundle")

    assert sign_archive.main(["sign_archive.py", str(odd), "v1.2.3"]) == 0

    sig = signature.decode_signature(odd.with_suffix(".zip.sig").read_bytes())
    assert signature.verify_archive("1.2.3", b"bundle", sig, public) is True


def test_an_archive_that_names_no_version_is_refused_without_writing(sign_archive, tmp_path, keypair,
                                                                     monkeypatch):
    private, _ = keypair
    monkeypatch.setenv(sign_archive.ENV_VAR, private.private_bytes_raw().hex())
    odd = tmp_path / "Flackey.zip"
    odd.write_bytes(b"bundle")

    assert sign_archive.main(["sign_archive.py", str(odd)]) == 1
    assert list(tmp_path.glob("*.sig")) == []


def test_an_unset_key_publishes_the_zip_unsigned_rather_than_failing(sign_archive, archive,
                                                                     monkeypatch):
    """A release from before the key existed still has to publish its installer."""
    monkeypatch.delenv(sign_archive.ENV_VAR, raising=False)

    assert sign_archive.main(["sign_archive.py", str(archive)]) == 0
    assert list(archive.parent.glob("*.sig")) == []


def test_a_key_that_is_not_a_key_fails_the_release(sign_archive, archive, monkeypatch):
    monkeypatch.setenv(sign_archive.ENV_VAR, "not hex")
    assert sign_archive.main(["sign_archive.py", str(archive)]) == 1
    monkeypatch.setenv(sign_archive.ENV_VAR, "ab" * 16)
    assert sign_archive.main(["sign_archive.py", str(archive)]) == 1
    assert list(archive.parent.glob("*.sig")) == []


def test_a_missing_archive_is_named_rather_than_traced(sign_archive, tmp_path):
    assert sign_archive.main(["sign_archive.py", str(tmp_path / "nope.zip")]) == 1
    assert sign_archive.main(["sign_archive.py"]) == 2
