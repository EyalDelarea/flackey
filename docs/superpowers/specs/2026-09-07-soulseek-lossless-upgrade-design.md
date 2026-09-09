# Soulseek lossless upgrade — Design Spec

Date: 2026-09-07
Status: Approved by the owner on 2026-09-07 after an independent review
Branch: `feat/soulseek-spike`
Background: `docs/research/2026-09-06-soulseek-as-audio-source.md` (desk research) and
`docs/research/2026-09-07-soulseek-spike-findings.md` (live measurements).

## 1. Purpose

Give every request a chance to be filed as a lossless AIFF instead of a 320 kbps MP3, by asking
the Soulseek network for the track right before the Deezer download, without making the existing
flow slower to fail or less reliable. Deezer stays the baseline; Soulseek is an upgrade attempt
with a hard time budget and a fallback on every miss.

Success looks like: the owner sends a link as today, and a few minutes later finds
`~/Music/DJ Library/<Artist>/<Artist> - <Title>.aiff`, tagged from Beatport with artwork, verified
lossless, with the request's Done line saying it came from Soulseek. When Soulseek has nothing usable
the owner sees exactly what they see today.

## 2. Decisions already taken (owner, 2026-09-07)

- slskd runs as a separately managed sidecar on the Mac; flackey talks to its REST API.
- Soulseek is tried first, silently; no prompt to the owner. On any miss, the Deezer MP3.
- 60 s cap to first byte, then a total transfer cap. Both are settings.
- The whole DJ Library is shared read-only through slskd (the owner accepts uploading).
- Lossless files are downloaded as FLAC (what the network has) and filed as AIFF (loads on every
  CDJ generation, carries ID3 tags and artwork). The target format is a setting.
- The matching gate is a rule pipeline with a stored report, so it can be tuned and replayed.

## 3. Scope

### In scope

- `SlskdClient` adapter: search to completion, read responses, enqueue one file, poll one
  transfer, cancel, request a share rescan, health check.
- `lossless` domain module: gate rules, rankers, policy, pick report. Pure, no I/O.
- Worker upgrade step inside the fetch stage, with caps and fallback.
- Same-recording check with Chromaprint against the Deezer preview, after verify.
- Lossless conversion to the configured filing format after the checks, before tagging.
- Persistence: per-request attempt row with pick report and event timeline, raw slskd
  responses on disk, `tracks.source`, `tracks.source_fmt`, `tracks.bit_depth`, `tracks.sample_rate`.
- Surfacing: the request's Done line, request detail in the web API, `/api/health`, attempt
  listing in the API and CLI, structured log lines (section 17).
- `crate lossless replay <request id>` to rerun a stored report under the current policy.
- Settings, `.env.example`, README section on setting up slskd.
- Tests at every layer, recorded slskd fixtures.

### Out of scope (this phase)

- Soulseek when Deezer has nothing (bootlegs, edits). The flow only runs after a Deezer candidate
  is chosen.
- Upgrading tracks already filed as MP3.
- Launching or supervising slskd from `crate start`, and a `docker-compose.yml` for the pair
  (the decision in section 2 is a native sidecar on the Mac).
- A transfers panel in the web UI; slskd's own UI at `http://127.0.0.1:5030` covers it.
- A "wait or take MP3" prompt in the UI.
- Non-Pioneer players. Files above 48 kHz: the gate accepts 44.1 and 48 kHz only (CDJs play
  nothing higher), so `plausible_size` bounds are FLAC 400 to 2500 kbps and WAV/AIFF 1400 to
  4700 kbps, which covers 24-bit/48 kHz. Bit depth is passed through.

## 4. User-facing behavior

### 4.1 Normal case

Nothing changes until the fetch stage. The request passes through identify, Beatport match,
Deezer search, decide, duplicate check and review exactly as today. When the worker is about to
fetch the chosen Deezer candidate and Soulseek is enabled, the request stays in `FETCHING` while:

1. slskd is asked for the track, using the Beatport artist, title, mix name and duration when a
   catalog record exists, otherwise the candidate's fields.
2. The gate picks the best lossless file, or nothing.
3. The file is downloaded with the caps in section 8.
4. The downloaded file is verified with the existing spectral check.
5. Its fingerprint is compared with the Deezer preview of the matched track (section 16.1).
6. It is converted to the filing format, tagged, filed, and the request is `DONE`.

The Done line states the format the owner now owns and where the audio came from, in that
order, before the verify detail:

```
Done: Goasia – Love & Peace (Original Mix) · 8:19 · AIFF 16-bit/44.1 kHz, from FLAC via Soulseek · content to 22050 Hz · Goa Trance / Suntrip Records · 96%
Done: Goasia – Love & Peace (Original Mix) · 8:19 · MP3 320 kbps via Deezer · content to 19.5 kHz · Goa Trance / Suntrip Records · 96%
```

