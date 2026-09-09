# Krater — Design Spec

Date: 2026-09-03
Status: Approved by owner after brainstorming and grilling session

## 1. Purpose

Krater turns "I like this track" into a verified, correctly tagged audio
file in a permanent DJ library on the owner's Mac, ready for import into
Rekordbox. It is a personal tool for one user.

Success looks like: the owner sends a track name or a YouTube Music link from
their phone, later starts the program on the Mac, and finds the track filed
under `~/Music/DJ Library/<Genre>/<Label>/`, tagged from Beatport, verified as
genuine 320 kbps or better, with an M3U8 playlist file for the playlist it
came from, ready to import into Rekordbox.

## 2. Scope

### In scope (v1)

- Telegram inbox bot owned by the user, accepting requests from the owner only.
- Durable request queue that survives the Mac being off.
- Fetching audio from the third-party Telegram bot `@DeezerMusicBot`, driven
  through the owner's own Telegram account.
- Track identification and metadata from Beatport.
- Confidence-scored matching with a review flow for uncertain matches.
- Quality verification via bitrate and spectrogram cutoff analysis.
- Tagging with full metadata and embedded cover art.
- Filing into a permanent library folder with a fixed layout.
- M3U8 playlist files, one per submitted YouTube playlist.
- Duplicate detection.
- Local React web UI: queue, review inbox, rejections, library, stats.
- Command-line entry point to start everything.
- Git repository, Docker image, automated tests.

### Deferred (explicitly not v1)

- Backup to an external drive (owner decision: handled later).
- Auto-start at login (launchd).
- Direct Deezer subscription client (the source interface is designed for it).
- Spotify, Deezer, SoundCloud, Shazam link parsing.
- Audio analysis of BPM or key. Rekordbox does its own on import.
- YouTube audio as a low-quality fallback. Not on the source means stop.
- Reading Rekordbox's own database or XML export for stats.
- Multiple users or an allowlist.

## 3. User-facing behavior

### 3.1 Sending a request

The owner sends the inbox bot one of:

- Free text, e.g. `astral projection into the void`.
- A YouTube or YouTube Music track link.
- A YouTube or YouTube Music playlist link.

Anything else gets the reply "Send me a track name, a YouTube Music track
link, or a playlist link." Messages from any Telegram user other than the
owner are ignored silently.

The bot replies immediately with "Queued" and the request's position. A
playlist is expanded into one request per track plus a playlist record; the
reply says how many tracks were queued and how many were already in the
library.

### 3.2 Processing

Requests are processed one at a time in arrival order whenever the program is
running. For each request:

1. **Identify.** Clean the text or the YouTube title into artist, title, and
   optional version. Look the track up on Beatport.
2. **Search the source.** Query `@DeezerMusicBot` with the cleaned text and
   collect its candidate results.
3. **Score.** Compute a confidence score for the best candidate (section 5).
4. **Decide.** Score ≥ 80 and a Beatport match: proceed automatically. Score
   < 80, or no Beatport entry: park the request in the review inbox and
   notify the owner (section 5.3).
5. **Fetch.** Trigger the source's download of the chosen candidate and
   receive the file through the owner's account.
6. **Verify.** Bitrate and spectrogram check (section 6). Failure deletes the
   file, records the rejection with its spectrogram image, notifies the
   owner, and ends the request.
7. **Tag and file.** Write tags and cover art, move the file to its final
   path (section 7), record it in the library index.
8. **Export.** Rewrite the M3U8 file of the playlist the track belongs to,
   if any.
9. **Report.** Telegram reply: `Done: Astral Projection – Into the Void
   (Original Mix) · 7:22 · 320 kbps verified · 142 BPM · A Major · Psy-Trance
   / Sacred Technology · 96%`.

### 3.3 Review inbox

A parked request shows in Telegram as a short message with numbered buttons
for the top candidates and a Cancel button, and in the UI with full detail:
the request, each candidate's artist, title, mix name, duration, source, and
Beatport match, and the reason it was flagged. Choosing a candidate in either
place resumes processing from step 5. Cancelling closes the request.

### 3.4 Duplicates

