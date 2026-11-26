"""The release workflow publishes, and signs, exactly the file names the in-app updater looks for, on both
Mac architectures. A rename on either side alone would leave installed copies finding nothing to update to."""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest
import yaml

from flackey.web import update

CHECKS = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "checks.yml"
VERSION = "9.9.9"


def _step(job: dict, name: str) -> str:
    """A step's script with the tag's version filled in and shell quoting dropped, so file names read as
    plain `dist/Flackey-9.9.9.zip`."""
    run = next(s["run"] for s in job["steps"] if s.get("name") == name)
    return run.replace('"${GITHUB_REF_NAME#v}"', VERSION).replace("${GITHUB_REF_NAME#v}", VERSION)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(CHECKS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def published(workflow) -> str:
    """The Publish step with its `$zip` / `$intel_zip` variables expanded."""
    text = _step(workflow["jobs"]["release"], "Publish")
    for var, value in re.findall(r'^\s*(\w+)="(dist/[^"]+)"$', text, re.MULTILINE):
        text = text.replace(f'"${var}', f'"{value}').replace(f"${var}", value)
    return text


@pytest.mark.parametrize("machine", ["arm64", "x86_64"])
def test_the_release_signs_and_publishes_what_this_mac_build_looks_for(monkeypatch, workflow, published, machine):
    monkeypatch.setattr(update, "sys", types.SimpleNamespace(platform="darwin", frozen=True))
    monkeypatch.setattr(update, "platform", types.SimpleNamespace(machine=lambda: machine))
    installer, archive = update.installer_name(), update.archive_name(VERSION)
    signing = _step(workflow["jobs"]["release"], "Sign the update archive")

    assert f"sign dist/{archive}" in signing
    assert f"test -s dist/{archive}.sig" in signing
    assert f"sign dist/{installer} {VERSION}" in signing
    assert f"test -s dist/{installer}.sig" in signing
    for name in (installer, installer + ".sig", installer + ".sha256", archive, archive + ".sig"):
        assert f"dist/{name}" in published, f"{name} is never published"


def test_the_intel_job_hands_the_release_the_intel_names(workflow):
    job = workflow["jobs"]["mac-intel"]
    installer = _step(job, "Installer")
    assert "mv Flackey.pkg Flackey-Intel.pkg" in installer
    assert '"${zips[0]%.zip}-intel.zip"' in installer
    uploaded = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact"))
    assert "packaging/build/Flackey-Intel.pkg" in uploaded["with"]["path"]
    assert "packaging/build/Flackey-*-intel.zip" in uploaded["with"]["path"]
