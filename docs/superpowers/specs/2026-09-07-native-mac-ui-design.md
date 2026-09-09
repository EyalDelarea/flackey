# Krater native Mac UI — Design Spec

Status: decided 2026-09-07 with the owner. Supersedes the *looks* half of
`2026-09-06-gui-design.md` (section 4 and `docs/superpowers/design/gui/DESIGN.md`) and pulls
sub-project 4's pywebview window forward. Behaviour, API and data flow in the 2026-09-06 spec
stand unchanged.

Mockups (source of truth for looks): Claude Design canvas
https://claude.ai/code/artifact/bb1324aa-2ed2-4d64-8f52-210d314688b4 — five artboards:
Welcome, Download light, Download dark, Library, Setup (Telegram step).

## 1. Why

The first GUI (branch `feat/gui-app`) was judged "generic AI design": Google display font plus a
monospace face for labels, tracked uppercase headers, near-black background with one warm
accent, identical hairline cards. The owner chose a **native Mac app** look instead: it should
be mistaken for something Apple shipped, not for a website in a window.

## 2. Decisions

| Question | Decision |
|---|---|
| Direction | Native macOS. System font, system light/dark appearance, real title bar and traffic lights drawn by macOS, sidebar with vibrancy, blue only for selection and the primary button. |
| Window | pywebview (WKWebView) window opened by `crate start`; the FastAPI server runs in a background thread, the window loop on the main thread. Closing the window stops the server. `--no-browser` and a plain browser tab keep working for development. |
| Font | System font stack only (`-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', sans-serif`). Bricolage Grotesque, Familjen Grotesk and Instrument Sans were compared on the canvas and rejected. Monospace for file paths: `'SF Mono', Menlo, Monaco, monospace`. Google Fonts links removed. |
| Status colours | Apple semantic set: orange = needs you, green = filed and verified, red = rejected. Light `#FF9500 #34C759 #FF3B30`, dark `#FF9F0A #30D158 #FF453A`. |
| What survives from the old design | Six progress dots plus the step words on in-progress rows. Nothing else. |
| Welcome screen | New, shown once before setup. The owner's DJ-rig illustration (four CDJs and a mixer, no person) animated in CSS. |
| Fake chrome | None in the real page. The mockups draw traffic lights only for context. |

## 3. Visual system

Design each mode on its own; never invert. Tokens live in `web/src/theme.css` as CSS variables
under `:root` (light) and `@media (prefers-color-scheme: dark)`.

| Token | Light | Dark |
|---|---|---|
| window (toolbar, settings, setup) | `#ECECEC` | `#1E1E1E` |
| content (table background) | `#FFFFFF` | `#1E1E1E` |
| sidebar | `rgba(233,233,235,.86)` + `backdrop-filter: saturate(180%) blur(20px)` | `rgba(43,43,43,.86)` + same blur |
| group / card | `#FFFFFF`, border `rgba(0,0,0,.10)` | `#2A2A2A`, border `rgba(255,255,255,.09)` |
| well (inputs) | `#FFFFFF`, border `rgba(0,0,0,.16)` | `#1B1B1B`, border `rgba(255,255,255,.16)` |
| text / secondary / tertiary | `#1D1D1F` / `rgba(0,0,0,.55)` / `rgba(0,0,0,.28)` | `rgba(255,255,255,.87)` / `.55` / `.28` |
| separator | `rgba(0,0,0,.09)` | `rgba(255,255,255,.09)` |
| accent (selection, primary button, focus ring, link) | `#007AFF` | `#0A84FF` |
| sidebar selection | `rgba(0,0,0,.07)` | `rgba(255,255,255,.09)` |
| table alternate row | `rgba(0,0,0,.03)` | `rgba(255,255,255,.03)` |
| secondary button | `#FFFFFF`, border `rgba(0,0,0,.14)`, shadow `0 .5px 1px rgba(0,0,0,.12)` | `rgba(255,255,255,.11)`, border `.10`, shadow `0 .5px 1px rgba(0,0,0,.3)` |
| orange / green / red wash | `rgba(255,149,0,.08)` / — / `rgba(255,59,48,.07)` | `rgba(255,159,10,.07)` / — / `rgba(255,69,58,.09)` |