A request whose track is already in the library is closed with "Already in
library" and a link to the existing file. Matching is by Beatport ISRC when
available, otherwise by normalized artist, title, mix name, and a duration
within 3 seconds.

Re-sending a playlist link syncs it: new tracks are queued, existing tracks
are added to the playlist record without re-downloading.

### 3.5 Web UI

Served by the program at `http://localhost:<port>` and opened in the browser
on start. Sections:

- **Queue**: every open request with its state and progress.
- **Review**: parked requests with candidate detail and choose or cancel
  actions.
- **Rejections**: failed verifications with reason and spectrogram image.
- **Library**: searchable, filterable list of filed tracks with tags, path,
  and quality verdict.
- **Stats**: counts of fetched, verified, rejected, and pending; storage
  used; breakdown by genre and label; requests per week.

The UI is designed as a distinctive product, not a generic admin dashboard.
The frontend design skill is invoked when building it.

## 4. Architecture

One Python process, started by `crate start`, running four cooperating
components on a single asyncio loop and sharing one SQLite database:

```
Telegram (owner's phone)
        │  messages to @<inbox bot>
        ▼
┌─────────────────┐     ┌──────────────────────────────┐
│  Inbox bot      │──▶──│  SQLite: requests, playlists, │
│  (aiogram)      │     │  candidates, tracks,          │
└─────────────────┘     │  rejections, settings         │
                        └──────────────┬───────────────┘
                                       │
                 ┌─────────────────────┼─────────────────────┐
                 ▼                     ▼                     ▼
       ┌─────────────────┐   ┌──────────────────┐   ┌────────────────┐
       │  Worker         │   │  Web API + UI    │   │  Exporter      │
       │  (pipeline)     │   │  (FastAPI+React) │   │  (m3u8         │
       └───────┬─────────┘   └──────────────────┘   │   playlists)   │
               │                                    └────────────────┘
   ┌───────────┼─────────────┬──────────────┐
   ▼           ▼             ▼              ▼
Beatport   Source:        Verifier      Tagger + Filer
lookup     @DeezerMusicBot (ffmpeg)     (mutagen)
(curl_cffi) via Telethon
```

### 4.1 Components

| Component | Responsibility | Depends on |
|---|---|---|
| `inbox` | Receive owner messages, parse into requests, reply with status, present review buttons | aiogram, `store` |
| `store` | SQLite schema and all reads/writes; the only module that touches the database | sqlite3 |
| `identify` | Turn free text or a YouTube link into a normalized query (artist, title, version). Expand playlists via yt-dlp | yt-dlp |
| `catalog` | Beatport search and track lookup. Returns `CatalogTrack` records | curl_cffi |
| `source` | Abstract `Source` interface: `search(query) -> [Candidate]`, `fetch(candidate) -> Path`. v1 implementation `DeezerBotSource` drives `@DeezerMusicBot` through Telethon | Telethon |
| `match` | Confidence scoring between query, candidates, and catalog | none |
| `verify` | Bitrate probe and spectrogram cutoff check; produces a verdict and a PNG | ffmpeg, ffprobe |
| `tag` | Write ID3 / Vorbis tags and cover art | mutagen |
| `library` | Final path computation, filing, duplicate detection | `store` |
| `export` | Write M3U8 playlist files from the library index and playlists | `store` |
| `worker` | The pipeline: pulls the next request, runs the steps, handles parking and resumption | all of the above |
| `web` | FastAPI routes serving JSON to the React UI and the built UI itself | `store` |
| `cli` | `crate start`, `crate status`, `crate export` | all |

Each module exposes a small typed interface and can be tested in isolation.
`source` and `catalog` are the two modules expected to break due to external
changes, so they contain no business logic and are covered by tests against
recorded fixtures.

### 4.2 Data model (SQLite)

- `requests`: id, created_at, raw_text, kind (text / yt_track / yt_playlist),
  playlist_id, state (queued / identifying / awaiting_review / fetching /
  verifying / filing / done / rejected / cancelled / not_found / error),
  query_artist, query_title, query_version, chosen_candidate_id,
  confidence, error_message, updated_at.
