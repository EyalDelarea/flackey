#!/usr/bin/env python
"""Cut `uv export --hashes` down to one package and what it depends on, for the release job's signing step.

    uv export --frozen --no-emit-project --no-dev | packaging/signing_requirements.py uv.lock cryptography

The signing step holds the release key, so what it installs is pinned by hash to exactly what uv.lock
resolved, and installed with `--require-hashes`. The whole export cannot be used as is: it lists every
dependency of the app, including macOS-only ones, and installing all of them would only slow the job. The
closure is read from uv.lock rather than written down here, so it follows the lock when cryptography's
own dependencies change. Fails, rather than printing less, when a package of the closure is missing from
the export or carries no hash. Standard library only: it runs before anything is installed.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

# The first line of one requirement: `name==version`, an optional `; marker`, and a trailing backslash.
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure(lock: Path, root: str) -> set[str]:
    """`root` and every package it depends on, transitively, by uv.lock's own dependency lists."""
    packages = {_canonical(p["name"]): p for p in tomllib.loads(lock.read_text(encoding="utf-8"))["package"]}
    seen: set[str] = set()
    todo = [_canonical(root)]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        if name not in packages:
            sys.exit(f"{name} is not in {lock}")
        seen.add(name)
        todo.extend(_canonical(d["name"]) for d in packages[name].get("dependencies", []))
    return seen


def filter_export(export: str, lock: Path, root: str) -> str:
    """The requirement blocks of `export` that belong to `root`'s closure, each with its hashes."""
    wanted = closure(lock, root)
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in export.splitlines():
        m = _REQUIREMENT.match(line)
        if m:
            name = _canonical(m.group(1))
            current = blocks.setdefault(name, []) if name in wanted else None
        elif not line.startswith(" "):
            current = None
        if current is not None and not line.lstrip().startswith("#"):
            current.append(line)
    missing = sorted(wanted - blocks.keys())
    if missing:
        sys.exit(f"not in the export: {', '.join(missing)}")
    unhashed = sorted(name for name, lines in blocks.items() if not any("--hash=" in x for x in lines))
    if unhashed:
        sys.exit(f"no hash for: {', '.join(unhashed)}")
    return "".join(line + "\n" for name in sorted(blocks) for line in blocks[name])


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <uv.lock> <package>  (uv export output on stdin)", file=sys.stderr)
        return 2
    sys.stdout.write(filter_export(sys.stdin.read(), Path(argv[1]), argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
