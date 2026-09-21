# Search correctness: get the audio we actually asked for — design record

Issue: https://github.com/EyalDelarea/flackey/issues/61 (sub-issues #63–#70). Working notes:
`docs/search-quality-review-61.md`, `docs/audit-issue-61.md`. Decided with the owner on 2026-09-20; simplified
the same day after a prior-art survey (below) and the owner's review of the first flow chart.

## Goal

When a track is asked for, the file that lands is that track, and a track that exists on Soulseek gets
fetched. Two failures, measured on the live database (207 rows / 143 distinct requests):

| failure | distinct | mechanism |
|---|---|---|
| wrong file filed | 3 | catalogue text similarity accepted a different recording |
| never fetched (`not_found`) | 20 | no Beatport / Deezer record, so Soulseek was never asked |
| review skipped (`cancelled`) | 10 | "not on Beatport; cannot be verified" reviews the owner skipped |
| errored with a Beatport match | 4 | Soulseek was asked; the pick rules rejected every file |

A live Soulseek probe found a lossless copy of all 16 never-fetched tracks it could probe.

## Values (decided)

1. **A wrong file is the worst outcome.** It corrupts the library and shadows the real track through the
   ISRC duplicate check. A verified 320 kbps copy is worse than lossless and better than nothing.
2. **Nothing auto-files without an acoustic match.** The Chromaprint fingerprint against the request's own
   audio is the only evidence that proves identity; text similarity only orders what to try.
