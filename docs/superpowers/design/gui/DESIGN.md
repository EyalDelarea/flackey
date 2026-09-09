# Krater GUI — visual design record

Source of truth: the owner's Claude Design project "Dark Pro-Audio Mockups",
file `Krater Mockups.dc.html`
(https://claude.ai/design/p/5c58f04e-9bb6-48ec-b641-b26820e343e6). Chosen 2026-09-06.
Every value below was read from that file's source, not eyeballed. A capture of the main
screen sits beside this file as `screen-download.jpg`; open the project link for the rest.

Design reasoning, in the designer's words: dark graphite like pro-audio gear (DJs live in
dark UIs: Rekordbox, Ableton); one warm amber accent for "needs you"; green strictly for
"verified and filed"; red for rejections. Every row answers three questions: a plain-words
status line says what is happening now, filed rows show the on-disk path in monospace, and
exactly one button per row says what to click. Six-step progress is dots plus words, never
jargon. Setup runs in a smaller focused window (760×600); the app itself is 1200×800.

## Tokens

### Colour

| Role | Value |
|---|---|
| Window background | `#141518` |
| Chrome (title bar, sidebar) | `#101114` |
| Card / panel | `#17181d` |
| Input / well | `#0e0f12` |
| Text | `#ecedef` |
| Text, muted | `#9a9da6` |
| Text, faint (mono labels, queued) | `#6b6e77` |
| Text, dimmed title (rejected row) | `#c8c9ce` |
| Amber accent (needs you, primary button) | `#f0a442` |
| Amber text | `#f5b95c` |
| Amber on-button text | `#1a1205` |
| Amber wash (active nav, choice row) | `rgba(240,164,66,.14)` nav · `rgba(240,164,66,.05)` row · `rgba(240,164,66,.12)` banner |
| Amber border | `rgba(240,164,66,.4)` chosen candidate · `rgba(240,164,66,.35)` banner, password box |
| Green (verified, filed, connected) | `#57c877` |
| Green wash | `rgba(87,200,119,.14)` fill · `rgba(87,200,119,.5)` border |
| Red (rejected, error) | `#e2695f` |
| Red wash | `rgba(226,105,95,.08)` fill · `rgba(226,105,95,.3)` border |
| Hairline | `rgba(255,255,255,.06)` chrome · `.07` card border · `.05` row divider · `.08` well border |
| Secondary button | fill `rgba(255,255,255,.06)`, border `rgba(255,255,255,.13)` (candidate variant `.07` / `.14`) |
| Input border | `rgba(255,255,255,.12)` |
| Progress dot, pending | `rgba(255,255,255,.15)` |
| Traffic lights | `#ff5f57` `#febc2e` `#28c840` |
| QR paper | `#f4f2ec`; Telegram blue badge `#2aa1da` |

### Type

Google Fonts: `Space Grotesk` 400/500/600/700 and `IBM Plex Mono` 400/500.
Body: `'Space Grotesk', system-ui, sans-serif`. Mono is for file paths, step labels,
match percentages, the setup stepper, the `queued` tag and column headers.

| Use | Font |
|---|---|
| Screen title (setup headings, Settings) | 24px 700 (Settings page title 20px 700) |
| Group heading (playlist name) | 15px 700 |
| Row title | 13.5px 600; version in parentheses 400 muted |
| Nav item | 13.5px, active 600 |
| Status line under title | 12px (choice row 12.5px) |
| Body copy in setup | 13.5px muted, line-height 1.55 |
| Button, primary | 600 13.5px (Add) · 600 12.5px (banner, setup) · 600 14px (Continue, Start digging) · 600 12px (Use this) |
| Button, secondary | 500 12px (row) · 500 12.5px (settings, library header) · 500 11.5px (library row) |
| File path | 400 11.5px IBM Plex Mono, green when verified |
| Step words / queued | 400 11px IBM Plex Mono, faint |
| Match % | 600 12px IBM Plex Mono |
| Column headers / setup stepper | 600 10.5px / 500 11.5px IBM Plex Mono, faint, letter-spacing .06–.08em |
| Hint under QR | 400 11px IBM Plex Mono, faint |