The web request detail and the library view show the same `format` block: filed format, bit
depth, sample rate, source format, source, and for Soulseek the peer. A 24-bit master reads
`AIFF 24-bit/44.1 kHz`. The owner is not asked anything during the attempt (owner decision); the
request detail shows `fetching: soulseek` or `fetching: deezer` while it runs.

### 4.2 Miss cases

Every miss logs a one-line reason, records it in the pick report, and continues with the Deezer
fetch. The owner is not notified of the miss; the Done line says `MP3 320 kbps via Deezer`. Misses:

- slskd unreachable or not logged in to the server.
- No search result survives the gate.
- The chosen peer does not start sending within the first-byte cap.
- The transfer does not finish within the total cap, or ends in any state other than succeeded.
- The file fails verify (lossless container with a lossy cutoff).
- The fingerprint does not match the Deezer preview (wrong recording).
- Conversion fails.

Only failures that happen *after* a Soulseek file was filed can reach the owner as errors, and
those are the same filing errors that exist today.

### 4.3 Where the owner sees what happened

- The request's Done line (web UI and log): format and source.
- Web request detail: a `soulseek` block with the pick summary (files seen, rejected per gate,
  chosen peer and file, transfer timings, outcome) and the policy values used.
- `/api/health`: `soulseek: "off" | "ok" | "unreachable" | "not_logged_in"` and
  `fpcalc: "ok" | "missing"`, plus the last 24 h attempt counts by outcome.
- `GET /api/lossless/attempts`: recent attempts with outcome, chosen peer, first-byte and total
  times; each request's detail carries its attempt with the timeline. This is what a GUI panel
  reads later.
- Library and stats: `tracks.source` and `tracks.source_fmt`, so "how many AIFFs came from
  Soulseek" is a query.
- `crate lossless replay <request id>`: prints the stored report re-evaluated against the current
  policy, and the differences.

## 5. Architecture

### 5.1 Components

| Module | Layer | Responsibility |
|---|---|---|
| `source/lossless.py` | adapter | `LosslessProvider` protocol: `name`, `health()`, `search(ref) -> list[LosslessFile]`, `download(file, dest_dir, on_event) -> Path`, `cancel()`. What the worker talks to. |
| `source/slskd.py` | adapter | `SlskdClient` (HTTP client for slskd: URLs, JSON shapes, states) and `SoulseekProvider`, the first `LosslessProvider`. |
| `lossless.py` | domain | `LosslessFile`, `Reference`, gate rules, rankers, `PickPolicy`, `pick()` returning a `PickReport`. Pure. |
| `convert.py` | adapter | ffmpeg conversion FLAC to AIFF/WAV/FLAC passthrough. |
| `fingerprint.py` | adapter | `fpcalc` wrapper (subprocess with a 60 s timeout, run in a thread like `verify`) plus the pure sliding comparison; preview download. |
| `deezer.py` | adapter | `DeezerTrack.preview_url` added to the parsed track. |
| `worker.py` | worker | The upgrade step, caps, fallback, moving the file, recording the report. |
| `store.py` | adapter | New table and columns, `_ensure_column` migration helper. |
| `web/requests.py`, `web/__init__.py` | web | Expose the report and health. |
| `cli.py` | cli | `lossless replay`. |

Layering stays `cli -> app -> web/inbox -> worker -> adapters -> domain -> models`. `lossless.py`
imports only `models`. `source/slskd.py` imports `lossless` for its result dataclass. The worker
holds a list of providers (one today) and depends only on the protocol, so a second network is a
new module under `source/` plus its settings; the gate, caps, attempts, evidence, CLI and API
are shared.

### 5.2 Data flow in the worker

```
_fetch_verify_file(req, cand, catalog)
  dup check (unchanged)
  FETCHING
  verdict = None
  if lossless enabled and no attempt row for req with outcome != "unavailable":
      got = await self._try_lossless(req, cand, catalog)     # (path, verdict) or None on any miss
  if got is None:
      tmp = await self.source.fetch(cand, tmp_dir)           # Deezer, unchanged
  else:
      tmp, verdict = got
  _verify_and_file(req, cand, catalog, tmp, verdict=verdict, source=...)
```

`_verify_and_file` gains an optional `verdict`: when given, the `VERIFYING` step is skipped
(the file was verified inside the attempt) and the verdict is used as is. The `finally:
tmp.unlink` in `_fetch_verify_file` still removes exactly one file, because `_try_lossless`
returns only the converted file and deletes the FLAC itself.

