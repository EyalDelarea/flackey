# Re-queue results — 2026-09-21 (Task 16, issue #75)

The last task of the search-correctness epic (#61). Every request the old pipeline had given up on was
put back in the queue and run against the epic build, on the real Soulseek network.

## What was run, and what the owner consented to

The owner was shown the numbers and said yes to all three on 2026-09-21:

1. **Re-queue the stuck requests**, and the real Soulseek traffic that follows.
2. **Delete track 35** — `Space Cat – Spacecat (Club Mix)`, the file the owner listened to during Task 7
   and ruled **wrong** (fingerprint 0.59 against its own video). Library file and `tracks` row.
3. **Quit the running Flackey app** so the database could be written.

Nothing outside that list was deleted, renamed or re-queued.

## Method

```bash
osascript -e 'quit app "Flackey"'
pgrep -x Flackey                  # NOT `pgrep -fli flackey`: that matches slskd's --app-dir path forever
sqlite3 "$DB" ".backup '$DB.bak-issue61-task16-<ts>'"   # a file copy would miss the 3.9 MB WAL
# then, through flackey.store.Store only — never raw SQL:
#   state=queued, attempts=0, retry_after=None, error_message/flag_reason/chosen_candidate_id/
#   catalog_track_id/confidence=None, lossless_retry=1
cd <repo> && uv run flackey start --no-browser   # the EPIC branch, not /Applications/Flackey.app
```

The app was started **from the repo on `epic/61-search-correctness`**, not from the installed
`/Applications/Flackey.app` — the installed build is `main` and would have proved nothing.

The quick ladder was given its settling window: the re-queue was written at **16:49** and nothing was
still moving by **17:27** — 38 minutes, faster than the 60–90 the plan budgeted, because the 38 requests
run concurrently. 40 Soulseek attempts were made in that window.

## Headline

**30 filed, 5 waiting for Soulseek with their first look done, 1 parked.** Plus **2 duplicates** — the
track was already in the library, which is a success, not a miss. 30 + 5 + 1 + 2 = 38.

> The 5 waits are **not failures.** Task 15 (#86) replaced "give up after half an hour" with "wait 6 h and
> look again, 12 times over 3 days", because Soulseek is a population, not a library. Each of the 5 has
> spent its first look and is sitting in `queued` with a `retry_after` about six hours out. **They may
> still file over the following three days without anyone touching them.** This report is a
> **first-pass snapshot** taken once the quick ladder settled, not a final verdict — waiting out the full
> 3-day budget would have delayed the epic by three days for no decision it would change.

All 38 were requests the **old** pipeline had already given up on (`not_found`, `cancelled`, `error`).

## The table

| request | state | text | video s | confidence | outcome | fp score | filed as | reason |
|---|---|---|---|---|---|---|---|---|
| 71 | rejected | Shiva Shidapu - Power Of Celtic | 531 | 80.00 | filed | - | - | **rejected** — a different recording: best score 0.77 below 0.79 |
| 72 | done | Release Me | 509 | 98.00 | filed | 0.98 | aiff soulseek | - |
| 121 | queued | Etnica - The Italian EP [1995] Spirit Zone Recording | 1479 | - | no_pick | - | - | waiting for Soulseek: nothing on Soulseek matched this track closely enough; 12 more looks |
| 122 | duplicate | Year 2000 | 400 | 98.00 | - | - | - | - |
| 123 | done | Disco | 476 | - | filed | 0.98 | aiff soulseek | - |
| 124 | done | The Infinity Project - Superbooster | 310 | 98.00 | filed | 0.98 | aiff soulseek | - |
| 125 | done | There Were Noises (Simon Posford Remix) | 541 | 98.00 | no_pick | - | mp3 deezer_bot | - |
| 126 | done | The Infinity Project - Hyperspaced (Doof Remix) | 431 | - | filed | 0.98 | aiff soulseek | - |
| 127 | done | Sheyba - Ganesh - Flying Rhino Records - 1995 | 431 | - | filed | 0.97 | aiff soulseek | - |
| 128 | done | Slinky Wizard - Funkus Munkus | 522 | - | filed | 0.94 | aiff soulseek | - |
| 129 | done | 01 - Slinky Wizard - Sacred Fist | 511 | - | filed | 0.99 | aiff soulseek | - |
| 130 | queued | Johann Bley-Stranded (The delta Remix) [only audio] | 491 | - | no_pick | - | - | waiting for Soulseek: nothing on Soulseek matched this track closely enough; 12 more looks |
| 132 | queued | Rain | 627 | - | unavailable | 0.72 | - | waiting for Soulseek: Soulseek was not reachable at the time; 12 more looks, one every 6 h |
| 133 | done | Witchcraft - Magic Frequencies | 460 | - | filed | 0.98 | aiff soulseek | - |
| 134 | done | Granada (Remix - 98) | 535 | 96.00 | filed | 0.95 | aiff soulseek | - |
| 135 | done | Tufáan - Probe (Green Nuns Of The Revolution Remix) | 487 | - | filed | 0.98 | aiff soulseek | - |
| 136 | queued | Psycho Train | 556 | - | no_pick | - | - | waiting for Soulseek: nothing on Soulseek matched this track closely enough; 12 more looks |
| 137 | done | Dynamix - The Rezistor | 410 | - | filed | 0.98 | aiff soulseek | - |
| 138 | done | 01. Universal Sound - Asteroids | 386 | - | filed | 0.98 | aiff soulseek | - |
| 139 | done | 02. Man With No Name - Silicon Trip | 416 | - | filed | 0.98 | aiff soulseek | - |
| 140 | done | 11. Space Tribe - Flipout the Dolphin (Voodoo edit) | 446 | - | filed | 0.98 | aiff soulseek | - |
| 141 | done | 07. Cydonia - Animals | 421 | - | filed | 0.98 | aiff soulseek | - |
| 142 | done | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | - | filed | 0.96 | aiff soulseek | - |
| 144 | done | Duvdev & Shidapu - Devil | 566 | - | filed | 0.94 | aiff soulseek | - |
| 145 | done | Horrorgram (Remastered 2024) | 453 | - | filed | 0.99 | aiff soulseek | - |
| 146 | done | Pigs In Space - Big Troubles In Outer Space | 481 | 99.00 | filed | 0.99 | aiff soulseek | - |
| 147 | done | Oforia | 599 | - | filed | 0.99 | aiff soulseek | - |
| 148 | done | Jiggle Of The Sphinx (Remastered 2024) | 401 | - | filed | 0.99 | aiff soulseek | - |
| 150 | done | Stranded (The Delta Remix (2022 Remaster)) | 490 | - | filed | 0.99 | aiff soulseek | - |
| 151 | done | Solar | 509 | 98.00 | filed | 0.99 | aiff soulseek | - |
| 194 | duplicate | Looking for You | 482 | - | - | - | - | - |
| 196 | done | Circus Is Open | 283 | - | filed | 0.99 | aiff soulseek | - |
| 206 | done | For Its Own Sake | 235 | - | filed | 0.98 | aiff soulseek | - |
| 207 | done | Ineffaceable Moments | 381 | - | filed | 0.99 | aiff soulseek | - |
| 210 | done | Staring Into the Abyss | 300 | - | filed | 0.98 | aiff soulseek | - |
| 212 | done | Healing Process (Last of the Star Makers) | 502 | - | filed | 0.98 | aiff soulseek | - |
| 220 | done | Orca | 481 | - | filed | 0.98 | aiff soulseek | - |
| 228 | queued | Inculcating Ideas (Filteria Remix) | 470 | - | no_pick | - | - | waiting for Soulseek: nothing on Soulseek matched this track closely enough; 12 more looks |


`confidence` is the identification score for YouTube requests. `fp score` is the acoustic score of the
file that was filed, against the request's own video audio; the floor is `lossless_fingerprint_min = 0.79`.
`filed as` shows the format and where it came from. For `done` rows the reason column is blank on purpose:
a transient message from earlier in the run (a bot timeout, a yt-dlp retry) is not a failure and the row
succeeded anyway.

**29 of the 30 filed are lossless AIFF from Soulseek**, at fingerprint 0.94–0.99 against their own video.
The one exception is request 125, filed as a 320 kbps MP3 through the Deezer lossy fallback — which, since
#82, is fingerprinted at the same threshold as a peer's file before it is allowed in.

## Counts

| state | n |
|---|---|
| `done` | 30 |
| `duplicate` | 2 |
| `queued` | 5 |
| `rejected` | 1 |

**Buckets**

| outcome | n |
|---|---|
| filed | 30 |
| waiting for Soulseek | 5 |
| duplicate (already in the library) | 2 |
| parked | 1 |

## The probe's 16 tracks

| request | track | filed? | state | fp score | file |
|---|---|---|---|---|---|
| 127 | Sheyba – Ganesh | **yes** | `done` | 0.97 | Sheyba - Ganesh - Flying Rhino Records - 1995.aiff |
| 128 | Slinky Wizard – Funkus Munkus | **yes** | `done` | 0.94 | Slinky Wizard - Funkus Munkus.aiff |
| 126 | Infinity Project – Hyperspaced [Doof Remix] | **yes** | `done` | 0.98 | The Infinity Project - Hyperspaced (Doof Remix).aiff |
| 135 | Tufáan – Probe (Green Nuns Rmx) | **yes** | `done` | 0.98 | Tufáan - Probe (Green Nuns Of The Revolution Remix).aiff |
| 141 | Cydonia – Animals | **yes** | `done` | 0.98 | 07. Cydonia - Animals.aiff |
| 130 | Johann Bley – Stranded (The Delta remix) | no | `queued` | - | - |
| 133 | Witchcraft – Magic Frequencies | **yes** | `done` | 0.98 | Witchcraft - Magic Frequencies.aiff |
| 196 | New Born – Circus Is Open | **yes** | `done` | 0.99 | New Born - Circus Is Open.aiff |
| 136 | Psycho Train (Red Headed) | no | `queued` | - | - |
| 129 | Slinky Wizard – Sacred Fist | **yes** | `done` | 0.99 | 01 - Slinky Wizard - Sacred Fist.aiff |
| 138 | Universal Sound – Asteroids | **yes** | `done` | 0.98 | 01. Universal Sound - Asteroids.aiff |
| 139 | Man With No Name – Silicon Trip | **yes** | `done` | 0.98 | 02. Man With No Name - Silicon Trip.aiff |
| 140 | Space Tribe – Flipout the Dolphin (Voodoo Edit) | **yes** | `done` | 0.98 | 11. Space Tribe - Flipout the Dolphin (Voodoo edit).aiff |
| 144 | Duvdev & Shidapu – Devil Rmx | **yes** | `done` | 0.94 | Duvdev & Shidapu - Devil.aiff |
| 121 | Etnica – "The Italian EP" (24-min EP upload) | no | `queued` | - | - |
| 147 | Oforia (bare artist, no title) | **yes** | `done` | 0.99 | NO REFUND - Oforia.aiff |

**13 of the 16 filed**, every one of them lossless and every one at 0.94–0.99 against its own video.
The pre-epic probe found lossless copies on the network for all 16 and predicted two were unreachable —
the 24-minute *Italian EP* upload and the bare-artist *Oforia*. **Oforia filed anyway** (see below); the
*Italian EP* is one of the three still waiting. The probe's promise is substantially kept.

## Every `fingerprint_failed` — and the file it rejected


**req 72** — searched `Infected Mushroom Release Me REBORN`

| rejected file | score |
|---|---|
| 1. Infected Mushroom - Release Me REBORN.flac | 0.672 |
| 1. Infected Mushroom - Release Me REBORN.flac | 0.672 |
| Infected Mushroom - RE-BORN - 01 - Release Me REBORN.flac | 0.672 |
| Infected Mushroom - RE-BORN - 01 - Release Me REBORN.flac | 0.672 |

**One `fingerprint_failed` in the whole run** — and it is the one that matters. Four lossless copies from
four different peers, all of `Release Me REBORN`, all refused at 0.672 against the 509 s video the request
actually pointed at. That refusal is what released the next spelling, which filed the right recording at
0.98. Attempt outcomes across the run: **29 `filed`, 6 `no_pick`, 3 `unavailable`, 1 `queued`,
1 `fingerprint_failed`** (40 attempts).

## Every `not_found`, `error`, `cancelled` and `rejected` — with its reason

| request | state | text | reason |
|---|---|---|---|
| 71 | `rejected` | Shiva Shidapu - Power Of Celtic | rejected: a different recording: best score 0.77 below 0.79 |

## Waiting for Soulseek (first look done, not a failure)

| request | text | attempts | looks again | last outcome |
|---|---|---|---|---|
| 121 | Etnica - The Italian EP [1995] Spirit Zo | 3 | 2026-09-21T20:20:39+00:00 | `no_pick` |
| 130 | Johann Bley-Stranded (The delta Remix) [ | 3 | 2026-09-21T20:21:12+00:00 | `no_pick` |
| 132 | Rain | 3 | 2026-09-21T19:55:54+00:00 | `unavailable` |
| 136 | Psycho Train | 3 | 2026-09-21T20:21:57+00:00 | `no_pick` |
| 228 | Inculcating Ideas (Filteria Remix) | 3 | 2026-09-21T20:26:30+00:00 | `no_pick` |

---

## Request 71 — *Shiva Shidapu – Power Of Celtic* — needs the owner, and nothing will retry it

This is the case #61 was written around, and it is the one row that ended **`rejected`**. Two facts sit
side by side, and both matter:

- **The spelling fix worked.** The old pipeline searched Soulseek for
  `Shiva Shidapu The Power Of The Dark Side` — a wrong Beatport record redirecting the download to a
  different track. This run searched **`Shiva Shidapu Power Of Celtic Tryambaka Remix`**. That is #85
  doing exactly what it was built to do.
- **The best copy the network offered still scored 0.77**, against the floor of **0.79** the owner set by
  ear during Task 7. The lossless picks did not clear it; the 320 kbps lossy fallback was fingerprinted
  too (spec §7, shipped in #82) and rejected with *"a different recording: best score 0.77 below 0.79"*.

Before this epic that lossy copy would have been **filed**, on the spectral check alone — 320 kbps, clean
cutoff, and nothing whatsoever said it was the recording that was asked for. Refusing it is the epic
working as designed.

**But `rejected` is not retryable.** `RETRYABLE_STATES = {ERROR, NOT_FOUND}`, so neither the 6-hour
ladder nor "Retry all" will ever pick this row up again. It is dead until the owner acts.

**Open question for the owner, deliberately not decided here:** is 0.77 a different *master* of the right
recording, or genuinely the wrong track? Two hundredths under a threshold that was itself moved by ear is
too fine a margin for this report to call. Listening to
`spectrograms/req71-15282665.png`'s audio would settle it. **No threshold change is proposed.**

## Request 72 — *Release Me* — the epic's thesis, end to end

The clearest single demonstration in the run:

1. Beatport matched the request to **`Release Me REBORN`** (396 s) — a different track from the 509 s video.
2. Soulseek was searched on that spelling and returned four lossless copies of it, from four peers.
3. **All four were fingerprinted against the request's own video audio and rejected at 0.672** (floor 0.79).
4. The `fingerprint_failed` outcome released the next spelling (#85), which found the right recording.
5. It filed as lossless AIFF at **0.98**.

Under the old pipeline step 3 did not exist: text similarity had already decided, and one of those four
files would have been filed. This is `no_pick` / `fingerprint_failed` earning the next search, exactly as
spec §Flow 5 intends.

## Request 147 — *Oforia* — not a spec divergence, despite appearances

The pre-epic review listed this as *"Oforia (bare artist, no title)"* and predicted it could not be
matched. It filed, at **0.99**. Spec §Flow 4 says bare-artist requests end `not_found`, so this looks
like a divergence — it is not. The parser took the **channel name** as the artist, so the row carries
`query_artist = "NO REFUND"`, `query_title = "Oforia"`: two fields, so the bare-artist guard never
applied. The query-candidate path (#83) searched `NO REFUND Oforia`, and the fingerprint vouched for what
came back at 0.99 against the video. The record is synthetic (`catalog_track_id` is negative, from
`_fallback_catalog`), which is #83's design.

## The consented deletion, and the loose ends it leaves

Track 35 — `Space Cat – Spacecat (Club Mix)` — was deleted: the 85 MB AIFF and the `tracks` row (via
`Store.delete_track`, which also cleared its one `playlist_tracks` membership). It scored **0.59** against
its own video in the Task 7 sweep and the owner ruled it wrong by ear.

Three consequences, none of them covered by the consent, so **none of them were acted on**:

1. **Request 48 is still `done`** and its track is gone. It was not re-queued — it was not in the stuck
   set, and re-queuing it was not consented. The owner may want to press Try again on it.
2. `~/Music/DJ Library/Playlists/Old Goa Trance.m3u8` still lists the deleted path. Left as-is.
3. `~/Music/DJ Library/Space Cat/` is now an empty folder. Left as-is.

## Where this report was committed, and why not as its own PR

Every other task in this epic landed as a PR into `epic/61-search-correctness` with a release-category
label. This report is committed **straight onto the epic branch** instead. The reason is narrow: the epic
PR body is specified to list the eleven task PRs (#72, #76–#79, #81–#86), and a twelfth docs PR would
desync that list from itself. There is no PR here to hang `ignore-for-release` on; the commit rides into
`main` inside the epic merge, and `docs/`-only commits do not reach the release notes anyway.

## Reproduce

```bash
osascript -e 'quit app "Flackey"'
pgrep -x Flackey          # NOT `pgrep -fli flackey` -- that matches slskd's --app-dir path forever
sqlite3 "$HOME/Library/Application Support/flackey/flackey.sqlite" \
  "select id, state, raw_text from requests where id in (<the 38 ids above>) order by id;"
```

Backup taken before the writes:
`~/Library/Application Support/flackey/flackey.sqlite.bak-issue61-task16-20260921-164930`
(17.1 MB, `sqlite3 .backup` so the 3.9 MB WAL is included; `pragma integrity_check` → ok).
