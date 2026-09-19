# Seamless in-place update (issue #58)

Status: approved 2026-09-19; built 2026-09-19. Supersedes nothing; extends the download path added in
#56. [As built](#as-built) records where the code differs from what is written below, and
[Turning it on](#turning-it-on) is the owner's remaining step — until it is done, every build takes the
installer path exactly as it did before.

## The problem

After #56 the Update button downloads `Flackey.pkg` and hands it to Installer.app. That works, but it
is an installer window and an admin password for something the owner already agreed to. This design
replaces that with a swap the app performs on itself.

Removing Installer.app removes the last checkpoint between bytes off the network and code running as
the user, so the payload has to carry its own proof. That proof is an Ed25519 signature verified
against a public key committed in the source — not Apple Developer ID, which was declined and would
cost $99/yr (see `packaging/README.txt`).

## Decisions

| id | decision | choice |
|----|----------|--------|
| D1 | Release assets | Keep `Flackey.pkg` for first install; add `Flackey-<version>.zip` and `Flackey-<version>.zip.sig` for updates |
| D2 | Verification failure | Refuse, show the error, offer the release page. Never auto-fall back to the pkg |
| D3 | Download timing | Only when the user presses Update. No background pre-download |
| D4 | Relaunch | Ask "restart now?" before quitting — the same dialog carries the busy warning |
| D5 | Key generation | Owner generates the keypair locally and pastes the private half into Actions himself |

Settled earlier in the same discussion:

- No Apple Developer ID signing. The app keeps its ad-hoc signature.
- The Ed25519 **public** key is committed to the repo, not injected from a repo secret. It ships inside
  every copy of the app anyway, so a secret would hide nothing; committed, swapping it needs a visible
  commit.
- The helper is a small compiled binary, not a shell script. Only a binary can swap a directory
  atomically.

## Architecture

Four pieces, each independently testable:

**`flackey.update.verify`** — pure function. Takes payload bytes and signature bytes, returns
`True`/`False` against the baked public key. No I/O, no network, no filesystem. Easy to test
exhaustively: valid, tampered payload, tampered signature, empty signature, wrong key, garbage.

**`flackey.web.update`** (existing) — grows a seamless path beside the pkg path. Downloads the zip and
the sig, calls `verify`, unpacks, stages, spawns the helper. Chooses the pkg path when the running app
is not at `/Applications/Flackey.app`.

**`flackey-update-helper`** — the compiled helper. Ships in `Contents/Resources/`, built by one `clang`
line in `build_app.sh`, ad-hoc signed with the bundle. Takes the parent pid, the staging path and the
target path; validates them; waits; swaps; relaunches. Its validation rules are given under
[Helper hardening](#helper-hardening) — they are the security boundary, not a formality.

**`.github/workflows/release-tag.yml`** — additionally builds the zip and signs it with the private key
from `secrets.FLACKEY_UPDATE_SIGNING_KEY`, publishing both as release assets.

## Flow

1. User presses **Update**.
2. App resolves the latest release (unchanged from #56) and downloads `Flackey-<v>.zip` and
   `Flackey-<v>.zip.sig`, bounded by the declared asset size as the pkg download already is.
3. **Verify the signature** against the committed public key. Any failure — mismatch, missing asset,
   malformed signature — refuses here. Nothing is unpacked and nothing is staged.
4. Unpack with `ditto` into `/Applications/.Flackey-staging-XXXXXX.app`. Inside `/Applications` because
   `rename` cannot cross filesystems. Unique name; refuse if the path already exists. Then normalise the
   staged bundle: `xattr -rc` to strip quarantine, `chmod -R go-w` to drop group and world write. Both
   are explained under [Quarantine](#quarantine) and [Permissions on the staged bundle](#permissions-on-the-staged-bundle).
5. Show the restart dialog. If Soulseek transfers are in flight, say how many. The user picks **Restart
   now** or **Install on quit**.
6. At the moment of quitting — not before — copy the helper out of the bundle into a fresh `mkdtemp`
   0700 directory. Deferring this matters for **Install on quit**, which may be hours later: a helper
   left sitting in a temp directory for a whole session is exactly the stray copy Sparkle warns about,
   and `/private/tmp` is swept on its own schedule.
7. Stop slskd, spawn the helper detached, quit.
8. Helper waits for the parent pid to exit, revalidates both paths, then swaps with `renameatx_np(dirfd,
   "<staging basename>", dirfd, "Flackey.app", RENAME_SWAP)` — where `dirfd` is an open descriptor on
   `/Applications`, not `AT_FDCWD`.
9. Helper deletes the old bundle — now sitting at the staging path — relaunches via `open`, removes its
   own temp directory, exits.

## Verification

Ed25519 over the raw bytes of the zip, detached signature as a separate release asset.

This needs **one new dependency**: `cryptography`, for `Ed25519PublicKey.from_public_bytes(...).verify(...)`.
The project has no Ed25519 implementation today — the `rsa` package in the lock file arrives via
telethon and is the wrong algorithm. `cryptography` is the mainstream, audited choice and PyInstaller
bundles it without special handling. Writing our own Ed25519 was considered and rejected: the code that
decides whether to execute a downloaded payload is the last place to hand-roll crypto.

The public key is a constant in `src/flackey/update_key.py`. The private key exists only as a GitHub
Actions secret, used only in the release job, never written to an artifact or a log.

Key rotation is deliberately manual: changing the public key is a commit, which is the property that
makes a silent swap impossible. A lost private key means published updates stop working and users
install the next version by pkg — recoverable, and the reason D5 keeps a copy on the owner's machine.

## Failure handling

The invariant, adopted from the wider practice around self-updating Mac apps:

> On every failure path, `/Applications/Flackey.app` points at a working bundle — the original or the
> new one, never deleted with no replacement.

| failure | result |
|---------|--------|
| Download incomplete or HTTP error | Existing #56 handling: partial discarded, error shown, retry works |
| Signature does not verify | Refuse. Staging never happens. Error names the problem and offers the release page |
| Signature asset missing | Refuse. Treated as a failure, never as "no signature required" |
| Staging path already exists | Refuse, do not overwrite — a planted path is not something to reuse |
| Helper cannot be copied out at quit time | Abort the update, not the quit. Say so, leave the staged bundle for the next attempt; the installed app is untouched |
| App quits but helper dies before the swap | Old bundle is untouched and still at the target path. User reopens Flackey normally |
| Staged bundle still carries `com.apple.quarantine` | Helper refuses to swap. Swapping it in would produce an unopenable app |
| Staging path fails a validation rule — symlink, wrong owner, wrong name, wrong bundle id | Helper refuses and exits without touching the target |
| `RENAME_SWAP` fails with `EPERM`/`EACCES` | Named error pointing at Privacy & Security → App Management. Both paths left as they are, old app relaunched |
| `RENAME_SWAP` fails otherwise | Helper logs errno, leaves both paths as they are, relaunches the old app |
| Relaunch fails | Bundle is already the new version; the user opens it from Applications |

## Quarantine

`ditto` propagates `com.apple.quarantine` from a zip onto every file it extracts. Measured:

```
zip xattrs:              com.apple.quarantine
extracted app xattrs:    [com.apple.quarantine]
extracted binary xattrs: [com.apple.quarantine]
```

A quarantined bundle with an ad-hoc signature is refused outright by Gatekeeper — the "Flackey is
damaged and can't be opened" dialog, with no way past it short of `xattr -d` in a terminal. That would
be an unrecoverable update for a normal user.

`httpx` does not set the attribute, so in the expected path the zip is clean and nothing propagates.
This is defence in depth against the day that changes:

- Strip it: `xattr -rc` on the staged bundle immediately after unpacking.
- Assert it: the helper refuses to swap in a bundle that still carries `com.apple.quarantine`. A
  quarantined staging directory means an assumption broke, and swapping it in would brick the install.

Note this is *not* about where the app was signed. An ad-hoc signature carries no identity at all, so
one produced on a GitHub runner is indistinguishable from one produced locally, and Gatekeeper treats
them identically. The quarantine attribute is the whole mechanism.

## Permissions on the staged bundle

`chmod -R go-w` on the staged bundle before the swap, so only the owning user can write to it.

What this does and does not buy is worth stating precisely, because it is easy to overclaim. Replacing
the bundle wholesale is *already* possible for any process running as an admin user — `/Applications`
is `775 root:admin`, and this design is built on that fact. What the ownership flip from `root:wheel`
to the user actually changes is the ability to edit files *inside* the bundle in place, without
replacing it. `go-w` narrows that to the single account rather than the `admin` group.

It does not affect `DYLD_INSERT_LIBRARIES`, which is gated by the hardened runtime — absent from an
ad-hoc build either way — and not by ownership.

## Helper hardening

The helper performs the one destructive operation in the design, so its input validation is the
security boundary. String checks alone are racy: anything validated by path can be swapped between the
check and the call. The rules, in order:

1. Open `/Applications` once with `O_DIRECTORY | O_NOFOLLOW` and keep the descriptor. Every subsequent
   check and the swap itself are relative to that descriptor, so the kernel never re-resolves the path.
2. `fstatat(..., AT_SYMLINK_NOFOLLOW)` — never `stat` — on both entries. A symlink at either name is a
   refusal, not something to follow.
3. The staging entry must match `.Flackey-staging-[A-Za-z0-9]{6,}.app` exactly, and the target entry
   must be exactly `Flackey.app`. Neither is taken from an argument without being checked against these.
4. `st_uid == getuid()` on the staging directory. A staging path owned by anyone else was not created
   by this app.
5. The staged bundle's `Info.plist` must declare `CFBundleIdentifier` of `com.flackey.app`, and the
   bundle must carry no `com.apple.quarantine`.
6. Only then `renameatx_np(dirfd, staging, dirfd, "Flackey.app", RENAME_SWAP)`.

`EPERM` or `EACCES` from the swap is handled explicitly and reported as a named failure — "macOS blocked
the update; grant Flackey permission under Privacy & Security → App Management" — rather than a silent
failure or a half-cleaned staging directory. Testing on macOS 26.6.2 shows App Management does not
intercept this today (see [Evidence](#evidence)), but that is one machine on one OS version, and Apple
can tighten the rule in a point release. The handler costs nothing and ages well.

## Security

Three of these come directly from Sparkle's own published CVEs.

- **Stray helper pre-planted in the temp directory** (Sparkle notes this in its own source). Fresh
  `mkdtemp` 0700 per run, never a fixed path, refuse if the copy target exists.
- **Symlink at an installer path** (fixed in Sparkle 2.9.5/2.9.6). Unique staging name, refuse if it
  exists, never follow a symlink when unpacking or swapping.
- **Driving the helper — argument injection or a race** (fixed in Sparkle 2.7.3). The helper exposes no
  XPC service and no socket, and validates every path against the rules under
  [Helper hardening](#helper-hardening) before swapping.
- **Privilege escalation via a root helper** — does not apply. Everything runs as the user; there is no
  privileged component. Sparkle's worst bugs were in exactly the component we do not have.
- **Downgrade** — the `_version_tuple` gate from #56 still applies.
- **Forged update trigger from a web page** — already handled in #56 and unchanged here.
  `from_the_app()` (`src/flackey/web/update.py:34`) rejects any request without the `x-flackey-app`
  header, which forces a CORS preflight that no outside origin satisfies. It guards both POST routes.
  The endpoints *without* that guard — `/api/reveal`, `/api/setup/reset`, `/api/telegram/logout` — are
  the ones listed under [Out of scope](#out-of-scope); the update trigger is not among them.

Accepted, not mitigated: after a self-update the bundle is owned by the user rather than `root:wheel`.
Two ownership states will exist depending on how a given user last updated. The pkg fallback runs as
root and works against either.

Not a control we rely on: the secrecy of the public key's location. PyInstaller compiles
`update_key.py` into the PYZ archive inside the executable, so it is not sitting in the bundle as
editable plaintext — but an attacker already running as the user can rewrite the whole bundle anyway.
The key is a source constant rather than a JSON asset like `build.json` because a constant cannot be
changed without a commit, not because the file is hard to reach.

## Testing

Unit, on `verify`: valid signature; tampered payload; tampered signature; truncated signature; empty
signature; signature from a different key. Each must return `False` and none may raise.

Unit, on the staging and helper-spawn logic, with the filesystem faked: refuses an existing staging
path, refuses when not installed to `/Applications`, refuses when the helper is missing.

Helper validation, against real directories in a temp tree — each must refuse and leave the target
untouched: staging path is a symlink; staging owned by another uid; staging name not matching the
expected shape; `CFBundleIdentifier` not `com.flackey.app`; staged bundle carrying
`com.apple.quarantine`. Plus the positive case, asserting the swap happened and the old bundle is at
the staging path afterwards.

Manual, on a real Mac — the part unit tests cannot prove:

1. Install 0.1.6 from the pkg. Confirm the bundle is `root:wheel`.
2. Publish a test release of 0.1.7 with a valid signature. Press Update, accept the restart, confirm the
   app relaunches showing 0.1.7 and that `/Applications/Flackey.app` is a complete bundle.
3. Publish a release whose `.sig` does not match. Confirm the app refuses, says so, and is still 0.1.7.
4. Delete the `.sig` asset. Confirm the app refuses rather than proceeding unsigned.
5. Start a Soulseek transfer, press Update, confirm the dialog reports it.
6. Confirm slskd is not orphaned after the restart.
7. Confirm the updated bundle carries no `com.apple.quarantine` and that a second update from 0.1.7
   works — i.e. the swap over a user-owned bundle behaves the same as over the `root:wheel` one.
8. On a Mac that has never built or run Flackey, install from the pkg and update. This is the only
   configuration that exercises Gatekeeper with no local history to fall back on.

## Out of scope

- Apple Developer ID signing and notarization.
- The CSRF shape on `/api/reveal`, `/api/setup/reset`, `/api/telegram/logout` noted during the #56
  review. Its own issue.
- Background or automatic updates (D3).

## As built

Seven places the code differs from the design above. Everything else was built as written.

| # | design said | built as | why |
|---|-------------|----------|-----|
| 1 | `.github/workflows/release-tag.yml` builds and signs the zip | `.github/workflows/checks.yml` | `release-tag.yml` only bumps the version and pushes the tag; the assets are published by the `release` job in `checks.yml`. The zip is built in the `mac` job, which is the only one with a built `.app`, and signed in the `release` job — the one step that needs the private key is not the step that runs npm install and PyInstaller |
| 2 | `flackey.update.verify`, `src/flackey/update_key.py` | `src/flackey/selfupdate/` — `signature.py`, `key.py`, `install.py` | `flackey/verify.py` already exists and means audio verification. One package is also one entry in the import-linter layer list, which is `exhaustive = true` |
| 3 | unpack straight into `.Flackey-staging-XXXXXX.app` | unpack into a temporary directory, then rename the bundle out of it | `ditto -x -k` of a `--keepParent` archive writes `<dest>/Flackey.app`, so the staging name has to be applied by a rename afterwards |
| 4 | helper takes the staging and target paths | helper takes the containing directory and the staging *name*; the target name is compiled in | One path this program is allowed to overwrite, and it is not negotiable from the command line. The directory stays an argument so the helper can be tested against a temp tree rather than against `/Applications` |
| 5 | helper relaunches | helper takes a relaunch flag | Somebody who chose "install on quit" asked for the app to go away. Reopening it for them a second later is not what they asked for |
| 6 | — | helper also refuses a bundle with no `Contents/MacOS/Flackey` | The invariant is that the installed path points at a *working* bundle. A directory with no executable satisfies every other rule and still leaves an app that cannot open |
| 7 | — | `Info.plist` is searched, not parsed | Linking CoreFoundation into a helper that has to stay trivially auditable, to read two strings, is a bad trade. This is an identity check; the security work is done by the no-symlink, owned-by-us and generated-name rules around it |

The signature asset is hex text rather than raw bytes, so it survives a copy-paste and reads back in a
terminal. An asset that is missing, oversized, not hex, or the wrong length is refused identically — all
four are "there is no signature here", which the design requires never to be read as "no signature
required".

### What the tests cover, and what they do not

`tests/test_selfupdate_signature.py`, `tests/test_selfupdate_install.py`,
`tests/test_update_helper.py` and `tests/test_web_seamless_update.py` cover every row of the failure
table that can be reached without a published release, including a real `RENAME_SWAP` against a real
directory tree.

Two things they cannot reach, stated plainly rather than implied:

- **Rule 4, the hostile half.** Handing the staging directory to another uid needs root. The test asserts
  the positive case and says in its own name that it does not assert the refusal.
- **Everything under [Testing](#testing) marked manual.** An app replacing its own bundle while running
  needs a real Mac and a real signed release. Nothing in CI proves it.

## Turning it on

Until this is done, `PUBLIC_KEY_HEX` in `src/flackey/selfupdate/key.py` is empty, every build reports
`seamless: false`, and the Update button opens Installer.app exactly as it did before. That is the
intended resting state, not a broken one — an unset key is a build that cannot do seamless updates,
which is deliberately not the same thing as a payload that failed to verify.

1. Generate the keypair locally (D5 — the private half should not be born on a CI runner):

   ```
   uv run python -c 'from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; \
   from cryptography.hazmat.primitives import serialization as s; k = K.generate(); \
   print("private:", k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex()); \
   print("public: ", k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex())'
   ```

2. Private half → the `FLACKEY_UPDATE_SIGNING_KEY` repository secret, and a password manager. Losing it
   does not break installed copies; it means published updates stop verifying until a new public key is
   committed, and people install by pkg until then.
3. Public half → `PUBLIC_KEY_HEX` in `src/flackey/selfupdate/key.py`, and delete
   `test_no_key_is_baked_into_the_source_yet` in `tests/test_selfupdate_signature.py`. That test exists
   so this cannot happen by accident: baking a key takes a deliberate edit and shows up in a diff.
4. Cut a release. `packaging/sign_archive.py` prints the public key it derived from the secret, so a
   mismatch between the secret and the committed key is visible in the release log rather than only as
   "this update could not be verified" on somebody's Mac.
5. Work through [Testing](#testing)'s manual list. Step 8 — a Mac that has never built or run Flackey —
   is the only configuration that exercises Gatekeeper with no local history to fall back on.

## Evidence

Measured on macOS 26.6.2, Apple Silicon, 2026-09-18/19:

- `/Applications` is `775 root:admin`; the owner is in `admin(80)`. Renaming an entry needs write on the
  directory, not the entry, so a `root:wheel` bundle can be replaced without admin rights.
- An ad-hoc signed app renamed the real `/Applications/Flackey.app` and renamed it back: `rc=0`, no
  prompt, no password. App Management does not protect ad-hoc bundles — `TeamIdentifier=not set`.
- A compiled helper, ad-hoc signed, copied to a `mkdtemp` 0700 directory and spawned detached,
  reparented to launchd (`ppid=1`), outlived its parent, and completed `RENAME_SWAP` with `rc=0`.
- The shell equivalent also worked, but needs two renames with a window where the target does not
  exist — which is why the helper is compiled.
- `ditto` propagates `com.apple.quarantine` from a zip onto every extracted file, including the app
  bundle and its executable. This is why the staging step strips it and the helper asserts its absence.

Measured 2026-09-19, while building this:

- The normalisation steps do not break the thing they are applied to. An ad-hoc signed bundle taken
  through the whole pipeline — `codesign --deep --sign -`, `ditto -c -k --keepParent`, `ditto -x -k`,
  `xattr -rc`, `chmod -R go-w` — still passes `codesign --verify --deep --strict`. Worth measuring
  rather than assuming: `xattr -rc` clears *every* extended attribute, and signatures for non-Mach-O
  files can live in `com.apple.cs.*` xattrs. In a bundle they do not — the main executable carries its
  signature inside the Mach-O and everything else is hashed into `Contents/_CodeSignature/CodeResources`.
- `ditto -x -k` of a `--keepParent` archive writes `<dest>/Flackey.app`, and symlinks inside the bundle
  survive the round trip. Both are why the archive is made with `ditto` and unpacked into a directory of
  its own; `zip` flattens the symlinks and produces a bundle that will not launch.
