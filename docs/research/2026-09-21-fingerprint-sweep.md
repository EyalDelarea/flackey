# Library fingerprint sweep — 2026-09-21 (Task 7, issue #73)

`flackey sweep` over the live database with the Flackey app quit. 114 filed tracks, each scored twice
against the audio of the video that asked for it: the **file** against the video's middle-30 s needles
(is this the video's recording?) and the filed Deezer candidate's **record** preview against the video's
full fingerprint (would audio identification have picked the same record?).

## Decision

**The owner listened to both unsure rows and ruled: track 50 `correct`, track 35 `wrong`.** The
arithmetic below is that ruling; the two rows and the "one answer that would trip the gate" section are
amended to match.

| | |
|---|---|
| `min_correct` | **0.86** — track 50, *Growling Mad Scientists – Do Androids Dream of Electric Sheep* (owner-certified by ear) |
| `max_wrong` | **0.72** — track 35 is `wrong` at 0.59, below the brief's Shiva Shidapu fallback, which therefore stands |
| gap (`min_correct - max_wrong`) | **0.14** — at or above 0.10, so the Step 3 gate does **not** trip |
| midpoint | (0.86 + 0.72) / 2 = **0.79** exactly |
| `round(midpoint, 2)` | 0.79 |
| capped at 0.90 | **threshold = 0.79** |
| identification disagreements (Step 4) | **0** — the gate does not trip |

**`lossless_fingerprint_min` becomes 0.79** (from 0.90). Task 8 applies it to
`src/flackey/config.py` and to `fingerprint_min` in `web/src/screenshot-harness.tsx`. Nothing is
changed here.

The lowest score of any `matched` row in the sweep is **0.94** (track 69, *Sun Project – Computer
Breath*), so on the executor's own rule — which cannot certify a row below the shipped 0.90 gate —
`min_correct` would have been 0.94 and the threshold 0.83. The owner's ear is what moves it: track 50 is
a real, legitimate 0.86 that the old gate rejected.

## Two rows the owner ruled on

The executor cannot listen to audio and has no channel to the owner, so **no row is called `wrong` on the
executor's authority**. Everything not certifiable from objective evidence is `unsure`. Two rows scored
under 0.90, and both were profiled further (below). Neither changes the arithmetic above — neither is
`correct` under the pre-registered rule, so neither can move `min_correct`.

| track | artist – title | file | record | file s | video s | verdict |
|---|---|---|---|---|---|---|
| 35 | Space Cat – Spacecat (Club Mix) | **0.59** | – | 503 | 507 | **`wrong`** (owner ruled, by ear) |
| 50 | Growling Mad Scientists – Do Androids Dream of Electric Sheep | **0.86** | – | 522 | 518 | **`correct`** (owner ruled, by ear) |

### Why the profile settles what a single score cannot

A 30 s needle was cut from the video at eight positions and slid along the library file. A pair that is
the *same recording* aligns at a **constant offset**; a pair that is not produces a **wandering** offset,
because nothing really lines up and the best match is noise.

**Track 50 — 0.86, offsets constant (+2.5 s throughout): the same recording.**

| video position | 37 s | 88 s | 140 s | 191 s | 243 s | 295 s | 346 s | 398 s |
|---|---|---|---|---|---|---|---|---|
| score | 0.845 | 0.900 | 0.835 | 0.899 | 0.866 | 0.904 | 0.895 | 0.889 |
| best offset in the file | 39 s | 91 s | 142 s | 194 s | 246 s | 298 s | 349 s | 401 s |

Every probe lands exactly 2–3 s later in the file than in the video, across the whole track. The reverse
direction agrees (0.833 / 0.848 / 0.895, offsets also constant). Audio lengths are 518 s and 516 s. Tags,
ISRC (`USQY50927763`), request text and the video title (*"Do Androids Dream Of Electric Sheep"*, a
`Growling Mad Scientists - Topic` auto-upload of the official release) all agree. The file is an AIFF from
Soulseek with a 22.1 kHz cutoff; the video is YouTube's encode of the same release.

This is a **different master of the same recording**, which is exactly the case the spike never sampled —
its numbers were *correct pairs 0.97–0.99, wrong pairs 0.53–0.76*, and 0.86 falls in the untested gap
between the two bands. That is the single most useful thing this sweep found: 0.86 is not a near-miss on
the wrong side, it is a real, legitimate score class the old 0.90 gate would reject. **The executor
proposes `correct`; the owner should confirm by ear.**

**Track 35 — 0.59, offsets incoherent: not the same recording.**

| video position | 35 s | 86 s | 136 s | 187 s | 237 s | 288 s | 338 s | 389 s |
|---|---|---|---|---|---|---|---|---|
| score | 0.594 | 0.602 | 0.601 | 0.589 | 0.592 | 0.561 | 0.593 | 0.597 |
| best offset in the file | 115 s | 116 s | 116 s | 121 s | 119 s | 131 s | **366 s** | **463 s** |

Flat around 0.59 everywhere, and the offsets jump from 116 s to 366 s to 463 s — the signature of noise,
not alignment. The reverse direction agrees (0.547–0.570, offsets 3 s / 32 s / 371 s). Everything *textual*
matches: the request asked for `Spacecat Club Mix`, the tags say `Spacecat (Club Mix)` on *Shapes of Sound*
with ISRC `UKR6V2050424`, and the video is titled *"Spacecat Club Mix"* on a `Space Cat - Topic` upload.
Only the audio differs. This is the same failure class as the *Nothing but a Title* case Task 1 fixed: the
text agreed all the way down and the recording was still wrong.

**The executor proposed `wrong` and did not write it; the owner then listened and ruled `wrong`.** That
does not change the threshold: 0.59 is below the 0.72 fallback, so `max_wrong` stays 0.72 either way.

### The owner's ruling

The owner listened to both rows and ruled **track 50 `correct`** and **track 35 `wrong`**. So
`min_correct = 0.86`, the gap is `0.86 − 0.72 = 0.14` (clear of 0.10, the Step 3 gate does not trip) and
the threshold is **0.79**. The other answer — track 50 `wrong` — would have made `max_wrong = 0.86` and
the gap `0.94 − 0.86 = 0.08`, under 0.10, which would have tripped Step 3 and forced the threshold to be
re-decided with the owner rather than computed. It was not the answer.

0.79 and 0.83 both reject track 35 and admit track 50; 0.79 simply leaves more headroom above the lowest
score now known to be correct. 0.83 would have left a margin of only 0.03 over track 50.

## Step 4 — identification agreement

> Every row whose **record** score is below the threshold while its file is `correct`.

**The question could only be put to 63 of the 96 certified tracks.** Of those 63, **zero** disagree.
The other 33 have no record score at all, so "0 disagreements" means *0 of 63 asked*, not *0 of 96*.

| bucket | count |
|---|---|
| `correct` rows whose record score is **below 0.79** | **0 of the 63 that could be asked — the gate does not trip** |
| `correct` rows whose record score is at or above 0.79 (agrees) | 63 |
| `correct` rows with **no** record score (the question cannot be put) | 33 |
| rows where record score is the `compare` sentinel `0.00` (needle longer than the hay) | 0 |

The lowest record score anywhere among certified rows is **0.91** (track 75, *Sun Project – Frisco
Machines*), then 0.93 (track 92); everything else is 0.97–0.99. Audio identification would have chosen the
same Deezer record as the text on every track it could be asked about. Nothing to take to the owner, and
Phase 5 (#68) is unblocked on this count — with the caveat that on the 33 unaskable tracks Task 10 will
reach `fingerprint_unavailable` rather than agreement, because there is no record to identify against.

The 33 unknowns are rows with no `chosen_candidate_id` or a chosen candidate carrying no `deezer_id` —
mostly the older Soulseek-sourced tracks. They are **not** agreement and **not** disagreement; the question
could not be put. Track ids:
`1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 29, 31, 33, 34, 36, 37, 39, 43, 44, 46, 48, 49, 51,
52, 54, 58, 89`.

## Coverage

| | count |
|---|---|
| tracks swept | 114 |
| scored | **106** |
| at or above 0.90 | **104** |
| under 0.90 | 2 (tracks 35 and 50, above) |
| unscored | 8 |

The 8 unscored are 5 Spotify requests (no video to compare against — out of scope per the spec) and
3 YouTube videos that have since been deleted (`ASn5Grx0Mew`, `P1YL1ic88xs`, `wkVgK1SFaY4`, tracks 42, 41,
32). These three can never be scored; nothing is wrong with the files.

**A first pass reported 15 unscored.** Seven of those were `HTTP Error 403: Forbidden` from YouTube —
rate-limiting from ~110 downloads in a burst, not real failures. Retried individually they all scored
**0.97–0.99 `matched`** (tracks 100, 96, 85, 81, 55, 51, 6), and the sweep was then re-run end to end so
the table below is one self-consistent measurement. None of them moves `min_correct`. Anyone reproducing
this should expect 403s on a cold run and retry them.

## Anchors (the brief's sanity check)

| track | expected | measured |
|---|---|---|
| 101 — New Born – Nothing but a Title (retagged in Task 1) | ≈ 0.94 | **0.98** |
| 30 — Hallucinogen – Trancespotter | ≈ 0.97 | **0.98** |
| 38 — Astral Projection – Searching For UFO's | ≈ 0.93 | **0.96** |

All three land at or slightly above the brief's figures, so the harness is measuring what it should. The
Task 4 change of reference (Deezer preview → the video's own audio, issue #67) is the intended baseline
shift and accounts for the small upward movement; it is not noise and not a defect.

## How the verdicts were decided

The certification rule was **written down before any score was read** so the gate outcome could not be
tuned to the numbers. A row is `correct` only when its status is `matched` (≥ 0.90, what production
already accepts), the file and video lengths agree within 15 s, and the track's artist and title agree
with what the request asked for. Everything below 0.90 is `unsure`; anything that looks wrong is
`unsure (proposed: wrong)`; nothing is ever written `wrong` without the owner. That rule certified 96 rows
`correct`; the owner's ruling then certified track 50 `correct` and wrote track 35 `wrong`, the one row in
this sweep the executor could not have certified either way. Eight `matched` rows are left `unsure` only because they fail the strict length or name check —
collaborations credited differently (tracks 53, 56, 16, 57), an `Original Mix` / `2023 Remaster` suffix
(98, 47), and two long uploads (38 at 626 s vs 392 s, 30 at 516 s vs 547 s). All eight score 0.96–0.99, so
none of them could lower `min_correct`; they are listed as `unsure` for honesty, not because they are in
doubt.

The two profile deep-dives were run **after** the threshold was computed. They changed none of the
arithmetic by themselves; the owner's ruling on the two rows they profiled is what moved the threshold
from 0.83 to 0.79.

## Reproduce

```bash
osascript -e 'quit app "Flackey"'      # pgrep -fli flackey — the app is named with a capital F
uv run flackey sweep --out docs/research/2026-09-21-fingerprint-sweep.md
```

The sweep's only write is the `request_references` row beside each request (see the task report for what
that changes). Measured: **11.4 min cold** (12:58 → 13:09, ~110 YouTube audio fetches) and **84 s warm**
(13:11 → 13:12) once the references are stored, since only the Deezer previews are refetched.

## Full table

Sorted by file score, unscored rows last.

| track | artist - title | file s | video s | file | record | note | verdict |
|---|---|---|---|---|---|---|---|
| 35 | Space Cat - Spacecat | 503 | 507 | 0.59 | - | failed | **wrong** (owner ruled by ear - incoherent offsets, flat 0.56-0.60: not the same recording) |
| 50 | Growling Mad Scientists - Do Androids Dream of Electric Sheep | 522 | 518 | 0.86 | - | failed | **correct** (owner ruled by ear - constant +2.5 s offset at all eight probe positions: same recording, different master) |
| 69 | Sun Project - Computer Breath | 520 | 520 | 0.94 | 0.98 | matched | correct |
| 34 | Delta - As a Child | 487 | 487 | 0.95 | - | matched | correct |
| 38 | Astral Projection - Searching For UFO's | 626 | 392 | 0.96 | - | matched | unsure (scored 0.96 >= 0.90 but length gap 234s > 15s) |
| 44 | Astral Projection - Mahadeva '99 | 503 | 505 | 0.97 | - | matched | correct |
| 8 | Astral Projection - Dreaming (Anything Can Happen) | 472 | 472 | 0.97 | - | matched | correct |
| 39 | Power Source - Vorlan | 582 | 583 | 0.97 | - | matched | correct |
| 51 | Growling Mad Scientists - The Last Block | 446 | 446 | 0.97 | - | matched | correct |
| 49 | Mandra Gora - Wicked Warp  | 319 | 319 | 0.97 | - | matched | correct |
| 58 | Sun Project - Dance Of The Witches | 460 | 460 | 0.98 | - | matched | correct |
| 30 | Hallucinogen - Trancespotter | 516 | 547 | 0.98 | - | matched | unsure (scored 0.98 >= 0.90 but length gap 31s > 15s) |
| 17 | Younger Brother - A Paradox of Witches | 435 | 435 | 0.98 | - | matched | correct |
| 97 | Sun Project - Going With The Flow | 364 | 364 | 0.98 | 0.98 | matched | correct |
| 53 | Gunslinger, Shpongle, GMS - Dreamcatcher | 371 | 371 | 0.98 | - | matched | unsure (scored 0.98 >= 0.90 but track name does not match the request text) |
| 68 | Sun Project - Sexdrugs & Acidtrance II | 523 | 524 | 0.98 | 0.97 | matched | correct |
| 2 | Sun Project - Space Dwarfs | 559 | 560 | 0.98 | - | matched | correct |
| 75 | Sun Project - Frisco Machines | 445 | 446 | 0.98 | 0.91 | matched | correct |
| 71 | Sun Project - Spaceships & Spacepeople | 452 | 453 | 0.98 | 0.98 | matched | correct |
| 37 | Hallucinogen - LSD | 409 | 408 | 0.98 | - | matched | correct |
| 33 | Prana - Kiba | 447 | 447 | 0.98 | - | matched | correct |
| 12 | Celestial Intelligence - Inevitable Feelings | 604 | 605 | 0.98 | - | matched | correct |
| 10 | Astral Projection - Nilaya | 566 | 567 | 0.98 | - | matched | correct |
| 82 | Sun Project - From Dusk Till Dawn | 373 | 374 | 0.98 | 0.98 | matched | correct |
| 79 | Sun Project - Energia Magica | 446 | 447 | 0.98 | 0.98 | matched | correct |
| 59 | Sun Project - I Feel | 555 | 556 | 0.98 | 0.97 | matched | correct |
| 74 | Sun Project - Thats A Trap | 488 | 489 | 0.98 | 0.98 | matched | correct |
| 65 | Sun Project - I Feel | 528 | 528 | 0.98 | 0.98 | matched | correct |
| 64 | Sun Project - Space Dwarfs | 532 | 532 | 0.98 | 0.98 | matched | correct |
| 55 | Shiva Shidapu - Journey in Goa  | 180 | 180 | 0.98 | 0.98 | matched | correct |
| 15 | Younger Brother - I Am A Freak | 540 | 540 | 0.98 | - | matched | correct |
| 9 | Talamasca - A Brief History Of Goa-Trance Hallucinogen | 494 | 494 | 0.98 | - | matched | correct |
| 5 | Astral Projection - People Can Fly | 595 | 596 | 0.98 | - | matched | correct |
| 102 | New Born - Omen | 410 | 411 | 0.98 | 0.98 | matched | correct |
| 93 | Sun Project - Sexdrugs & Acidtrance | 408 | 409 | 0.98 | 0.97 | matched | correct |
| 89 | Sun Project - Drosophila | 439 | 440 | 0.98 | - | matched | correct |
| 70 | Sun Project - Subsonic Overdrive | 516 | 516 | 0.98 | 0.98 | matched | correct |
| 66 | Sun Project - Crazy Stories | 522 | 522 | 0.98 | 0.98 | matched | correct |
| 57 | Simon Posford, Space Cat - Invasion | 510 | 510 | 0.98 | 0.98 | matched | unsure (scored 0.98 >= 0.90 but track name does not match the request text) |
| 120 | Tandu - Rodeo | 716 | 716 | 0.98 | 0.98 | matched | correct |
| 99 | Sun Project - Subsonic Overdrive | 556 | 556 | 0.98 | 0.98 | matched | correct |
| 98 | S.U.N Project - Cyber Space Sucker | 489 | 489 | 0.98 | 0.98 | matched | unsure (scored 0.98 >= 0.90 but track name does not match the request text) |
| 95 | Sun Project - T.O.T.C. | 347 | 347 | 0.98 | 0.98 | matched | correct |
| 90 | Sun Project - The Awakening | 508 | 508 | 0.98 | 0.98 | matched | correct |
| 31 | Deedrah - Self Oscillation | 444 | 445 | 0.98 | - | matched | correct |
| 118 | Tandu - Naughty Moves | 539 | 540 | 0.98 | 0.98 | matched | correct |
| 117 | Tandu - New Aura | 610 | 611 | 0.98 | 0.99 | matched | correct |
| 107 | New Born - Something With Stuff | 453 | 454 | 0.98 | 0.98 | matched | correct |
| 101 | New Born - Nothing but a Title | 352 | 353 | 0.98 | 0.98 | matched | correct |
| 72 | Sun Project - Computer Breath | 493 | 494 | 0.98 | 0.98 | matched | correct |
| 52 | Space Tribe - The Ultraviolet Catastrophie | 740 | 741 | 0.98 | - | matched | correct |
| 47 | Oforia - Cream (2023 Remaster) | 468 | 468 | 0.98 | - | matched | unsure (scored 0.98 >= 0.90 but track name does not match the request text) |
| 96 | Sun Project - The Norb | 360 | 361 | 0.98 | 0.97 | matched | correct |
| 94 | Sun Project - Transformation | 366 | 366 | 0.98 | 0.98 | matched | correct |
| 84 | Sun Project - Solitude | 510 | 510 | 0.98 | 0.98 | matched | correct |
| 80 | Sun Project - Simplicity | 463 | 464 | 0.98 | 0.98 | matched | correct |
| 77 | Sun Project - Energia Magica | 445 | 445 | 0.98 | 0.98 | matched | correct |
| 73 | Sun Project - That's A Trap | 435 | 435 | 0.98 | 0.98 | matched | correct |
| 54 | Children Of The Doc - Police 106 | 588 | 585 | 0.98 | - | matched | correct |
| 46 | Bamboo Forest - Breath | 483 | 483 | 0.98 | - | matched | correct |
| 36 | Astrix - On Fire | 384 | 385 | 0.98 | - | matched | correct |
| 122 | Tandu - Visually Distorted | 558 | 559 | 0.99 | 0.98 | matched | correct |
| 113 | New Born - Gentle Perfection | 490 | 491 | 0.99 | 0.98 | matched | correct |
| 83 | Sun Project - Luna | 463 | 463 | 0.99 | 0.98 | matched | correct |
| 78 | Sun Project - Going With the Flow | 367 | 368 | 0.99 | 0.98 | matched | correct |
| 63 | Sun Project - The Saw | 378 | 379 | 0.99 | 0.97 | matched | correct |
| 7 | Goasia - Love & Peace | 499 | 499 | 0.99 | - | matched | correct |
| 111 | New Born - Fish | 538 | 539 | 0.99 | 0.98 | matched | correct |
| 103 | New Born - Playing Games | 449 | 450 | 0.99 | 0.98 | matched | correct |
| 100 | Sun Project - Looking For You | 482 | 482 | 0.99 | 0.98 | matched | correct |
| 92 | Sun Project - Under Control | 502 | 503 | 0.99 | 0.93 | matched | correct |
| 91 | Sun Project - At The Edge Of Time | 490 | 490 | 0.99 | 0.98 | matched | correct |
| 87 | Sun Project - Tribolus | 473 | 473 | 0.99 | 0.98 | matched | correct |
| 86 | Sun Project - Fatal Error | 439 | 440 | 0.99 | 0.98 | matched | correct |
| 81 | Sun Project - T8 | 451 | 451 | 0.99 | 0.97 | matched | correct |
| 62 | Miranda - Year 2000 | 399 | 400 | 0.99 | 0.98 | matched | correct |
| 60 | Sun Project - Casio-Paya | 535 | 536 | 0.99 | 0.98 | matched | correct |
| 48 | Orion - Nazca Spider  | 537 | 537 | 0.99 | - | matched | correct |
| 13 | Filteria - Dog Days Bliss | 531 | 532 | 0.99 | - | matched | correct |
| 6 | Talamasca - A Brief History Of Goa-Trance Etnica | 514 | 514 | 0.99 | - | matched | correct |
| 3 | Goasia - Space Travellers | 469 | 469 | 0.99 | - | matched | correct |
| 1 | Hallucinogen - Orphic Thrench | 442 | 443 | 0.99 | - | matched | correct |
| 119 | Tandu - The System | 621 | 622 | 0.99 | 0.98 | matched | correct |
| 109 | New Born - Going Somewhere Else | 613 | 614 | 0.99 | 0.98 | matched | correct |
| 108 | New Born - Far from Home | 376 | 377 | 0.99 | 0.98 | matched | correct |
| 105 | New Born - a Sleeper's Guide | 581 | 582 | 0.99 | 0.99 | matched | correct |
| 88 | Sun Project - Chapachoolka | 435 | 436 | 0.99 | 0.98 | matched | correct |
| 67 | Sun Project - At The Edge of Time | 504 | 504 | 0.99 | 0.98 | matched | correct |
| 61 | Sun Project - The Awakening | 466 | 466 | 0.99 | 0.98 | matched | correct |
| 29 | Man With No Name - Deliverance | 440 | 440 | 0.99 | - | matched | correct |
| 115 | New Born - The New World | 491 | 492 | 0.99 | 0.98 | matched | correct |
| 85 | Sun Project - Paranormal | 456 | 457 | 0.99 | 0.98 | matched | correct |
| 56 | Raja Ram, Space Cat - Snorkelblaster | 519 | 519 | 0.99 | 0.98 | matched | unsure (scored 0.99 >= 0.90 but track name does not match the request text) |
| 11 | Filteria - Made Out Of Trance | 674 | 674 | 0.99 | - | matched | correct |
| 76 | Sun Project - Out Of My Brain | 480 | 480 | 0.99 | 0.98 | matched | correct |
| 121 | Tandu - Interstealer Dawn | 595 | 596 | 0.99 | 0.98 | matched | correct |
| 114 | New Born - Ancient Piano Melody | 164 | 165 | 0.99 | 0.98 | matched | correct |
| 112 | New Born - Touch Nothing | 549 | 550 | 0.99 | 0.98 | matched | correct |
| 110 | New Born - Birds of Imagination | 557 | 558 | 0.99 | 0.98 | matched | correct |
| 106 | New Born - Sun Worshiper | 496 | 497 | 0.99 | 0.97 | matched | correct |
| 14 | Hallucinogen - L.S.D. | 403 | 404 | 0.99 | - | matched | correct |
| 4 | Mindsphere - Depth of Consciousness | 498 | 498 | 0.99 | - | matched | correct |
| 104 | New Born - Universal Bus | 672 | 673 | 0.99 | 0.99 | matched | correct |
| 43 | Hallucinogen - Mi-loony-um! | 652 | 655 | 0.99 | - | matched | correct |
| 16 | Mirabelle Rose, Younger Brother - I Belong To Nowhere | 460 | 460 | 0.99 | - | matched | unsure (scored 0.99 >= 0.90 but track name does not match the request text) |
| 116 | New Born - These Yellow Eyes | 452 | 453 | 0.99 | 0.97 | matched | correct |
| 42 | Oforia - Maximiser | 460 | 461 | - | - | no reference: ERROR: [youtube] ASn5Grx0Mew: Video unavailable | unsure (unscored: no reference: ERROR: [youtube] ASn5Grx0Mew: Video unavailable) |
| 41 | Oforia - Cream | 468 | 468 | - | - | no reference: ERROR: [youtube] P1YL1ic88xs: Video unavailable | unsure (unscored: no reference: ERROR: [youtube] P1YL1ic88xs: Video unavailable) |
| 32 | Tandu - Alien Pump | 534 | 535 | - | - | no reference: ERROR: [youtube] wkVgK1SFaY4: Video unavailable | unsure (unscored: no reference: ERROR: [youtube] wkVgK1SFaY4: Video unavailable) |
| 28 | Younger Brother - Happy Pills | 529 | 529 | - | - | not a YouTube request | unsure (unscored: not a YouTube request) |
| 27 | Ott - One Day I Wish To Have This Kind Of Time | 514 | 514 | - | - | not a YouTube request | unsure (unscored: not a YouTube request) |
| 26 | Shpongle - Shpongle Spores | 436 | 436 | - | - | not a YouTube request | unsure (unscored: not a YouTube request) |
| 25 | Hallucinogen - L.S.D. | 560 | 560 | - | - | not a YouTube request | unsure (unscored: not a YouTube request) |
| 24 | Shpongle - Starshpongled Banner | 503 | 503 | - | - | not a YouTube request | unsure (unscored: not a YouTube request) |
