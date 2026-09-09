# Visual review — native Mac UI, 2026-09-07

Spec section 7 asks for Playwright screenshots of every screen in light and dark, reviewed against the
HIG checklist (`.claude/skills/macos-design-guidelines`) before merge. This is that record. Screenshots
live in `screens/` (Chromium, `?titlebar=inset`, 720×540 for Welcome/setup, 1100×720 for the main app,
plus one capture of the real pywebview window).

## Screens checked

| Screen | Light | Dark | Notes |
| --- | --- | --- | --- |
| Welcome | `welcome-light.png` | `welcome-dark.png` | Rig illustration keyed out of its grey crop; rings, waveforms and meters sit on their wheels/screens at both sizes checked. |
| Setup: Folder | `folder-light.png` | `folder-dark.png` | Stepper, well-style path field, `Choose…`, bottom bar with Back. |
| Setup: Telegram | `telegram-light.png` | `telegram-dark.png` | QR card, phone-number alternative, error text spacing. |
| Download | `download-light.png` | `download-dark.png` | Toolbar, grouped inset rows, empty state. |
| Library | `library-light.png` | `library-dark.png` | Table header, alternating rows, selection uses the accent tokens. |
| Settings | `settings-light.png` | `settings-dark.png` | System Settings style grouped rows. |
| Real window | — | `real-window-dark.png` | pywebview window on macOS: inset traffic lights over the sidebar strip, no title text, Dock icon and `Krater` app menu confirmed. |

## HIG items applied

- 2.1 Resizable with sensible minimums: window min 720×540, main app 1100×720.
- 2.4 / 2.6 Title bar and traffic lights: title hidden, lights inset over a 52px drag strip; no fake lights in the page.
- 4.1 / 4.2 Sidebar: leading edge, source-list style with vibrancy, selection in accent blue only.
- Typography: system font only, no display or mono headers; mono reserved for paths.
- Colour: blue for selection and primary actions; orange = needs you, green = filed/verified, red = rejected.
- Appearance: light and dark designed separately (tokens in `web/src/theme.css`), not inverted.
- Reduced motion: rig animation disabled under `prefers-reduced-motion`.

## Findings and what was done

1. Dark mode multiplied the rig into near-black → replaced by an SVG colour-matrix key-out (alpha 0 above ~87 % grey), which also removes the faint grey rectangle in light mode. First version spilled opaque black past the image (default filter region); fixed by pinning the region to the image box.
2. Selected library rows used raw hex → accent tokens.
3. Setup error text sat flush under the field → 8px top margin.
4. Stale test title in `presentation.test.ts` → corrected.
5. From the whole-branch code review: the transparent window lost its drop shadow, and the window bounced 1100→720→1100 on every launch → both fixed in the final fix round (`desktop.py`, `App.tsx`).

Accepted as-is: `.artwork` hex gradient, `.candidate.chosen` 1.5px border.

Not exercised by hand: the resize to 1100×720 after finishing setup in the real window (unit-tested only); Ctrl-C with the window open still hard-exits through pywebview (parked, see the plan).
