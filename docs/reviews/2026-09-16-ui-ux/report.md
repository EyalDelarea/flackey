# Flackey UI and UX review

**Reviewed:** September 16, 2026 · local `main` at `19b760d` (0.1.2 release commit).

**Recommendation:** Keep the restrained Mac-style design. Focus the next iteration on first-use clarity, truthful status, readable text, and finding finished music. The foundation is coherent; several concrete interaction defects currently make it less dependable than it looks.

This is an expert inspection, not a usability study. Predictions about user confusion are hypotheses; reproduced UI behavior is identified below. No app code was changed.

## Scope and evidence

Reviewed the current React UI served directly from `main`, using headless Chrome with isolated, synthetic API responses. Walked through Welcome → Folder → Telegram QR/phone → skip → Soulseek → skip → Ready → workspace. Inspected active downloads, candidate choice, completed/failed history, empty/populated library, search/filter interaction, Settings, Uploads, and disconnected presentation. Captured light/dark themes at 1100 × 720, setup at 720 × 600, downloads at 900 × 600, and library at the native app's supported minimum, 720 × 540. Used keyboard input to check folder-field focus and calculated contrast from theme tokens.

**Limits:** No real Telegram/Soulseek login, account creation, audio download, native folder dialog, Finder action, installer execution, or Rekordbox import was performed. The existing backend and personal library were not modified. Screenshots illustrate frontend states with demo content; they do not prove backend reliability or real matching accuracy. Native WKWebView/titlebar behavior, screen-reader operation, large libraries, and 200% text zoom still need separate validation. The website and installation instructions were inspected in source, not visually audited. This evaluates the local `main` commit, not a separately fetched remote revision.

Priority: **P1** = fix before broader new-user testing; **P2** = next usability iteration; **P3** = refinement. There were no verified data-loss findings in this review.

## Journey assessment

| Stage | Assessment | Main action |
|---|---|---|
| Installation and first promise | Clear product intent; inconsistent format/install wording | Align website, README, and packaged instructions |
| Welcome and folder | Attractive, focused, easy to start | Clarify supported links and manual Rekordbox import |
| Account setup | QR instructions, fallback and skip are useful | Explain source roles/sharing; distinguish skipped from connected |
| First request | Primary action is obvious | Add a stable label, examples and readable inline errors |
| Waiting and choosing | Good progress vocabulary and version comparison | Correct source/disconnection status; prioritize action-needed states |
| Completion and failure | Actions exist in History | Make the transition and destination visible |
| Library and Rekordbox | Useful metadata and Finder handoff | Fix filters, empty state, narrow layout and first-import guidance |
| Settings and sharing | Sensible grouping and technical disclosure | Make adding a source direct; explain folder changes |

## Findings and recommendations

### 1. P1 — Download status contradicts a working Soulseek connection

**Reproduced.** With Telegram disabled/unauthorized and Soulseek connected, the sidebar says “Connected” while a fetching row says “Paused — will continue after you reconnect.” The renderer treats Telegram authorization as the universal condition for progress, although either source is supported. Its early return also omits the normal Stop action in this state.

**Why it matters:** A user who deliberately skipped Telegram is told their valid configuration is broken. They cannot trust the visible progress.

**Change:** Derive row status from actual worker/request availability and the relevant source. Distinguish “Telegram off,” “Soulseek connected,” and “No sources connected.” Preserve applicable request controls.

**Acceptance:** Soulseek-only downloads show their real phase; disabling an unused source never changes another source's active request to paused.

Evidence: [Screenshot](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/17-soulseek-only.png), [presentation.ts](/Users/delarea/Desktop/code/cratedigger/web/src/presentation.ts:202), [Sidebar.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/Sidebar.tsx:10).

### 2. P1 — Library filters silently remain active after their labels reset

**Reproduced.** Select “Example Artist” in Folder, then search for “Summer,” a track in another folder. The dropdown displays “All folders,” but the old folder value still filters the result to zero. The same state/display split exists for Format.

**Why it matters:** The user sees a matching search, apparently unrestricted filters, and no track. This looks like lost music or broken search.

**Change:** Normalize the actual filter state when options disappear, or keep the selected value visible and explain that it yields no results. Provide “Clear filters.”

