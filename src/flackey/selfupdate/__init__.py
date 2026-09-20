"""Replacing the running app with a newer copy of itself.

- `signature` decides whether a downloaded payload is allowed to exist on disk at all. Pure.
- `key` holds the public half of the signing key, as source rather than as data.
- `install` unpacks and stages, then hands the swap to a compiled helper that runs after this process
  is gone, because a process cannot replace the bundle it is executing from.

See `docs/specs/2026-09-19-issue-58-update-design.md` for why each step is shaped the way it is.
"""
