"""Intel Mac builds take the Intel assets, Apple Silicon builds the ones they always took.

A release carries both Mac builds side by side: `Flackey.pkg` + `Flackey-<v>.zip` (Apple Silicon, the names
every installed copy already looks for) and `Flackey-Intel.pkg` + `Flackey-<v>-intel.zip`. The Apple Silicon
app does not open on an Intel Mac, so an Intel copy must never be offered it, not even as a fallback.
"""
from __future__ import annotations

import asyncio
import types

import httpx
import pytest
import respx

from flackey.selfupdate import install as selfupdate
from flackey.web import update
from flackey.web.update import RELEASES_URL

DOWNLOAD = "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/"
ARM = ["Flackey.pkg", "Flackey.pkg.sig", "Flackey-9.9.9.zip", "Flackey-9.9.9.zip.sig"]
INTEL = ["Flackey-Intel.pkg", "Flackey-Intel.pkg.sig", "Flackey-9.9.9-intel.zip", "Flackey-9.9.9-intel.zip.sig"]


def _feed(names: list[str]) -> list[dict]:
    return [{"draft": False, "prerelease": False, "tag_name": "v9.9.9",
             "published_at": "2026-12-01T10:00:00Z",
             "html_url": "https://github.com/EyalDelarea/flackey/releases/tag/v9.9.9",
             "assets": [{"name": n, "size": 100 + i, "browser_download_url": DOWNLOAD + n}
                        for i, n in enumerate(names)]}]


@pytest.fixture(autouse=True)
def _real_names_and_a_seamless_mac(monkeypatch):
    # A copy in /Applications that can swap itself, so `seamless` turns on whatever machine runs the suite.
    monkeypatch.setattr(selfupdate, "seamless_available", lambda *a, **k: True)
    monkeypatch.setattr(selfupdate, "stray_copy", lambda *a, **k: None)


def _packaged_mac(monkeypatch, machine: str) -> None:
    """A frozen Mac build, as far as `web/update.py` can tell. Only that module's `sys` and `platform`
    are swapped, so the rest of the process stays on the real platform."""
    monkeypatch.setattr(update, "sys", types.SimpleNamespace(platform="darwin", frozen=True))
    monkeypatch.setattr(update, "platform", types.SimpleNamespace(machine=lambda: machine))


def _check(names: list[str]) -> dict:
    with respx.mock:
        respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_feed(names)))
        return asyncio.run(update.latest_release())


def test_an_intel_build_takes_the_intel_installer_and_archive(monkeypatch):
    _packaged_mac(monkeypatch, "x86_64")
    info = _check(ARM + INTEL)
    assert info["url"] == DOWNLOAD + "Flackey-Intel.pkg"
    assert info["installer_signature_url"] == DOWNLOAD + "Flackey-Intel.pkg.sig"
    assert info["seamless"] is True and info["available"] is True
    assert info["archive_url"] == DOWNLOAD + "Flackey-9.9.9-intel.zip"
    assert info["signature_url"] == DOWNLOAD + "Flackey-9.9.9-intel.zip.sig"


def test_an_apple_silicon_build_sees_exactly_what_it_always_did(monkeypatch):
    _packaged_mac(monkeypatch, "arm64")
    for feed in (ARM, ARM + INTEL):
        info = _check(feed)
        assert info["url"] == DOWNLOAD + "Flackey.pkg"
        assert info["installer_signature_url"] == DOWNLOAD + "Flackey.pkg.sig"
        assert info["archive_url"] == DOWNLOAD + "Flackey-9.9.9.zip"
        assert info["signature_url"] == DOWNLOAD + "Flackey-9.9.9.zip.sig"
        assert info["seamless"] is True and info["available"] is True


def test_an_intel_build_is_never_offered_the_apple_silicon_assets(monkeypatch):
    """A release without the Intel build (an older one, or one whose Intel job did not run) is newer but
    has nothing an Intel copy can install: no fallback to `Flackey.pkg` or `Flackey-<v>.zip`."""
    _packaged_mac(monkeypatch, "x86_64")
    info = _check(ARM)
    assert info["newer"] is True
    assert info["available"] is False and info["seamless"] is False
    assert info["url"] is None and info["archive_url"] is None and info["signature_url"] is None


def test_an_intel_archive_without_its_signature_is_not_seamless(monkeypatch):
    _packaged_mac(monkeypatch, "x86_64")
    info = _check(ARM + [n for n in INTEL if n != "Flackey-9.9.9-intel.zip.sig"])
    assert info["seamless"] is False and info["archive_url"] is None


@pytest.mark.parametrize(("platform_name", "frozen", "machine", "expected"), [
    ("darwin", True, "x86_64", True),
    ("darwin", True, "arm64", False),
    ("darwin", False, "x86_64", False),   # a source checkout installs nothing from a release
    ("win32", True, "AMD64", False),
    ("linux", True, "x86_64", False),
])
def test_only_a_packaged_x86_64_mac_build_is_the_intel_build(monkeypatch, platform_name, frozen, machine, expected):
    fake_sys = types.SimpleNamespace(platform=platform_name)
    if frozen:
        fake_sys.frozen = True
    monkeypatch.setattr(update, "sys", fake_sys)
    monkeypatch.setattr(update, "platform", types.SimpleNamespace(machine=lambda: machine))
    assert update.intel_mac() is expected


def test_the_names_line_up_with_what_the_release_publishes(monkeypatch):
    _packaged_mac(monkeypatch, "x86_64")
    assert update.installer_name() == "Flackey-Intel.pkg"
    assert update.archive_name("1.2.3") == "Flackey-1.2.3-intel.zip"
    _packaged_mac(monkeypatch, "arm64")
    assert update.installer_name() == "Flackey.pkg"
    assert update.archive_name("1.2.3") == "Flackey-1.2.3.zip"