**Acceptance:** Displayed filter values always match the applied query. Repeat with folder, format, playlist changes and search clearing.

Evidence: [Screenshot](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/11-filter-mismatch.png), [LibraryPage.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/library/LibraryPage.tsx:49), [dropdown rendering](/Users/delarea/Desktop/code/cratedigger/web/src/components/library/LibraryPage.tsx:93).

### 3. P1 — The library breaks at a supported window size

**Reproduced at 720 × 540.** Track/genre/label headers overlap, track text becomes almost unreadable, and toolbar controls extend beyond the visible pane. The 220px sidebar and reserved actions column leave too little room for track information. At the default 1100px width, titles and genres are already noticeably truncated.

**Change:** Introduce a compact library layout: prioritize title/artist/version, hide optional metadata behind a details view, collapse the actions to a compact menu, and let toolbar content wrap. A collapsible sidebar could help. Raising the minimum window size is a fallback, but check small laptop screens and zoom before relying on it.

**Acceptance:** At the declared minimum, users can identify a track, search, clear filters and reach its actions without overlap or clipping. Verify separately in native WKWebView.

Evidence: [Minimum-size screenshot](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/19-library-minimum.png), [default library](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/10-library-light.png), [table grid](/Users/delarea/Desktop/code/cratedigger/web/src/app.css:215), [native minimum](/Users/delarea/Desktop/code/cratedigger/src/flackey/desktop.py:21).

### 4. P1 — Important text and action colors have insufficient contrast

**Calculated and visually inspected.** Several pieces of operating guidance use 11px tertiary text: account caveats, QR help, optional-source notes, field instructions and filter counts. They are difficult to read, especially in light mode.

| Token pairing | Approximate contrast |
|---|---:|
| Light tertiary text on `#ECECEC` | 1.97:1 |
| Dark tertiary text on `#1E1E1E` | 2.53:1 |
| White text on light blue primary button | 4.02:1 |
| White text on dark blue primary button | 3.65:1 |
| Light orange text on white | 2.20:1 |
| Light green text on white | 2.22:1 |

These are calculations from declared colors, including alpha compositing for tertiary text, not a complete pixel-level audit. Normal-size informative text should meet 4.5:1. Decorative indicators and inactive controls have different rules. [WCAG contrast guidance](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum)

**Change:** Separate decorative/faint tokens from readable helper text. Use darker semantic text colors in light mode and adjust primary button colors. Keep colored dots/icons, with readable neutral status text where useful. Move important instructions toward 12–14px; preserve optional compact metadata.

**Acceptance:** Audit each informative text/background combination in both themes, including selected and warning surfaces; meet 4.5:1 for normal text.

Evidence: [Telegram](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/03-telegram.png), [Soulseek](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/05-soulseek.png), [history](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/09-history-light.png), [theme](/Users/delarea/Desktop/code/cratedigger/web/src/theme.css:7).

### 5. P1 — Losing live updates does not communicate stale state

**Frontend gap confirmed.** The event stream has an open handler but no error/disconnected state. An offline browser simulation left the footer on “Connected.” The interface retains the last provider health and does not label it as stale. This was a fixture-based check, not a backend outage test.

**Change:** Track connection freshness separately from source login. Show “Reconnecting to Flackey…” and the last successful update when the stream fails beyond a short grace period. Preserve cached rows with a stale-state notice; refresh when connectivity returns.

**Acceptance:** A real local-server interruption cannot leave a frozen queue looking live indefinitely. Recovery resynchronizes without dropping the user's place.

Evidence: [Offline screenshot](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/20-offline.png), [live.ts](/Users/delarea/Desktop/code/cratedigger/web/src/live.ts:74).

### 6. P1 — The first successful download lacks a clear Rekordbox handoff

**Source-confirmed mismatch; likely new-user confusion.** Welcome says files are placed “where Rekordbox will find it”; Folder says “Rekordbox reads straight from this folder.” The README actually instructs the user to drag files/folders into the collection and import M3U8 playlists. The playlist view has good import instructions, but someone downloading a single track may never see them.

**Change:** Say “Saved in your library folder, ready to import into Rekordbox.” After the first successful track, offer “Show in Finder” and a short “Import into Rekordbox” guide. Reuse the existing playlist guidance. Explain that Rekordbox handles BPM/key analysis.

