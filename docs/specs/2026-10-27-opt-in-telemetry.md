# Optional usage stats (opt-in telemetry)

Status: **draft for the owner's review, 2026-10-27.** This is phase 1 of 4: research and specification only.
No app code, collector or server exists yet. Section numbers are referenced from the summary.

---

## One-page summary

**Goal.** Learn how Flackey is really used and where it fails, so fixes target real problems. This holds
only for people who switch it on. It is off by default. Nothing about *what* anyone downloads ever leaves
the machine.

**Top 10 questions** (each is answered by the events in §3. §8 lists the charts.)

1. Why do requests end without a file? Count by terminal state, failed stage and last Soulseek outcome.
2. When Soulseek has no pick, was it "nobody had it" (0 results) or "results, but none close enough"?
3. How long do Soulseek queue waits and first bytes take, and how often does waiting cost the track?
   The waits are `queued`, `first_byte_timeout` and `too_slow`.
4. Which stage fails or crashes, per OS and app version, and did a release make it worse?
5. Which route delivers? Soulseek lossless, Deezer MP3 fallback, or nothing. How much do a paused
   Telegram and a blocked Beatport cost?
6. How often do the lossless and recording checks refuse copies, at which tier, and in which format?
7. How often is "Keep it anyway" used per 100 refusals, and after listening? This signals that a check is
   too strict.
8. Are extended-mix offers found, accepted, kept or ignored, and does "Get extended" succeed?
9. Where does onboarding drop off, and which steps are skipped (Telegram, Soulseek) or fail?
10. Do self-updates succeed end to end, and how fast do active installs reach the latest version?

**Events (15).** Each event is described in §3.

- `app_started`
- `setup_step`, `setup_finished`
- `request_added`, `request_finished`, `review_answered`, `request_action`
- `lossless_attempt_finished`, `quality_check`, `keep_anyway`
- `extended_offer_found`, `extended_offer_answered`
- `update_step`, `source_status_changed`, `error_caught`

**Never collected:**

- Track, artist, title, mix, album, label or genre names, and ISRCs or catalogue IDs
- Links of any kind, and search text or spellings
- File and folder names or paths, and library contents
- Exact file sizes, lengths, timestamps or fingerprints
- Soulseek usernames, peer names or shares
- Telegram identity or session
- IP addresses, host or OS user names, locale and time zone
- Error messages, log lines and paths in stack traces
- Bug-report contents

The full list is in §4.1.

**Decisions for the owner** (each is argued in §10):

| # | Choice | Recommendation |
|---|---|---|
| D1 | Random resettable install ID, or no ID at all (Homebrew-style) | **Random ID.** Without it there is no onboarding funnel, no "installs affected", and no delete-my-data. |
| D2 | Collector on a small separate VPS, the existing VPN server, or ClickHouse Cloud | **Separate VPS.** It is a few euros a month and isolated from the VPN. ClickHouse Cloud has no free tier, only a 30-day trial. |
| D3 | Raw-event retention of 90 or 180 days | **180 days.** That spans about six releases for before/after comparisons. A retention promise can only ever be shortened. |
| D4 | Turning stats off also deletes past data, or keeps it | **Delete.** This is the Firefox model and the simplest promise to state and keep. |
| D5 | Ask on the setup wizard's Welcome step, or after the first filed track | **Welcome step**, with the box unticked. Otherwise question 9, onboarding drop-off, cannot be answered at all. |

**Rollout.** The gates between phases are in §11.

1. **Local dogfood.** The app builds events and writes them to a local file only. Nothing is sent.
2. **Owner's machine to the owner's own collector.** The collector is the local ClickHouse lab, in its own
   `telemetry` database.
3. **Beta.** A VPS collector and a published privacy notice, with 3–5 testers who opt in knowingly.
4. **General opt-in.** The prompt ships in a release, with the README and site disclosure.

---

## 1. Ground rules

- **Opt-in only.** It is off by default and off for every existing install after updating. Nothing is
  sent, queued or generated until the owner of the machine says yes.
- **Built from an allowlist, never redacted.** Every event is constructed field by field from enums,
  booleans, small integers and buckets. No database row, attempt timeline, error message or log line is
  ever serialised into telemetry.
  - The existing `Redactor` (`src/flackey/web/report.py`) is the wrong tool on purpose. Its docstring says
    it keeps track, artist and file names, because a bug report needs them.
  - Telemetry must not reuse it.
- **Failure-proof.**
  - Emitting an event can never raise, block or slow the worker.
  - Sending happens on its own task with its own HTTP client.
  - A dead collector costs nothing but a bounded local file (§6.1).
- **Seeable.** Everything queued or sent can be viewed locally in plain JSON before and after it leaves
  (§5.3).
- **One source of truth.** One schema module defines every event. The app, the collector's validator, the
  tests and the published docs all read it (§6.3).

## 2. Where the data already is

These are the code paths that hold what the events need. Every field in §3 points at one of them.

| Path | What it already knows | Hook point |
|---|---|---|
| `Worker._set_state` (`worker/core.py`) | Every state change for every request goes through it. It stamps `failed_stage` from `STAGE_OF_STATE` (`worker/policy.py`) and sets `reviewed`. | The single choke point for `request_finished`, on entry to any of the `TERMINAL_STATES`. It is also where per-request timestamps (first `identifying`, first `fetching`) would be kept in memory. Today only `created_at`/`updated_at` are stored. |
| `PipelineMixin._identify` (`worker/pipeline.py`) | Whether the record was chosen by audio (`identify_record`) or by text (`decide`), whether the catalogue was blocked (`_catalog_blocked`), whether the bot was skipped (`_bot_skipped`), and the `_search_on_the_request` and `catalog_candidate` routes | Fields of `request_finished` |
| `AttemptRecorder.finish` (`attempts.py`) and `_download_and_check` (`worker/lossless_attempt.py`) | `outcome` (one of `ATTEMPT_OUTCOMES` in `models.py`), the pick `report` (`seen`, `survivors`), and timeline events (`enqueue`, `first_byte`, `completed`, `too_slow`, `slow_fallback`, `disk_full`, `verify`, `fingerprint`). `first_byte_ms`, `total_ms` | `lossless_attempt_finished`. **The timeline carries `peer=` and `file=` on `enqueue`, and `LosslessAttempt.query` is search text, so neither is read beyond counts.** |
| `_check_and_convert` (`worker/lossless_attempt.py`), `_verify_and_file` (`worker/filing.py`) | `Verdict.level` (`clear`/`unverified`/`reject`), `cutoff_hz`, format, bit depth and sample rate | `quality_check` |
| `accept_rejection` (`worker/filing.py`), `keep_held_lossless` (`worker/lossless_attempt.py`) | The two "Keep it anyway" buttons (`POST /requests/{rid}/accept` and `/keep-held`), and `Rejection.kind` | `keep_anyway` |
| `Worker.choose`, `cancel`, `retry`, `_fresh_pass` (`worker/core.py`), and `web/requests.py` routes (`retry-failed`, `remove`, `clear-failed`, `DELETE`) | Owner actions on a request | `review_answered` and `request_action` |
| `ExtendedMixin` (`worker/extended.py`) | Offer status (`available`, `none`, `kept`, `unmatched`), `length_s` and the filed `duration_s`. The result of `get_extended` | `extended_offer_found` and `extended_offer_answered` |
| `web/update.py`, `selfupdate/` | The update state machine (`idle` → … → `error`), and the signature check | `update_step` |
| `refresh_lossless_health`, `process()` `SourceUnauthorized`, `_catalog_search`, `sharing.py` port check | Transitions in Soulseek health, Telegram sign-in, Beatport blocking and port reachability | `source_status_changed` |
| `supervise_worker` (`app.py:170`), `desktop.py:337` server thread, the generic `except` in `Worker.process` | Unhandled exceptions. **There is no `sys.excepthook`, `threading.excepthook` or asyncio loop exception handler today.** Crash capture is new code. | `error_caught` |
| `web/src/components/setup/*` (Welcome, Folder, Format, Telegram, Soulseek, Ready), `POST /setup/done`, `POST /telegram/skip` | The wizard's five numbered steps (`SetupShell.NAMES`), skip and done | `setup_step` and `setup_finished`. The UI posts to a local `/api/telemetry/ui` route, which validates against the same schema. |
| `POST /api/requests` (`web/requests.py`), `flackey add` (`cli.py`), `inbox.py` | The `RequestKind` of each request, and playlist expansion | `request_added` |
| `config.Settings` | `source_enabled`, `lossless_filing_format`, `library_layout`, `extended_mixes`, `auto_update_check` | `app_started` |

