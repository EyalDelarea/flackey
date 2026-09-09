# Flackey

Personal DJ library builder with a dark, pro-audio-style desktop GUI. Paste a
YouTube or YouTube Music link into the app; a worker running on the Mac finds
the track through a Telegram source bot (using your own Telegram account),
matches it on Beatport, verifies it is genuine 320 kbps or better, tags it
and files it under `~/Music/DJ Library/<Artist>/`, and writes M3U8 playlists
for Rekordbox import. BPM and key are left to Rekordbox's analysis; genre and
label are in the tags.

## Requirements

- macOS
- Homebrew `ffmpeg` and `yt-dlp`
- [uv](https://docs.astral.sh/uv/), Python 3.12

## Setup

```bash
cp .env.example .env   # fill in TELEGRAM_API_ID, TELEGRAM_API_HASH
uv sync
npm --prefix web install
npm --prefix web run build   # builds web/dist, which flackey start serves
```

## Usage

```bash
uv run flackey start          # worker + web UI, until Ctrl-C; opens the UI in a browser
uv run flackey start --no-browser
```

The project was called cratedigger until the rename, so `crate` is kept as an
alias for `flackey` — every command below works under either name.

The first launch walks you through a short setup: pick the library folder,
then sign in to Telegram (scan a QR code with the Telegram app, or use a
phone number instead) — this authorizes your own Telegram account, via
Telethon, as the account that talks to the source bot. `uv run flackey login`
is an optional, terminal-only way to do that same sign-in instead of the
setup screen; neither path stores the two-step verification password.

Then paste a YouTube or YouTube Music track/playlist link into the UI.
Plain text is refused and queues nothing. Useful companions:

```bash
uv run flackey add "https://music.youtube.com/watch?v=…"   # enqueue from the Mac, no phone needed
uv run flackey status                                     # queue counts and library size
uv run flackey export                                     # rewrite every M3U8 playlist file
```

## Rekordbox import

No `rekordbox.xml` is written, on purpose: Rekordbox's XML bridge is a
one-way import, and a generated XML carries none of the owner's hot cues,
memory cues, or ratings, so regenerating it after every track would make one
careless drag destructive. Instead:

1. Drag `~/Music/DJ Library` (or an artist folder) into the Rekordbox
   collection. Rekordbox skips tracks it already knows, so this is safe to
   repeat, and existing tracks' Rekordbox data is never touched. New tracks
   pick up their Beatport tags and artwork from the file itself.
2. File -> Import -> Playlist, choose `~/Music/DJ Library/Playlists/<name>.m3u8`.
   Re-importing the same file after new tracks are added is also safe: cues
   set on tracks already in the collection are preserved.

See `docs/superpowers/specs/2026-09-03-flackey-design.md` section 8 for
the full reasoning.

## Notes on Telegram behaviour

- **Session expiry**: if the owner's Telethon session goes missing or
  expires, `flackey start` does not exit — the web UI keeps running so links
  still queue, but the worker pauses. `/api/health` reports
  `telegram_authorized: false`, and the UI shows an amber "Telegram signed
  out" banner with a Reconnect button. Reconnecting takes you through the
  same sign-in screen as first-time setup (QR code or phone number), without
  restarting `flackey start`; the worker resumes automatically once you're
  signed back in.

## Lossless via Soulseek

Optional. With a running [slskd](https://github.com/slskd/slskd) sidecar, every request first asks the
Soulseek network for a FLAC of the matched track. The file is verified (spectral cutoff), proven to be the
same recording as the Deezer match with a Chromaprint fingerprint against Deezer's 30 s preview, converted to
AIFF (audio untouched, peer tags dropped) and tagged from Beatport. On any miss the Deezer MP3 is fetched as
before. The Done line says what you got: `AIFF 16-bit/44.1 kHz, from FLAC via Soulseek` or
`MP3 320 kbps via Deezer`.

1. `brew install chromaprint` (for `fpcalc`; without it the recording check is skipped and shown as such).
2. Download the slskd release for macOS, put it in `~/Library/Application Support/Flackey/slskd/` with a
   `slskd.yml` like:

   ```yaml
   web:
     port: 5030
     authentication:
       api_keys:
         flackey: { key: "<random 32+ chars>", role: readwrite }
   soulseek:
     username: <your soulseek account>
     password: <its password>
     listen_port: 50300
   shares:
     directories: ["~/Music/DJ Library"]
   directories:
     downloads: ~/Library/Application Support/Flackey/slskd/downloads
   ```

   Start it with `./slskd --app-dir "~/Library/Application Support/Flackey/slskd"`. Sharing your library is
   what makes peers serve you; forward TCP 50300 on the router so they can reach it (searching works without).
3. Put the same key in `.env` as `SLSKD_API_KEY=` (or in `settings.json` as `slskd_api_key`). Restart.
4. `/api/health` shows `lossless.provider.status` (`ok`, `not_logged_in`, `unreachable`), whether `fpcalc` is
   present, the last 24 h of attempt outcomes and the size of the raw attempt folder.

Every attempt is recorded: `GET /api/lossless/attempts`, the attempt block on a request, and the raw slskd
responses under `<data dir>/lossless/attempts/<id>/` (pruned after `LOSSLESS_KEEP_RAW_DAYS`, default 30).
`flackey lossless replay <request id>` re-runs the pick under the current settings and shows what changed.
A transfer cancelled by a cap can still complete on slskd's side; such files stay in slskd's downloads folder
and can be deleted at any time.

## Where data lives

- `~/Library/Application Support/Flackey/` on macOS; `~/.config/flackey/` elsewhere. Two renames are
  behind us — the project was called cratedigger, and before the Mac-native move its data lived under
  `~/.config` — so first launch looks for either older folder, newest first, and brings it across:
  copied, verified file by file, and only then is the old one removed, with `cratedigger.sqlite` (and
  its `-wal`/`-shm` sidecars) and `cratedigger.log` renamed by prefix on the way. If the copy is
  incomplete both folders are kept and the failure is logged, never a half-migrated library.
  Contains the sqlite database, `settings.json`
  (with the library folder and Telegram api id/hash), the Telethon session, tmp downloads, and spectrogram PNGs.
  `settings.json` is created from env vars and can be updated via the app; env vars always override it.
- `~/Music/DJ Library/` — one folder per artist, plus `Playlists/*.m3u8`.

## Docker

```bash
docker build -t flackey .
docker run --env-file .env -v flackey-data:/data -v /path/to/library:/library -p 8765:8765 flackey
```

Open `http://localhost:8765` and sign in to Telegram from the setup screen
(QR code or phone number). The Telethon session is stored in the `/data`
volume, so this only needs to happen once. `uv run flackey login` also works
as a terminal-only alternative, run once beforehand:

```bash
docker run --env-file .env -it -v flackey-data:/data flackey uv run flackey login
```

## Development

```bash
npm --prefix web install
npm --prefix web run build    # needed once before flackey start
uv run pytest -q
uv run ruff check src tests
uv run lint-imports   # module layering; see [tool.importlinter] in pyproject.toml for the exact contract
```

`flackey start` serves `web/dist` when present; otherwise the UI stays up but shows a 404. During development, run `npm --prefix web run dev` to start Vite on `:5173` proxying to `:8765`.

## More

- Design spec: `docs/superpowers/specs/2026-09-03-flackey-design.md`
- Implementation plan: `docs/superpowers/plans/2026-09-03-flackey-core.md`
- Source bot protocol notes: `docs/source-bot-protocol.md`