`_try_lossless` in order: insert the attempt row; build `Reference`; `provider.search()` until
completed; `pick()`; record report; `provider.download()` with the first-byte and total caps;
move the file into `tmp_dir` as `req<id>-lossless.<ext>`; verify (in a thread, as today);
fingerprint against the Deezer preview; convert to the filing format and re-probe; unlink the
FLAC; update the attempt row; return `(converted path, verdict)`. Every step appends an event to
the attempt timeline (section 17). Any miss at any step cancels the transfer if one is open,
deletes whatever landed in `tmp_dir`, records the outcome, logs, and returns `None`. Nothing
raises out of `_try_lossless`, so no `LosslessMiss` exception crosses a function boundary.

Re-entry: a Deezer failure after a miss goes through `_retry_or_fail` and re-enters `_process`
with the candidate already chosen (`worker.py:176`). The attempt-row check above makes the retry
skip Soulseek, so a request never runs the 11-minute attempt more than once; only an
`unavailable` outcome (sidecar down) is retried with the request. Any exception from the adapter or a
cap expiry cancels the transfer, records the outcome, logs, and returns `None`.

A Soulseek file that fails verify or the fingerprint check is a miss (fallback, not
`REJECTED`). No rejection row is written, because the web UI shows the latest rejection on the
request and deletes its spectrogram with the request; the spectrogram path is stored on the
attempt row instead.

## 6. The gate

### 6.1 Types

```python
@dataclass(frozen=True)
class LosslessFile:
    provider: str                 # "soulseek" today; the adapter that produced it
    username: str; path: str; extension: str; size: int
    length_s: int | None; bitrate_kbps: int | None; sample_rate: int | None; bit_depth: int | None
    has_free_slot: bool; upload_speed_bps: int; queue_length: int

@dataclass(frozen=True)
class Reference:
    artist: str; title: str; mix_name: str | None; duration_s: int | None

@dataclass(frozen=True)
class PickPolicy:
    lossless_extensions: frozenset[str] = frozenset({"flac", "wav", "aiff", "aif"})
    duration_tolerance_s: int = 3
    title_ratio: int = 90          # rapidfuzz partial token ratio, title only, after normalisation
    require_artist: bool = False   # artist appears in path (file or folder); off by default
    max_queue_length: int | None = None
    banned_users: frozenset[str] = frozenset()
```

### 6.2 Rules

A rule is a class with a `name` and `check(file, ref, policy) -> str | None` (a rejection reason
or `None` for pass). Gates, in order, each cheap to evaluate:

| Name | Rejects when |
|---|---|
| `extension` | extension not in `lossless_extensions` |
| `has_length` | `length_s` is `None` |
| `plausible_size` | bytes do not fit `length_s` at 400 to 2500 kbps for FLAC or 1400 to 4700 kbps for WAV/AIFF, or `sample_rate` is known and above 48000 (see 16.1) |
| `duration` | `abs(length_s - ref.duration_s) > duration_tolerance_s` (passes if the reference has no duration; the note says so) |
| `title` | the file name is split on ` - ` after stripping a leading track number (`02. `, `09-`), separators (`_`, `--`) and a trailing hash; the last segment, with its parsed version and the artist's tokens removed, is the file's title. `token_set_ratio` of the normalised reference title against it is below `title_ratio`. Measured on the spike's 467 lossless names on 2026-09-07: `token_set_ratio` keeps every correct pick, including album titles such as "Still Dreaming (Anything Can Happen)" and "orphic_thrench_remastered", where `token_sort_ratio` at 90 loses 35; the superset case that `token_set_ratio` lets through (a remix or other version of the same title) is exactly what the `version` rule rejects next, and the fingerprint settles identity. |
| `version` | `identify.parse_version` is run on the file's title segment; its result plus any title tokens that are not in the reference title are checked for version words (`remix rmx mix edit version dub rework bootleg mashup live instrumental acoustic vip remixed`). For an original reference, any version word without "original" rejects; for a remix reference, the distinguishing words of the mix name (the remixer) must all appear. The spike showed `parse_version` alone misses "(Rmx)", "Oliver Lieb remix" and similar unbracketed forms, hence the token check. |
| `artist` | `require_artist` and normalised first artist absent from the whole path |
| `queue` | `max_queue_length` set and exceeded |
| `banned_user` | username in `banned_users` |

Rankers return a sort key; survivors are sorted by the tuple in this order:

| Name | Prefers |
|---|---|
| `free_slot` | `has_free_slot` |
| `bit_depth` | 16 over 24 (CD master, smaller), unknown last |
| `queue_length` | shorter |
| `upload_speed` | faster |
| `size` | smaller among equals |

The title rule is a cheap filter against downloading the wrong track, not the identity check;
the fingerprint (section 16.1) is. Measured ratios that set the default of 90: "Still Dreaming
(Anything Can Happen)" against "Dreaming (Anything Can Happen)" scores 90.3 and passes, which is
right, because the fingerprint test on 2026-09-07 proved it is the same recording under the album
title; a one-letter typo scores 97; "Love & Peace (Goasia Remix)" against "Love & Peace" scores
68 and fails. `token_set_ratio` and `partial_ratio` score 100 for any superset and are not used.