## 3. Events and fields

### 3.0 Envelope (on every event)

| Field | Type | Example | Class | Why |
|---|---|---|---|---|
| `schema` | UInt8 | `1` | SAFE | Lets the collector reject shapes it does not know |
| `event` | enum | `request_finished` | SAFE | |
| `install_id` | UUIDv4 | `5b0e…` | SENSITIVE-DERIVED | Distinct installs affected, funnels, delete-my-data. Random, created at opt-in, resettable (§4.3) |
| `session_id` | UUIDv4 | `c1d4…` | SENSITIVE-DERIVED | Crash-free sessions. Random per launch, never stored on disk |
| `seq` | UInt32 | `17` | SAFE | Orders events within a session without exact timestamps |
| `event_date` | Date (UTC) | `2026-11-02` | SENSITIVE-DERIVED: a day, not a time | Trends by day. The exact time is never sent (§4.2) |
| `app_version` | enum-ish string, regex `^\d+\.\d+\.\d+$` | `0.6.0` | SAFE | Q4, Q10 |
| `os` | enum `macos`/`windows`/`linux` | `macos` | SAFE | Q4 |
| `os_major` | enum-ish (`12`–`26`, `10`, `11`) | `15` | SAFE | Q4. Never a build number |
| `arch` | enum `arm64`/`x64` | `arm64` | SAFE | |
| `channel` | enum `packaged`/`checkout`/`docker` | `packaged` | SAFE | Keeps the developer's own checkout out of user numbers |

### 3.1 Shared buckets

Every measured value is sent as one of these labels, never as a raw number.

- **dur:** `<1s`, `1-5s`, `5-15s`, `15-60s`, `1-5m`, `5-15m`, `15-60m`, `1-6h`, `6-24h`, `1-3d`, `>3d`
- **n (counts):** `0`, `1`, `2-4`, `5-19`, `20-99`, `100+`
- **size (MB):** `<10`, `10-30`, `30-60`, `60-120`, `120+`
- **length (track):** `<3m`, `3-5m`, `5-7m`, `7-10m`, `10m+`
- **score (%):** `<50`, `50-69`, `70-84`, `85-94`, `95+`
- **speed (kB/s):** `<50`, `50-200`, `200-1000`, `1000+`
- **cutoff (kHz):** `<16`, `16-19`, `19-20`, `20-21`, `21+`, `none`

### 3.2 Event catalogue

Enum values below are the existing ones in the code wherever the code has them. A field marked
**(new code)** needs a code that does not exist yet, and must be added as an enum, never as free text.

#### `app_started`: once per launch

Answers Q4, Q5, Q10 and active installs.

| Field | Type | Example | Class | Q |
|---|---|---|---|---|
| `telegram` | enum `connected`/`signed_out`/`not_set_up`/`off` | `off` | SAFE | 5 |
| `soulseek` | enum `ok`/`not_logged_in`/`unreachable`/`off` | `ok` | SAFE | 5 |
| `filing_format` | enum `aiff`/`wav`/`flac` | `aiff` | SAFE | 6 |
| `library_layout` | enum (`LIBRARY_LAYOUTS`) | `artist` | SAFE | |
| `extended_mixes` | enum `ask`/`always`/`off` | `ask` | SAFE | 8 |
| `auto_update_check` | bool | `true` | SAFE | 10 |
| `has_ffmpeg`, `has_fpcalc` | bool | `true` | SAFE | 4 |
| `library_tracks` | n bucket | `100+` | SENSITIVE-DERIVED → bucket | context |
| `port_reachable` | enum `yes`/`no`/`unknown` | `no` | SAFE | 3 |
| `days_since_opt_in` | enum `<1w`/`1-4w`/`1-3mo`/`3mo+` | `1-4w` | SENSITIVE-DERIVED → bucket | retention of use |

#### `setup_step`: each step shown, completed, skipped, backed out of or failed

Answers Q9.

| Field | Type | Example | Class |
|---|---|---|---|
| `step` | enum `welcome`/`folder`/`format`/`telegram`/`soulseek`/`ready` | `telegram` | SAFE |
| `action` | enum `shown`/`completed`/`skipped`/`back`/`failed` | `skipped` | SAFE |
| `method` | enum `qr`/`phone`/`keys`/`existing`/`new`/`` | `qr` | SAFE |
| `error_code` | enum (new code: e.g. `slskd_download_failed`, `login_rejected`, `port_closed`, `folder_not_writable`) | `login_rejected` | SAFE |
| `time_on_step` | dur bucket | `1-5m` | SAFE |
| `rerun` | bool (after `/setup/reset`) | `false` | SAFE |

Consent is given on the Welcome step (D5), so the funnel starts at that click. People who decline are not
in it, and the dashboard says so.

#### `setup_finished`

Answers Q9.

| Field | Type | Example | Class |
|---|---|---|---|
| `total_time` | dur bucket | `5-15m` | SAFE |
| `telegram_connected`, `soulseek_connected` | bool | `false`, `true` | SAFE |
| `folder_default` | bool (the default library path was kept) | `true` | SAFE. The path itself is NEVER |
| `filing_format` | enum | `aiff` | SAFE |