Type (system font): body 13px/1.35; captions and status lines 11px; group heading 15px 600
with `-0.01em`; setup heading 22px 700 `-0.02em`; Welcome title 34px 700 `-0.03em`. File paths
11px mono, secondary colour. No uppercase, no letter-spaced labels, no monospace anywhere else.

Shape: window radius comes from macOS. Groups and cards 10px, candidate cards and wells 8px,
fields 7px, primary button 6px (24px tall, 13px 500), secondary button 5px (22px tall, 12px),
sidebar items 6px (28px tall). Borders are 0.5px. Hover on rows and buttons is a 2–4% tint, not
a colour change.

Motion: transitions on state changes only (120–200ms). The single non-user-triggered animation
in the app is the Welcome rig.

## 4. Window and shell

- `crate start` creates one pywebview window, title "Krater", 1100×720, minimum 900×560,
  `background_color` matching the window token so nothing flashes white or black. The setup
  and Welcome screens resize the same window to 720×540 and back.
- Server thread: `run()` in `app.py` moves to a daemon thread; the main thread calls
  `webview.start()`. The window's `closed` event sets the server's `should_exit`. Ctrl-C in the
  terminal still stops everything (existing behaviour).
- Title bar: try the inset look (traffic lights over the sidebar) by setting
  `titlebarAppearsTransparent`, hidden title and the full-size-content style mask on the native
  `NSWindow` via `window.native` after the window is shown. If that handle is unavailable, keep
  the standard title bar. The page learns which it got from a query flag the launcher adds
  (`?titlebar=inset`) and reserves a 52px drag strip at the top of the sidebar only in the inset
  case. The drag strip uses the `pywebview-drag-region` class.
- Dark mode: WKWebView reports the system appearance through `prefers-color-scheme`; no code.
- The browser path (`--no-browser`, or opening `http://localhost:8765` by hand) shows the same
  page with the standard, non-inset layout.

## 5. Screens

### 5.1 Welcome (new; 720×540; first launch only)