If the chosen file fails verify or the fingerprint, or the peer will not send it (the transfer
is rejected or no first byte arrives inside the cap), and the total budget has more than
`lossless_first_byte_s` left, `_try_lossless` moves to the next ranked survivor
(`lossless_max_picks`, default 4) before giving up; every pick appears in the timeline. A refused
transfer is a fact about the peer, not the file, so it must not end an attempt that still has good
survivors: measured on 2026-09-09, request 1 ("Hallucinogen - LSD") saw 119 files, kept 6, chose a
free-slot FLAC, and the peer answered `Completed, Rejected` after two seconds -- under the old rule
(`verify_failed`/`fingerprint_failed` only) the whole attempt ended there and the track stayed at
the 320 kbps Deezer baseline. `transfer_timeout` stays out: bytes were flowing and merely too slow,
which the next peer on the same link is unlikely to improve.

### 6.3 Report

```python
@dataclass
class Rejection: file: LosslessFile; rule: str; reason: str
@dataclass
class PickReport:
    reference: Reference; policy: PickPolicy; seen: int
    rejections: list[Rejection]; survivors: list[LosslessFile]  # ranked
    chosen: LosslessFile | None
    summary: str   # "14 files: 9 duration, 2 title, 1 extension; chose loginty (slot, q0)"
```

`pick(files, ref, policy) -> PickReport`. The report is serialisable to JSON and back, which is
what the replay command relies on. `lossless.py` imports `rapidfuzz` like `match.py` does; that
is the only dependency beyond `models` and `identify`.

## 7. slskd adapter

`SlskdClient(base_url, api_key, http: httpx.AsyncClient)`, methods:

| Method | slskd call | Notes |
|---|---|---|
| `health()` | `GET /api/v0/application` | returns server state and login; 5 s timeout |
| `search(text, *, idle_ms=5000, response_limit=100, wait_s=30)` | `POST /api/v0/searches`, poll `GET /searches/{id}` until state contains `Completed`, `GET /searches/{id}/responses`, `DELETE` | `searchTimeout` is milliseconds; retry `POST` on 429 at 1 s intervals within the same `wait_s` budget; file lists are empty until completed |
| `enqueue(file)` | `POST /api/v0/transfers/downloads/{username}` with `[{filename, size}]` | exact size, backslash path; returns transfer id |
| `transfer(username, id)` | `GET /api/v0/transfers/downloads/{username}/{id}` | state, `bytesTransferred`, `size`, `averageSpeed` |
| `cancel(username, id)` | `DELETE .../{id}?remove=true` | best effort |
| `rescan_shares()` | `PUT /api/v0/shares` | called once at worker start and at most once a day after a filing, not per track: a scan walks the whole library |

Errors map to `SlskdUnavailable` (connection, 5xx, not logged in) and `SlskdError` (anything
else). The adapter never raises through the worker: `_try_lossless` catches both.

The transfer object carries no local path (checked against the live instance on 2026-09-07).
slskd writes a completed file to `<downloads>/<last segment of the remote folder>/<file name>`.
The adapter resolves that path, requires the resolved real path to be inside
`slskd_downloads_dir` (a peer string with `..` or a drive letter fails the check and the attempt
ends `transfer_failed`), and the worker moves it into `tmp_dir` under the fixed name
`req<id>-lossless.<ext>`, never under the peer's file name. The slskd downloads directory is a
setting; its default is `<data_dir>/slskd/downloads`.

## 8. Caps and timing

| Setting | Default | Meaning |
|---|---|---|
| `lossless_search_wait_s` | 30 | give up on a search that has not completed |
| `lossless_first_byte_s` | 60 | from enqueue until `bytesTransferred > 0` |
| `lossless_transfer_s` | 600 | from enqueue until `Completed, Succeeded` |
| `lossless_poll_s` | 2 | transfer poll interval |

A poll that reports `bytesTransferred` above the advertised size cancels the transfer and ends
the attempt `transfer_failed`; `plausible_size` only checks what the peer advertised.

The worker processes one request at a time, so the worst case a Soulseek attempt adds to a
request is search wait plus first-byte cap plus transfer cap, about 11.5 minutes, before the
Deezer path runs. The spike measured 20 to 200 s for successful downloads. Retry and backoff for
the Deezer path are unchanged; a Soulseek miss never consumes a retry attempt.

## 9. Conversion and filing

- `convert.to_format(src, fmt) -> Path` runs ffmpeg with `-vn -map 0:a -map_metadata -1 -c:a pcm_s16be` for AIFF,
  `pcm_s16le` for WAV, at the source sample rate and bit depth (24-bit sources become `pcm_s24be`
  or `pcm_s24le`). FLAC target is a rename. Output goes next to the source in `tmp_dir`.