#### `request_added`

Answers Q1 (the denominator).

| Field | Type | Example | Class |
|---|---|---|---|
| `kind` | enum `RequestKind` | `spotify_playlist` | SAFE |
| `via` | enum `ui`/`cli`/`telegram` | `ui` | SAFE |
| `playlist_tracks` | n bucket (playlists only) | `5-19` | SENSITIVE-DERIVED → bucket |

#### `request_finished`: on entry to a terminal state

This is the core event. It answers Q1, Q2, Q4, Q5 and Q6.

| Field | Type | Example | Class | Q |
|---|---|---|---|---|
| `kind` | enum `RequestKind` | `yt_track` | SAFE | 1 |
| `state` | enum `done`/`duplicate`/`rejected`/`cancelled`/`not_found`/`error` | `error` | SAFE | 1 |
| `failed_stage` | enum `search`/`choose`/`download`/`verify`/`unknown`/`` | `download` | SAFE | 1, 4 |
| `route` | enum `soulseek_lossless`/`deezer_mp3`/`none` | `deezer_mp3` | SAFE | 5 |
| `identified_by` | enum `audio`/`text`/`catalog_only`/`request_words`/`none` | `audio` | SAFE | 1 |
| `reviewed` | bool (`Request.reviewed`) | `false` | SAFE | 1 |
| `passes` | UInt8, capped at 20 (`attempts` plus long-retry rounds) | `3` | SAFE | 1 |
| `lossless_outcome` | enum `ATTEMPT_OUTCOMES` or `` | `no_pick` | SAFE | 1, 2 |
| `lossless_no_results` | bool (`Store.lossless_no_results` / `found_nothing`) | `true` | SAFE | 2 |
| `spellings_tried` | n bucket | `2-4` | SAFE | 2 |
| `catalog` | enum `ok`/`blocked`/`no_match` | `blocked` | SAFE | 5 |
| `bot` | enum `ready`/`paused`/`off` | `paused` | SAFE | 5 |
| `interpret` | enum `confirmed`/`agreed`/`ambiguous`/`skipped`/`` | `agreed` | SAFE | 1 |
| `verify_tier` | enum `clear`/`unverified`/`reject`/`` | `clear` | SAFE | 6 |
| `filed_format` | enum `aiff`/`wav`/`flac`/`mp3`/`` | `aiff` | SAFE | 6 |
| `source_format` | enum `flac`/`wav`/`aiff`/`alac`/`mp3`/`other`/`` | `flac` | SAFE | 6 |
| `bit_depth` | enum `16`/`24`/`other`/`` | `16` | SAFE | 6 |
| `tag_source` | enum `beatport`/`deezer`/`discogs`/`deezer+discogs`/`none` | `deezer` | SAFE | 5 |
| `extended_cut` | bool | `false` | SAFE | 8 |
| `wait_to_start` | dur bucket (created → first `identifying`, new code: kept in memory) | `<1s` | SENSITIVE-DERIVED → bucket | 3 |
| `total_time` | dur bucket (created → terminal) | `15-60m` | SENSITIVE-DERIVED → bucket | 3 |
| `error_class` | exception class name, regex `^[A-Za-z][A-Za-z0-9_]{0,48}$`, only for `state=error` from `process()`'s `except` | `SourceTimeout` | SAFE. `error_message` is NEVER | 4 |

#### `review_answered`: a parked request is chosen or cancelled

Answers Q1.

| Field | Type | Example | Class |
|---|---|---|---|
| `answer` | enum `picked`/`cancelled` | `picked` | SAFE |
| `picked_rank` | enum `1`–`5`/`other` (the display order in `_ask_review`) | `1` | SAFE |
| `candidates` | n bucket | `2-4` | SAFE |
| `top_score` | score bucket | `70-84` | SAFE |
| `reason_code` | enum (new code: `decide` returns free text today) | `version_mismatch` | SAFE |
| `wait` | dur bucket | `1-6h` | SAFE |

#### `request_action`: owner actions on requests and tracks

Answers Q1 and Q7.

| Field | Type | Example | Class |
|---|---|---|---|
| `action` | enum `retry`/`try_now`/`retry_all`/`cancel`/`remove`/`remove_many`/`clear_failed`/`find_lossless` | `retry` | SAFE |
| `from_state` | enum `RequestState` | `not_found` | SAFE |
| `failed_stage` | enum, as above | `download` | SAFE |
| `count` | n bucket (batch actions) | `5-19` | SAFE |
| `result` | enum `ok`/`refused` (`ValueError` paths) | `ok` | SAFE |

#### `lossless_attempt_finished`: on `AttemptRecorder.finish`

Answers Q2, Q3 and Q6.

| Field | Type | Example | Class | Q |
|---|---|---|---|---|
| `provider` | enum `soulseek` | `soulseek` | SAFE | |
| `purpose` | enum `main`/`retry`/`find_lossless`/`get_extended` | `main` | SAFE | 8 |
| `outcome` | enum `ATTEMPT_OUTCOMES` | `first_byte_timeout` | SAFE | 2, 3 |
| `spelling_index` | UInt8 (0 = first spelling) | `1` | SAFE | 2 |
| `results_seen` | n bucket (`results_seen()`) | `0` | SAFE | 2 |
| `survivors` | n bucket | `2-4` | SAFE | 2 |
| `picks_tried` | UInt8 (count of `enqueue`) | `2` | SAFE | 3 |
| `slow_switches` | UInt8 | `1` | SAFE | 3 |
| `slow_fallback` | bool | `false` | SAFE | 3 |
| `search_time` | dur bucket | `5-15s` | SAFE | 2 |
| `queue_wait` | dur bucket (first `enqueue` → `first_byte`, or → giving up) | `5-15m` | SAFE | 3 |
| `first_byte` | dur bucket (`first_byte_ms`) | `15-60s` | SAFE | 3 |
| `transfer_time` | dur bucket (`total_ms`) | `1-5m` | SAFE | 3 |
| `speed` | speed bucket | `200-1000` | SAFE | 3 |
| `file_size` | size bucket | `30-60` | SENSITIVE-DERIVED → bucket. An exact size can pin one peer's file | 3 |
| `disk_full` | bool | `false` | SAFE | 4 |
| `fingerprint_score` | score bucket | `95+` | SAFE | 6 |

#### `quality_check`: each verify verdict

Answers Q6 and Q7.

| Field | Type | Example | Class |
|---|---|---|---|
| `copy` | enum `lossless`/`lossy` | `lossless` | SAFE |
| `format_in` | enum, as `source_format` | `flac` | SAFE |
| `tier` | enum `clear`/`unverified`/`reject` | `unverified` | SAFE |
| `flagged_by` | array of enum: the evidence-family names from `verify.summary`; the numbers are dropped | `["spectral"]` | SAFE |
| `reason_code` | enum (new code: `Verdict.reason` is a sentence today) | `cutoff_low` | SAFE |
| `cutoff` | cutoff bucket | `16-19` | SENSITIVE-DERIVED → bucket |
| `bit_depth` | enum | `24` | SAFE |
| `duration_mismatch` | bool | `false` | SAFE |