- `candidates`: id, request_id, rank, source, source_ref, artist, title,
  mix_name, duration_s, score, catalog_track_id.
- `catalog_tracks`: id (Beatport track id), isrc, artist, title, mix_name,
  label, genre, sub_genre, catalog_number, release_date, bpm, key,
  duration_ms, artwork_url, fetched_at.
- `tracks` (the library index): id, catalog_track_id, path, format,
  bitrate_kbps, cutoff_hz, verified_at, file_size, added_at, request_id.
- `playlists`: id, source_url, name, created_at, updated_at.
- `playlist_tracks`: playlist_id, track_id, position.
- `rejections`: id, request_id, reason, bitrate_kbps, cutoff_hz,
  spectrogram_path, created_at.
- `settings`: key, value.

### 4.3 Configuration

`.env` in the project root, never committed:

- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`: owner's app credentials from
  my.telegram.org.
- `INBOX_BOT_TOKEN`: token from BotFather for the inbox bot.
- `OWNER_TELEGRAM_ID`: numeric user id allowed to use the inbox.
- `SOURCE_BOT_USERNAME`: `DeezerMusicBot`.
- `LIBRARY_ROOT`: default `~/Music/DJ Library`.
- `WEB_PORT`: default 8765.

The Telethon session file lives in `~/.config/krater/` with mode 600 and
is never committed.

### 4.4 Lifecycle

`crate start` runs the inbox bot, the worker, and the web server together and
opens the UI. On start, the worker resumes any request that was mid-flight
when the program last stopped (states other than done, rejected, cancelled,
not_found, error, awaiting_review are reset to queued). Telegram holds inbox
messages for 24 hours while the program is off; anything older is lost, which
is accepted for v1 and noted in the README.

## 5. Matching and confidence

### 5.1 Normalization

Artist and title strings are lowercased, stripped of punctuation and of
noise tokens common in YouTube titles (`official`, `video`, `audio`, `hd`,
`lyrics`, `visualizer`, bracketed uploader tags), and the version is
extracted from parentheses or after a dash (`Original Mix`, `<name> Remix`,
`Radio Edit`, `Extended Mix`, `Live`).

### 5.2 Score

Score is 0 to 100, computed for each candidate against the query and the best
Beatport entry:

| Signal | Weight | Notes |
|---|---|---|
| Artist similarity | 25 | Token-set ratio |
| Title similarity | 25 | Token-set ratio |
| Version agreement | 20 | Full if the requested version matches; if no version was requested, full for Original Mix, zero for remixes, edits, live |
| Duration agreement with Beatport | 30 | Full within 2 s, linear to zero at 15 s difference |
| ISRC match | override | Beatport ISRC equal to the source's ISRC when the source exposes it: score is 100 |

Rules:

- If the request names a version, only candidates with that version are
  eligible.
- If it does not, and only non-original versions exist, the request is
  parked regardless of score.
- No Beatport entry found: parked regardless of score, and the duration
  signal is replaced by agreement with the YouTube duration when the request
  came from a link.

### 5.3 Review notification

Telegram message: request text, then up to five candidates as
`1. Artist – Title (Mix) · 7:22 · 61%`, then the flag reason, with inline
buttons `1` to `5` and `Cancel`. The same information appears in the UI's
Review section with the Beatport comparison expanded.

## 6. Quality verification

Every fetched file passes two checks:

1. **Bitrate probe** via ffprobe. MP3 must report ≥ 320 kbps. FLAC, WAV, and
   AIFF pass this check by definition. AAC/M4A and Opus are rejected in v1
   because the source does not deliver them and their ladders are undefined.
2. **Spectrogram cutoff** via ffmpeg. Analyze a 60 second window from the
   middle of the track. Compute the highest frequency band whose average
   energy sits above a noise floor. A genuine 320 kbps MP3 shows content to
   roughly 19.5 to 20 kHz. A cutoff below 18 kHz indicates the file was
   upsampled from a lower bitrate and fails. Lossless files must show content
   above 20 kHz or be flagged as suspicious (a lossless container around a
   lossy source) and rejected.

Verdict, measured cutoff, and a PNG spectrogram are stored. Failures delete
the audio file and keep the record and image.

Quality ladder when the source offers several formats: FLAC, then WAV/AIFF,
then MP3 320. Nothing below.

## 7. Tagging and filing

Tags written (ID3v2.4 for MP3, Vorbis comments for FLAC): title, artist,
album (Beatport release name), album artist, genre (Beatport genre), label
(publisher), catalog number, release year and date, mix name (in the title
suffix and as a custom field), ISRC, BPM, initial key (classical notation
such as `A Major`, which Rekordbox can display as Camelot), comment
`krater: verified 320 kbps · cutoff 19.8 kHz · beatport <id>`, and the
Beatport artwork embedded at 1400 px.

Final path:

```
<LIBRARY_ROOT>/<Genre>/<Label>/<Artist> - <Title> (<Mix Name>).<ext>
```

Path segments are sanitized for the filesystem. Files are never moved after
filing. A collision with an existing path is treated as a duplicate (section
3.4).

## 8. Playlist export and Rekordbox import

No `rekordbox.xml` is written, on purpose. Rekordbox's XML bridge is a
one-way import: pulling a track that is already in the collection from the
bridge again refreshes it from the XML, and a generated XML carries none of
the owner's hot cues, memory cues or ratings. A tool that rewrites that file
after every track would make one careless drag destructive. (Decided
2026-09-04.)

Instead the files carry everything in their tags (section 7) and the owner
imports them by dragging `<LIBRARY_ROOT>` or a Genre/Label folder into the
Rekordbox collection. Rekordbox skips files it already knows, so this is safe
to repeat, and Rekordbox's own data for existing tracks is never touched.

Playlists are exported as Extended M3U files: `<LIBRARY_ROOT>/Playlists/<name>.m3u8`,
UTF-8, `#EXTM3U` header, `#PLAYLIST:<name>`, one `#EXTINF:<seconds>,<Artist> - <Title>`
line plus the absolute file path per track, in playlist order. The file for a
playlist is rewritten atomically whenever one of its tracks is filed, and all
of them on `crate export`. The owner imports one with File → Import →
Playlist in Rekordbox (tracks not yet in the collection are added by that
import).