- Verified locally on 2026-09-07 on the six spike downloads: decoded PCM and sample counts of
  FLAC, AIFF and WAV identical before and after tagging. Rekordbox 7 analysed the AIFF, the FLAC
  and the library MP3 of each track to the same BPM, key and length, and read title, artist, album,
  label, genre, year, artwork and comment from the AIFF like from the MP3. The WAV lost label,
  artwork and comment, which is why WAV is not the default.
- Source bit depth is kept (a 24-bit master files as a 24-bit AIFF); no dither to 16-bit.
- Conversion runs inside `_try_lossless` after verify and fingerprint. The worker re-probes the
  converted file so `Verdict.fmt`, bitrate, bit depth and sample rate describe the AIFF; the
  cutoff comes from the FLAC verify and is kept. The FLAC is deleted right after a successful
  conversion, so at most one temporary file exists when `_verify_and_file` runs.
- ffmpeg runs with `-map_metadata -1` as well as `-vn -map 0:a`: `-vn` drops the picture stream
  but ffmpeg copies container metadata by default, and peer tags must not survive.
- `write_tags` already handles AIFF and WAV through ID3 and FLAC through Vorbis comments. The
  comment field gains `via <provider>`.
- `final_path` is unchanged; the extension follows the filed file.
- Setting `lossless_filing_format`: `aiff` (default), `wav`, or `flac`.

## 10. Configuration

New `Settings` fields, all optional, read from `.env` and, for the ones marked, `settings.json`:

| Field | Default | File key |
|---|---|---|
| `slskd_url` | `http://127.0.0.1:5030` | yes |
| `slskd_api_key` | `None` (Soulseek off when unset) | yes |
| `slskd_downloads_dir` | `<data_dir>/slskd/downloads` | yes |
| `lossless_filing_format` | `aiff` | yes |
| `lossless_search_wait_s`, `lossless_first_byte_s`, `lossless_transfer_s`, `lossless_poll_s` | 30, 60, 600, 2 | no |
| `lossless_duration_tolerance_s`, `lossless_title_ratio`, `lossless_require_artist`, `lossless_max_queue` | 3, 90, false, unset | no |
| `lossless_fingerprint_min` | 0.90 | no |
| `lossless_max_picks` | 4 | no |

`Settings.soulseek_enabled` is `bool(slskd_api_key)`; the provider list is built from whichever
providers have credentials, and `lossless_enabled` is "at least one". `save_settings` in
`config.py` special-cases only `library_root` as a `Path`; it is generalised to convert any
`Path`-typed field, which `slskd_downloads_dir` needs. The API key is never logged or returned by
the API.

## 11. Data model changes

- No candidate rows for Soulseek picks. The chosen peer and file live on the attempt row, which
  the request detail shows. Writing candidates would duplicate rows on every retry (the case
  `test_fetch_failure_backs_off_without_duplicating_candidates` guards) and leak into the review
  menu.
- `tracks.source TEXT NOT NULL DEFAULT 'deezer_bot'` (the provider name; `soulseek` for this
  phase), `tracks.source_fmt TEXT` (format as downloaded, `flac` for a converted file),
  `tracks.bit_depth INTEGER`, `tracks.sample_rate INTEGER`. `Probe` and `Verdict` gain
  `bit_depth` and `sample_rate` from ffprobe so the Done line and library view have the data
  they show. Existing rows get NULLs; the library view falls back to the file's extension.
- `requests.fetch_source TEXT` (nullable): `soulseek` or `deezer` while `FETCHING`, cleared after;
  this is what the request detail shows as `fetching: soulseek`.
- New table `lossless_attempts(id, request_id, provider, created_at, query, report_json, outcome,
  timeline_json, fingerprint_json, spectrogram_path, first_byte_ms, total_ms, raw_dir)`.
  The row is inserted at the start of the attempt (so log lines carry its id) and updated at
  the end. `outcome` is one of
  `filed`, `no_pick`, `first_byte_timeout`, `transfer_timeout`, `transfer_failed`,
  `verify_failed`, `fingerprint_failed`, `convert_failed`, `unavailable`, `interrupted`.
  `spectrogram_path` is a column so a fallback keeps its spectrogram without a rejection row.
  `fingerprint_json`
  holds score, offset and both raw fingerprints; `raw_dir` points at the on-disk raw responses
  (section 17).
- `Store._ensure_column(table, column, ddl)` reads `PRAGMA table_info` and issues `ALTER TABLE`
  once; called from `__init__` after the schema script. This is the repo's first migration and
  the pattern for later ones.

## 12. Error handling