#### `keep_anyway`: either "Keep it anyway" button

Answers Q7.

| Field | Type | Example | Class |
|---|---|---|---|
| `which` | enum `refused_copy` (`/accept`)/`held_lossless` (`/keep-held`) | `refused_copy` | SAFE |
| `rejection_kind` | enum `quality`/`different_recording` | `quality` | SAFE |
| `tier` | enum | `reject` | SAFE |
| `listened` | bool (the rejection audio route was fetched first; new code, a per-rejection in-memory flag) | `true` | SAFE |
| `decision_delay` | dur bucket | `1-5m` | SAFE |
| `result` | enum `filed`/`could_not_check`/`failed` | `filed` | SAFE |

#### `extended_offer_found`: the background scan settles a track

Answers Q8.

| Field | Type | Example | Class |
|---|---|---|---|
| `status` | enum `available`/`none` | `available` | SAFE |
| `word` | enum `extended`/`club mix` (`OFFER_WORDS`) | `extended` | SAFE |
| `gain` | enum `30-60s`/`1-2m`/`2-4m`/`4m+` (offer `length_s` − filed `duration_s`) | `1-2m` | SENSITIVE-DERIVED → bucket |
| `filed_length` | length bucket | `3-5m` | SENSITIVE-DERIVED → bucket |

#### `extended_offer_answered`: Get extended or Keep this

Answers Q8. "Ignored" is derived as offers found minus offers answered.

| Field | Type | Example | Class |
|---|---|---|---|
| `answer` | enum `get`/`keep` | `get` | SAFE |
| `result` | enum `replaced`/`not_matched`/`not_extended`/`failed`/`` | `replaced` | SAFE |
| `offer_age` | dur bucket | `1-3d` | SAFE |

#### `update_step`

Answers Q10.

| Field | Type | Example | Class |
|---|---|---|---|
| `stage` | enum `check`/`download`/`verify_signature`/`install`/`restart` | `verify_signature` | SAFE |
| `result` | enum `ok`/`failed`/`cancelled`/`none_available` | `ok` | SAFE |
| `to_version` | version | `0.7.0` | SAFE |
| `trigger` | enum `auto`/`manual` | `auto` | SAFE |
| `error_code` | enum (new code) | `signature_invalid` | SAFE |
| `time` | dur bucket | `15-60s` | SAFE |

#### `source_status_changed`: only on a transition, at most one per source per hour

Answers Q5.

| Field | Type | Example | Class |
|---|---|---|---|
| `source` | enum `telegram`/`soulseek`/`beatport`/`port` | `beatport` | SAFE |
| `from`, `to` | enum (`ok`/`blocked`/`signed_out`/`unreachable`/`not_logged_in`/`open`/`closed`) | `ok` → `blocked` | SAFE |

#### `error_caught`: any unhandled or last-resort exception

Answers Q4 and crash-free sessions.

| Field | Type | Example | Class |
|---|---|---|---|
| `where` | enum `worker_loop`/`request`/`server_thread`/`excepthook`/`asyncio`/`ui`/`slskd_start` | `worker_loop` | SAFE |
| `exc_class` | class name (regex as above) | `KeyError` | SAFE |
| `module` | the innermost **flackey** frame's dotted module, allowlisted against the package's own modules | `flackey.worker.filing` | SAFE |
| `function` | that frame's function name (same allowlist idea, from the code, not the data) | `_file_track` | SAFE |
| `line` | UInt16 | `388` | SAFE (a code coordinate) |
| `fatal` | bool | `false` | SAFE |

The message, the other frames, local variables and any path are NEVER sent. A third-party frame is
reported as its top package name only (`httpx`), if at all.

## 4. Privacy

### 4.1 Never collected

These are hard rules. A test enforces them (§6.3), and the published notice repeats them.

- **Music.** Track, artist, title, mix, album, label and genre names, plus ISRCs and Deezer, Beatport and
  Discogs IDs. Label and genre count too: a small label is close to a name.
- **Links and searches.** YouTube, YouTube Music and Spotify URLs, playlist names and links, the request's
  raw text, and any spelling or query sent to Soulseek or the bot.
- **Files.** File and folder names and paths, including the library folder. Exact file sizes, exact track
  lengths, hashes, Chromaprint fingerprints, spectrograms and audio.
- **Other people and accounts.**
  - Soulseek: usernames, peer names, share contents, and anything from slskd responses.
  - Telegram: phone number, user ID, session, chats.
  - Discogs and other API keys.
- **The machine.** IP addresses (§4.4), host name, OS user name, home folder, locale, time zone, screen,
  hardware IDs and MAC addresses.
- **Text.** Error messages, log lines, stack traces with paths, `flag_reason`, `error_message`,
  `Verdict.reason` and any other sentence the app writes.