### Shape and spacing

| Element | Values |
|---|---|
| Window | 1200×800, radius 12, border `rgba(255,255,255,.09)`, shadow `0 24px 64px rgba(0,0,0,.55)` |
| Title bar | height 44, padding 0 16, traffic lights 12px circles with 8px gap, centred title 13px 500 muted |
| Sidebar | width 180, padding 14 10, items gap 3; item padding 8 10, radius 8; 8px icon glyph before label (square for Download, ring for Library, rotated square for Settings) |
| Sidebar footer | padding 9 10, top hairline, 7px status dot + 12px text |
| Paste bar | padding 18 22 14, bottom hairline; input padding 11 14, radius 9; Add button padding 0 22, radius 9 |
| Content | padding 0 22 22; group heading padding 18 2 10 |
| Card | radius 11, border `.07`, overflow hidden |
| Row | grid `44px minmax(0,1fr) auto auto`, gap 14, padding 11 14, divider `.05` |
| Artwork | 44×44 radius 6 (library rows 36×36 radius 5). Placeholder: radial "record" gradient over a two-stop diagonal gradient |
| Progress | six 6px dots, gap 4: green done, amber current (pulse 1.2s), `.15` white pending. Step words to the right of the dots on in-progress rows |
| Row button | padding 6 12, radius 7 |
| Candidate card | radius 9, padding 12 14, gap 8, indented 58px under the row; chosen one has the amber border |
| Spectrogram well | margin 0 14 14 72, radius 8, padding 12 14; image 84px tall, radius 5, dashed red cut-off line with mono label |
| Error banner (in content) | margin-top 14, radius 10, padding 11 14, 8px dot, text 12.5px, "Try now" button |
| Signed-out banner (under title bar) | padding 12 18, amber wash + bottom border, "Reconnect" primary button 8 18 radius 8 |
| Settings card | max-width 640, radius 10, padding 16 18, gap 12 between cards |
| Setup window | 760×600, title "Welcome to Krater"; content centred, gap 24–26; stepper `1 Folder · 2 Telegram · 3 Ready` in mono, current amber, done green with ✓ |
| Setup folder well | min-width 440, radius 10, padding 14 18, folder glyph 34×26 amber gradient, path in mono 500 13px |
| QR | 216×216 on `#f4f2ec`, radius 12, padding 14, Telegram badge 44px centred; "refreshes every 30 seconds" under it |

## Screens and copy

### Setup 1 of 3 — folder (760×600)

Heading "Where should your music live?" Body "Every finished track is filed here, one folder
per artist. Rekordbox reads straight from this folder." Well shows `~/Music/DJ Library` with
"Choose folder". Primary "Continue".

### Setup 2 of 3 — Telegram (760×600)

Left: QR. Right: heading "Scan with the Telegram app on your phone"; body "Open Telegram →
Settings → Devices → Link Desktop Device, then point your camera at the code."; amber-bordered
box "Your account has a two-step password" with a password field and "Sign in" (shown only when
Telegram asks for it); link "Use phone number instead"; faint footnote after a hairline: "You
are signing in with your own Telegram account. Unofficial apps are occasionally flagged by
Telegram; a spare number works too."

### Setup 3 of 3 — ready (760×600)

Green ✓ disc 64px. Heading "You're set". Body "Telegram is connected and your music will be
filed into" then the path in a mono well. Primary "Start digging".

### Download (main screen, 1200×800)

Sidebar: Download (active), Library, Settings; footer "Telegram connected" with green dot.
Paste bar: placeholder "Paste a YouTube or YouTube Music link", button "Add".

Group "Progressive Psy Set 2026" with summary "12 of 40 filed · 1 needs your choice ·
1 rejected" (amber and red spans). Row states, in order:

1. Filed: "Astral Projection – Into the Void (Original Mix)"; green mono path
   "Astral Projection / Astral Projection - Into the Void (Original Mix).mp3 · 320 kbps verified";
   six green dots; "Show in Finder".
2. In progress: "Ace Ventura – Rezonate"; amber status "Fetching the file — step 3 of 6";
   dots green green amber(pulse) pending×3; mono "Identify · Match · Fetch · Verify · File · Done".
3. Needs your choice (amber-washed row): "Vini Vici – The Tribe"; amber status "Needs your
   choice — the video is 8:41 long, but the best match is only 6:12. Pick the version you want."
   Two candidate cards side by side: "Vini Vici – The Tribe (Extended Mix)" · "92% match" ·
   "8:42 · On Beatport · same length as the video" · primary "Use this" (amber border card);
   "Vini Vici – The Tribe (Original Mix)" · "74% match" · "6:12 · On Beatport · 2:29 shorter
   than the video" · secondary "Use this".
4. Rejected: artwork replaced by a red × tile; dimmed title "Liquid Soul – Devotion"; red status
   "Sounds like a 128 kbps upscale. Deleted, not added to your library."; button "Hide why";
   spectrogram well open underneath with the dashed line labelled "nothing above 16 kHz" and
   the caption "A real 320 kbps file has sound up to 20 kHz. This one stops at 16 kHz — it was
   blown up from a smaller file."
5. Duplicate: "Astrix – Deep Jungle Walk"; muted "Already in your library — skipped, nothing
   downloaded twice"; "Show in Finder".
6. Queued (row at 60% opacity): "Ajja – Shanti Shanti"; "Waiting its turn"; mono tag "queued".

Below the group, the error banner: "Beatport is unreachable. Matching is paused — trying again
in 30 seconds. Nothing is lost." with "Try now".

Group "Single tracks" · "1 of 1 filed": "Captain Hook – Human Design (Original Mix)" filed row.

### Download — Telegram signed out

Banner under the title bar: "Telegram signed out. Reconnect to keep digging — tracks already
filed are untouched." with primary "Reconnect". Sidebar footer turns amber: "Telegram signed
out". Rows dim to 60%: "Ace Ventura – Rezonate" · "Paused — will continue after you reconnect"
· mono "paused at step 3 of 6"; queued row unchanged.

### Library (1200×800)

Sidebar gains a mono "PLAYLISTS" label and the list "All tracks", "Progressive Psy Set 2026"
(selected, `.08` white fill), "Full-on Classics", "Sunrise Set".
Header row: search input "Search artist, title or label" · "418 tracks · 6 playlists · 3.2 GB on
disk" · secondary "Open library folder".
Selected-playlist card: "Progressive Psy Set 2026 · 12 tracks"; "In Rekordbox: File → Import →
Playlist, then choose this file. Safe to re-import after new tracks are added."; primary
"Show playlist file".
Table: grid `40px minmax(0,2.2fr) 1fr 1.3fr 52px 70px 110px`, mono headers TRACK · GENRE ·
LABEL · YEAR · KBPS. Row: artwork 36; title 13px 600 with version 11.5px muted beneath; genre;
"TIP Records · 2002"; green mono "320 ✓"; "Show in Finder" 11.5px button.

### Settings (1200×800)

Title "Settings" 20px. Cards, max-width 640: "Library folder" / mono path / "Change";
"Telegram" / green dot "Connected as +31 6 •••• ••42" / "Sign out"; "App version" /
"Krater 1.0.3 — up to date" / "Check for updates"; "App data" / mono
`~/Library/Application Support/Krater` / "Show in Finder".

## Deviations to decide during implementation

- The mockup moves app data to `~/Library/Application Support/Krater`; the code today
  uses `~/.config/krater`. The Mac-native path is the better fit for a packaged app.
- "Check for updates" implies an update channel that does not exist yet; ship the card
  without the button until there is one.
- The Beatport banner copy says 30 seconds; the worker's real backoff decides the number.