- Adapter errors and cap expiries: log at INFO with the outcome, record the attempt, fall back.
- slskd unreachable: health reports it; the worker checks health once per request before
  searching so a down sidecar costs one failed HTTP call, not a 30 s wait.
- Restart during an attempt: `reset_inflight` requeues a `FETCHING` request while slskd keeps
  transferring. At worker start the adapter cancels every download that is not completed and the
  store marks attempt rows without an outcome as `interrupted`; the requeued request then runs a
  fresh attempt (an `interrupted` outcome does not block re-entry, unlike a real miss).
- A cancelled transfer that later completes anyway leaves a file in slskd's downloads folder;
  the worker ignores it and a note in the README says the folder can be emptied.
- A verify rejection of a Soulseek file writes no rejection row (section 5); the attempt row keeps
  the spectrogram path and the verdict reason in its timeline, and the request continues on Deezer.
- The worker never raises out of `_try_lossless`; `SourceUnauthorized` semantics are untouched.

## 13. Testing

- `lossless.py`: one test per rule with hand-built files, the spike's "Still Dreaming" (passes) and
  "no length" cases, ranker order, report summary text, JSON round trip. Property-style test that
  `pick` never chooses a rejected file.
- `source/slskd.py`: respx against fixtures recorded from the live instance on 2026-09-07
  (application, search in progress with empty files, search completed, responses, enqueue,
  transfer states, 429 on concurrent search).
- `convert.py`: converts a short generated FLAC, asserts decoded PCM hash equality, 24-bit path.
- `fingerprint.py`: the six raw fingerprint pairs recorded on 2026-09-07 as fixtures; correct
  pairs score above 0.95, every cross pair below 0.80; missing `fpcalc` yields `skipped`.
- Timeline: every worker test asserts the attempt row's outcome and that the timeline ends with
  the matching event.
- Re-entry: a miss followed by a Deezer failure requeues the request; the retry runs no second
  attempt and writes no candidate row.
- Every outcome leaves `tmp_dir` empty (FLAC and converted file both gone).
- First-byte, total and search-wait expiry each assert `cancel` or `DELETE` on the fake provider.
- The path-containment guard rejects a reported path that resolves outside the downloads dir.
- Conversion drops foreign tags and an embedded picture, not only PCM equality.
- Fingerprint `skipped` paths: preview `429` after retries, `fpcalc` missing; the track is still
  filed and the evidence row says `skipped`.
- `/api/settings`, `/api/health` and the log never contain `slskd_api_key`.
- Caps use an injectable clock; no real sleeps in tests.
- Restart: an attempt without an outcome becomes `interrupted` and the fake provider's cancel is
  called for open downloads.
- `worker.py`: fake `SlskdClient` covering filed, no pick, first-byte timeout, transfer failure,
  verify failure, convert failure, slskd down; each asserts the Deezer fetch ran and the attempt
  row and `tracks.source` are right.
- `cli.py`: replay prints the same chosen file for the stored policy and a different one for a
  tightened policy.
- `import-linter` contracts extended for the new modules.

## 14. Repository and operations

- `.env.example`: the new keys with comments.
- `README.md`: "Lossless via Soulseek" section: install slskd, config file shape (no secrets),
  what is shared, port forward note, how to check health, how to empty the downloads folder.
- The spike scripts under `docs/research/spikes/soulseek/` stay as they are.

## 15. Open items for the owner

1. Forward TCP 50300 on the router so peers can reach the share; searching works without it.
2. Whether slskd should be added to login items or a launchd plist now, or stay manual.
3. Whether `lossless_require_artist` should default on once the artist rule has fixtures from
   a few weeks of attempts.
4. Later: a `sources` setting (for example `soulseek, deezer_bot`) where each entry is active only
   when its credentials are set, so Telegram becomes optional once the attempt data shows how
   often Soulseek covers requests. A request with no source left ends as not found. Telegram is
   today only the transport for the Deezer bot; the inbox and Done line are already in the web UI.

## 16. Trust and defences against peer-supplied files

Two separate problems. Nothing from a peer is trusted until it has passed the checks below.

### 16.1 Is it the right recording, and really lossless?

| Layer | What it proves | Where |
|---|---|---|
| Gate: duration, title, version | The peer's own metadata says it is this track. A filter, not proof. | `lossless.py` |
| Size sanity (gate `plausible_size`) | Bytes fit the reported duration at 400 to 2500 kbps for FLAC, 1400 to 4700 kbps for WAV/AIFF. Rejects truncated or absurd files before any download. | `lossless.py` |
| Verify | Decoded audio has content above 20 kHz: a genuine lossless source, not a lossy transcode. | `verify.py`, unchanged |
| Same-recording check (`fingerprint.py`) | Chromaprint fingerprint of Deezer's 30 s preview for the matched candidate, slid along the fingerprint of the downloaded file; the best window must agree on at least `lossless_fingerprint_min` of its bits (default 0.90). Measured on the six spike downloads: correct pairs score 0.97 to 0.99, wrong tracks 0.53 to 0.76, so the threshold sits well inside the gap. Costs the preview download (about 400 kB) and under a second of CPU. The preview is trusted because it comes from Deezer's copy of the recording we already matched, not from the peer. | `fingerprint.py` (adapter over `fpcalc`), called by the worker after verify |

