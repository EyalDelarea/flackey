# No-pick replay — 2026-09-21 (Task 7, issue #73)

Ran `flackey replay-picks` against a **copy** of the live database (`DATA_DIR` pointed at a scratch
folder; `sqlite3 .backup` of `~/Library/Application Support/flackey/flackey.sqlite`). No network, no
Soulseek, nothing written back. Reproduce with:

```bash
mkdir -p "$TMPDIR/flackey-replay"
sqlite3 "$HOME/Library/Application Support/flackey/flackey.sqlite" ".backup '$TMPDIR/flackey-replay/flackey.sqlite'"
DATA_DIR="$TMPDIR/flackey-replay" uv run flackey replay-picks --out docs/research/2026-09-21-no-pick-replay.md
```

## Decision: **keep `version` first in `IDENTITY_RANKERS`.** Do not move `duration` ahead of it.

Task 13 applies nothing here.

**The decision does not depend on how clause 2 is read.** The proposed intervention — putting `duration`
ahead of `version` — was measured directly against all 47 far attempts:

- **43 of 47: it changes nothing.** The nearer file is not a survivor, so no ranker can reach it. The top
  pick is already the nearest survivor.
- **4 of 47: it makes things worse.** It promotes `09 - Boris Blenn - Rain (California Sunshine Remix).flac`
  — a *different recording* — and is still 122 s from the asked length instead of 125 s.

A change that is a no-op in 91 % of cases and harmful in the other 9 % should not ship, whichever way the
spec's clause 2 is construed. The clause-by-clause reading below reaches the same answer.

## The numbers Step 6 asks for

| question | answer |
|---|---|
| attempts replayed | 216 |
| candidate files summed across them | 9,046 |
| attempts that now have a survivor | **116** of 216 |
| top pick within 10 s of the asked length | **67** of 116 |
| top pick more than 60 s off | **47** of 116 |
| attempts with no survivor at all | 100 (17 saw zero files; 83 rejected entirely on `extension`, i.e. every file offered was lossy) |

These reproduce Task 6's smoke run exactly (216 / 9,046 / 116 / 47; Task 6 also reported 67 within 10 s,
17 zero-file and 83 extension-only). Nothing changed between the two runs.

### The percentage that drives the decision

- **40.5 %** — 47 of the **116 attempts that actually have a top pick**. This is the reading used, because
  an attempt with no survivor has no top pick and cannot be "more than 60 s off".
- 21.8 % — 47 of all **216** attempts, for completeness. Under the 25 % line on this denominator.

So clause 1 ("more than 25 % of top picks are more than 60 s off") **passes** on the reading that makes
sense, and is reported both ways so the owner can check it.

### Clause 2 — "and a nearer file existed in the list"

This clause can be read two ways, and the reading matters for the letter of the rule but not for the
outcome. A ranker can only reorder files that **survive the hard rules**, so that is the reading used
here; on the literal reading ("anywhere in the list the peers returned") clause 2 would pass at 45/47.
The measured effect of the change, above, is what settles it either way.

| where we look for a nearer file | far attempts where one exists |
|---|---|
| among the **survivors** (what a ranker can actually reach) | **4 of 47  (8.5 %)** |
| among every file the peers offered, including the lossy ones the `extension` rule drops | 45 of 47 |

The 45 counts files no ranker can reach. In almost every far attempt the genuinely near file is an **.mp3** —
e.g. `03-california_sunshine--rain-shelter.mp3` at 626 s against a 627 s request, one second off — and the
`extension` hard rule removes it before any ranker sees it. Re-ordering `duration` ahead of `version`
cannot reach a file that was never a survivor.

And in the 4 attempts where a nearer *survivor* does exist, it is nearer by **3 seconds** (122 s off
instead of 125 s) and it is a different recording: `09 - Boris Blenn - Rain (California Sunshine Remix).flac`.
Putting `duration` first would trade the original for a remix and still be 122 s from the asked length.
That is the exact failure `version`-first exists to prevent.