Illustration 560px wide, centred, then "Krater" 34px, one sentence ("Paste a YouTube link.
Get the real 320, tagged and filed where Rekordbox will find it."), a primary "Get started",
and an 11px note "Two minutes: pick a folder, connect Telegram." Get started goes to Setup step
1. Shown when `setup_done` is false and the user has not passed it this session; Reconnect
skips it.

Animation (CSS only, overlays positioned as percentages of the illustration so a higher-res
file drops in without re-measuring):
- Rig: fade and rise on load (0.7s, once), then a 5px float, 5s ease-in-out alternate.
- Jog wheels (four): a thin ring with a bright cyan arc, `conic-gradient` masked to a ring,
  `mix-blend-mode: screen`, spinning 1.8s linear, staggered.
- Screens (four): a scrolling waveform strip (inline SVG data URI, 200px tile) behind a fixed
  playhead line, masked to soft edges, rotated to each deck's tilt (−20°, −8°, 8°, 20°).
- Mixer: four VU bars bouncing at 0.47s (128 bpm), staggered, green/amber/red.
- `prefers-reduced-motion: reduce` turns every animation off; the rig is shown static.

Asset: the owner supplies the original illustration. Requirements: no person, transparent
background, at least 1200px wide (2× for a 560px slot), PNG or WebP. Until then the build uses
`docs/superpowers/design/gui/welcome-rig-crop.jpg` (a 1078×460 crop from a screenshot with a
`#E9E9E9` background). The page keys that background out with an SVG colour-matrix filter
(alpha 0 for anything lighter than ~87% grey), so the rig sits on the window colour in both
light and dark mode; the same filter is harmless on a transparent original.

### 5.2 Setup (720×540, three steps)

Same window, standard title "Welcome to Krater". A three-step indicator centred at the top
(16px numbered discs: done = green with a check, current = blue, next = grey). Content
grid as in the mockup: QR on the left, instructions on the right, the two-step-password box in
an orange-bordered group when Telegram asks, "Use phone number instead" as a link, the honesty
note under a separator. Bottom bar: "Back" left, status text right ("Waiting for Telegram…").
Steps and copy unchanged from the 2026-09-06 spec.

### 5.3 Download (main, 1100×720)

Sidebar 220px: Download, Library, Settings with 16px stroke icons; footer "Telegram connected"
with a green dot. Toolbar row 52px at the top of the content area: paste field and a primary
"Add". Content is inset grouped lists: one group per playlist, heading 15px + summary
("12 of 40 filed · 1 needs your choice · 1 rejected", orange and red counts). Rows 54px:
36px artwork, title 13px 500 with the version in secondary, an 11px status line; right side
shows either the progress dots + step words, or a green check + "320 kbps verified", or
nothing; one secondary button at most ("Show in Finder", "Why?"). A row that needs a choice
gets the orange wash and two candidate cards indented under it; the recommended candidate has a
1.5px blue border and a primary "Use this". Rejected status text is red. Queued rows are 60%
opacity. Empty state: one centred sentence, "Paste a link above to start digging."

### 5.4 Library (1100×720)

Sidebar gains a "Playlists" section (11px 600 tertiary header, then All tracks and one item
per playlist). Toolbar: search field with a magnifier (260px), then counts in secondary text,
then "Open library folder". Content: a playlist card (icon, name, "· 12 tracks", one line on
how to import into Rekordbox, "Show playlist file") when a playlist is selected, then a table:
header row 26px (Track, Genre, Label, Year, Kbps), rows 44px with alternating tint, a blue
selected row with white text, kbps right-aligned with a green check when verified. "Show in
Finder" is a row action: a secondary button that appears on hover at the right of the row,
and double-clicking a row does the same. No permanent button in every row.

### 5.5 Settings

Toolbar-less. Title "Settings" 22px, then inset grouped rows like System Settings: Library
folder (path + Change / Choose… / Save / Cancel), Telegram (status dot + line + Sign out or
Reconnect), App version, App data (path + Show in Finder + Show logs). Max width 640.

## 6. Files

- `web/index.html`: remove Google Fonts links.
- `web/src/theme.css`, `web/src/app.css`: rewritten from the tokens above.
- `web/src/components/Shell.tsx`, `Sidebar.tsx`: no fake title bar; inset drag strip flag;
  icons.
- `web/src/components/setup/*`: Welcome step added (`WelcomeStep.tsx`), `SetupShell` gets the
  stepper and bottom bar; `App.tsx` routes Welcome → Folder → Telegram → Ready.
- `web/src/components/library/TrackTable.tsx`: header, alternating rows, selection.
- `web/src/components/SettingsPage.tsx`: grouped rows.
- `web/public/welcome-rig.*`: the illustration.
- `src/krater/app.py`, `cli.py`: pywebview window, server thread, `--no-browser` path.
- `pyproject.toml`: `pywebview>=6.2` dependency.

## 7. Testing

- Existing Vitest suites keep passing; selectors that name old classes are updated, no
  behaviour tests removed.
- New tests: Welcome renders and "Get started" advances to step 1; SetupShell shows done /
  current / next states; TrackTable selection; Shell reserves the drag strip only with the
  inset flag.
- Python: `crate start --no-browser` still serves without pywebview being importable (lazy
  import), and a unit test covers the server-thread start/stop handshake.
- Visual check before merge: Playwright screenshots of every screen in light and dark, reviewed
  against the HIG checklist skill (`.claude/skills/macos-design-guidelines`).

## 8. Out of scope

PyInstaller bundle, notarisation, bundled ffmpeg — still sub-project 4 of the 2026-09-06 spec.
