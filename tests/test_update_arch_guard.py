"""An Intel (x86_64) Mac build never takes an update: every release is Apple Silicon only, and the arm64 app
it would install over itself does not open on an Intel Mac."""
from __future__ import annotations

import asyncio
import sys

import pytest
import respx

from flackey.web import update


@pytest.fixture
def packaged_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_a_packaged_intel_build_does_not_fit_the_releases(packaged_mac, monkeypatch):
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
    assert update.releases_fit_this_build() is False


def test_a_packaged_apple_silicon_build_fits_the_releases(packaged_mac, monkeypatch):
    monkeypatch.setattr(update.platform, "machine", lambda: "arm64")
    assert update.releases_fit_this_build() is True


@pytest.mark.parametrize("platform_name", ["win32", "linux"])
def test_other_systems_are_unaffected(monkeypatch, platform_name):
    monkeypatch.setattr(sys, "platform", platform_name)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(update.platform, "machine", lambda: "AMD64")
    assert update.releases_fit_this_build() is True


def test_a_source_checkout_on_an_intel_mac_still_checks(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
    assert update.releases_fit_this_build() is True


@respx.mock(assert_all_called=False)
def test_an_intel_build_never_looks_at_the_feed_and_offers_nothing(packaged_mac, monkeypatch, respx_mock):
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
    feed = respx_mock.get(update.RELEASES_URL)
    info, code = asyncio.run(update.check_release())
    assert not feed.called
    assert info["ok"] is False and info["available"] is False and info["newer"] is False
    assert "seamless" not in info or not info["seamless"]
    assert info["error"] == update.FOREIGN_ARCH_ERROR
    assert code == "other"
