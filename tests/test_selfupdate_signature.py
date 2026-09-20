"""The gate between bytes off the network and code running as the user. Everything answers False
rather than raising: a caller that must wrap `verify_archive` is one that can wrap it wrong.
"""
from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flackey.selfupdate import signature

PAYLOAD = b"pretend this is Flackey-0.1.7.zip" * 100
VERSION = "0.1.7"


def sign(private, payload=PAYLOAD, version=VERSION):
    return private.sign(signature.signing_message(version, payload))


@pytest.fixture
def keypair():
    private = Ed25519PrivateKey.generate()
    return private, signature.encode_public_key(private.public_key())


def test_a_signature_from_the_matching_key_verifies(keypair):
    private, public = keypair
    assert signature.verify_archive(VERSION, PAYLOAD, sign(private), public) is True


def test_a_tampered_payload_is_refused(keypair):
    private, public = keypair
    sig = sign(private)
    assert signature.verify_archive(VERSION, PAYLOAD + b"!", sig, public) is False
    assert signature.verify_archive(VERSION, b"x" + PAYLOAD[1:], sig, public) is False


def test_a_tampered_signature_is_refused(keypair):
    private, public = keypair
    sig = bytearray(sign(private))
    sig[0] ^= 0x01
    assert signature.verify_archive(VERSION, PAYLOAD, bytes(sig), public) is False


def test_a_truncated_signature_is_refused(keypair):
    private, public = keypair
    assert signature.verify_archive(VERSION, PAYLOAD, sign(private)[:-1], public) is False


def test_an_empty_signature_is_refused(keypair):
    _, public = keypair
    assert signature.verify_archive(VERSION, PAYLOAD, b"", public) is False


def test_a_signature_for_another_version_over_the_same_archive_is_refused(keypair):
    """The downgrade: an old archive and its real signature, re-published under a newer tag."""
    private, public = keypair
    old = sign(private, version="0.1.6")
    assert signature.verify_archive("0.1.6", PAYLOAD, old, public) is True
    assert signature.verify_archive("0.1.7", PAYLOAD, old, public) is False


def test_a_tag_and_a_bare_version_are_the_same_release(keypair):
    private, public = keypair
    tagged = sign(private, version="v0.1.7")
    bare = sign(private, version="0.1.7")
    assert signature.verify_archive("0.1.7", PAYLOAD, tagged, public) is True
    assert signature.verify_archive("v0.1.7", PAYLOAD, bare, public) is True
    assert signature.verify_archive(" v0.1.7\n", PAYLOAD, sign(private), public) is True


def test_the_signed_message_names_the_version_and_digests_the_archive():
    """Both sides build this; a change here is a change every published signature must survive."""
    assert signature.signing_message("v1.2.3 ", PAYLOAD) == (
        b"flackey-update-v1\n1.2.3\n" + hashlib.sha256(PAYLOAD).digest())


def test_a_signature_from_a_different_key_is_refused(keypair):
    _, public = keypair
    other = Ed25519PrivateKey.generate()
    assert signature.verify_archive(VERSION, PAYLOAD, sign(other), public) is False


def test_garbage_where_the_signature_should_be_is_refused(keypair):
    _, public = keypair
    assert signature.verify_archive(VERSION, PAYLOAD, b"not a signature at all", public) is False


def test_an_empty_payload_with_its_own_valid_signature_still_verifies(keypair):
    private, public = keypair
    assert signature.verify_archive(VERSION, b"", sign(private, b""), public) is True


@pytest.mark.parametrize("bad", [b"", b"\x00" * 31, b"\x00" * 33, b"short"])
def test_a_public_key_that_is_not_32_bytes_refuses_rather_than_raising(bad):
    assert signature.verify_archive(VERSION, PAYLOAD, b"\x00" * 64, bad) is False


def test_the_signature_is_read_from_the_hex_the_release_publishes(keypair):
    private, _ = keypair
    sig = sign(private)
    assert signature.decode_signature(sig.hex().encode() + b"\n") == sig
    assert signature.decode_signature(b"  " + sig.hex().upper().encode() + b"  ") == sig


@pytest.mark.parametrize("bad", [b"", b"zz", b"abc", b"not hex", b"\xff\xfe"])
def test_an_unreadable_signature_asset_decodes_to_nothing(bad):
    assert signature.decode_signature(bad) is None


def test_a_signature_asset_of_the_wrong_length_decodes_to_nothing(keypair):
    private, _ = keypair
    assert signature.decode_signature(sign(private).hex()[:-2].encode()) is None


def test_the_release_key_is_baked_in_and_well_formed():
    """A cleared constant is silent: every copy falls back to the installer saying nothing."""
    key = signature.baked_public_key()
    assert key is not None and len(key) == signature.PUBLIC_KEY_BYTES
    assert signature.seamless_updates_configured() is True