**Acceptance:** A first-time user can go from one pasted link to a track visible in Rekordbox without opening the repository README.

Evidence: [WelcomeStep.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/setup/WelcomeStep.tsx:31), [FolderStep.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/setup/FolderStep.tsx:25), [existing playlist guidance](/Users/delarea/Desktop/code/cratedigger/web/src/components/library/PlaylistCard.tsx:7), [README](/Users/delarea/Desktop/code/cratedigger/README.md).

### 7. P1 — Source setup does not explain library sharing before connection

**Source-confirmed.** The Soulseek account screen explains lossless audio and password preservation, but not that the selected DJ Library is shared with peers. This is documented elsewhere, and account setup passes the library folder into the sharing configuration. Folder changes also update the shared directory.

**Change:** Before “Create account,” explain plainly: “While Flackey is running, other Soulseek users can download files from your DJ Library folder.” Link to the sharing settings and show the selected folder. Explain what happens when that folder changes. Keep this near the action, with readable text.

**Acceptance:** A user can explain what is shared, with whom, and when before enabling Soulseek. If sharing is inseparable from enabling the source, say that explicitly.

Evidence: [Soulseek screen](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/05-soulseek.png), [SoulseekStep.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/setup/SoulseekStep.tsx:144), [sharing configuration](/Users/delarea/Desktop/code/cratedigger/src/flackey/slskd_config.py:222), [settings update](/Users/delarea/Desktop/code/cratedigger/src/flackey/web/library.py:196).

### 8. P2 — Completing setup visually overstates readiness

**Reproduced.** Skipping both sources produces green checkmarks for Telegram and Soulseek, a large green check, and “Start digging.” The “Almost set” paragraph correctly says both sources are off; preserve that honesty. The graphics and action still suggest readiness to download.

**Change:** Track step outcome as connected/skipped/pending, separately from visited/completed. With no source, offer “Connect a source” as the primary action and “Explore the app” as secondary. Keep optional skipping available. Mark skipped steps neutrally.

**Acceptance:** Ready, pending and exploration-only states have consistent text, icons and button labels.

Evidence: [Skipped setup](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/06-ready-skipped.png), [SetupShell.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/setup/SetupShell.tsx:11), [ReadyStep.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/setup/ReadyStep.tsx:39).

### 9. P2 — The empty library provides no next step

**Reproduced.** A brand-new library renders the toolbar and filters with a completely blank content area. The fallback “Nothing here yet” inside TrackTable is bypassed when there are no folder groups. Search/no-match states also use generic wording that does not explain how to recover.

**Change:** Add distinct loading, no-tracks, no-search-results and no-filter-results states. For the first visit: “Your finished tracks will appear here” plus “Add your first link.” For filtered results: “No tracks match these filters” plus “Clear filters.”

**Acceptance:** Every empty state explains the situation and provides a relevant next action.

Evidence: [Empty library](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/07-empty-library.png), [LibraryPage.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/library/LibraryPage.tsx:98).

### 10. P2 — Completed and failed requests disappear from the active workflow

**Source-confirmed behavior; discovery risk.** Terminal requests are moved to History, including failures. History has no count or new-result indicator. A user watching the active queue can lose the row just as its result becomes important. Group totals are calculated from the selected view, so they do not express the complete original batch.

**Change:** Keep the active/history split, but announce “1 track saved — View in Library” or “1 download failed — Review.” Add a new-result/failure count on History. Keep a stable batch summary across active and completed items, or explicitly label the scope.

**Acceptance:** Users can tell success from failure without searching tabs. Finishing the final item produces an outcome state, not only “No downloads in progress.”

Evidence: [Active](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/08-downloads-light.png), [History](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/09-history-light.png), [DownloadPage.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/download/DownloadPage.tsx:25).

### 11. P2 — Submission feedback competes with the input and truncates

**Reproduced with a simulated validation error.** The message is squeezed beside the paste field, limited to 40% width, and ellipsized even at 1100px. The input has placeholder-only instructions. An empty Add click silently does nothing, and success text disappears after six seconds.

**Change:** Add a persistent label such as “Track or playlist link.” Put feedback below the input, allow wrapping, preserve the entered URL on failure, and use an accessible live status/error region. Disable empty submission or explain what is missing. Keep a completion destination beyond a timed message.

