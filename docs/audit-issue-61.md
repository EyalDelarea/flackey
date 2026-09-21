# Audit of issue #61 and its eight sub-issues

Checked against the source tree at `8e9fe72` and the live database
(`~/Library/Application Support/flackey/flackey.sqlite`, read from a copy).

## Verdict

The plan is sound in shape and the evidence behind it is real — every code claim I
re-checked is accurate to the line, and every database count I recomputed came back
identical. Three things need changing before the work starts.

1. **#63's artist floor cannot be built at any threshold.** Blocking.
2. **#64's mechanism is free to fix today and a recall regression once #69 lands.** Keep the gate, change the mechanism.
3. **#69 undersizes the duration tolerance by an order of magnitude.** It sizes it at 4 tracks from
   a 16-track probe; the attempt log says it is the first rejection for 1,584 files. It deserves its
   own measurement rather than riding in behind #67/#68.

---

## What checked out

Code, verified line by line:

| claim | where | status |
|---|---|---|
| `best_match` accepts on `0.5*artist + 0.5*title >= 70` | `catalog.py:139` | ✓ |
| `preferred_isrc` is only a sort tie-breaker, so a naive floor filters before it | `catalog.py:133-137` | ✓ |
| unknown duration scores the full 30/30 | `match.py:70-73` | ✓ |
| `ref_title` takes the catalogue unconditionally while `ref_dur` defends the video | `match.py:85` vs `:96-97` | ✓ |
| `decide` returns `chosen=None` only for an empty candidate list | `match.py:106` | ✓ |
| `worker.py:546` already handles `Decision(False, None, …)` | `worker.py:546-550` | ✓ exact |
| `_split_artist_title` is bracket-blind | `identify.py:90-94` | ✓ |
| `Granada (Remix - 98)` → artist `Granada (Remix`, title `98)` | run live | ✓ |
| `compare` returns `0.0` when `len(hay) < len(needle)` | `fingerprint.py:69-70` | ✓ |
| yt-dlp runs with `skip_download: True` | `youtube.py:47` | ✓ |
| `_enrich` leaves `duration_s=None` on a Deezer failure | `source/deezer_bot.py:94-97` | ✓ |
| `'no Deezer results'` is only reachable with `catalog is None` | `worker.py:494-501` | ✓ |

Database, recomputed:

- 207 rows / 143 distinct requests — exact.
- `done` 110, `not_found` 50, `cancelled` 27 — exact.
- All 50 `not_found` rows carry `error_message = 'no Deezer results'` — exact.
- `catalog_track_id IS NULL` = 76 = 50 + 26 — exact.
- Fingerprint: **matched 45, skipped 45, failed 0**; all 45 skips read
  `no deezer id for this request`; matched scores span **0.906–0.997** — exact.