Deezer's public API allows 50 requests per 5 s per IP. The check costs one `GET /track/{id}` and
one preview download per request, the worker runs one request at a time, and the identify stage
already makes comparable calls today, so the limit is not reachable. The preview URL is signed and
expires after a few hours, so it is fetched when the check runs, not stored. A `429` or `5xx` is
retried three times with a short backoff; if the preview still cannot be fetched the check records
`skipped` in the attempt row and the evidence table, the file is still filed (verify already
passed), and the track does not get the verified-recording mark. This keeps a Deezer outage from
blocking a lossless file while leaving a trace that the check did not run.

A file that passes verify but not the fingerprint check falls back to Deezer like any other miss.
Its attempt row stores the score, the offset and both raw fingerprints so a threshold change can be
replayed. `fpcalc` comes from Homebrew's chromaprint; if it is missing the check is skipped and
`/api/health` reports it, the same way it reports a missing slskd.

### 16.2 Could the file harm the Mac?

- Audio files are data and are never executed. The exposure is the parsers (ffprobe, ffmpeg,
  mutagen), which already process Deezer files today. Mitigation: only the extensions in the
  policy are accepted, ffmpeg is kept current through Homebrew, and parsing runs in
  subprocesses with the existing 300 s timeout.
- Paths: the local path is derived from the peer's folder and file name the way slskd derives
  it, then checked to resolve inside the downloads directory before anything touches it, and
  the file is renamed to a fixed per-request name on the move (section 7). Peer strings shown in the web UI are escaped like any other
  data.
- Peer metadata is discarded: conversion runs with `-vn -map 0:a` so embedded pictures and
  foreign tags are dropped, and tags are written fresh from Beatport. When the filing format is
  FLAC, `write_tags` clears existing Vorbis comments and pictures first.
- Network exposure is slskd's, not flackey's: its API is bound to loopback with a key, the
  share is read-only and limited to the library folder, and the Soulseek listening port only
  speaks the Soulseek protocol.
- The slskd downloads directory is treated as untrusted scratch space: nothing there is indexed,
  tagged or served until the worker has moved and verified it.

## 17. Observability: keeping a record of every Soulseek call

The gate and the caps are the parts most likely to need tuning, and the owner asked that the
information to tune them is kept, not just logged and rotated away. Three layers, cheapest first.

### 17.1 Log lines

One INFO line per phase, prefixed `req=<id> slsk=<attempt id>`: search started (query), search
completed (responses, files, elapsed), pick (summary from the report), enqueue (peer, file, size),
first byte (elapsed), transfer state changes (state, percent, speed), completed (elapsed, average
speed), verify (cutoff), fingerprint (score, offset), convert (format, elapsed), outcome. Every
slskd HTTP call is logged at DEBUG with method, path, status and elapsed ms; the API key never
appears. These go to the existing `flackey.log`.

### 17.2 Attempt timeline in the store

`lossless_attempts.timeline_json` is an ordered list of events
`{"t_ms": 1234, "event": "transfer_state", "detail": {...}}` appended by `_try_lossless` as it goes
and written to the row (inserted at the start) at every phase change, so a crash mid-attempt
leaves a partial timeline rather than none. The same events as the log lines, but
structured and queryable. `first_byte_ms` and `total_ms` are copied out as columns so the
attempt list can sort and aggregate without parsing JSON. The row is written even when the
outcome is `unavailable`, so a down sidecar shows up as a pattern.

### 17.3 Raw responses on disk

Each attempt gets a folder `<data dir>/lossless/attempts/<attempt id>/` holding the raw slskd JSON
exactly as received: `search.json` (the completed search object), `responses.json`, each transfer
poll that changed state as `transfer-<n>.json`, and `fingerprint.json` with both raw fingerprints.
These are the fixtures for `crate lossless replay`, for new gate rules, and for tests; the pick
report alone is not enough once a rule needs a field it did not keep. Size is a few hundred kB per
attempt, so a month of daily use is tens of MB.

Retention: `lossless_keep_raw_days` (default 30) is the window in which the gate is expected to be
tuned; after that the attempt row and its report keep everything the owner reads back, and the raw
JSON only matters for replaying a rule change. The worker prunes older folders once a day at
start and every 24 h after; `/api/health` shows the folder's size. Pruning never touches attempt
rows or tracks. No manual clear command in this phase (owner decision after review).