**Acceptance:** Supported sources and the full recovery instruction stay readable at every supported width and after the user starts typing.

Evidence: [Validation screenshot](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/15-error-dark.png), [PasteBar.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/download/PasteBar.tsx:21), [message CSS](/Users/delarea/Desktop/code/cratedigger/web/src/app.css:81).

### 12. P2 — Keyboard and assistive-technology support needs a focused pass

**Verified keyboard defect:** Tabbing into the setup folder field gives it focus but no visible outline or shadow; neither its parent nor the field draws a focus indicator.

**Source-confirmed gaps:** The library is composed of generic divs rather than a semantic table; its headings are not programmatically associated with values. Sidebar selection lacks `aria-current` or equivalent state. Generic banners and paste feedback lack live-region semantics. Several Telegram fields depend on placeholders; buttons with repeated labels lack track-specific context. These deserve screen-reader testing before claiming accessibility conformance.

**Change:** Add `:focus-within` to the folder well, permanent labels, table semantics, selected navigation state and appropriate live announcements. Give repeated actions contextual accessible names. Preserve existing keyboard-visible row actions: the CSS already reveals them on `:focus-within`.

Controls commonly measure 22–24px high. Audit the 24px target-size criterion and its spacing exceptions before calling every small button a failure; consider larger targets for the primary onboarding actions. [WCAG 2.2](https://www.w3.org/TR/WCAG22/)

**Acceptance:** Complete setup navigation, request submission, library search and row actions using only the keyboard; test with VoiceOver. Focus must remain visible, and status changes must be understandable without color or sight.

Evidence: [Folder focus](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/22-folder-keyboard-focus.png), [focus suppression](/Users/delarea/Desktop/code/cratedigger/web/src/app.css:312), [TrackTable.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/library/TrackTable.tsx), [Banner.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/Banner.tsx).

### 13. P2 — Settings makes adding a skipped source harder than setup promises

**Source-confirmed.** Soulseek says it can be added later from Settings. Settings tells an unconfigured user to run setup again, with the action in a separate group lower down the page. This revisits Welcome, Folder and Telegram for a single-source task. Routine settings also expose “Helper web login,” which most users need only for troubleshooting.

**Change:** Put “Connect Soulseek” directly on the source row and open just that flow. Keep full setup replay secondary. Move helper credentials under technical details. Retain the existing readable connection labels and credential Show/Hide behavior.

Also clarify library-folder changes: current settings code changes the destination/sharing configuration, without a visible explanation of whether existing tracks move. State the actual behavior before Save.

**Acceptance:** A skipped source can be added from its Settings row, and changing the folder does not create a false expectation of moving existing music.

Evidence: [Settings](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/12-settings-light.png), [SettingsPage.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/SettingsPage.tsx), [folder update](/Users/delarea/Desktop/code/cratedigger/src/flackey/web/library.py:179).

### 14. P2 — Version selection would benefit from clearer decision support

**Design recommendation, not a demonstrated failure.** Candidate cards already expose mix name, duration and matching evidence. “88% match” can nevertheless look like a probability that the audio is correct. Repeated “Use this” actions require scanning back to the candidate title, and there is no preview in this component.

**Change:** Make version and duration the strongest differentiators. Explain what the match score measures, or use a qualified textual recommendation where warranted. Prefer an action such as “Choose Extended Mix.” Consider an external reference/preview only if it helps real users resolve ambiguity and the source supports it.

**Acceptance:** In a usability test, users can explain why they chose a version and do not interpret the score as guaranteed correctness.

Evidence: [Candidate comparison](/Users/delarea/Desktop/code/cratedigger/docs/reviews/2026-09-16-ui-ux/08-downloads-light.png), [CandidateCard.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/download/CandidateCard.tsx).

### 15. P3 — Align product promises and simplify first-use language

**Source-confirmed inconsistencies.** Welcome only mentions YouTube; the paste bar supports YouTube Music and Spotify too. The website emphasizes “From link to FLAC,” while README describes AIFF converted from FLAC and MP3 fallback. README's download introduction mentions a ZIP and Terminal command; packaged instructions and site install steps describe a PKG. “Best copy that exists” and “Two minutes” are stronger claims than this review can validate.

**Change:** Use one capability statement across surfaces, for example: “Paste a YouTube or Spotify link. Flackey finds a matching copy, checks its quality, and organizes it for Rekordbox.” Clarify output formats where relevant. Describe required accounts and optional sources before setup, without a rigid completion-time promise. Audit installer guidance against the actual distributed package.

**Acceptance:** Website, package instructions and app agree on inputs, outputs, required setup and the import workflow.

Evidence: [WelcomeStep.tsx](/Users/delarea/Desktop/code/cratedigger/web/src/components/setup/WelcomeStep.tsx), [site source](/Users/delarea/Desktop/code/cratedigger/site/index.html), [README](/Users/delarea/Desktop/code/cratedigger/README.md), [packaged instructions](/Users/delarea/Desktop/code/cratedigger/packaging/README-for-friends.md).

## What should stay

- **Visual identity and restraint.** The silver record gives the welcome screen character. System typography, restrained blue actions, neutral surfaces and a compact sidebar suit a desktop music utility. A new visual identity or dashboard-style redesign would add little value here.
- **Focused onboarding steps.** One major task per screen, a visible sequence, Back, and source skipping provide a sound structure. Keep it; improve outcome labels and explanation.
- **Telegram QR instructions and phone fallback.** The instructions tell users exactly where to go in Telegram. Keep both routes; improve readable labels and error recovery.
- **Explicit quality/fallback information.** The code distinguishes lossless delivery from MP3 fallback and gives reasons. Keep the evidence available, with a plain-language summary first.
- **Progress phase vocabulary.** Search, Choose, Download, Verify and Done are understandable. The smaller-width download layout already moves steps below the title; extend similar care to Library.
- **Candidate comparison as a deliberate interruption.** Asking about ambiguous versions is better than silently committing to a questionable match. Keep duration and mix information adjacent to the action.
- **Local-file and playlist handoff.** Finder actions and the playlist import instructions are useful. Bring them into the first-success journey.
- **Technical details behind disclosure.** Existing folded API-key and diagnostic sections are a good pattern. Apply it consistently to helper login details.
- **Light/dark themes and reduced-motion accommodations.** Both themes feel coherent. Continue the accessibility work: one reduced-motion selector targets `.dot6.current`, while the current step pulse uses `.step.current .step-mark`, so that pulse deserves correction.

The default-size screens inspected had a clear primary action and no broad layout collision. The serious layout failure was the narrow library. Small text, subtle secondary labels and reserved empty action space are the main visual weaknesses; more decoration is not the remedy.

## Recommended order of work

| Order | Work package | Expected benefit |
|---|---|---|
| 1 | Fix source-aware status, stale connection indication and filter state | Users can trust progress and find their music |
| 2 | Fix contrast, keyboard focus and minimum-size library layout | Core tasks remain readable and operable |
| 3 | Add first-library state, completion feedback and Rekordbox guide | Users reach their first useful outcome |
| 4 | Clarify sharing and skipped/pending setup; add direct source connection | Fewer surprises and easier recovery |
| 5 | Refine version decisions, settings disclosure and cross-surface copy | Lower ongoing cognitive load |

Avoid expanding scope into waveform players, configurable dashboards or a complete design-system rewrite before testing these changes. They are not required to fix the observed journey.

## Validate with new users

After the P1 fixes, run a small moderated test with about five DJs who have not used Flackey, including different levels of Rekordbox experience. This is a practical qualitative starting point, not statistical validation.

Ask them to install the build, explain the source/sharing choices, add one supported link, resolve an ambiguous version, locate the finished file, import it into Rekordbox, recover from a failed request, and add a previously skipped source. Include a search/filter task and one narrow-window or keyboard-only task.

Record task completion without hints, first-success time, wrong turns, missed errors, misunderstood connection states and whether participants can correctly explain where files are stored/shared. Use observed breakdowns to rank further visual refinements; do not treat aesthetic preference as equivalent to task failure.

## Basis for the review

Recommendations use visibility of system status, recognizable actions, error recovery and consistency as inspection principles. These are heuristics, not proof that a particular redesign will improve conversion. [Nielsen Norman Group: usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/)

Accessibility targets reference normal-text contrast, visible focus, meaningful programmatic structure, status messages and target sizing. This report is not a full WCAG conformance assessment. [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/) · [W3C contrast explanation](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum)