3. **"Same track" means the fingerprint matches.** Length differences only order which file is tried first
   (Searching For UFO's at 626 s vs a 392 s video is the same recording and was certified correct).

## The one rule

**The request's own audio is the reference. A Deezer record is the requested track when its 30 s preview
matches that audio. A downloaded file is the requested track when it matches that audio. Text only orders
what to try first.** Everything below follows from applying that rule to both the record and the file.

Prior art (surveyed 2026-09-20): slsk-batchdl filters Soulseek results by format and a 3 s length
tolerance, ranks the rest, never checks audio, and runs an artist-free search for YouTube input because
the artist is often a channel name. beets, Picard and Lidarr fingerprint a file and ask AcoustID *what*
it is, which silently degrades to text matching when the recording is not in the database. No surveyed
tool compares a download against the requester's own audio. Chromaprint's author states a fingerprint
changes with any content change, so the comparison is sound; the field just never wired it to a
user-supplied reference.

## Flow

1. **Identify.** Parse the title into artist, title, version, length. `_split_artist_title` splits on the
   first ` - ` at bracket depth 0 (#66). The other three parser bugs stay cut (0 of 11 rescues).
2. **Reference (`AcousticReference`, fetched once per request, persisted).**
   - YouTube request: yt-dlp fetches the best audio stream; fpcalc produces the **full-length**
     fingerprint (the hay for identifying the record) and four 30 s middle-excerpt fingerprints at the
     sub-frame trims (the needles for checking a download). The audio file is deleted. Verified
     2026-09-20: download 2 s, opus; fpcalc reads it.
   - Any other request, or a YouTube request whose audio could not be fetched: the chosen Deezer record's
     30 s preview, fingerprinted at the four trims; the preview itself is the hay.
   - Persisted in a `request_references` table (not on the request row: a full fingerprint is ~50 KB and
     `list_requests` feeds the UI). Retries, the lossy fallback and the library sweep reuse it.
3. **Beatport.** Searched on the parsed words as today. `best_match` refuses a record whose title agreement
   (`token_set_ratio`) is below `TITLE_FLOOR = 75` unless the source's ISRC identifies it (#63). Beatport
   is **never a gate**: it supplies the first Soulseek spelling and the tags (label, genre, mix name,
   artwork). Measured: of 105 filed requests with a Beatport match, 103 score ≥ 90, none in 70–89, the
   two below 70 are the two wrong files. The artist floor is dropped (the worst correct artist score,
   44.4, sits 1.5 points above the wrong one).
4. **Which record.** Deezer candidates come from the bot search as today, each with a Deezer id.
   - **With a video reference:** `identify_record` fetches the preview of up to 5 candidates in text-score
     order and compares each (as needle) against the video's full fingerprint. The first at or above
     `lossless_fingerprint_min` is the record: chosen automatically, confidence = that score. No match
     among them: **no record** (a text score of 95 does not overrule the audio). No Choose window on this
     path.
   - **Without a video reference** (text, Spotify, or the video audio failed): today's `decide` chooses
     by text, with the Choose window when it is unsure. The "not found on Beatport; information cannot be
     verified" review is removed (#68): a candidate with no Beatport record auto-files when its score
     clears the threshold, and the download check protects it. Spotify's embed carries no ISRC, so
     Spotify requests are text requests with clean words.
   - Once a record is chosen and has an ISRC, `best_match` is re-run with that ISRC preferred (today's
     code). The duplicate check runs as today.
   - **No record and the request parsed to an artist and a title:** a `query_candidate` carries the
     parsed words and the video length to Soulseek (#69); tags come from `_fallback_catalog` of it. No
     lossy fallback on that path. Bare-artist requests end `not_found` with a message saying so.
5. **Lossless on Soulseek** (#69). Spellings are tried in order, the next only after `no_pick` or
   `fingerprint_failed`, and only when its tokens differ: Beatport's, the record's, the parsed words.
   The hard rules that remain reject only what can never satisfy the goal: lossy extension, missing length,
   implausible size, queue cap, banned user. Everything about identity **ranks** survivors instead:
   version agreement, length distance to the nearest known length in 5 s buckets, title score, artist in
   path, then today's free slot / bit depth / queue / speed / size. Worst case stays
   `lossless_max_picks = 4` transfers per search; the per-pick budget is unchanged. The settings
   `lossless_duration_tolerance_s`, `lossless_title_ratio`, `lossless_require_artist` and their Settings
   page copy are removed.
6. **The fingerprint decides** (#68). `matched` files. `failed` moves to the next survivor. `skipped`
   (fpcalc missing or failing, or no reference of any kind) is a new attempt outcome
   `fingerprint_unavailable` that ends the attempt (the reference is missing for every pick alike) and,
   on the lossy path, ends the request in `error` with the reason. `NO_FINGERPRINT_FLAG` goes away: no
   path files without the check.
7. **Lossy fallback.** Only for a chosen Deezer record. The 320 kbps download is fingerprinted too; a
   mismatch is a rejection. A query-candidate request with no lossless match ends `error` ("nothing on
   Soulseek matched", retryable).
8. **Tags.** A chosen record tags the file (Beatport's record when it agrees, else the Deezer record's
   data) whichever spelling found the file. With no record, tags come from the words that found the file:
   Beatport's when its spelling found it, the parsed words otherwise.
9. **Retries** (decided 2026-09-21). The quick ladder stays: three attempts, 30 s then 120 s, 15 min after a
   Soulseek queue or no-pick. After it, a miss whose answer changes as the people online turn over
   (`no_pick`, `fingerprint_failed`, peer refused / never started / stopped part-way, `queued`,
   `unavailable`, `interrupted`) waits 6 h in `queued` ("waiting for Soulseek: …") and looks again, 12
   times over 3 days, then parks in `error` with the count in the reason. `fingerprint_failed` skips the
   quick ladder: a verdict the next minute would only repeat. Permanent misses (no reference to check
   against, nothing to search for) park at once. A yt-dlp failure fetching the video's audio retries on the
   quick ladder before the request falls to the text path, because that audio is what identifies the
   record. Try now and Try again keep working as today. Why: Soulseek is a population, not a library; the
   search for "Space Dwarfs" found nothing at 11:40 on 2026-09-10 and two copies at 14:51.

What this removes from the first version of this design: the junk-candidate gate before `decide` (#64)
and the unknown-length rule (#65) — a wrong record's preview simply does not match; `decide` itself for
requests with audio; the "not on Beatport" review; filing on a flag without a check. What it keeps as
text thresholds: `TITLE_FLOOR` in `best_match` only, because a wrong Beatport record would still name
the file (`Nothing but a Title` → `Between The Lines` scores title 44, average 72, and would be accepted
for tags without it).

## Measurement gates (run before the behaviour flips)

- **Library sweep** (`flackey sweep`): for every filed track with a YouTube request, (a) the file against
  the video's excerpt needles — is the file the video's recording? — and (b) the filed Deezer candidate's
  preview against the video's full fingerprint — would audio identification have picked the same record?
  Report only; the owner decides on mismatches. The same run sets the threshold: `lossless_fingerprint_min`
  becomes the midpoint of the gap between the highest wrong score and the lowest correct score, never
  above the current 0.90. If the gap is under 0.10 the executor stops and asks. If (b) disagrees with the
  filed record on any track the owner certified correct, the executor stops and asks before Phase 5.
- **No-pick replay** (`flackey replay-picks`): the 216 stored `no_pick` reports (9,046 files) re-run
  through the proposed hard rules and rankers, without the network. If the top pick is routinely a file
  the fingerprint cannot match (more than 60 s off with a nearer file in the list), the ranker order is
  tuned before Phase 6 ships.

## Housekeeping (#70, done first)

Delete two files whose audio is a different recording (tracks 40 and 45), retag and rename one whose
audio is right and whose tags are wrong (track 101, `Between The Lines` → `Nothing but a Title`), fix its
row, ISRC and playlist, and mark requests 71 and 72 `not_found` so the re-queue at the end fetches the
right tracks. The executor confirms with the owner before each delete or rename.

## Out of scope

#53 preview clips. The artist floor. Full-EP uploads. Any new review kind in the UI. Audio identification
for Spotify requests (no audio and no ISRC from the embed). Identifying a record when the Deezer bot
offers none (a different source of candidates).

## Order

0. Rewrite the GitHub sub-issues so each PR references an issue that says what the PR does; commit the
   working notes, this record and the plan.
1. #70 housekeeping (agent, with confirmation per destructive step).
2. #66 bracket split; #63 title floor. One PR each.
3. #67 reference module, persisted, annotate-only: the download check runs against the video, outcomes
   unchanged.
4. Sweep + replay; threshold, identification agreement and ranker decisions recorded in `docs/research/`.
5. The fingerprint decides: audio identification of the record, `fingerprint_unavailable`, the "not on
   Beatport" review removed, the lossy fallback checked. Strictly after 4.
6. Open the gate (query candidate) + rules → rankers + the spelling sequence. Strictly after 5.
7. Retries: the long wait for Soulseek and the video-audio retry. After 6.
8. Re-queue the stuck requests (34 distinct: 20 not-found, 10 cancelled, 4 errored, plus #70's two) as
   the acceptance test; report what filed, what the fingerprint rejected, what still missed.

Branching (decided 2026-09-21): every task PR targets the epic branch `epic/61-search-correctness` and is
squash-merged there, one commit per task, so any one task can be reverted alone. The epic is merged into
`main` with a merge commit once the owner has A/B tested the epic build against `main`.

## Access the executor has

A copy of the live database (`~/Library/Application Support/flackey/flackey.sqlite`); the library folder
read-only except for #70; live Soulseek searches through the running slskd (no downloads during
measurement); YouTube audio downloads; Deezer's public API for previews. Nothing else.

## Acceptance

- The two wrong files are gone, the mistagged one is right, and the three requests are correct.
- No path files a track without a `matched` fingerprint or an explicit owner choice.
- A YouTube request whose top text candidate is a different recording does not file it (the
  `Nothing but a Title` case reproduced in a test).
- Of the 34 re-queued requests, the ones the probe found within 10 s file automatically; each remaining
  miss has an attempt outcome and a reason the owner can read.
- `test_miss_reasons_match_the_ui` and every existing test pass; `ruff`, `lint-imports`, vitest, build
  all green.