- **Time.** Exact timestamps. Only the UTC date plus bucketed durations are sent.
- **Library.** Its contents and any per-track listing. Only one bucketed count is sent.
- **Bug reports.** Their contents. The bug report (#33) and telemetry stay separate pipes, and neither
  carries the other's IDs.

### 4.2 Why the schema cannot reveal what someone downloaded

The claim is narrow on purpose: **the data cannot tell anyone what you downloaded or searched for.**

1. **No content-bearing field exists.** Every field is an enum, a boolean, a small capped integer, a bucket
   label, a version string matching a regex, or a random UUID. No free-text field exists anywhere in the
   schema, so nothing can carry a title by accident. The collector rejects any batch with an unknown field
   or a value outside its enum (§6.3).
2. **No exact value that could fingerprint a file.**
   - Size, track length, offer gain, cutoff and fingerprint score are all bucketed.
   - The narrowest join, "a FLAC of exactly 41,233,118 bytes fetched at 14:07", would match one peer's
     slskd upload log, and both halves of it are coarsened: the size bucket is 30–60 MB and the time is a
     date.
   - Events are sent in batches every ~15 minutes with jitter, so arrival time does not narrow it either.
3. **No join to an account.**
   - No Soulseek, Telegram or Discogs identifier is sent.
   - The install ID is generated by the app from a CSPRNG at opt-in. It is not derived from hardware, the
     MAC address (VS Code's approach) or any account.
4. **No network identity.** The collector never stores or logs the IP (§4.4).

### 4.3 The install ID

- **Created** at opt-in (UUIDv4 from `secrets`) and stored in `settings.json` as `telemetry_install_id`.
  None exists before consent.
- **Reset** in Settings: the app first sends a delete for the old ID, then makes a new one.
  - The app never keeps a list of old IDs, so a reset leaves nothing behind that cannot be deleted.
  - There is no automatic rotation. Rotating silently would strand old data that the user could no
    longer delete, which conflicts with delete-by-ID.
- **Deleted** when stats are turned off (D4): delete request, then the ID is wiped.
- **Session ID**: random per launch, held in memory only.
- **The ID is a bearer secret.**
  - Knowing it is what authorises a delete.
  - It is never shown in dashboards, which group by aggregates only.
  - Settings shows only its first 8 characters.

### 4.4 No IP storage at the collector

- The reverse proxy's access log is turned off (Caddy logs nothing unless a `log` directive is set; nginx
  needs `access_log off`).
- The collector does not read `X-Forwarded-For` except to rate-limit.
- The rate limiter keys on an HMAC of the IP under a random key held in memory only. The key is replaced
  every 10 minutes, and the buckets go with it (§6.2).
- ClickHouse has no column for an IP. The process never writes one to disk or to its own logs (no request
  logging middleware).
- **Honest limit.** The IP is necessarily seen in transit (TCP/TLS), and the hosting provider can see
  connection metadata. The notice says so.

### 4.5 Rare values and small numbers (k-anonymity)

- **Population.** In the beta (phase 3) the population is a handful of people the owner knows. Then
  `os=windows, os_major=10, app_version=0.7.1, first seen 2026-11-03` is one specific friend. This cannot
  be designed away.
  - So the UI and notice do not say "we can't tell who you are". They say "not linked to your name or
    accounts, and never anything about what you download".
  - In GDPR terms this is pseudonymous data, and §5.4 treats it as such.
- **Small-cell suppression.**
  - Dashboards show a cell only when it covers at least **5 distinct installs**. Otherwise it is merged
    into "other" or hidden.
  - Version and OS-major values with fewer than 5 installs that week are folded into `other`.
- **Archive suppression.** Rollups kept past raw retention apply the same k≥5 rule before writing. The
  long-term archive then holds anonymous aggregates only (§7.4).
- **Bucket edges.** Bucket edges are fixed by the schema. They are never tuned to "make a chart look
  better" in a way that would isolate one user.
- **Threat model.** Three parties are considered:
  - the owner reading dashboards (sees aggregates; raw access is only for debugging the pipeline);
  - a breach of the collector (gets IDs plus enums, but no content and no IP);
  - a legal request (same as a breach; nothing to hand over about content).

## 5. Opt-in experience and controls

### 5.1 The prompt

The prompt is a card on the Welcome step of the setup wizard (D5), with the checkbox **unticked**.

> **Help make Flackey better** *(optional)*
> Send usage stats: which steps work, which fail, and how long they take. No names, links or file names,
> and nothing about the music you download. You can see every report before it leaves, and turn this off
> in Settings at any time, which also deletes what was sent.
> ☐ Share usage stats · [See exactly what's sent]

- "See exactly what's sent" opens the event catalogue (§3) with a real example of each event.
- **Existing installs** get the same card once, as a dismissible notice on the Downloads page after
  updating, with two equal buttons: **Share usage stats** and **No thanks**.
  - Either answer is final. Nothing re-asks later, and there are no "remind me" loops.
- `channel=checkout` and Docker follow the same rule. `FLACKEY_TELEMETRY=off` in the environment
  overrides everything, for the people who manage settings with env vars.

### 5.2 Settings › Privacy

- **Share usage stats**: a toggle. Off by default.
- **What's sent**: shows the pending outbox and the last 500 sent events as formatted JSON, with Copy.
  This is what VS Code's "Show Telemetry" channel does.
- **Your ID**: `5b0e2c1a…` with **New ID**, which deletes the old ID's data first.
- **Delete my data**: sends the delete and shows when it was confirmed. It works with stats on or off.
- A link to the privacy notice (§5.5).

### 5.3 Local preview and log

- `telemetry-outbox.jsonl` (pending) and `telemetry-sent.jsonl` (last 500) live in the data dir. They are
  readable by the user and are exactly the bytes that are sent, before gzip.
- `FLACKEY_TELEMETRY=local` builds events and writes the outbox, but never sends. This is phase 1, and is
  also useful to anyone auditing.
- `flackey telemetry show` prints the outbox, and `flackey telemetry catalog` prints every possible event.
  The latter follows VS Code's `--telemetry` flag.

### 5.4 GDPR basics

- **Personal data.** A random install ID plus behaviour is pseudonymous, therefore personal data (Recital
  26). The whole regime applies.
- **Lawful basis.** Consent, under Art. 6(1)(a).
  - Storing the ID on the device also needs consent under ePrivacy Art. 5(3).
  - The unticked box covers both.
  - Consent must be as easy to withdraw as it was to give (Art. 7(3)). The toggle does that.
- **Purpose limitation** (Art. 5(1)(b)).
  - The only purpose is improving Flackey's reliability and features.
  - No advertising, no sale, no sharing, no profiling of individuals.
  - Any new purpose needs new consent.
- **Data minimisation** (Art. 5(1)(c)): §3 and §4.1.
- **Storage limitation** (Art. 5(1)(e)): raw events live for 180 days (D3). Anonymous k≥5 aggregates are
  kept longer (§7.4).
- **Erasure** (Art. 17).
  - Delete-my-data, and opt-out, remove raw rows within 24 hours and recompute rollups (§7.3).
  - Backups, if any, must expire within 7 days, so erasure is complete within 7 days. The notice says so.
- **Controller and processor.**
  - The owner is the controller and is named in the notice, with a contact address.
  - The VPS provider is a processor: accept its data-processing agreement. EU providers such as Hetzner
    offer one.
- **Access requests.** "What's sent" plus the local sent log is the honest answer. The collector can also
  export rows by ID.

### 5.5 README and site disclosure (draft)

> ### 📊 Usage stats (optional, off by default)
> Flackey can send usage stats that help find and fix what breaks, if you switch them on (setup's first
> screen, or Settings › Privacy).
>
> - **What is sent:** which steps worked or failed, how long they took in rough ranges, the app version,
>   your OS and its major version.
>   - Each event is tied to a random ID made on your computer when you opt in.
>   - The full list of events is in `docs/telemetry.md`, and Settings › Privacy › What's sent shows every
>     report, before and after it leaves.
> - **What is never sent:** track, artist or label names, links, search text, file names or paths,
>   Soulseek or Telegram account details, your IP address, error messages or logs.
> - **Where it goes:** the developer's own server at `telemetry.<domain>`. It does not log IP addresses,
>   keeps raw stats for 180 days, and keeps only anonymous totals after that. Nothing is sold or shared.
> - **Your controls:** turn it off at any time. Turning it off deletes what was sent within a day, and the
>   same goes for Settings › Privacy › Delete my data.
>
> The update check (which asks GitHub for new releases) and bug reports (which you send yourself) are
> separate and work exactly as before.

## 6. Transport and collector

### 6.1 Client: it must never affect downloads

- **Emitting.**
  - `telemetry.emit(name, **fields)` checks consent first; when consent is off, it is a no-op.
  - It builds the event through the schema, dropping (and counting) any field that fails validation, and
    appends to an in-memory deque of at most 500 events.
  - The body is wrapped in `try/except Exception`: it never raises and never awaits.
  - A full deque drops the oldest event.
- **Writing.** A writer task appends the deque to `telemetry-outbox.jsonl` every 60 seconds.
  - The outbox is capped at **1 MB or 2,000 events**, whichever comes first, dropping the oldest.
  - Drops are counted, and the count is sent in the next batch envelope as `dropped`.
- **Sending.**
  - A sender task runs every 15 minutes ±5 minutes of jitter, with a separate `httpx.AsyncClient` (not the
    worker's).
  - It POSTs up to 200 events, gzipped, with a 10-second timeout.
  - **2xx:** move the events to the sent log.
  - **4xx other than 429:** drop the batch, so the client never retries a shape the collector refuses.
  - **429, 5xx or a network error:** keep the batch and back off exponentially from 15 minutes to 6 hours.
  - Shutdown gets one try of at most 1 second, and anything left waits for the next launch.
- **Isolation.**
  - No telemetry code runs under the worker's `_cpu` semaphore or locks.
  - The sender task is supervised: if it dies, it restarts with backoff.
  - import-linter forbids `flackey.telemetry` from importing `worker`, `web` or `source`. Callers import
    it; it imports nothing of theirs.
- **Opting out.**
  - Clear the deque and delete both files.
  - Persist a pending delete (a one-line file) and retry it with the same backoff until it gets a 2xx.
  - Firefox's deletion-request ping is retried the same way.

### 6.2 Collector

- **Shape.** A small Python service (FastAPI, matching the app's stack and reusing the schema module) sits
  behind Caddy for TLS, on its own domain. Its endpoints:
  - `POST /v1/batch`: body `{schema, install_id, dropped, events: [...]}` gzip, at most 256 KB
    compressed and 200 events.
    - Validated strictly against the schema. One bad event rejects the batch with a 400 that names the
      field.
    - Returns 202.
    - Writes with ClickHouse async inserts (`async_insert=1, wait_for_async_insert=1`).
  - `DELETE /v1/installs/{install_id}`: returns 202.
    - Records the SHA-256 of the ID in `deletions`.
    - Runs `DELETE FROM telemetry.events WHERE install_id = …` (a lightweight delete).
    - Recomputes the rollups (§7.3).
  - `GET /v1/health`.
- **No IP storage:** §4.4.
- **Abuse and rate limiting.** The app is open source, so no embedded secret can authenticate clients. Fake
  events are possible and the design limits the damage:
  - per IP (HMAC key held in memory): 30 requests per hour;
  - per `install_id`: 2,000 events per day, after which the rest is dropped with a 429;
  - global: an ingest ceiling of 100 requests per second;
  - payloads are size-capped;
  - dashboards count distinct installs rather than raw events, so one noisy ID cannot dominate a chart.
- **ClickHouse is never exposed.** It listens on localhost of the VPS only. The collector writes as an
  insert-only user. Dashboards run as a separate read-only user over an SSH tunnel.

### 6.3 One schema, enforced

- `src/flackey/telemetry/schema.py` defines each event as a frozen mapping of field to type: enum,
  bucket, bool, capped int, UUID or regex-bounded string.
- That module produces:
  - the collector's validator;
  - `docs/telemetry.md` (the published catalogue);
  - `flackey telemetry catalog`.
- **Tests that make §4 a property, not a promise.**
  - Every string field is an enum, a bucket or a regex of at most 50 characters with no spaces.
  - No field name matches `name|title|artist|path|url|query|user|peer|message|ip|email`.
  - A fuzz test runs the pipeline on fixtures full of real-looking titles and paths, and asserts that none
    of those strings appears in any emitted event.
- Bumping the schema version is a reviewed change, and any new field is listed in the release notes.

### 6.4 Hosting options

| Option | Cost | For | Against |
|---|---|---|---|
| **Small separate VPS** (e.g. 2 vCPU / 4 GB, EU) running Caddy, the collector and single-node ClickHouse | A few euros a month | Full control of logging, and the IP never stored anywhere. Separate blast radius. Same ClickHouse the owner learned in the lab | The owner patches one more box (unattended-upgrades, firewall to 443 only) |
| **The owner's existing VPN server** | ~0 | Already paid for | **No.** It mixes a public, internet-facing ingest endpoint with the box that carries the owner's own traffic and holds the WireGuard keys, so a collector bug becomes a VPN compromise. Its IP and uptime are tied to the owner's personal use: restarts for VPN work drop telemetry, and the WireGuard set-up already caused Soulseek port trouble (2026-10-14). A privacy notice saying "a server that also runs my personal VPN" is a harder sell |
| **ClickHouse Cloud** | **No free tier.** A trial of $300 / 30 days, then pay-as-you-go. ClickHouse's own billing example for Basic is about $67/month ([billing docs](https://clickhouse.com/docs/en/cloud/manage/billing)) | Managed, with backups | A collector is still needed in front of it, because a write credential cannot be shipped in an open-source app. So there is a VPS anyway, plus a second processor to disclose and a bill roughly ten times higher. The premise of a free dev tier does not hold; check [clickhouse.com/pricing](https://clickhouse.com/pricing) before deciding |

Recommendation: **separate VPS** (D2).

- Use a domain the owner controls (`telemetry.<domain>`), never a raw IP or a provider hostname.
- The URL is baked into every released build, and old versions keep sending to it for years. Only a
  domain can move with the service.

### 6.5 Retention

| Data | Retention |
|---|---|
| Raw `events` | `TTL received_date + 180 DAY` (D3) |
| Recomputed rollups | Cover the same window |
| k≥5 archive | 2 years |
| `deletions` log (ID hashes, to prove deletions ran) | 30 days |
| Backups | At most 7 days |

## 7. ClickHouse schema

The schema lives in its own `telemetry` database. The lab's `schema.sql` begins with
`DROP DATABASE IF EXISTS flackey` and `etl.py` runs it on every load, so telemetry in `flackey` would be
wiped by the next lab reload. Phase 2 uses a separate `telemetry.sql` that `etl.py` never touches. The SQL
targets ClickHouse 25.8, the lab's pinned version.

### 7.1 Raw events

```sql
CREATE DATABASE IF NOT EXISTS telemetry;

CREATE TABLE telemetry.events
(
    event_date     Date,                               -- client's UTC date, no time of day
    received_date  Date DEFAULT today(),               -- collector's; bounds clock-skewed clients
    install_id     UUID,
    session_id     UUID,
    seq            UInt32,
    schema         UInt8,
    event          LowCardinality(String),
    app_version    LowCardinality(String),
    os             LowCardinality(String),
    os_major       LowCardinality(String),
    arch           LowCardinality(String),
    channel        LowCardinality(String),
    -- Event fields. Every value is an enum/bucket label by construction (§6.3), so the key and the
    -- value are both low-cardinality; typed views below give each event real columns.
    props          Map(LowCardinality(String), LowCardinality(String)),
    flagged_by     Array(LowCardinality(String))       -- quality_check only; empty elsewhere
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(received_date)
ORDER BY (event, event_date, app_version, os, install_id, session_id, seq)
TTL received_date + INTERVAL 180 DAY DELETE
SETTINGS ttl_only_drop_parts = 1;
```

- **`ORDER BY` starts with `event`.** Every dashboard query filters on one event type first. Then
  `event_date` for time ranges, then `app_version` and `os` for the release and OS splits.
- **`install_id` comes late.** It is in the key so delete-by-ID and distinct counts are cheap within a
  granule. It comes after the dimensions because nothing filters on it except deletion.
- **One table with a `Map`, not 15 tables.** A schema bump stays a code change, not a migration. The data
  is small: even 1,000 installs × 200 events a day is about 200 k rows a day, nothing for ClickHouse.
- **`ttl_only_drop_parts`.** The TTL drops whole monthly partitions instead of rewriting parts.

```sql
-- Typed views, one per hot event (the rest are read straight from props).
CREATE VIEW telemetry.v_request_finished AS
SELECT event_date, app_version, os, os_major, install_id,
       props['kind'] AS kind, props['state'] AS state, props['failed_stage'] AS failed_stage,
       props['route'] AS route, props['lossless_outcome'] AS lossless_outcome,
       props['lossless_no_results'] = 'true' AS no_results, props['bot'] AS bot,
       props['catalog'] AS catalog, props['verify_tier'] AS verify_tier,
       props['total_time'] AS total_time, props['wait_to_start'] AS wait_to_start
FROM telemetry.events WHERE event = 'request_finished';

CREATE VIEW telemetry.v_attempt AS
SELECT event_date, app_version, os, install_id,
       props['outcome'] AS outcome, props['purpose'] AS purpose, props['results_seen'] AS results_seen,
       props['queue_wait'] AS queue_wait, props['first_byte'] AS first_byte, props['speed'] AS speed
FROM telemetry.events WHERE event = 'lossless_attempt_finished';
```

### 7.2 Ingest-health rollup (incremental MV)

This rollup contains no IDs and no dimensions finer than the event name, so a deletion does not need to
touch it.

```sql
CREATE TABLE telemetry.ingest_daily
(received_date Date, event LowCardinality(String), n UInt64)
ENGINE = SummingMergeTree ORDER BY (received_date, event)
TTL received_date + INTERVAL 2 YEAR;

CREATE MATERIALIZED VIEW telemetry.ingest_daily_mv TO telemetry.ingest_daily AS
SELECT received_date, event, count() AS n FROM telemetry.events GROUP BY received_date, event;
```

### 7.3 Product rollups (refreshable MVs, recomputed so deletions propagate)

- **Why not incremental MVs.** An incremental `uniqState(install_id)` keeps the hashes of individual IDs
  for small sets. ClickHouse's `uniq` stores exact hashes below its threshold, so it would survive a
  delete-my-data.
- **What instead.** The product rollups are refreshable MVs that rebuild from the raw table every hour.
  Their target is replaced atomically on each refresh, so a deleted ID disappears from every chart within
  an hour.

```sql
CREATE TABLE telemetry.request_outcomes_daily
(day Date, app_version LowCardinality(String), os LowCardinality(String),
 kind LowCardinality(String), state LowCardinality(String), failed_stage LowCardinality(String),
 route LowCardinality(String), lossless_outcome LowCardinality(String), no_results UInt8,
 requests UInt64, installs UInt32)
ENGINE = MergeTree ORDER BY (day, app_version, os, state);

CREATE MATERIALIZED VIEW telemetry.request_outcomes_daily_rmv
REFRESH EVERY 1 HOUR TO telemetry.request_outcomes_daily AS
SELECT event_date AS day, app_version, os, kind, state, failed_stage, route, lossless_outcome,
       no_results, count() AS requests, uniqExact(install_id) AS installs
FROM telemetry.v_request_finished
GROUP BY day, app_version, os, kind, state, failed_stage, route, lossless_outcome, no_results;
```

The same pattern gives four more rollups:

| Rollup | Columns | Powers |
|---|---|---|
| `attempts_daily` | `day, app_version, os, purpose, outcome, results_seen, queue_wait, first_byte, speed, attempts, installs` | C3, C4 |
| `setup_funnel` | `app_version, os, step, action, installs`. Per install, the furthest step reached, via `argMax(step_order, seq)` | C6 |
| `active_daily` | `day, app_version, os, channel, installs, sessions, crashed_sessions`, from `app_started` and `error_caught` with `fatal` | C9, C10, C11 |
| `actions_daily` | `day, app_version, event, action/answer/which, result, n, installs`. Covers `keep_anyway`, `extended_*`, `request_action`, `review_answered` and `update_step` | C7, C8, C9 |

### 7.4 Anonymous archive (k ≥ 5)

Once a day, an `APPEND` refreshable MV copies the day that is 170 days old from each rollup into
`telemetry.archive_daily`, as `(day, metric, dims Map, n, installs)`. Before it writes:

- any row with `installs < 5` is folded into `dims = {'other': '1'}`;
- the install counts are kept, but no ID or ID hash is.

The archive holds no personal data, so it may outlive the raw data. Its TTL is 2 years.

### 7.5 Deletions

```sql
CREATE TABLE telemetry.deletions
(id_sha256 FixedString(32), requested_at DateTime DEFAULT now())
ENGINE = MergeTree ORDER BY requested_at TTL requested_at + INTERVAL 30 DAY;
```

## 8. Charts

Every chart applies the k≥5 suppression of §4.5.

| # | Chart | Source | Answers |
|---|---|---|---|
| C1 | Outcome mix per release: a stacked share of done, duplicate, rejected, not found, error and cancelled by `app_version` | `request_outcomes_daily` | Q1, Q4 |
| C2 | Failure stage × OS × version heatmap (installs affected, not events) | `request_outcomes_daily` | Q4 |
| C3 | Why Soulseek misses: outcome share, with `no_pick` split into "0 results" and "nothing close enough" | `attempts_daily`, `request_outcomes_daily.no_results` | Q2 |
| C4 | Queue-wait and first-byte distributions (bucket histograms), and the share of attempts ending in `queued`, `first_byte_timeout` or `too_slow` | `attempts_daily` | Q3 |
| C5 | Route share over time (Soulseek lossless, Deezer MP3, none), split by `bot` and `catalog` state | `request_outcomes_daily` | Q5 |
| C6 | Onboarding funnel: the share of installs reaching each step, and skip and fail rates for Telegram and Soulseek | `setup_funnel` | Q9 |
| C7 | Extended offers: the found rate per filed track, answered get, keep or ignored, and the get result | `actions_daily`, `ingest_daily` | Q8 |
| C8 | Keep it anyway per 100 refusals by tier and kind, and the share after listening | `actions_daily` and `quality_check` counts | Q6, Q7 |
| C9 | Update funnel (check → download → signature → install → restart) and version-adoption curves | `actions_daily`, `active_daily` | Q10 |
| C10 | Crash-free sessions per version, and the top `exc_class`/`module` pairs | `active_daily`, raw `error_caught` | Q4 |
| C11 | Daily and weekly active installs by OS and channel | `active_daily` | context |

## 9. Prior art (primary sources)

- **Homebrew** ([docs.brew.sh/Analytics](https://docs.brew.sh/Analytics)).
  - Opt-out with a notice shown before the first event.
  - The payload has no user ID and no IP field.
  - Events are kept 365 days in InfluxDB, and aggregates are public.
  - Sending runs in a detached background process that fails silently with a short timeout.
  - `HOMEBREW_ANALYTICS_DEBUG` shows what is sent.
  - **Taken:** fail-silent background sending, a debug view, public aggregates as a later option.
  - **Not taken:** opt-out (Flackey is opt-in), and having no ID. Without an ID, Homebrew can do neither
    funnels nor per-user deletion.
- **VS Code** ([code.visualstudio.com/docs/configure/telemetry](https://code.visualstudio.com/docs/configure/telemetry)).
  - `telemetry.telemetryLevel` is `all`, `error`, `crash` or `off`.
  - "Developer: Show Telemetry" streams events as they are sent, and they are also written to
    `telemetry.log`.
  - `code --telemetry` dumps every possible event.
  - Stack traces are scrubbed of user paths.
  - **Taken:** the local "what's sent" view and the full catalogue command.
  - **Counter-example:** it identifies machines by a hash of the network adapter, and it says that
    approach is "not reliable enough for us to 'provide your data'". Flackey's random, resettable ID is
    what makes deletion possible.
- **Firefox.**
  - Opting out of technical and interaction data "will also [be treated] as a request to delete any data
    that was previously collected … within 30 days"
    ([support.mozilla.org](https://support.mozilla.org/en-US/kb/telemetry-clientid)).
  - On opt-out, a `deletion-request` ping carrying the client ID is sent, and retried until delivered,
    even with upload disabled
    ([deletion-request ping](https://firefox-source-docs.mozilla.org/toolkit/components/telemetry/data/deletion-request-ping.html)).
  - **Taken:** opt-out means delete (D4), with a persistent retry.
- **Plausible** ([plausible.io/data-policy](https://plausible.io/data-policy)).
  - It counts unique visitors with a hash of a daily-rotating salt, the site, the IP and the user agent,
    and says it never stores the raw IP.
  - **Not verified against the primary page in this session:** the site refused connections, and only
    mirrors of the policy were reachable. Re-check before quoting it.
  - **Lesson:** unique counts do not need a stored IP. Flackey does not need even the hash, because it has
    a consented random ID instead.

## 10. Decisions for the owner

- **D1: install ID or none.** A random, resettable ID: funnels (Q9), "installs affected" rather than
  "events" (Q4), crash-free sessions, and delete-my-data all need one. The cost is that the data is
  pseudonymous, so GDPR applies in full (§5.4). With no ID (Homebrew) none of those questions can be
  answered. **Pick: random ID.**
- **D2: where the collector runs.** A separate VPS, the VPN server, or ClickHouse Cloud. **Pick: separate
  VPS**, for the reasons in §6.4.
- **D3: raw retention, 90 or 180 days.**
  - 180 days spans about six releases, so a regression can be compared against several earlier versions.
  - 90 days is safer but loses the before/after comparison for slow-moving problems.
  - The promise can only ever be shortened later. **Pick: 180 days.**
- **D4: does opt-out delete.** Yes. It is one sentence in the notice, it matches Firefox, and it removes
  the awkward "I turned it off but you still have my data" case. **Pick: delete.**
- **D5: when to ask.**
  - The Welcome step (unticked) makes Q9 answerable, but asks before the user has seen the app work.
  - After the first filed track, people are better informed, but the onboarding funnel is lost entirely:
    anyone who drops off never reaches the question.
  - **Pick: Welcome step.** Onboarding friction is a known problem (#113).

## 11. Rollout

| Phase | What | Gate to the next phase |
|---|---|---|
| **1. Local dogfood** | Build `flackey.telemetry`, the schema, the tests and the Settings › Privacy UI. `FLACKEY_TELEMETRY=local` writes the outbox and never sends. The owner uses the app for a week and reads the outbox. | The schema tests pass. The owner has read a week of outbox and found no content. Every one of the top 10 questions can be computed from the outbox by hand. |
| **2. Owner's machine to the owner's collector** | The collector runs beside the lab's `docker-compose.yml` on `127.0.0.1`, writing the `telemetry` database (§7) with `telemetry.sql`, not `schema.sql`. The app gets `FLACKEY_TELEMETRY_URL=http://127.0.0.1:<port>`. The dashboards (C1–C11) are built. | The charts answer the questions. Delete-my-data, reset-ID and opt-out deletion are verified end to end. The outbox caps and the dead-collector path are tested (stop the container for a day: downloads unaffected, outbox capped). |
| **3. Beta** | VPS collector live on `telemetry.<domain>`: Caddy with logs off, firewall, backups ≤ 7 days, DPA accepted. The privacy notice is published. 3–5 testers opt in after being told directly. | Two weeks with no ingest errors, no content found in a raw-table audit, and k≥5 suppression working on dashboards. The testers' feedback on the wording is in. |
| **4. General opt-in** | The Welcome-step card and the one-time notice for existing installs ship in a release. README and site disclosure. Release note labelled `enhancement`. | — |

## 12. Hard to reverse: the promises that bind

Once data is collected under a promise, that promise holds for that data. Each of these is a choice that
cannot be quietly undone later.

1. **The never-collect list (§4.1) and "nothing about what you download".**
   - Widening it later needs fresh consent from everyone, not just a release note.
   - Field names that would ever break it should not be reserved "for later".
2. **The retention ceiling (D3).** Data collected under "180 days" cannot later be kept for a year.
3. **The purpose (§5.4).** Usage data collected "to improve Flackey" cannot later feed anything else.
4. **The ID semantics (D1).**
   - Starting without an ID cannot be fixed retroactively; funnels for that period are lost.
   - Starting with one creates GDPR duties from day one.
5. **The collector URL.** Every shipped build sends to it for as long as it is installed, so use an owned
   domain (§6.4).
6. **New kinds of data**, such as full stack traces or per-track timing, are not a schema bump. They are a
   new consent: ask again, and keep the old consent's data under the old rules.