- Title-floor blast radius (#63): of 105 distinct `done`/`duplicate` requests with a
  Beatport match, **103 score titleAgr ≥ 90, none fall in 70–89, and the only 2 below 70 are
  the two bad files**. The claim holds; a title floor at 75–80 is safe.

---

## 1. The artist floor in #63 has no threshold (blocking)

#63 asks for the distribution check to be run rather than guessed. Run on the live data, it
refutes the proposal.

`token_sort_ratio(query_artist, catalog_artist)` over the same 105 filed requests:

```
 44.4   'ShpongleMusic'  -> 'Gunslinger, Shpongle, GMS'   CORRECT (filed, fingerprint 0.966)
 56.2   'Space Cat'      -> 'Simon Posford, Space Cat'    CORRECT (filed, fingerprint 0.969)
 42.9   'Universal Sound'-> 'Outputmessage'               WRONG   (the match #63 wants blocked)
```

The gap between the worst correct match and the wrong one is **1.5 points**. #63 proposes
"something near 60"; a floor at 60 rejects both of those tracks — and **#70's own
"cleared on inspection" table certifies both of them as correct**, at 0.966 and 0.969.
#63 also cites 72.2 (`Tryambaka, Shiva Shidapu`) as the correct lower bound; the real
lower bound in the data is 44.4.

**Do:** ship the title floor — it is measured and safe. Drop the artist floor from #63, or
replace it with a different mechanism. Per-credit matching looks promising: comparing the
query artist against each comma-separated credit separately scores 76.2 / 100 / 100 on the
three correct cases and 42.9 on the wrong one. But `token_set_ratio` per credit also scores
`Hallucinogen In Dub` against `Hallucinogen` at 100, which is exactly the invariant
`catalog.py:118` exists to protect — so that needs its own measurement before it is trusted.

## 2. #64's mechanism is free to fix now, and a recall regression the moment #69 lands

Returning `Decision(False, None, …)` lands at `worker.py:546`, which sets `NOT_FOUND`
terminally — skipping the catalogue-only Soulseek path at `worker.py:508-528`, which is reached
only when `cands` is empty.

Today this costs nothing: **26 of the 27 cancelled rows have `catalog_track_id IS NULL`**, and
`decide` already forces a review on `catalog is None` (`match.py:127`). Exactly one cancelled row
has a Beatport match. So #64's `chosen=None` and the alternative below land in the same place
right now.

It stops being free the moment #69 opens the catalogue gate, because then the catalogue-only
Soulseek path is the whole point. Worth building correctly while it is still a one-line
difference.

**Do:** drop the weak-title candidates from `cands`, then let the existing `if not cands` branch
carry the request to `catalog_candidate(catalog)` and Soulseek. Only return `chosen=None` when
there is no catalogue match either.

## 3. #69 undersizes the duration tolerance by an order of magnitude

The umbrella's outcome table omits `state='error'` entirely: **12 rows / 4 distinct requests**,
every one with a valid Beatport match, every one reading *"nothing on Soulseek matched this
track closely enough"*. Soulseek was asked. The pick rules said no.

Aggregated over all 216 `no_pick` attempts (9,046 candidate files seen):

| rejection reason | files |
|---|---|
| `extension` (lossy — correct) | 7,195 |
| **`rule_duration` (length vs reference)** | **1,584** |
| sample rate above max | 144 |
| version words missing | 77 |
| no length reported | 43 |
| **`rule_title`** | **1** |

Of the ~1,850 files that were already in a lossless format, **86% met the duration rule first**.
The title rule has rejected one file in the project's history.

The rules short-circuit on the first failure, so 1,584 is *"duration rejected it before anything
else got a look"*, not *"1,584 files a wider window recovers"* — they would still face version
words, sample rate and title. Even so, #69 sizes this change at 4 tracks out of a 16-track probe,
and the attempt log puts it in front of 1,584 files. It is independent of #67 and #68, and it is
measurable today by replaying the stored `report_json` at a wider window without downloading
anything. Measure it on its own rather than shipping it as a rider on #69.

The umbrella's outcome table should also grow an `error` row. Four requests are sitting there that
Beatport identified and Soulseek was asked about.

---

## Implementation gaps worth writing into the sub-issues

- **#69 has no Candidate to work with.** It says tags fall back to `_fallback_catalog`, but that
  takes a `Candidate` (`worker.py:143`). On the path #69 opens there is neither a Beatport record
  nor a Deezer candidate. It needs a `query_candidate(query)` mirroring
  `catalog_candidate(catalog)`.
- **Per-reference tolerance needs more than `Reference.durations`.** The property returns a bare
  tuple and `rule_duration` (`lossless.py:170-176`) applies one `p.duration_tolerance_s` to every
  entry. The shape is right; each entry has to carry its own window.
- **#68's ordering claim has an unstated dependency.** "Cannot check → review" happens *after*
  download, in `_verify_and_file`. The `catalog is None` gate that #69 relaxes fires *before*
  download, in `decide` (`match.py:127`). For #68 to actually protect #69, `decide` must start
  auto-accepting with `catalog is None` and lean on the post-download fingerprint. Neither issue
  says this, and #68's whole sequencing argument rests on it.
- **#70's shadow argument is stronger than stated.** `find_duplicate` (`library.py:59-67`) checks
  ISRC *first*. The mistagged file carries `TSRC=QT6EC2645809` from the wrong recording, so a
  future request for the real *Between The Lines* is shadowed at the ISRC level, not just by
  normalised artist/title/mix.

## One correction to #67's blast radius

#67 says *"89 of 110 filed tracks are Soulseek AIFF, and each carries the `NO_FINGERPRINT_FLAG`."*
The second half is wrong. Joining the 90 Soulseek tracks to their filed attempt:

```
matched  45
skipped  45
```

Half of the Soulseek downloads **were** fingerprinted — the ones whose chosen candidate carried a
Deezer id. Only 45 are acoustically dark, not 90. That halves the stated blast radius of #67 and
#68 without changing the argument for either: 45 files filed on text similarity alone is still
the right thing to fix.

One smaller correction: the umbrella says 17 distinct `not_found`. By `raw_text`, by
`artist|title`, and by YouTube video id alike the DB gives **20**. (Its "118 filed tracks" and
"282 attempts" both reconcile — 118 is `done` + `duplicate` rows, and the attempts table has grown
since the audit was written.)

## Sequencing

Order is right as written. #70 first, #68 before #69 — both arguments hold. Two amendments:

1. Split #63: ship the title floor now, take the artist floor back to the drawing board.
2. Insert the duration-tolerance measurement early and on its own, rather than as a rider on #69.