### The 47 far attempts are 5 requests, not 47 tracks

| request | asked | text | far attempts (retries) |
|---|---|---|---|
| 132 | 627 s | Rain | 15 |
| 101 | 627 s | Rain | 15 |
| 51 | 627 s | Rain | 14 |
| 205 | 353 s | Nothing but a Title | 2 |
| 47 | 510 s | Invasion Original Mix | 1 |

44 of the 47 are retries of the same track (California Sunshine – *Rain*, queued three times). The 116
attempts with a survivor are only **14 distinct requests**; the 67 within 10 s are **7**. The 40.5 % is
therefore one hard track counted forty-four times, not a broad ranking failure — a second reason not to
retune the ranker on it.

### Per-request detail for the far cases

| request | asked | top pick | nearest **survivor** | nearest file of any kind | could a ranker fix it? |
|---|---|---|---|---|---|
| 51 / 132 | 627 s | `3-01 California Sunshine - Rain.flac` (502 s, −125 s) | `09 - Boris Blenn - Rain (California Sunshine Remix).flac` (505 s, −122 s) | `03-california_sunshine--rain-shelter.mp3` (626 s, −1 s) | only to a remix 122 s off — no |
| 101 | 627 s | `01-california_sunshine_-_rain_2010-flachedelic.flac` (540 s, −87 s) | same file | `03-california_sunshine--rain-shelter.mp3` (626 s, −1 s) | no nearer survivor |
| 205 | 353 s | `01-07 Between the Lines.flac` (249 s, −104 s) | same file | same file | no — one file was offered in total |
| 47 | 510 s | `04. Space Cat & Simon Posford - Invasion (Space Cat 2014 Mix).flac` (441 s, −69 s) | same file | `…Invasion - Original Mix.mp3` (509 s, −1 s) | no nearer survivor |

Request 47 is the clearest case for keeping `version` first: the survivor is a *2014 Mix* of a request
that asked for the *Original Mix*, and the only near file is lossy. `rank_version` is what demotes it.

### What this measurement actually says is wrong

Not the ranker order — **lossless availability**. 83 of the 100 no-survivor attempts, and the near miss in
four of the five far requests, are the same fact: the right recording was on the network as an mp3 and the
`extension` rule dropped it. That is #53 / the lossy fallback, explicitly out of scope for this epic, and
it is recorded here only so the number is not misread as a ranking defect.

---


- attempts with a survivor now: 116
- top pick within 10 s of the asked length: 67
- top pick more than 60 s off: 47

