# Releasing Flackey

## Label every PR

Each PR should carry exactly one release-category label so the notes generator
can group it:

- `enhancement` — new feature or user-facing improvement
- `bug` — bug fix
- `documentation` — docs, site, README changes
- `chore` — everything else (refactor, CI, deps, tooling)

`enhancement` and `bug` are already auto-applied by the feature-request and
bug-report issue templates, so PRs fixing/implementing those issues usually
just need the same label carried over. The `pr-labels` workflow
(`.github/workflows/pr-labels.yml`) flags a PR with none of these labels; it's
advisory and doesn't block merging. An unlabeled PR still shows up in release
notes, just under "Other Changes" instead of its own section.

## Decide the version bump

Flackey uses semver (`MAJOR.MINOR.PATCH`). The bump is a manual call — whether
a change is major, minor, or patch is decided by judgment, not automation.

1. Update the version in **both**:
   - `pyproject.toml` (`version = "X.Y.Z"`)
   - `src/flackey/__init__.py` (`__version__ = "X.Y.Z"`)
   `packaging/check_version.sh` compares both against the release tag and
   fails the build if they disagree.
2. Commit the bump to `main`.

## Cut the release

1. Tag the bump commit and push the tag:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
2. The `checks` workflow (`.github/workflows/checks.yml`) builds and
   smoke-tests the macOS installer, then its `release` job publishes a GitHub
   Release for the tag with `gh release create --generate-notes`.
3. GitHub groups the generated notes using `.github/release.yml` —
   `enhancement`-labeled PRs under "🚀 Features", `bug` under "🐛 Bug Fixes",
   `documentation` under "📝 Documentation", `chore` under "🧹 Chores", and
   anything unlabeled under "Other Changes" — pulled straight from merged PR
   titles and labels since the previous tag.

Because `.github/release.yml` only affects notes for tags created *after* it
lands on `main`, land this file (and label your PRs) before cutting the next
tag, or that release's notes will still come out flat.
