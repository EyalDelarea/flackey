# Flackey GUI — Design Spec

Status: decisions agreed 2026-09-06; visual design chosen the same day. The owner picked the
"dark pro-audio" mockups made in Claude Design
(https://claude.ai/design/p/5c58f04e-9bb6-48ec-b641-b26820e343e6). Exact tokens, layout
values, and copy are recorded in `docs/superpowers/design/gui/DESIGN.md`; the screens below
describe behaviour and defer to that file for looks.
Supersedes section 3.5 (Web UI) and the Telegram inbox parts of sections 3.1, 3.3, 4.3 and 4.4
of `2026-09-03-flackey-design.md`. Everything else in that spec stands.

## 1. Purpose

Replace the Telegram inbox bot with a desktop GUI, so that a friend with no technical
background can install Flackey on their Mac, connect it to Telegram, paste YouTube links,
and at every moment understand what is happening to each track, where the files went, and
what to click next.

## 2. Decisions

| Question | Decision | Why |
|---|---|---|
| Who runs it | Each friend runs their own copy on their own Mac | The Deezer source is driven through a Telegram *user* account and files land on local disk. A hosted copy would put all traffic through one account and one machine. |
| Telegram inbox bot | Removed entirely: no aiogram, no bot token, no phone notifications | Two fewer credentials to set up; the GUI is the only way in. |
| Middleman / relay through one account | Rejected for now; kept possible | One serial pipe per account (~5 tracks a minute), one ban kills everyone, owner uptime becomes everyone's uptime, all downloads performed by the owner. The `Source` interface (search, fetch) already isolates this, so a relay source can be added later without touching the GUI. |
| Telegram login | QR code first, phone + code as fallback, 2FA password when the account has one | Same flow as logging into Telegram Desktop; nothing developer-facing. |
| App credentials (api id / hash) | Live in the settings file (`settings.json` in the app data folder), never in the repo; `.env` overrides them for development. A packaged copy ships with a prefilled settings file. Decided 2026-09-06 (replaces "package constants"). | They identify the *software*, not the person, and every Telegram client must present a pair. Keeping them out of git means the public repo carries no credentials; the friend still never sees them because the packaged app writes them for them. |
| Honesty note on the setup screen | One plain sentence: you are signing in with your own Telegram account; unofficial clients are occasionally flagged; a spare number works too | Friends should know their account is the thing at stake. |
| Input | YouTube / YouTube Music track and playlist links only | Same as today. Free-text search is out of scope. |
| Distribution | A packaged Mac app (double-click to run) | Friends are not developers. |
| Screen quality bar | A first-time user understands each track's progress, the file's location, and the next action without reading docs | This is the acceptance test for every screen. |

## 3. Stack

- **App shell:** pywebview native macOS window over the existing FastAPI server, packaged
  with PyInstaller into a `.app`. In development the same server opens in a browser via
  `crate start`.
- **Frontend:** React + Vite + TypeScript, built to static files served by FastAPI from
  `web/dist` (already wired in `app.py` and `web.py`).
- **Live updates:** Server-Sent Events. An in-process event bus receives every request
  state change from the worker; the page holds one `EventSource` and patches its state.
- **Settings:** `settings.json` in the app data folder holds the library folder and the Telegram
  api id/hash; the API reads and writes it so the setup screen can save the folder. Environment
  variables and `.env` still override every key, for development and Docker. The data folder
  moves from `~/.config/flackey/` to `~/Library/Application Support/Flackey/` on macOS
  (the Mac-native place the mockup shows) and is migrated on first launch if the old one exists;
  `DATA_DIR` still overrides it, so Docker is unaffected.
- **Notifications:** the `Notifier` port stays (the worker still describes what it did) but the only
  implementation is a log-line notifier; the GUI learns everything from the SSE stream. The
  Telegram notifier goes with the bot.
- **Bundled tools:** ffmpeg and ffprobe as static binaries inside the app bundle; yt-dlp
  as the Python package already in the dependencies.
- **Signing:** notarised with an Apple Developer ID if the owner gets one (99 USD/year);
  otherwise friends right-click → Open once on first launch.

## 4. Screens

A 1200×800 window with a left sidebar of three sections, Download, Library, Settings, and a
persistent Telegram status in the sidebar footer. A 760×600 setup window runs before the
first launch. Stats live in the Library header rather than on a page of their own. Looks,
exact copy, and measurements: `docs/superpowers/design/gui/DESIGN.md`.

### 4.1 Setup (first launch, and again from "Reconnect")

1. **Folder.** "Where should your music live?" Default `~/Music/DJ Library`, native folder
   picker, Continue.
2. **Telegram.** A QR code that refreshes every 30 seconds, with the three-step instruction
   for the Telegram app. When Telegram answers that the account has a two-step password,
   a password box appears in place. "Use phone number instead" switches to phone → code →
   password. The honesty note (section 2) is the footnote of this step.
3. **Ready.** "You're set", the library path, and "Start digging". Behind the scenes this
   step also confirms the bundled ffmpeg and yt-dlp run; a failure replaces the ✓ with the
   reason and a "Try again" button.

### 4.2 Download (main screen)

- **Paste bar** at the top: one field, "Paste a YouTube or YouTube Music link", and Add.
  On Add the link resolves immediately: a playlist expands into its rows; a link that
  cannot be read explains itself under the field.
- **Groups**: one card per pasted playlist, newest first, headed by the playlist name and a
  summary "12 of 40 filed · 1 needs your choice · 1 rejected". Single tracks share one
  "Single tracks" group.
- **Row anatomy**: artwork (Beatport art once matched, a placeholder before), title with the
  version in muted parentheses, a plain-words status line, a five-rung progress ladder, and at
  most one button. The rungs are Search, Choose, Download, Verify, Done, each with hover text
  saying what happens in it; the current one pulses amber, finished ones are green. There is no
  File rung: a rung earns its place if a request can stop on it, and filing never stops one.
- **Row states**: in progress ("Downloading the file"); filed (green mono path
  relative to the library folder plus "320 kbps verified", button "Show in Finder"); needs
  your choice (amber-washed row, reason with the video length, one card per candidate with
  length, Beatport presence, match percentage and "Use this", plus a quiet "Skip this
  track" link that the mockup lacks but the cancel action needs);
  rejected (red status, "See why" toggles the spectrogram with a one-sentence explanation);
  already in your library ("skipped, nothing downloaded twice", "Show in Finder"); queued
  ("Waiting its turn", dimmed).
- **Beatport unreachable** shows as a red banner under the affected group: "Beatport is
  unreachable. Matching is paused — trying again in N seconds. Nothing is lost." with
  "Try now". N is the worker's real backoff.
- **Telegram signed out** shows as an amber banner under the title bar with "Reconnect",
  the sidebar footer turns amber, and in-flight rows dim with "Paused — will continue after
  you reconnect".

### 4.3 Library

- Sidebar grows a "Playlists" list: All tracks, then one entry per playlist.
- Header: search "Search artist, title or label", the counts "418 tracks · 6 playlists ·
  3.2 GB on disk", and "Open library folder".
- With a playlist selected, a card shows its name and track count, the two-line Rekordbox
  import instruction, and "Show playlist file", which reveals the M3U8 in Finder.
- Table columns: artwork, track (title, version beneath), genre, label · year, kbps with a
  green ✓ when verified, and "Show in Finder".

### 4.4 Settings

Four cards: Library folder with "Change"; Telegram with the masked phone number and
"Sign out"; App version (no update button until an update channel exists); App data with
the folder path and "Show in Finder".

## 5. API changes (sub-project 1)

- `POST /api/requests` `{url}` → resolves the link with yt-dlp, queues requests, returns
  the created request ids and the playlist record; 400 with a plain-language message on a
  link that cannot be read.
- `GET /api/events` → SSE stream. Events: `request` (a full request bundle whenever its
  state changes), `status` (telegram_authorized, worker_running), `track` (a newly filed
  track).
- `GET /api/settings`, `PUT /api/settings` → library folder; read-only fields for the data
  folder and version.
- Setup / login endpoints (sub-project 3): `POST /api/telegram/qr` starts a QR login and
  returns the login URL to encode; `GET /api/telegram/qr/{id}` polls its state
  (waiting / password_needed / done / expired); `POST /api/telegram/password`;
  `POST /api/telegram/phone`, `POST /api/telegram/code`.
- Removed: everything aiogram. `Inbox` keeps only `enqueue_youtube` and becomes the
  service behind `POST /api/requests`.
- Reveal-in-Finder: `POST /api/reveal` `{path}` restricted to paths inside the library
  folder; calls `open -R`.

## 6. Sub-projects, in build order

Decided 2026-09-06: sub-projects 1, 2 and 3 are built now on one branch (`feat/gui-app`);
sub-project 4 waits until the app is judged good in a browser.

1. **Backend for the GUI.** Remove aiogram and the inbox bot. Add the submit endpoint,
   the event bus + SSE, the settings file, the data-folder move. Keep `crate start` working
   in a browser.
2. **The GUI.** Vite project under `web/`, the three tabs, live updates, review inline,
   Finder reveal. Built output is not committed; `npm run build` produces `web/dist`, and
   `crate start` says so when it is missing.
3. **Guided setup.** QR login flow end to end, phone fallback, reconnect banner, tool
   check.
4. **Mac app** (later, own branch). pywebview window, PyInstaller spec, bundled
   ffmpeg/ffprobe, signing notes, a README for friends.

One implementation plan covers 1 to 3: `docs/superpowers/plans/2026-09-06-gui-app.md`.

## 7. Open items for the owner

- Apple Developer account for notarisation (99 USD/year), or accept the right-click → Open
  step for friends.
- Whether the `crate` CLI is shipped to friends at all (current assumption: no; it stays
  a developer tool).