| attempt | request | asked s | seen | survivors | #1 file | #1 s | delta | #2 | #3 | #4 |
|---|---|---|---|---|---|---|---|---|---|---|
| 334 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 333 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 332 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 331 | Rain | 627 | 245 | 53 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 California Sunshine - Rain  (540s) | 01-california_sunshine_-_rain_ (540s) | 01 - California Sunshine - Rai (540s) |
| 330 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 329 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 328 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 327 | Rain | 627 | 241 | 53 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 California Sunshine - Rain  (540s) | 01 - California Sunshine - Rai (540s) | 01. california sunshine - rain (540s) |
| 326 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 325 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 324 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 323 | Rain | 627 | 242 | 55 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 California Sunshine - Rain  (540s) | 01-california_sunshine_-_rain_ (540s) | 01 - California Sunshine - Rai (540s) |
| 322 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 321 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 320 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 319 | Rain | 627 | 228 | 49 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 - California Sunshine - Rai (540s) | 01. california sunshine - rain (540s) | 01-california_sunshine_-_rain_ (540s) |
| 318 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 317 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 316 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 315 | Rain | 627 | 235 | 48 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 01 - California Sunshine - Rai (540s) | 01. california sunshine - rain (540s) |
| 314 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 313 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 312 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 311 | Rain | 627 | 235 | 47 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 01 - California Sunshine - Rai (540s) | 01. california sunshine - rain (540s) |
| 310 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 309 | Horrorgram (Remastered 2024) | 453 | 1 | 0 | - | ? | ? | - | - | - |
| 308 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 307 | Rain | 627 | 105 | 25 | 01 - California Sunshine - Rain 2010.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 01. california sunshine - rain (540s) | 09 Electric Universe - Rain.fl (502s) |
| 306 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 305 | Horrorgram (Remastered 2024) | 453 | 1 | 0 | - | ? | ? | - | - | - |
| 304 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 303 | Rain | 627 | 106 | 25 | 01 - California Sunshine - Rain 2010.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 01. california sunshine - rain (540s) | 09 Electric Universe - Rain.fl (502s) |
| 302 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 301 | Horrorgram (Remastered 2024) | 453 | 1 | 0 | - | ? | ? | - | - | - |
| 300 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 299 | Rain | 627 | 111 | 27 | 01 - California Sunshine - Rain 2010.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 01. california sunshine - rain (540s) | 09 Electric Universe - Rain.fl (502s) |
| 298 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 297 | Horrorgram (Remastered 2024) | 453 | 0 | 0 | - | ? | ? | - | - | - |
| 296 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 295 | Rain | 627 | 92 | 18 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 - Rain - Rain.flac (503s) | 09 - Rain.flac (505s) | California_Sunshine_Har_El_Vis (503s) |
| 294 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 293 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 292 | Horrorgram (Remastered 2024) | 453 | 1 | 0 | - | ? | ? | - | - | - |
| 291 | Rain | 627 | 93 | 18 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 - Rain - Rain.flac (503s) | 09 - Rain.flac (505s) | California_Sunshine_Har_El_Vis (503s) |
| 290 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 289 | Horrorgram (Remastered 2024) | 453 | 1 | 0 | - | ? | ? | - | - | - |
| 288 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 287 | Rain | 627 | 91 | 18 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 - Rain - Rain.flac (503s) | 09 - Rain.flac (505s) | California_Sunshine_Har_El_Vis (503s) |
| 286 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 285 | Horrorgram (Remastered 2024) | 453 | 0 | 0 | - | ? | ? | - | - | - |
| 284 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 283 | Rain | 627 | 93 | 18 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | California_Sunshine_Har_El_Vis (503s) | 19 - California Sunshine (Nash (502s) | 01 - Rain - Rain.flac (503s) |
| 282 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 281 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 280 | Horrorgram (Remastered 2024) | 453 | 1 | 0 | - | ? | ? | - | - | - |
| 279 | Rain | 627 | 98 | 20 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | California_Sunshine_Har_El_Vis (503s) | 19 - California Sunshine (Nash (502s) | 01 - Rain - Rain.flac (503s) |
| 278 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 277 | Horrorgram (Remastered 2024) | 453 | 1 | 0 | - | ? | ? | - | - | - |
| 276 | Ancient Piano Melody | 165 | 4 | 0 | - | ? | ? | - | - | - |
| 275 | These Yellow Eyes | 453 | 4 | 0 | - | ? | ? | - | - | - |
| 274 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 273 | Rain | 627 | 90 | 18 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | California_Sunshine_Har_El_Vis (503s) | 19 - California Sunshine (Nash (502s) | 01 - Rain - Rain.flac (503s) |
| 272 | The New World | 492 | 442 | 119 | 04 Born Under A Bad Sign.flac | 455 | 37 | 03 New York Philharmonic & Mor (307s) | 10. Deathmachine - Fighting fo (302s) | 03. Belladonna - A New Born Da (282s) |
| 271 | Gentle Perfection | 491 | 4 | 0 | - | ? | ? | - | - | - |
| 270 | Touch Nothing | 550 | 5 | 0 | - | ? | ? | - | - | - |
| 269 | Fish | 539 | 13 | 0 | - | ? | ? | - | - | - |
| 268 | Birds of Imagination | 558 | 3 | 0 | - | ? | ? | - | - | - |
| 267 | Going Somewhere Else | 614 | 5 | 0 | - | ? | ? | - | - | - |
| 266 | Far from Home | 377 | 5 | 0 | - | ? | ? | - | - | - |
| 265 | Sun Worshiper | 497 | 5 | 0 | - | ? | ? | - | - | - |
| 264 | Something With Stuff | 454 | 4 | 0 | - | ? | ? | - | - | - |
| 263 | Playing Games | 450 | 4 | 0 | - | ? | ? | - | - | - |
| 262 | a Sleeper's Guide | 582 | 3 | 0 | - | ? | ? | - | - | - |
| 261 | Universal Bus | 673 | 5 | 0 | - | ? | ? | - | - | - |
| 260 | Omen | 411 | 6 | 0 | - | ? | ? | - | - | - |
| 259 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 258 | Horrorgram (Remastered 2024) | 453 | 1 | 0 | - | ? | ? | - | - | - |
| 257 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 256 | Rain | 627 | 95 | 18 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 - Rain - Rain.flac (503s) | 09 - Rain.flac (505s) | California_Sunshine_Har_El_Vis (503s) |
| 255 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 254 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 1 | 0 | - | ? | ? | - | - | - |
| 253 | Horrorgram (Remastered 2024) | 453 | 1 | 0 | - | ? | ? | - | - | - |
| 252 | Rain | 627 | 92 | 18 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 - Rain - Rain.flac (503s) | 09 - Rain.flac (505s) | 09 Electric Universe - Rain.fl (502s) |
| 251 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 1 | 0 | - | ? | ? | - | - | - |
| 250 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 249 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 248 | Rain | 627 | 215 | 47 | 01 California Sunshine - Rain 2010.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 01-california_sunshine_-_rain_ (540s) | 01 Rain.aiff (540s) |
| 247 | Nothing but a Title | 353 | 1 | 1 | 01-07 Between the Lines.flac | 249 | 104 | - | - | - |
| 246 | Nothing but a Title | 353 | 1 | 1 | 01-07 Between the Lines.flac | 249 | 104 | - | - | - |
| 244 | Snorkelblaster (Original Mix) | 519 | 1 | 0 | - | ? | ? | - | - | - |
| 212 | Year 2000 | 400 | 72 | 11 | 04 Year 2000 - Miranda.aif | 399 | 1 | 03. Miranda - Year 2000 - Mira (384s) | 03. Miranda - Year 2000.flac (384s) | Distance To Goa 06 - 03 - Mira (384s) |
| 201 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 200 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 199 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 198 | Rain | 627 | 209 | 36 | 01 California Sunshine - Rain 2010.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 09 - Rain.flac (505s) | 3-01 California Sunshine - Rai (502s) |
| 197 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 196 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 195 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 194 | Rain | 627 | 212 | 43 | 01 California Sunshine - Rain 2010.flac | 540 | 87 | 09 - Rain.flac (505s) | 3-01 California Sunshine - Rai (502s) | 19 - California Sunshine (Nash (502s) |
| 193 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 192 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 191 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 190 | Rain | 627 | 188 | 38 | 01 California Sunshine - Rain 2010.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 09 - Rain.flac (505s) | 3-01 California Sunshine - Rai (502s) |
| 189 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 188 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 187 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 186 | Rain | 627 | 231 | 37 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01. California Sunshine - Befo (524s) | 09 - Rain.flac (505s) | 3-01 California Sunshine - Rai (502s) |
| 185 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 184 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 183 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 182 | Rain | 627 | 225 | 39 | 01 California Sunshine - Rain 2010.flac | 540 | 87 | 01. California Sunshine - Befo (524s) | 09 - Rain.flac (505s) | 3-01 California Sunshine - Rai (502s) |
| 181 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 180 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 179 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 178 | Rain | 627 | 216 | 30 | 09 - Rain.flac | 505 | 122 | 3-01 California Sunshine - Rai (502s) | 19 - California Sunshine (Nash (502s) | 01-california_sunshine_-_rain- (503s) |
| 177 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 176 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 175 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 174 | Rain | 627 | 226 | 32 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 19 - California Sunshine (Nash (502s) | 01-california_sunshine_-_rain- (503s) |
| 173 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 172 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 171 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 170 | Rain | 627 | 222 | 35 | 01 California Sunshine - Rain 2010.flac | 540 | 87 | 09 - Rain.flac (505s) | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) |
| 169 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 168 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 167 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 166 | Rain | 627 | 228 | 40 | 01 California Sunshine - Rain 2010.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 09 - Rain.flac (505s) | 3-01 California Sunshine - Rai (502s) |
| 164 | Snorkelblaster (Original Mix) | 519 | 0 | 0 | - | ? | ? | - | - | - |
| 163 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 161 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 160 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 159 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 158 | Rain | 627 | 188 | 34 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 19 - California Sunshine (Nash (502s) |
| 157 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 156 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 155 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 154 | Rain | 627 | 191 | 35 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 19 - California Sunshine (Nash (502s) |
| 153 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 152 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 151 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 150 | Rain | 627 | 187 | 33 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 19 - California Sunshine (Nash (502s) |
| 149 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 148 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 147 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 146 | Rain | 627 | 188 | 33 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 - California Sunshine - Rai (503s) | 3-01 California Sunshine - Rai (502s) | 09 Electric Universe - Rain.fl (502s) |
| 145 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 144 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 143 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 142 | Rain | 627 | 188 | 33 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 - California Sunshine - Rai (503s) | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) |
| 141 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 140 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 139 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 138 | Rain | 627 | 181 | 34 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 01 - California Sunshine - Rai (503s) | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) |
| 137 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 136 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 135 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 134 | Rain | 627 | 184 | 31 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 301. California Sunshine (Nash (502s) |
| 133 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 132 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 131 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 130 | Rain | 627 | 183 | 31 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 301. California Sunshine (Nash (502s) |
| 129 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 128 | Horrorgram (Remastered 2024) | 453 | 3 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 127 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 126 | Rain | 627 | 184 | 31 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 301. California Sunshine (Nash (502s) |
| 124 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 123 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 120 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 119 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 118 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 7 | 0 | - | ? | ? | - | - | - |
| 117 | Rain | 627 | 202 | 28 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 301. California Sunshine (Nash (502s) |
| 115 | Snorkelblaster (Original Mix) | 519 | 0 | 0 | - | ? | ? | - | - | - |
| 114 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 113 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 112 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 111 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 7 | 0 | - | ? | ? | - | - | - |
| 110 | Rain | 627 | 189 | 24 | 3-01 California Sunshine - Rain.flac | 502 | 125 | 01-california_sunshine_-_rain- (503s) | 301. California Sunshine (Nash (502s) | 09 - Rain.flac (505s) |
| 109 | Snorkelblaster (Original Mix) | 519 | 0 | 0 | - | ? | ? | - | - | - |
| 108 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 2 | 1 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | - | - | - |
| 107 | Horrorgram (Remastered 2024) | 453 | 2 | 1 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | - | - | - |
| 106 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 105 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 5 | 0 | - | ? | ? | - | - | - |
| 103 | Rain | 627 | 205 | 29 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 301. California Sunshine (Nash (502s) |
| 101 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 2 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | 16 - Hallucinogen - The Albums (400s) | - | - |
| 100 | Snorkelblaster (Original Mix) | 519 | 1 | 0 | - | ? | ? | - | - | - |
| 99 | Horrorgram (Remastered 2024) | 453 | 3 | 2 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | 12 - Hallucinogen - The Albums (453s) | - | - |
| 98 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 97 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 4 | 0 | - | ? | ? | - | - | - |
| 96 | Rain | 627 | 186 | 34 | 01 - california sunshine - rain 2010.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) |
| 94 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 2 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | 16 - Hallucinogen - The Albums (400s) | - | - |
| 93 | Snorkelblaster (Original Mix) | 519 | 1 | 0 | - | ? | ? | - | - | - |
| 92 | Horrorgram (Remastered 2024) | 453 | 3 | 2 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | 12 - Hallucinogen - The Albums (453s) | - | - |
| 91 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 90 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 89 | Rain | 627 | 197 | 35 | 01 - california sunshine - rain 2010.flac | 540 | 87 | 01-california_sunshine_-_rain_ (540s) | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) |
| 88 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 3 | 2 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | 16 - Hallucinogen - The Albums (400s) | - | - |
| 87 | Snorkelblaster (Original Mix) | 519 | 1 | 0 | - | ? | ? | - | - | - |
| 86 | Horrorgram (Remastered 2024) | 453 | 3 | 2 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | 12 - Hallucinogen - The Albums (453s) | - | - |
| 85 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 84 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 6 | 0 | - | ? | ? | - | - | - |
| 82 | Rain | 627 | 192 | 31 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 301. California Sunshine (Nash (502s) |
| 81 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 4 | 2 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | 16 - Hallucinogen - The Albums (400s) | - | - |
| 80 | Snorkelblaster (Original Mix) | 519 | 103 | 8 | 09 - Rain.flac | 505 | 14 | 09 - Rain.flac (505s) | 301. California Sunshine (Nash (502s) | 01 - Rain - Rain.flac (503s) |
| 68 | Snorkelblaster (Original Mix) | 519 | 0 | 0 | - | ? | ? | - | - | - |
| 67 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 4 | 2 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | 16 - Hallucinogen - The Albums (400s) | - | - |
| 66 | Horrorgram (Remastered 2024) | 453 | 4 | 2 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | 12 - Hallucinogen - The Albums (453s) | - | - |
| 65 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 64 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 7 | 0 | - | ? | ? | - | - | - |
| 63 | Rain | 627 | 215 | 32 | 3-01 California Sunshine - Rain.flac | 502 | 125 | 01-california_sunshine_-_rain- (503s) | 301. California Sunshine (Nash (502s) | 09 - Rain.flac (505s) |
| 57 | Snorkelblaster (Original Mix) | 519 | 0 | 0 | - | ? | ? | - | - | - |
| 56 | Jiggle Of The Sphinx (Remastered 2024) | 401 | 4 | 2 | 16. Hallucinogen - Jiggle Of The Sphinx (Remastered).flac | 400 | 1 | 16 - Hallucinogen - The Albums (400s) | - | - |
| 51 | Horrorgram (Remastered 2024) | 453 | 4 | 2 | 12. Hallucinogen - Horrorgram (Remastered).flac | 453 | 0 | 12 - Hallucinogen - The Albums (453s) | - | - |
| 47 | Journey in Goa | 180 | 0 | 0 | - | ? | ? | - | - | - |
| 45 | Space Cat, Riktam  & Raja Ram  - Snorkel Blaster | 560 | 7 | 0 | - | ? | ? | - | - | - |
| 38 | Rain | 627 | 218 | 32 | 01-california_sunshine_-_rain_2010-flachedelic.flac | 540 | 87 | 3-01 California Sunshine - Rai (502s) | 01-california_sunshine_-_rain- (503s) | 301. California Sunshine (Nash (502s) |
| 36 | Invasion Original Mix | 510 | 20 | 2 | 04. Space Cat & Simon Posford - Invasion (Space Cat 2014 Mix | 441 | 69 | 02. Space Cat & Simon Posford  (439s) | - | - |
