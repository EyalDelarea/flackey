# Contributing to Flackey

Thanks for taking a look. Flackey is a small, opinionated project — issues
and PRs are welcome, but read this first so your change lands smoothly.

## Before you start

For anything more than a small fix, open an issue first (there are templates
for bug reports, feature requests, and questions). It saves you from building
something that doesn't fit the project's direction.

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 and
pulls in yt-dlp with the other Python dependencies), Node.js 22 with npm (the
version CI uses), and `ffmpeg`/`ffprobe` plus Chromaprint's `fpcalc` on your
PATH (`brew install ffmpeg chromaprint` on a Mac).

```bash
cp .env.example .env   # optional: TELEGRAM_API_ID, TELEGRAM_API_HASH
uv sync
npm --prefix web install
npm --prefix web run build
```

See the [README](README.md#-run-from-a-checkout) for running the app locally.

**Windows.** A Windows checkout works with the same commands (run them in
PowerShell). CI runs `pytest` on Windows as well as Linux, and builds and
smoke-tests the Windows installer, so keep paths and subprocess calls
platform-neutral. Platform branches live behind `sys.platform` checks (see
`src/flackey/config.py` and `src/flackey/tools.py`). Building the installer
yourself is `packaging/build_windows.ps1`.

## Checks

CI (`.github/workflows/checks.yml`) runs these on every PR — run them yourself
before pushing:

```bash
uv run ruff check src tests
uv run lint-imports   # module layering; see [tool.importlinter] in pyproject.toml
uv run pytest -q
npm --prefix web test -- --run
npm --prefix web run build
```

## Opening a PR

- **Label it.** Every PR needs exactly one of `enhancement`, `bug`,
  `documentation`, `chore`, or `ignore-for-release` — the release notes
  generator groups by label. See [`docs/RELEASING.md`](docs/RELEASING.md).
- **Title it for the changelog, not for yourself.** If the PR is labeled
  `enhancement`, `bug`, or `documentation`, its title is pulled verbatim into
  the release notes. Write what changed for the user
  ("Fix downloads silently failing when Soulseek disconnects"), not the
  internal mechanism ("Fix race in retry queue").
- Fill in the PR template's Summary and Test plan.

## Code style

- Python: [ruff](https://docs.astral.sh/ruff/) for linting, module layering
  enforced by `lint-imports` (see `[tool.importlinter]` in `pyproject.toml`).
- No enforced commit message format — keep commit subjects short and in the
  imperative mood ("Fix X", "Add Y"), matching the existing log.
- Match the existing code's style in the file you're touching over introducing
  a new one.

## Reporting security issues

Don't open a public issue for a security vulnerability — see
[`SECURITY.md`](SECURITY.md) for how to report one privately.
