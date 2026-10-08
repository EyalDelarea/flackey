# Releasing Flackey

## Label and title every PR

Release notes come from `gh release create --generate-notes`, grouped by
`.github/release.yml` using each merged PR's **label** and its **title,
verbatim**. Give every PR one of:

- `enhancement` (Features), `bug` (Bug Fixes), `documentation`, `chore`
- `dependencies` (Dependabot sets it; gets its own bucket)
- `ignore-for-release`: left out of the notes entirely

An unlabeled PR still appears, under "Other Changes". The `pr-labels`
workflow flags a PR with no label but does not block it. Titles of
`enhancement`, `bug` and `documentation` PRs are read by users, so write them
as what changed for them ("Added X for Y"), not the mechanism.

## Cut a release

1. Actions tab, workflow **`release`** (`.github/workflows/release-tag.yml`),
   **Run workflow**, `version` = `0.2.3` or `v0.2.3`. There is no `ref`
   input: it always releases `main`.
2. The workflow, authenticated entirely with the **`RELEASE_PAT`** secret
   (a `GITHUB_TOKEN` push would not trigger the checks the release needs):
   - refuses an invalid version or one whose tag already exists;
   - sets the version in `pyproject.toml` and `src/flackey/__init__.py`
     (`packaging/check_version.sh` confirms both match);
   - opens `release/vX.Y.Z` as a PR labeled `ignore-for-release` and turns
     on auto-merge (squash), then waits up to 20 minutes for it to merge
     once its checks pass;
   - tags the merged commit on `main` and pushes `vX.Y.Z`.
   If the tree already has the version, it skips the PR and tags `main`.
3. The tag push runs **`checks`** (`.github/workflows/checks.yml`). After the
   test jobs (ubuntu, macOS, Windows) and the web build pass:
   - `mac` builds `Flackey.app`, smoke-launches it, and packs `Flackey.pkg`
     plus the seamless-update archive `Flackey-X.Y.Z.zip`;
   - `windows` builds and smoke-launches `Flackey-Setup.exe`;
   - `release` signs and publishes them.

## Secrets

| Secret | Used by | For |
| --- | --- | --- |
| `RELEASE_PAT` | `release` | pushing the bump branch and tag, opening and auto-merging the PR |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | `checks`, build steps of `mac` and `windows` | baked into the app's `build.json` **on tag builds only**; main and PR builds are keyless and ask for keys at setup |
| `FLACKEY_UPDATE_SIGNING_KEY` | `checks`, `release` job | Ed25519 private key (64 hex chars) that signs the release |

## Signing and what the app downloads

`packaging/sign_archive.py` writes a detached Ed25519 signature (`.sig`, hex)
for `Flackey-X.Y.Z.zip`, `Flackey.pkg` and `Flackey-Setup.exe`. It signs a
message naming the version and file, so a signature can't be replayed onto
another release or file. `cryptography` is pinned to the version in `uv.lock`.
A missing key or `.sig` fails the release: installed copies refuse unsigned
updates.

Release assets: `Flackey.pkg`, `Flackey.pkg.sha256`, `Flackey.pkg.sig`,
`Flackey-X.Y.Z.zip`, `Flackey-X.Y.Z.zip.sig`, `Flackey-Setup.exe`,
`Flackey-Setup.exe.sha256`, `Flackey-Setup.exe.sig`.

The in-app updater (`src/flackey/web/update.py`) reads the GitHub releases
API, takes the newest release that is neither a draft nor a pre-release, and
downloads only assets under this repo's releases URL:

- **macOS**: `Flackey-X.Y.Z.zip` and its `.sig` for a seamless in-place
  swap; otherwise `Flackey.pkg`, which it opens only if `Flackey.pkg.sig`
  verifies.
- **Windows**: `Flackey-Setup.exe` and its `.sig`.

## Pre-releases

A tag on a commit that is **not on `main`** (say, a branch build for a tester)
is published as a pre-release, not marked latest. The app and the download
site skip pre-releases, so no installed copy is offered code that hasn't
reached `main`.

## Download site

`site/` is deployed to GitHub Pages by the **`pages`** workflow whenever
`site/**` changes on `main`, or on manual dispatch. Releases do not redeploy
it: the site reads the latest release from the GitHub API.

## A bad tag

The `release` workflow never moves an existing tag. Delete it deliberately,
then run `release` again:

```bash
git push origin :refs/tags/vX.Y.Z
git tag -d vX.Y.Z
```