## 9. Error handling

- **Source unavailable** (bot silent for 90 seconds, or returns an error):
  request state `error`, owner notified, request retried up to three times
  with backoff on the next worker cycle, then left in `error` for the UI.
- **Beatport unreachable**: request parked with reason "Beatport unreachable,
  retry later", not treated as "not on Beatport". Retried automatically.
- **yt-dlp failure on a link**: reply "Could not read that link. yt-dlp may
  need an update." and state `error`.
- **Verification failure**: as in section 6.
- **Telethon session expired**: program logs a clear message and the UI shows
  a banner "Telegram login required, run `crate login`".
- All errors are visible in the UI with the request they belong to. Nothing
  fails silently.

## 10. Testing

- Unit tests for `identify`, `match`, `verify`, `tag`, `library`, `export`
  with fixtures: real spectrograms of a genuine 320 and a fabricated fake,
  saved Beatport pages, sample YouTube titles.
- `source` and `catalog` tested against recorded interactions, plus one
  opt-in live smoke test each.
- Worker tested end to end with fake `Source` and `Catalog` implementations.
- Frontend component tests for the review flow and queue rendering.
- One manual QA path documented in the README: send a known track, watch it
  land, drag it into Rekordbox.

## 11. Repository layout

```
krater/
  pyproject.toml          # uv-managed, Python 3.12
  src/krater/        # modules listed in 4.1
  web/                    # React + TypeScript + Vite
  tests/
  docs/superpowers/specs/ # this file and successors
  Dockerfile
  .env.example
  README.md
```

## 12. Open items requiring owner input during the build

- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from my.telegram.org.
- `INBOX_BOT_TOKEN` from BotFather.
- One interactive login to the owner's Telegram account for Telethon.
- Confirmation of `@DeezerMusicBot`'s exact reply shapes, captured during the
  first live run and turned into fixtures.