### 17.4 Reading it back

- `GET /api/lossless/attempts?limit=50&outcome=...` lists rows with request id, query, outcome,
  chosen peer, first-byte and total ms, fingerprint score, plus counts per outcome and median
  times for the selection.
- `GET /api/requests/{id}` includes the attempt with its timeline and report.
- `crate lossless replay <request id>` re-runs `pick()` on `responses.json` under the current
  policy and diffs the chosen file and rejection counts against the stored report.
- `/api/health` carries the last 24 h counts per outcome, which is the first thing to look at when
  Soulseek "feels slow".

## 18. Groundwork for per-track evidence

The smallest piece of this is in scope: the `track_evidence` table and the rows that cost
nothing because the attempt already computed them (`fingerprint`, `recording_match`, `source`).
`spectral_cutoff` rows for Deezer downloads and `pcm_md5` (a full extra decode per file) are not
written in this phase. Everything after the table is later work. Two things come out of every Soulseek attempt that are
facts about the filed audio, not about the attempt: the fingerprint of the filed file and the
result of the checks it passed.

- New table `track_evidence(id, track_id, kind, value_json, created_at)`, one row per fact, kind
  from a small set: `spectral_cutoff` (Hz, spectrogram path), `fingerprint` (the Chromaprint raw
  fingerprint of the filed audio, about 4 kB per track), `recording_match` (score, offset,
  reference `deezer:<id>`, or `skipped` with a reason), `pcm_md5` (hash of the decoded audio,
  which proves the filed AIFF is the verified FLAC and lets a future re-verify detect edits),
  `source` (peer, remote path, original format, bit depth, sample rate). Rows are append-only;
  the newest of a kind is the current one.
- The fingerprint and the match score are computed during the attempt anyway; the hash is not,
  which is why `pcm_md5` waits. Deezer downloads can get `spectral_cutoff` and `source` rows later
  so the table describes the whole library, not only Soulseek files.

What this makes possible later, none of it built now:

- A verified-recording badge in the library and on the Done line: `recording_match` passed and
  `spectral_cutoff` passed. Files with `skipped` show as unverified until a re-check.
- Duplicate detection by audio rather than by name: two tracks whose fingerprints match at a
  high score are the same recording under different tags, which the current `find_duplicate`
  cannot see.
- Master comparison: a low but passing `recording_match` score, or a Rekordbox BPM or length that
  differs from Beatport's, points at a different master or an edit. Rekordbox's own analysis can be
  read from its database (done by hand on 2026-09-07 with pyrekordbox) and stored as another
  evidence kind.
- Peer reliability for the ranker: attempts grouped by peer give first-byte times and failure
  rates, which can become a `known_peer` ranker once there are enough rows.
- Score distributions over time tell whether the 0.90 threshold and the 3 s duration tolerance
  are right, which is the reason the attempt rows keep the score even for misses.

## 19. Alternatives to Soulseek, and why the design does not depend on it

Considered on 2026-09-07 so the choice is on record. None replaces Soulseek for this library
today; two are plausible second providers later, which is why section 5 puts a provider protocol
between the worker and slskd.

| Source | Single-track lossless for old psytrance | Automation | Cost / access | Verdict |
|---|---|---|---|---|
| Soulseek (slskd) | Strong: 20/20 in the spike, per-track search, FLAC common | REST API, measured | Free, account only | First provider |
| Private music trackers (Redacted, Orpheus) | Strongest quality control (logged CD rips), but album-oriented | JSON API and torrent client, well trodden | Invite-only, ratio to maintain, rules on automation | Second provider if the owner has an account; adapter is small |
| Public torrents | Weak: discographies, dead swarms for 1990s releases, no per-track search | Indexer plus client | Free, IP visible in swarm | Not worth an adapter |
| Usenet | Album-based, good retention for recent releases | Mature (indexers, NZB clients) | Paid provider and indexer | Possible later, low priority |
| Purchase (Beatport WAV/AIFF, Bandcamp FLAC, Qobuz) | The genuine master, legal, per-track | No purchase API; download pages are scriptable but fragile | Money per track | Best "verified" source for tracks Soulseek lacks; a buy-link step rather than an adapter |
| Streaming lossless rips (Tidal, Qobuz, Deezer HiFi via private tools) | Per-track FLAC | Tools break with every service change | Paid account, terms violation | No |

What keeps the design portable: the gate and rankers work on `LosslessFile`, which any provider
can fill; the caps, attempt rows, evidence rows, fingerprint check, conversion, CLI and API never
mention slskd; and the only slskd-specific surfaces are `source/slskd.py`, the `slskd_*` settings
and the sidecar setup in the README. Swapping or adding a provider touches those and nothing else.
