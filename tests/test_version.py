"""Keep the package version in sync across release inputs."""

import tomllib
from pathlib import Path

import flackey

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert flackey.__version__ == project["version"]


def test_version_is_a_plain_semver_triple():
    major, minor, patch = flackey.__version__.split(".")
    assert all(part.isdigit() for part in (major, minor, patch))
