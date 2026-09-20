# Search Correctness (issue #61) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a track is asked for, the file that lands is that track, and a track that exists on Soulseek gets fetched: the request's own audio decides which Deezer record and which downloaded file are the recording, and text only orders what to try.

**Architecture:** Two small precision fixes (`identify` bracket split, `best_match` title floor for tags) ship first. A new `reference` module turns the request's YouTube audio into a persisted Chromaprint reference (full fingerprint plus 30 s needles); `fingerprint.check` compares downloads against it and `reference.identify_record` compares Deezer previews against it. The worker then makes both load-bearing: audio picks the record for YouTube requests, `decide` stays for text and Spotify, a new `fingerprint_unavailable` outcome ends attempts that cannot be checked, the lossy fallback is checked too. Then the catalogue gate opens: a `query_candidate` carries the parsed words to Soulseek, identity pick rules become rankers, and spellings are tried in sequence. Last, a transient Soulseek miss waits and looks again for three days instead of parking, and a yt-dlp failure retries before the request falls to the text path.

**Tech Stack:** Python 3.12, rapidfuzz, numpy, yt-dlp (library), Chromaprint `fpcalc`, ffmpeg/ffprobe, SQLite via `store.py`, httpx (Deezer public API), pytest (`asyncio_mode=auto`), ruff, import-linter, `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-09-20-issue-61-search-correctness-design.md` — read it first; every decision below argues from it. Evidence: `docs/search-quality-review-61.md`, `docs/audit-issue-61.md`.

## Global Constraints

- Run from the repo root with `uv run …`. CI runs exactly: `uv run ruff check src tests`, `uv run lint-imports`, `uv run pytest -q`, `npm --prefix web test -- --run`, `npm --prefix web run build`. All five must be green before every PR.
- Line length 100 (ruff). `pyproject.toml` import-linter layers are `exhaustive = true`: every new module must be added to a layer line or `lint-imports` fails. Modules on one line separated by ` : ` may not import each other.
- **Branching: everything lands on the epic branch `epic/61-search-correctness`**, cut from `main` in Task 0. One PR per task unless the task says otherwise, branched from the epic branch, opened with `gh pr create --base epic/61-search-correctness`, and **labelled before opening** (`bug` for precision fixes, `enhancement` for recall, `chore` for tools and measurement, `documentation` or `ignore-for-release` for docs-only). Each task PR is **squash-merged** into the epic (one commit per task, so a single task can later be reverted alone with `git revert <sha>`). The epic is merged into `main` at the very end with a **merge commit** (`gh pr merge --merge`, never squash), so those per-task commits survive on `main`. The owner A/B tests the epic build against `main` before that merge. The epic PR carries `enhancement` and lists every task PR in its body, because `release.yml` builds notes from PRs merged to `main` and groups them by label.
- Commit messages end with the attribution lines the session provides. PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- The live database is `~/Library/Application Support/flackey/flackey.sqlite`. Measurement reads a **copy** except the sweep, which writes only reference rows; only Task 1 and Task 16 change requests or tracks in the live one, and only through `flackey.store.Store`.
- Never download from Soulseek during measurement. YouTube audio downloads and Deezer preview downloads are allowed.
- Confirm with the owner before every delete or rename of a library file (Task 1), before flipping behaviour if the sweep's identification check disagrees with a certified track (Task 7), and before starting the re-queue (Task 16).
- Do not build: an artist floor, a junk-candidate gate before `decide`, an unknown-length rule, a per-reference duration tolerance, a new review kind, EP handling, audio identification for Spotify, #53.

Facts checked against the code on 2026-09-20 that the executor should not re-derive: `lenv` in `tests/test_worker_lossless.py` yields the 6-tuple `(settings, store, notifier, provider, fake_check, clock)`; `env` in `tests/test_worker.py` yields `(settings, store, notifier)`; `FakeSource(cands=None, error=None, kbps=320, fetch_error=None)`, `FakeCatalog(tracks=None, fail=False)`, `CT`, `TEXT`, `good_cand`, `no_art`, `_mp3` live in `tests/test_worker.py` and are imported by `tests/test_worker_lossless.py`; `AttemptRecorder(store, raw_dir, request_id, provider, query, clock=time.monotonic)` lives in `attempts.py`; `Settings.db_path` and `tmp_dir` are properties of `data_dir`, and the settings have no env prefix (`DATA_DIR=…` overrides); `Track` has no `*_norm` fields (they are columns); `CATALOG_SOURCE` lives in `worker.py`; `PickReport.to_dict()` rejections are `{"file", "rule", "reason"}`; the Deezer bot's candidates carry `deezer_id` at search time (`source/deezer_bot.py:53`); `DeezerApi.track(id)` returns `DeezerTrack` with `preview_url`; `fingerprint.compare(needle, hay)` returns `(score, offset)` and `0.0, -1` when the needle is longer than the hay.

---

## Phase 0 — Issues and docs

### Task 0: Rewrite the sub-issues to match the design, commit the working notes

**Files:**
- Commit: `docs/audit-issue-61.md`, `docs/search-quality-review-61.md` (untracked today), the spec, this plan.
- GitHub: issues #63, #64, #65, #68, #69 edited or closed; three new issues; #61 gets a comment.

- [ ] **Step 1: Cut the epic branch, commit the docs on a branch off it, open the docs PR against the epic**

```bash
git checkout -b epic/61-search-correctness main && git push -u origin epic/61-search-correctness
git checkout -b docs/issue-61-design epic/61-search-correctness
git add docs/audit-issue-61.md docs/search-quality-review-61.md
# docs/superpowers/ is in .gitignore (line 35); the owner wants this design record in the repo, so force-add these three
git add -f docs/superpowers/specs/2026-09-20-issue-61-search-correctness-design.md docs/superpowers/plans/2026-09-20-issue-61-search-correctness.md docs/superpowers/plans/2026-09-20-issue-61-orchestrator-prompt.md
git commit -m "docs: design record and plan for issue #61 search correctness"
gh pr create --base epic/61-search-correctness --label ignore-for-release --title "docs: design record and plan for #61" --body "Design decisions and the execution plan for #61. See docs/superpowers/specs/2026-09-20-issue-61-search-correctness-design.md."
```

- [ ] **Step 2: Rewrite #63 to a tags-only title floor**

```bash
gh issue edit 63 --repo EyalDelarea/flackey --title "best_match: floor title agreement (tags only; artist floor dropped)" --body-file - <<'EOF'
Part of #61. Rewritten 2026-09-20 after the audit (docs/audit-issue-61.md §1) and the simplified design.

`catalog.best_match` accepts on `0.5*artist + 0.5*title >= 70`, so artist 100 carries title 44 over the bar. Two wrong files were filed that way (`Nothing but a Title` → `Between The Lines`, `Power Of Celtic` → `The Power Of The Dark Side`).

In the new design Beatport is never a gate: the request's audio picks the record (#68) and the file. But Beatport still names and tags the file, so a wrong Beatport record would still mistag a right file.

**Change:** refuse any record whose title agreement (`token_set_ratio`) is below `TITLE_FLOOR = 75`, unless the source's ISRC identifies it (`preferred_isrc`). Measured on 105 filed requests with a Beatport match: 103 score ≥ 90, none in 70–89, the only 2 below 70 are the two wrong files. Blast radius zero.

**Not built: the artist floor.** The worst *correct* artist score is 44.4 (`ShpongleMusic` → `Gunslinger, Shpongle, GMS`, fingerprint 0.966) and the wrong one is 42.9. No threshold separates them.
EOF
```

- [ ] **Step 3: Close #64 and #65 as superseded**

```bash
gh issue close 64 --repo EyalDelarea/flackey --comment "Superseded by the simplified #61 design (2026-09-20): for a YouTube request the record is chosen by comparing each Deezer candidate's preview against the video's audio (#68), so a junk candidate never reaches the file. Text and Spotify requests keep today's Choose window. See docs/superpowers/specs/2026-09-20-issue-61-search-correctness-design.md."
gh issue close 65 --repo EyalDelarea/flackey --comment "Superseded by the simplified #61 design (2026-09-20): length no longer decides anything. The record is chosen by audio (#68) and the file is checked by audio; length only orders which Soulseek file is tried first (#69)."
```

- [ ] **Step 4: Rewrite #68 as the fingerprint deciding both the record and the file**

```bash
gh issue edit 68 --repo EyalDelarea/flackey --title "The fingerprint decides: which Deezer record, and which file" --body-file - <<'EOF'
Part of #61. Depends on #67. Rewritten 2026-09-20.

The fingerprint is the only stage that compares audio to audio and today the only one that cannot end a request. `skipped` passes with a flag; 45 of 90 filed Soulseek tracks took that path.

**Changes**
1. **Which record.** For a YouTube request, `identify_record` fetches the 30 s preview of up to 5 Deezer candidates (text-score order) and compares each against the video's full fingerprint. The first at or above `lossless_fingerprint_min` is the record, chosen automatically. None: no record, whatever the text score said. No Choose window on that path. Text and Spotify requests (no audio) keep `decide`.
2. `skipped` becomes the attempt outcome `fingerprint_unavailable`, ends the lossless attempt (the reference is missing for every pick alike) and, on the lossy path, ends the request in `error` with the reason. `NO_FINGERPRINT_FLAG` is removed: no path files without the check. No new review kind.
3. `decide` auto-accepts a candidate with no Beatport record when its score clears the threshold; the post-download fingerprint protects it. The "not on Beatport; information cannot be verified" review (26 of 27 cancelled rows) is removed.
4. The lossy Deezer fallback is fingerprinted too; a mismatch is a rejection.

**Threshold:** set from the library sweep — the midpoint of the gap between the highest wrong score and the lowest correct score, never above 0.90. If the gap is under 0.10, stop and ask. The same sweep checks that audio identification agrees with every certified-correct filed record.

Must ship before #69.
EOF
```

- [ ] **Step 5: Rewrite #69 as gate + rankers + spelling sequence**

```bash
gh issue edit 69 --repo EyalDelarea/flackey --title "Open the catalogue gate: search on the request's own words, rank instead of reject" --body-file - <<'EOF'
Part of #61. Depends on #68. Rewritten 2026-09-20 (audit §3 and the "alternative to #69" comment on #61).

20 distinct requests are `not_found` and 10 were cancelled because neither Beatport nor Deezer had a record. A live Soulseek probe found lossless copies of all 16 it could probe, 14 within 10 s.

**Changes (three PRs)**
1. **Open the gate.** When no record was chosen and the request parsed to artist + title, a `query_candidate` carries the parsed words and the video length to Soulseek; tags from `_fallback_catalog`. No lossy fallback on that path. Bare-artist requests end `not_found` with a message saying so.
2. **Rules → rankers.** `duration`, `title`, `version`, `artist` leave the hard rules and order survivors (version agreement, length distance in 5 s buckets, title score, artist in path, then slot / bit depth / queue / speed / size). Hard rules that stay: lossy extension, missing length, implausible size, queue cap, banned user. `lossless_duration_tolerance_s`, `lossless_title_ratio`, `lossless_require_artist` are removed. Gated by the no-pick replay.
3. **Spelling sequence.** Beatport's spelling, then the chosen record's, then the parsed words; the next only after `no_pick` or `fingerprint_failed`, and only when its tokens differ. With no chosen record, tags follow the search that produced the verified file.

**Not built:** the per-reference duration tolerance (moot once duration ranks).
EOF
```

- [ ] **Step 6: Open the measurement, retry and acceptance issues and comment on #61**

```bash
gh issue create --repo EyalDelarea/flackey --label chore --title "Measure: fingerprint sweep of the library and replay of the no-pick reports" --body-file - <<'EOF'
Part of #61. Runs after #67 (annotate-only) and before #68 / #69 ship.

- `flackey sweep`: every filed track with a YouTube request gets two scores: the file against the video (is the file the video's recording?) and the filed Deezer candidate's preview against the video (would audio identification have picked the same record?). Report only (`docs/research/`); the owner decides on mismatches. Sets `lossless_fingerprint_min` per the rule in #68.
- `flackey replay-picks`: the 216 stored `no_pick` reports (9,046 files) re-run through the proposed hard rules and rankers, no network. Reports what ranks #1 and how far from the requested length. Tunes the ranker order before #69 ships.
EOF
gh issue create --repo EyalDelarea/flackey --label enhancement --title "Retry on our own: wait for Soulseek, retry the video audio" --body-file - <<'EOF'
Part of #61. After #69.

Soulseek is a population, not a library: the search for "Space Dwarfs" found nothing at 11:40 on 2026-09-10 and two copies at 14:51. Today the whole retry budget (3 attempts, 30 s / 120 s, 15 min after a queue or no-pick) is spent within about half an hour, and after that a track only gets another look when the owner presses Try again.

**Changes**
1. After the quick ladder, a miss whose answer changes as the people online turn over (`no_pick`, `fingerprint_failed`, `transfer_failed`, `first_byte_timeout`, `transfer_timeout`, `queued`, `unavailable`, `interrupted`) waits 6 h in `queued` ("waiting for Soulseek: …") and looks again, 12 times over 3 days, then parks in `error` with the count in the reason. `fingerprint_failed` skips the quick ladder: a verdict the next minute would only repeat. Permanent misses (no reference to check against, nothing to search for) park at once, as today.
2. A yt-dlp failure fetching the video's audio retries on the quick ladder before the request falls to the text path, because that audio is what identifies the record (#68).
3. The row countdown learns hours.

Try now and Try again keep working as today.
EOF
gh issue create --repo EyalDelarea/flackey --label chore --title "Re-queue the stuck requests once the recall work has landed" --body-file - <<'EOF'
Part of #61. Last step. Re-queue the 34 distinct stuck requests (20 not-found, 10 cancelled, 4 errored) plus #70's two, with the owner told first, and report what filed, what the fingerprint rejected, what still missed.
EOF
gh issue comment 61 --repo EyalDelarea/flackey --body "Plan finalised 2026-09-20 and simplified the same day: docs/superpowers/specs/2026-09-20-issue-61-search-correctness-design.md and the plan next to it. One rule — the request's own audio decides which Deezer record and which file. #63 is a tags-only title floor, #64 and #65 are superseded, #68 absorbs audio identification and the cannot-check outcome, #69 becomes gate + rankers + spelling sequence. Three new issues carry the measurement, the retries and the re-queue."
```

---

## Phase 1 — Housekeeping (#70)

### Task 1: Correct the three wrong files in the library

**Files:**
- Live library under `~/Music/DJ Library/`, live database via `Store`. No code changes.

**Interfaces:**
- Consumes: `flackey.worker._fallback_catalog(cand)`, `flackey.library.final_path(root, catalog, ext)`, `flackey.library.file_track(tmp_path, dest)`, `flackey.tag.write_tags(path, catalog, verdict, artwork, artwork_mime="image/jpeg", source="deezer_bot")`, `flackey.tag.read_tags(path)`, `flackey.verify.probe(path)`, `flackey.export.write_playlist(store, playlist_id, library_root)`, `flackey.models.norm`.

Facts from the live database (2026-09-20): track 101 is `New Born/New Born - Between The Lines.mp3` (request 205, wrong catalogue 29485047; the correct Deezer candidate is id **182**, `Nothing but a Title`, 352 s, ISRC `QZFZ71963325`, playlist 13). Track 45 is `Shiva Shidapu - The Power Of The Dark Side.aiff` (request 71, playlist 6). Track 40 is `Infected Mushroom - Release Me REBORN.aiff` (request 72, playlist 6).

- [ ] **Step 1: Quit the app** so the worker is not running against the database. Confirm with `pgrep -fl flackey` returning nothing.

- [ ] **Step 2: Retag and rename track 101 (ask the owner first: "Rename `New Born - Between The Lines.mp3` to `New Born - Nothing but a Title.mp3` and retag it?")**

```bash
uv run python - <<'EOF'
from flackey.config import load_settings
from flackey.export import write_playlist
from flackey.library import file_track, final_path
from flackey.models import Verdict, norm
from flackey.store import Store
from flackey.tag import write_tags
from flackey.verify import probe
from flackey.worker import _fallback_catalog

s = load_settings()
store = Store(s.db_path)
t = store.get_track(101)
cand = store.get_candidate(182)
assert t.path.name == "New Born - Between The Lines.mp3", t.path
assert (cand.title, cand.isrc) == ("Nothing but a Title", "QZFZ71963325"), cand
catalog = _fallback_catalog(cand)          # negative id; artist/title/mix/isrc from the Deezer candidate
store.upsert_catalog_track(catalog)
pr = probe(t.path)
verdict = Verdict(passed=True, fmt=pr.fmt, bitrate_kbps=pr.bitrate_kbps, cutoff_hz=t.cutoff_hz, reason="retagged (#70)")
write_tags(t.path, catalog, verdict, None, source=t.source)
dest = final_path(s.library_root, catalog, "mp3")
file_track(t.path, dest)
store.update_track(101, path=dest, artist=catalog.artist, title=catalog.title, mix_name=catalog.mix_name,
                   duration_s=cand.duration_s, isrc=cand.isrc, catalog_track_id=catalog.id,
                   # the *_norm columns feed find_track_by_meta; they are DB columns, not Track fields, and
                   # update_track does not recompute them
                   artist_norm=norm(catalog.artist), title_norm=norm(catalog.title), mix_norm=norm(catalog.mix_name))
store.update_request(205, catalog_track_id=catalog.id)
print("playlist:", write_playlist(store, 13, s.library_root))
print("now:", store.get_track(101).path, store.get_track(101).isrc)
EOF
```

Expected: the file is at `~/Music/DJ Library/New Born/New Born - Nothing but a Title.mp3`, `read_tags` shows `title=Nothing but a Title`, `isrc=QZFZ71963325`, and the playlist M3U8 for playlist 13 lists the new path.

- [ ] **Step 3: Verify the retag**

```bash
uv run python -c "from pathlib import Path; from flackey.tag import read_tags; print(read_tags(Path('~/Music/DJ Library/New Born/New Born - Nothing but a Title.mp3').expanduser()))"
```

- [ ] **Step 4: Delete tracks 45 and 40 (ask the owner first, one question per file, naming the path)**

```bash
uv run python - <<'EOF'
from pathlib import Path
from flackey.config import load_settings
from flackey.export import write_playlist
from flackey.models import RequestState
from flackey.store import Store

s = load_settings()
store = Store(s.db_path)
for track_id, request_id in ((45, 71), (40, 72)):
    t = store.get_track(track_id)
    assert t.request_id == request_id, (t.id, t.request_id)
    print("removing", t.path)
    store.clear_evidence(track_id)
    if t.spectrogram_path:
        Path(t.spectrogram_path).unlink(missing_ok=True)
    t.path.unlink()
    store.delete_track(track_id)                 # drops the row and its playlist memberships
    store.update_request(request_id, state=RequestState.NOT_FOUND, track_id=None,
                         error_message="the file filed for this request was a different recording; removed (#70)")
print("playlist:", write_playlist(store, 6, s.library_root))
EOF
```

Expected: both `.aiff` files gone, `flackey status` shows two fewer tracks, requests 71 and 72 in `not_found` (they are re-queued in Task 16, after the identification work, so the same wrong Beatport match cannot repeat).

- [ ] **Step 5: Comment on #70 with what was done** (paths, the three request states) and close it.

---

## Phase 2 — Precision fixes

### Task 2: #66 — bracket-aware artist/title split

**Files:**
- Modify: `src/flackey/identify.py:90-94` (`_split_artist_title`)
- Test: `tests/test_identify.py` (imports `classify, parse_text, parse_version, parse_youtube_title` already)

- [ ] **Step 1: Failing tests**

```python
def test_split_ignores_a_dash_inside_brackets():
    q = parse_youtube_title("Granada (Remix - 98)", uploader="Granada - Topic")
    assert (q.artist, q.title, q.version) == ("Granada", "Granada", "Remix - 98")
    q = parse_text("Artist - Title (Some - Thing)")
    assert (q.artist, q.title) == ("Artist", "Title (Some - Thing)")
    q = parse_text("Artist [Live - 1997] - Title")
    assert (q.artist, q.title) == ("Artist [Live - 1997]", "Title")
```

- [ ] **Step 2: Run** `uv run pytest tests/test_identify.py -q -k inside_brackets` — FAIL (`artist == "Granada (Remix"`).

- [ ] **Step 3: Implement**

```python
_SPLIT_TOKENS = re.compile(r"[(\[]|[)\]]|\s+[-–—]\s+")


def _split_artist_title(s: str) -> tuple[str | None, str]:
    """Artist and title around the first ` - ` that is not inside brackets. `Granada (Remix - 98)` is one
    title with a version in it, not artist `Granada (Remix` and title `98)` (issue #66)."""
    depth = 0
    for m in _SPLIT_TOKENS.finditer(s):
        tok = m.group(0)
        if tok in ("(", "["):
            depth += 1
        elif tok in (")", "]"):
            depth = max(depth - 1, 0)
        elif depth == 0:
            return s[: m.start()].strip(), s[m.end():].strip()
    return None, s.strip()
```

- [ ] **Step 4: Run** `uv run pytest tests/test_identify.py tests/test_inbox.py -q` — PASS.
- [ ] **Step 5: Commit, PR** (`fix/66-bracket-split`, label `bug`, "Closes #66").

### Task 3: #63 — title floor in `best_match` (tags only)

**Files:**
- Modify: `src/flackey/models.py` (add `TITLE_FLOOR`), `src/flackey/catalog.py:107-146` (`best_match`)
- Test: `tests/test_catalog.py` (imports `best_match`, `CatalogTrack`, `Query` already)

**Interfaces:**
- Produces: `models.TITLE_FLOOR: int = 75`. `best_match(query, tracks, preferred_isrc=None)` unchanged signature.

- [ ] **Step 1: Write the failing tests**

```python
def _ct(**kw) -> CatalogTrack:
    base = {"id": 1, "artist": "New Born", "title": "Between The Lines", "mix_name": "Original Mix",
            "label": "L", "genre": "G", "duration_ms": 355000}
    return CatalogTrack(**{**base, **kw})


def test_best_match_refuses_a_record_whose_title_disagrees():
    # req#205: artist 100, title 44, average 72.2 -- accepted today, and the file was mistagged for it
    q = Query(raw="", artist="New Born", title="Nothing but a Title", duration_s=353)
    assert best_match(q, [_ct()]) is None


def test_best_match_keeps_a_superset_title():
    # token_set_ratio scores a polluted-but-correct title at 100; the floor must not touch it
    q = Query(raw="", artist="Sheyba", title="Ganesh - Flying Rhino Records - 1995", duration_s=431)
    t = _ct(artist="Sheyba", title="Ganesh", duration_ms=431000)
    assert best_match(q, [t]) is t


def test_source_isrc_bypasses_the_title_floor():
    q = Query(raw="", artist="New Born", title="Nothing but a Title")
    t = _ct(isrc="QT6EC2645809")
    assert best_match(q, [t]) is None
    assert best_match(q, [t], preferred_isrc="qt6ec2645809") is t
```

- [ ] **Step 2: Run** `uv run pytest tests/test_catalog.py -q -k "title_disagrees or superset_title or bypasses_the_title_floor"` — the first and third FAIL, the second passes already.

- [ ] **Step 3: Implement**

In `src/flackey/models.py`, after `norm`:

```python
# The least a Beatport record's title may agree with the request (token_set_ratio) and still name the file.
# Measured on 105 filed requests with a Beatport match: 103 score >= 90, none between 70 and 89, and the
# only two below 70 are the two files that were wrong (issue #63). Tags only: the record and the file are
# chosen by audio, not by this number.
TITLE_FLOOR = 75
```

In `src/flackey/catalog.py`: import `TITLE_FLOOR` from `.models`; add above `best_match`:

```python
def _same_isrc(t: CatalogTrack, isrc: str | None) -> bool:
    return bool(isrc and t.isrc and t.isrc.upper() == isrc.upper())
```

and inside the `if query.artist and query.title:` branch of `best_match`, right after `ti = ...`:

```python
            if ti < TITLE_FLOOR and not _same_isrc(t, preferred_isrc):
                # A weighted average lets artist 100 carry title 44 over the bar. The source's ISRC is the one
                # thing that outranks the title: it names the recording itself.
                continue
```

Update the `scored.sort` key (line 138) to use `_same_isrc(x[1], preferred_isrc)` in place of the inline comparison.

- [ ] **Step 4: Run** `uv run pytest tests/test_catalog.py tests/test_worker.py -q` — PASS.
- [ ] **Step 5: Commit, PR** (`fix/63-title-floor`, label `bug`, "Closes #63").

---

## Phase 3 — The request's own audio as the reference (#67, annotate-only)

### Task 4: `AcousticReference`, `youtube.fetch_audio`, `reference` module, `check` on needles, persisted per request

**Files:**
- Modify: `src/flackey/fingerprint.py` (add `AcousticReference`, `length_s` to `fingerprint`, new `check`; remove `_preview_url`, `DEEZER_TRACK`, `DEEZER_TRIES`), `src/flackey/youtube.py` (add `video_id`, `fetch_audio`), `src/flackey/store.py` (`request_references` table, `get_reference`, `set_reference`), `pyproject.toml` (layer line), `src/flackey/worker.py` (`Acoustic`, `_video_reference`, `_acoustic_reference`, threading, evidence label)
- Create: `src/flackey/reference.py`
- Test: `tests/test_fingerprint.py` (rewrite the `check` tests), `tests/test_reference.py` (new), `tests/test_youtube.py`, `tests/test_store.py`, `tests/test_worker_lossless.py` (the `fake_check` signature in `lenv`), `tests/test_worker.py` (`env`)

**Interfaces:**
- Produces:
  - `fingerprint.AcousticReference(kind: str, ref: str, needles: list[list[int]], full: list[int], excerpt_start_s: float, excerpt_s: float)` with `.label -> str` (`"youtube:<id>"` / `"deezer:<id>"`), `.to_dict()`, `AcousticReference.from_dict(d)`. `needles` are the reference's 30 s excerpt at each sub-frame trim (the needle when a download is the hay); `full` is the whole reference audio (the hay when a candidate's preview is the needle, Task 10).
  - `fingerprint.fingerprint(path, start_s=0.0, length_s: float | None = None) -> list[int]`.
  - `fingerprint.check(path, reference: AcousticReference | None, *, minimum: float, missing: str = "") -> FingerprintResult`; `FingerprintResult.reference: str | None` (7th field, in `to_dict`).
  - `youtube.video_id(url) -> str`; `youtube.fetch_audio(url, dest_dir: Path) -> Path` (async, raises `YouTubeError`).
  - `reference.EXCERPT_S = 30.0`; `reference.youtube_reference(url, tmp_dir, *, duration_s=None) -> AcousticReference` (async; raises `YouTubeError` / `FingerprintError`); `reference.deezer_needles(deezer_id, http, tmp_dir) -> list[list[int]]` (async; raises `FingerprintError`); `reference.deezer_reference(deezer_id, http, tmp_dir) -> AcousticReference` (async; raises `FingerprintError`).
  - `store.get_reference(request_id) -> dict | None`; `store.set_reference(request_id, reference: dict | None) -> None` (None deletes).
  - `worker.Acoustic(reference: AcousticReference | None, missing: str = "")`; `Worker._video_reference(req) -> tuple[AcousticReference | None, str]`; `Worker._acoustic_reference(req, cand) -> Acoustic`.

- [ ] **Step 1: Failing tests for the pure parts**

`tests/test_fingerprint.py` — replace `test_check_matches_and_fails_by_threshold`, `test_check_is_skipped_when_deezer_or_fpcalc_is_unavailable`, `test_check_is_skipped_when_fpcalc_fails` with:

```python
def _ref(pairs: dict, tid: str) -> AcousticReference:
    return AcousticReference("deezer", str(pairs[tid]["deezer_id"]), [pairs[tid]["preview"]], pairs[tid]["preview"],
                             0.0, 30.0)


async def test_check_matches_and_fails_by_threshold(tmp_path: Path, fake_fpcalc, pairs: dict):
    ok = await check(tmp_path / "6-track.flac", _ref(pairs, "6"), minimum=0.90)
    assert ok.status == "matched" and ok.score >= 0.93 and ok.offset_s == pytest.approx(240 / FPS, abs=0.2)
    assert ok.reference == "deezer:6025986" and ok.to_dict()["reference"] == ok.reference
    assert ok.preview and ok.track and "preview" not in ok.to_dict()
    bad = await check(tmp_path / "8-track.flac", _ref(pairs, "6"), minimum=0.90)
    assert bad.status == "failed" and bad.score < 0.80 and "below 0.90" in bad.reason


async def test_check_is_skipped_without_a_reference_or_fpcalc(tmp_path: Path, fake_fpcalc, pairs: dict, monkeypatch):
    r = await check(tmp_path / "6-track.flac", None, minimum=0.9, missing="video: yt-dlp timed out")
    assert r.status == "skipped" and r.reason == "video: yt-dlp timed out" and r.reference is None
    monkeypatch.setattr(fp, "fpcalc_available", lambda: False)
    r = await check(tmp_path / "6-track.flac", _ref(pairs, "6"), minimum=0.9)
    assert r.status == "skipped" and "fpcalc" in r.reason


async def test_check_is_skipped_when_fpcalc_fails(tmp_path: Path, fake_fpcalc, pairs: dict, monkeypatch):
    def boom(path, start_s=0.0, length_s=None):
        raise FingerprintError("fpcalc timed out")
    monkeypatch.setattr(fp, "fingerprint", boom)
    r = await check(tmp_path / "6-track.flac", _ref(pairs, "6"), minimum=0.9)
    assert r.status == "skipped" and r.reason == "fpcalc timed out"


def test_acoustic_reference_round_trips():
    ref = AcousticReference("youtube", "SEfza8xb4fU", [[1, 2], [3, 4]], [1, 2, 3, 4, 5], 161.5, 30.0)
    assert AcousticReference.from_dict(ref.to_dict()) == ref and ref.label == "youtube:SEfza8xb4fU"


@requires_ffmpeg
@requires_fpcalc
def test_fingerprint_can_take_a_bounded_excerpt(tmp_path: Path):
    whole = tmp_path / "whole.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:seed=3:duration=40:sample_rate=44100", "-ac", "2", str(whole)], check=True)
    part = fingerprint(whole, 10.0, 10.0)
    assert 70 <= len(part) <= 90                       # 10 s at 8.06 frames/s
    assert compare(part, fingerprint(whole))[0] >= 0.85
```

The `fake_fpcalc` fixture's fake must take the new signature, `def fake(path: Path, start_s: float = 0.0, length_s: float | None = None)`, and its `preview-` branch goes (needles now come from the reference).

`tests/test_youtube.py`:

```python
async def test_fetch_audio_downloads_into_the_folder(tmp_path: Path, monkeypatch):
    class FakeYDL:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=True):
            assert download and self.opts["format"] == "bestaudio/best" and self.opts["noplaylist"]
            out = Path(self.opts["outtmpl"].replace("%(ext)s", "webm"))
            out.write_bytes(b"audio")
            return {"id": "jNQXAC9IVRw", "ext": "webm"}
        def prepare_filename(self, info):
            return self.opts["outtmpl"].replace("%(ext)s", info["ext"])
    monkeypatch.setattr("flackey.youtube.YoutubeDL", FakeYDL)
    got = await fetch_audio("https://www.youtube.com/watch?v=jNQXAC9IVRw", tmp_path / "req1")
    assert got == tmp_path / "req1" / "reference.webm" and got.read_bytes() == b"audio"


def test_video_id():
    assert video_id("https://www.youtube.com/watch?v=jNQXAC9IVRw") == "jNQXAC9IVRw"
    assert video_id("https://youtu.be/jNQXAC9IVRw") == "jNQXAC9IVRw"
```

`tests/test_reference.py` (new):

```python
from pathlib import Path

import httpx
import pytest
import respx

from flackey import fingerprint as fp
from flackey import reference as ref_mod
from flackey.fingerprint import SUBFRAME_TRIMS_S, FingerprintError
from flackey.reference import EXCERPT_S, deezer_needles, deezer_reference, youtube_reference


@pytest.fixture
def fake_fp(monkeypatch):
    calls = []
    def fake(path: Path, start_s=0.0, length_s=None):
        calls.append((path.name, start_s, length_s))
        return [1, 2, 3] if length_s else list(range(300))
    monkeypatch.setattr(fp, "fingerprint", fake)
    monkeypatch.setattr(ref_mod, "fingerprint", fake)
    return calls


async def test_youtube_reference_cuts_the_middle_and_deletes_the_audio(tmp_path: Path, monkeypatch, fake_fp):
    async def fake_fetch(url, dest_dir):
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / "reference.webm"
        p.write_bytes(b"x")
        return p
    monkeypatch.setattr(ref_mod, "fetch_audio", fake_fetch)
    ref = await youtube_reference("https://www.youtube.com/watch?v=SEfza8xb4fU", tmp_path, duration_s=353)
    assert ref.kind == "youtube" and ref.ref == "SEfza8xb4fU" and len(ref.needles) == len(SUBFRAME_TRIMS_S)
    assert ref.full == list(range(300))
    assert ref.excerpt_start_s == pytest.approx((353 - EXCERPT_S) / 2) and ref.excerpt_s == EXCERPT_S
    assert [c[2] for c in fake_fp] == [None] + [EXCERPT_S] * len(SUBFRAME_TRIMS_S)
    assert not (tmp_path / "reference.webm").exists()


@respx.mock
async def test_deezer_needles_download_the_preview_once_and_remove_it(tmp_path: Path, fake_fp):
    respx.get("https://api.deezer.com/track/6025986").mock(return_value=httpx.Response(200, json={
        "id": 6025986, "preview": "https://cdn.test/6-preview.mp3"}))
    respx.get("https://cdn.test/6-preview.mp3").mock(return_value=httpx.Response(200, content=b"mp3"))
    async with httpx.AsyncClient() as http:
        needles = await deezer_needles(6025986, http, tmp_path)
        ref = await deezer_reference(6025986, http, tmp_path)
    assert len(needles) == len(SUBFRAME_TRIMS_S) and needles[0] == [1, 2, 3]
    assert ref.label == "deezer:6025986" and ref.full == ref.needles[0]
    assert not list(tmp_path.glob("preview-*"))


@respx.mock
async def test_deezer_needles_raise_when_there_is_no_preview(tmp_path: Path, fake_fp):
    respx.get("https://api.deezer.com/track/2").mock(return_value=httpx.Response(200, json={"id": 2, "preview": ""}))
    api = respx.get("https://api.deezer.com/track/1").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        with pytest.raises(FingerprintError, match="no preview"):
            await deezer_needles(2, http, tmp_path)
        with pytest.raises(FingerprintError, match="503"):
            await deezer_needles(1, http, tmp_path)
    assert api.call_count == 3
```

`tests/test_store.py`:

```python
def test_request_reference_is_kept_beside_the_request(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    rid = store.add_request("x", RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=a")
    assert store.get_reference(rid) is None
    ref = {"kind": "youtube", "ref": "a", "needles": [[1]], "full": [1, 2], "excerpt_start_s": 0, "excerpt_s": 30}
    store.set_reference(rid, ref)
    assert store.get_reference(rid) == ref
    store.set_reference(rid, {**ref, "ref": "b"})
    assert store.get_reference(rid)["ref"] == "b"
    store.set_reference(rid, None)
    assert store.get_reference(rid) is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_fingerprint.py tests/test_youtube.py tests/test_reference.py tests/test_store.py -q` — FAIL on imports.

- [ ] **Step 3: Implement `fingerprint.py`**

Module docstring: "Same-recording check (spec §16.1): Chromaprint raw fingerprints of a ~30 s reference excerpt (the request's own video, or the Deezer preview) slid along the downloaded file …". Add:

```python
@dataclass
class AcousticReference:
    """The audio the owner pointed at, as fingerprints. `needles` are a short excerpt at each sub-frame trim
    (SUBFRAME_TRIMS_S), because a cut is never frame-aligned: the needle when a download is the hay.
    `full` is the whole reference audio: the hay when a Deezer preview is the needle (identifying the
    record, issue #68). Persisted once per request so every pick, the lossy fallback, a retry and the
    library sweep reuse it."""
    kind: str                      # "youtube" | "deezer"
    ref: str                       # video id, or Deezer track id
    needles: list[list[int]]
    full: list[int]
    excerpt_start_s: float
    excerpt_s: float

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.ref}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "needles": self.needles, "full": self.full,
                "excerpt_start_s": self.excerpt_start_s, "excerpt_s": self.excerpt_s}

    @classmethod
    def from_dict(cls, d: dict) -> AcousticReference:
        return cls(d["kind"], str(d["ref"]), [list(n) for n in d["needles"]], list(d["full"]),
                   float(d["excerpt_start_s"]), float(d["excerpt_s"]))
```

`fingerprint(path, start_s=0.0, length_s=None)`: when `start_s or length_s`, run the ffmpeg trim with `["-ss", f"{start_s}"]` plus `["-t", f"{length_s}"]` when `length_s` is given (both before `-i`).

`FingerprintResult` gains `reference: str | None = None` as the 7th field, and `to_dict` includes `"reference": self.reference`.

`check`:

```python
async def check(path: Path, reference: AcousticReference | None, *, minimum: float,
                missing: str = "") -> FingerprintResult:
    """Never raises. `skipped` when the check cannot run (no reference, no fpcalc, fpcalc failed) --
    `missing` says why there is no reference; `failed` only when it ran and the best score is low."""
    if reference is None:
        return FingerprintResult("skipped", None, None, missing or "no acoustic reference for this request")
    if not fpcalc_available():
        return FingerprintResult("skipped", None, None, "fpcalc not installed", reference=reference.label)
    try:
        track_fp = await asyncio.to_thread(fingerprint, path)
    except (FingerprintError, OSError, ValueError) as e:
        return FingerprintResult("skipped", None, None, str(e) or type(e).__name__, reference=reference.label)
    best, best_needle = (0.0, -1), None
    for needle in reference.needles:
        got = compare(needle, track_fp)
        if got > best:
            best, best_needle = got, needle
    score, offset = best
    offset_s = round(offset / FPS, 1) if offset >= 0 else None
    if score >= minimum:
        return FingerprintResult("matched", round(score, 3), offset_s,
                                 f"{reference.label} found at {offset_s} s, score {score:.2f}",
                                 best_needle, track_fp, reference.label)
    return FingerprintResult("failed", round(score, 3), offset_s, f"best score {score:.2f} below {minimum:.2f}",
                             best_needle, track_fp, reference.label)
```

Delete `_preview_url`, `DEEZER_TRACK`, `DEEZER_TRIES` and the `httpx` / `uuid4` imports (they move to `reference.py`).

- [ ] **Step 4: Implement `youtube.py`**

```python
_AUDIO_OPTS = {"format": "bestaudio/best", "quiet": True, "no_warnings": True, "noplaylist": True}


def video_id(url: str) -> str:
    p = urlparse(url)
    if "youtu.be" in p.netloc:
        return p.path.strip("/")
    return parse_qs(p.query).get("v", [""])[0] or url


def _download_audio(url: str, dest_dir: Path) -> Path:
    """Blocking. The best audio-only stream, whatever container YouTube serves (opus in webm, usually):
    fpcalc decodes it directly, so nothing is transcoded."""
    opts = {**_AUDIO_OPTS, "outtmpl": str(dest_dir / "reference.%(ext)s")}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


async def fetch_audio(url: str, dest_dir: Path) -> Path:
    """The video's audio on disk, for fingerprinting against (issue #67). Same library, same timeout and
    same error shape as `fetch_youtube`; the caller deletes the file when it has what it needs."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        return await asyncio.wait_for(asyncio.to_thread(_download_audio, url, dest_dir),
                                      timeout=YOUTUBE_FETCH_TIMEOUT_S)
    except TimeoutError:
        raise YouTubeError(f"yt-dlp timed out after {YOUTUBE_FETCH_TIMEOUT_S}s fetching the audio") from None
    except Exception as e:
        raise YouTubeError(str(e).strip()[-500:] or "yt-dlp failed") from e
```

(`urlparse`, `parse_qs` from `urllib.parse`; `Path` from `pathlib`.)

- [ ] **Step 5: Create `src/flackey/reference.py`**

```python
"""The audio the owner actually asked for, as fingerprints (issue #67). For a YouTube request that is the
video itself: it needs no Deezer id and no Beatport record, and it exists for every such request. The
Deezer 30 s preview is the reference for requests with no audio of their own (text, Spotify). Above
`youtube`, `verify` and `fingerprint` in the import layers because it needs all three; the worker and
the sweep are its callers."""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx

from .fingerprint import SUBFRAME_TRIMS_S, AcousticReference, FingerprintError, fingerprint
from .verify import VerifyError, probe
from .youtube import fetch_audio, video_id

EXCERPT_S = 30.0          # the Deezer preview's shape; `compare` needs the needle shorter than the hay
DEEZER_TRACK = "https://api.deezer.com/track/{id}"
DEEZER_TRIES = 3


def _needles(path: Path, start_s: float, length_s: float | None) -> list[list[int]]:
    return [fingerprint(path, start_s + trim, length_s) for trim in SUBFRAME_TRIMS_S]


async def youtube_reference(url: str, tmp_dir: Path, *, duration_s: float | None = None) -> AcousticReference:
    """Fetch the video's audio, fingerprint all of it and EXCERPT_S from its middle at each sub-frame trim,
    delete the audio. Raises YouTubeError (fetch) or FingerprintError (ffmpeg, fpcalc, unreadable audio)."""
    audio = await fetch_audio(url, tmp_dir)
    try:
        length = float(duration_s) if duration_s else await asyncio.to_thread(_length_s, audio)
        start = max((length - EXCERPT_S) / 2, 0.0)
        full = await asyncio.to_thread(fingerprint, audio)
        needles = await asyncio.to_thread(_needles, audio, start, EXCERPT_S)
    finally:
        audio.unlink(missing_ok=True)
    return AcousticReference("youtube", video_id(url), needles, full, round(start, 3), EXCERPT_S)


def _length_s(audio: Path) -> float:
    try:
        return probe(audio).duration_s
    except VerifyError as e:
        raise FingerprintError(f"cannot read the video's audio: {e}") from e


async def _preview_url(deezer_id: int, http: httpx.AsyncClient) -> str | None:
    last = "no response"
    for i in range(DEEZER_TRIES):
        try:
            r = await http.get(DEEZER_TRACK.format(id=deezer_id), timeout=20)
        except httpx.HTTPError as e:
            last = type(e).__name__
        else:
            if r.status_code == 200:
                return r.json().get("preview") or None
            last = f"deezer http {r.status_code}"
            if r.status_code < 500:
                break
        if i < DEEZER_TRIES - 1:
            await asyncio.sleep(0)
    raise FingerprintError(last)


async def deezer_needles(deezer_id: int, http: httpx.AsyncClient, tmp_dir: Path) -> list[list[int]]:
    """The Deezer preview at each sub-frame trim. Unique file name per call: two requests for one
    recording can be here at once. Raises FingerprintError when Deezer has no preview or the download
    fails."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    preview = tmp_dir / f"preview-{deezer_id}-{uuid4().hex[:8]}.mp3"
    try:
        url = await _preview_url(deezer_id, http)
        if not url:
            raise FingerprintError("deezer has no preview for this track")
        try:
            r = await http.get(url, timeout=30, follow_redirects=True)
        except httpx.HTTPError as e:
            raise FingerprintError(f"preview download {type(e).__name__}") from e
        if r.status_code != 200 or not r.content:
            raise FingerprintError(f"preview download http {r.status_code}")
        preview.write_bytes(r.content)
        return await asyncio.to_thread(_needles, preview, 0.0, None)
    finally:
        preview.unlink(missing_ok=True)


async def deezer_reference(deezer_id: int, http: httpx.AsyncClient, tmp_dir: Path) -> AcousticReference:
    """The Deezer preview as the reference, for a request that has no audio of its own."""
    needles = await deezer_needles(deezer_id, http, tmp_dir)
    return AcousticReference("deezer", str(deezer_id), needles, needles[0], 0.0, EXCERPT_S)
```

`pyproject.toml` layers: change the line `"attempts",` to `"attempts : reference",`. (`reference` imports only the adapter layer below it. Modules on one ` : ` line may not import each other, which is why the sweep and replay tools, which import `reference`, go on the `worker` line in Task 5.)

- [ ] **Step 6: Store**

`store.py` `SCHEMA` (next to `lossless_attempts`):

```sql
CREATE TABLE IF NOT EXISTS request_references (
  request_id INTEGER PRIMARY KEY REFERENCES requests(id) ON DELETE CASCADE,
  json TEXT NOT NULL
);
```

```python
    def get_reference(self, request_id: int) -> dict | None:
        """The request's acoustic reference (`fingerprint.AcousticReference.to_dict`). Its own table: a
        full fingerprint is ~50 KB and `list_requests` feeds the UI."""
        r = self.conn.execute("SELECT json FROM request_references WHERE request_id=?", (request_id,)).fetchone()
        return json.loads(r["json"]) if r else None

    def set_reference(self, request_id: int, reference: dict | None) -> None:
        with self.conn:
            if reference is None:
                self.conn.execute("DELETE FROM request_references WHERE request_id=?", (request_id,))
            else:
                self.conn.execute("INSERT INTO request_references(request_id, json) VALUES (?, ?) "
                                  "ON CONFLICT(request_id) DO UPDATE SET json=excluded.json",
                                  (request_id, json.dumps(reference)))
```

(Match the connection and transaction style the other writers in `store.py` use; `json` is already imported there.)

- [ ] **Step 7: Worker — obtain the reference once, thread it through, keep outcomes as they are**

Imports: `from .fingerprint import FPS, AcousticReference, FingerprintError, FingerprintResult`, `from .reference import deezer_reference, youtube_reference`, `from .youtube import YouTubeError`.

```python
@dataclass
class Acoustic:
    """What the fingerprint compares a download against, or why there is nothing to compare against."""
    reference: AcousticReference | None
    missing: str = ""
```

```python
    async def _video_reference(self, req: Request) -> tuple[AcousticReference | None, str]:
        """The video's own audio as the reference (issue #67), stored once found, or None and the reason.
        Playlist entries are YT_TRACK rows with their own source_url, so they qualify too."""
        stored = self.store.get_reference(req.id)
        if stored and stored["kind"] == "youtube":
            return AcousticReference.from_dict(stored), ""
        if req.kind != RequestKind.YT_TRACK or not req.source_url:
            return None, "not a YouTube request"
        try:
            async with self._cpu:
                ref = await youtube_reference(req.source_url, self.settings.tmp_dir / f"req{req.id}",
                                              duration_s=req.query_duration_s)
        except (YouTubeError, FingerprintError) as e:
            log.warning("req#%d: no video reference: %s", req.id, e)
            return None, f"video: {e}"
        self.store.set_reference(req.id, ref.to_dict())
        return ref, ""

    async def _acoustic_reference(self, req: Request, cand: Candidate) -> Acoustic:
        """The request's own video when there is one, else the candidate's Deezer preview. Fetched once and
        kept beside the request: every pick, the lossy fallback, a retry and the library sweep reuse it.
        A failure to get one is a fact about this request, worded for the attempt row and the owner."""
        stored = self.store.get_reference(req.id)
        if stored:
            return Acoustic(AcousticReference.from_dict(stored))
        ref, why = await self._video_reference(req)
        reasons = [why] if ref is None and why else []
        if ref is None and cand.deezer_id:
            try:
                async with self._cpu:
                    ref = await deezer_reference(cand.deezer_id, self.http, self.settings.tmp_dir)
            except FingerprintError as e:
                reasons.append(f"deezer preview: {e}")
        if ref is None:
            return Acoustic(None, "; ".join(reasons) or "no video and no Deezer id to fingerprint against")
        self.store.set_reference(req.id, ref.to_dict())
        return Acoustic(ref)
```

Threading: `_fetch_verify_file` computes `acoustic = await self._acoustic_reference(req, cand)` right after `self._set_state(req, RequestState.FETCHING)` and passes it to `_try_lossless(req, cand, catalog, acoustic)` and `_verify_and_file(req, cand, catalog, tmp, hit, acoustic)`. `_try_lossless` → `_attempt(provider, rec, req, ref, policy, acoustic)` → `_download_and_check(provider, rec, req, ref, file, n, acoustic)` → `_check_and_convert(rec, req, ref, file, tmp, acoustic)`, where the call becomes:

```python
            fp = await fingerprint_check(tmp, acoustic.reference, minimum=s.lossless_fingerprint_min,
                                         missing=acoustic.missing)
```

`upgrade()` computes `acoustic = await self._acoustic_reference(req, cand)` before `_try_lossless`. `_record_evidence` writes `"reference": fp.reference` instead of `f"deezer:{cand.deezer_id}"`. `_verify_and_file` takes `acoustic: Acoustic | None = None` and does nothing with it yet (Task 11 uses it).

`tests/test_worker_lossless.py` `lenv`: the fake becomes

```python
    async def fake_check(path, reference, *, minimum, missing=""):
        return replace(fake_check.result, reference=reference.label if reference else None)
```

(`from dataclasses import replace`; `fake_check.result` stays the way tests set it). The requests in these tests have no `source_url`, and `good_cand()` has a `deezer_id`, so `_acoustic_reference` would call `deezer_reference` for real. Add to `lenv`, and to the `env` fixture in `tests/test_worker.py` (give it a `monkeypatch` parameter):

```python
    async def fake_deezer(deezer_id, http, tmp_dir):
        return AcousticReference("deezer", str(deezer_id), [[1, 2, 3]], [1, 2, 3], 0.0, 30.0)
    monkeypatch.setattr(worker_mod, "deezer_reference", fake_deezer)
```

Add one worker test:

```python
async def test_the_video_is_the_reference_and_is_kept_beside_the_request(lenv, monkeypatch):
    _, store, _, provider, fake_check, _ = lenv
    calls = []
    async def fake_youtube(url, tmp_dir, *, duration_s=None):
        calls.append(url)
        return AcousticReference("youtube", "abc", [[9, 9, 9]], [9, 9, 9, 9], 10.0, 30.0)
    monkeypatch.setattr(worker_mod, "youtube_reference", fake_youtube)
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.DONE and calls == ["https://www.youtube.com/watch?v=abc"]
    assert store.get_reference(rid)["ref"] == "abc"
    ev = {e.kind: e.value for e in store.list_evidence(r.track_id)}
    assert ev["recording_match"]["reference"] == "youtube:abc"
```

- [ ] **Step 8: Run everything**

Run: `uv run pytest -q && uv run ruff check src tests && uv run lint-imports`
Expected: all PASS. `test_fingerprint_skipped_still_files_and_says_so` still passes (outcomes unchanged in this phase).

- [ ] **Step 9: Commit, PR** (`feat/67-video-reference`, label `enhancement`, "Part of #67 — annotate-only; the fingerprint now runs against the request's own video, outcomes unchanged").

---

## Phase 4 — Measurement tools and the measurement

### Task 5: `flackey sweep` — file vs video, and filed record vs video

**Files:**
- Create: `src/flackey/sweep.py`
- Modify: `src/flackey/cli.py`, `pyproject.toml` (the layer line `"worker : events : inbox",` becomes `"worker : events : inbox : sweep : replay",` — `replay` is Task 6; add it now so the line is edited once)
- Test: `tests/test_sweep.py`

**Interfaces:**
- Consumes: `reference.youtube_reference`, `reference.deezer_needles`, `fingerprint.check`, `fingerprint.compare`, `fingerprint.AcousticReference`, `store.list_tracks(search=None, limit=500, playlist_id=None)`, `store.get_request`, `store.get_candidate`, `store.get_reference`, `store.set_reference`.
- Produces: `sweep.SweepRow(track_id, artist, title, file_s, video_s, file_score, record_score, note)`; `sweep.run(store, settings, http, *, progress=lambda line: None) -> list[SweepRow]` (async); `sweep.render(rows, minimum) -> str` (markdown).

- [ ] **Step 1: Failing test** (`tests/test_sweep.py`)

```python
from pathlib import Path

import httpx

from flackey import sweep as sweep_mod
from flackey.config import Settings
from flackey.fingerprint import AcousticReference, FingerprintResult
from flackey.models import Candidate, RequestKind
from flackey.store import Store
from flackey.sweep import SweepRow, render, run


async def test_sweep_scores_file_and_record_against_the_video_and_reuses_the_reference(tmp_path: Path, monkeypatch):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h", library_root=tmp_path / "lib",
                        data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    rid = store.add_request("A - B", RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    store.update_request(rid, query_duration_s=300)
    [cid] = [c.id for c in store.add_candidates(rid, [Candidate("deezer_bot", "dz_track:7:send", "A", "B", deezer_id=7)])]
    store.update_request(rid, chosen_candidate_id=cid)
    f = tmp_path / "lib" / "A" / "A - B.aiff"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    tid = store.add_track(path=f, fmt="aiff", bitrate_kbps=1411, cutoff_hz=20000, file_size=1, artist="A", title="B",
                          mix_name="Original Mix", duration_s=298, isrc=None, catalog_track_id=None, request_id=rid)
    fetched = []
    full = list(range(100, 400))
    async def fake_youtube(url, tmp_dir, *, duration_s=None):
        fetched.append(url)
        return AcousticReference("youtube", "abc", [full[135:376]], full, 135.0, 30.0)
    async def fake_check(path, reference, *, minimum, missing=""):
        return FingerprintResult("matched", 0.97, 12.0, "ok", reference=reference.label)
    async def fake_needles(deezer_id, http, tmp_dir):
        return [full[50:290]]                              # a slice of the video: the preview is the same recording
    monkeypatch.setattr(sweep_mod, "youtube_reference", fake_youtube)
    monkeypatch.setattr(sweep_mod, "check", fake_check)
    monkeypatch.setattr(sweep_mod, "deezer_needles", fake_needles)
    async with httpx.AsyncClient() as http:
        rows = await run(store, settings, http)
        assert rows == [SweepRow(tid, "A", "B", 298, 300, 0.97, 1.0, "matched")]
        assert store.get_reference(rid)["ref"] == "abc"
        await run(store, settings, http)
    assert fetched == ["https://www.youtube.com/watch?v=abc"]      # the second sweep reused the stored reference
    text = render(rows, 0.9)
    assert "| 0.97 | 1.00 |" in text and "A - B" in text
```

- [ ] **Step 2: Run** `uv run pytest tests/test_sweep.py -q` — FAIL (no module).

- [ ] **Step 3: Implement**

```python
"""Fingerprint every filed track against the audio of the video that asked for it (issues #67, #68, #70).
Two questions per track: is the file the video's recording (file vs the video's needles), and would audio
identification have chosen the same Deezer record (the filed candidate's preview vs the video's full
fingerprint)? Report only: it writes nothing but the reference beside each request, which the worker
reuses."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .config import Settings
from .fingerprint import AcousticReference, FingerprintError, check, compare
from .models import RequestKind
from .reference import deezer_needles, youtube_reference
from .store import Store
from .youtube import YouTubeError


@dataclass(frozen=True)
class SweepRow:
    track_id: int
    artist: str
    title: str
    file_s: int | None
    video_s: int | None
    file_score: float | None        # the file against the video
    record_score: float | None      # the filed Deezer candidate's preview against the video
    note: str


async def run(store: Store, settings: Settings, http: httpx.AsyncClient, *,
              progress: Callable[[str], None] = lambda line: None) -> list[SweepRow]:
    rows: list[SweepRow] = []
    for t in store.list_tracks(limit=1_000_000):
        row = await _one(store, settings, http, t)
        rows.append(row)
        progress(f"{row.track_id:>4}  file {_fmt(row.file_score)}  record {_fmt(row.record_score)}  "
                 f"{row.artist} - {row.title}  ({row.note})")
    return rows


def _fmt(score: float | None) -> str:
    return "  - " if score is None else f"{score:.2f}"


async def _one(store: Store, settings: Settings, http: httpx.AsyncClient, t) -> SweepRow:
    base = dict(track_id=t.id, artist=t.artist, title=t.title, file_s=t.duration_s, video_s=None,
                file_score=None, record_score=None)
    if t.request_id is None:
        return SweepRow(**base, note="no request")
    try:
        req = store.get_request(t.request_id)
    except KeyError:
        return SweepRow(**base, note="request removed")
    base["video_s"] = req.query_duration_s
    if req.kind != RequestKind.YT_TRACK or not req.source_url:
        return SweepRow(**base, note="not a YouTube request")
    stored = store.get_reference(req.id)
    if stored and stored["kind"] == "youtube":
        ref = AcousticReference.from_dict(stored)
    else:
        try:
            ref = await youtube_reference(req.source_url, settings.tmp_dir / "sweep", duration_s=req.query_duration_s)
        except (YouTubeError, FingerprintError) as e:
            return SweepRow(**base, note=f"no reference: {e}")
        store.set_reference(req.id, ref.to_dict())
    result = await check(t.path, ref, minimum=settings.lossless_fingerprint_min)
    base["file_score"] = result.score
    note = result.status if result.status != "skipped" else f"skipped: {result.reason}"
    if req.chosen_candidate_id is not None:
        cand = store.get_candidate(req.chosen_candidate_id)
        if cand.deezer_id:
            try:
                needles = await deezer_needles(cand.deezer_id, http, settings.tmp_dir / "sweep")
                base["record_score"] = round(max(compare(n, ref.full)[0] for n in needles), 3)
            except FingerprintError as e:
                note += f"; record: {e}"
    return SweepRow(**base, note=note)


def render(rows: list[SweepRow], minimum: float) -> str:
    scored = sorted((r for r in rows if r.file_score is not None), key=lambda r: r.file_score)
    unscored = [r for r in rows if r.file_score is None]
    lines = [f"# Library fingerprint sweep ({len(rows)} tracks, threshold {minimum:.2f})", "",
             "| track | artist - title | file s | video s | file | record | note |", "|---|---|---|---|---|---|---|"]
    for r in scored + unscored:
        lines.append(f"| {r.track_id} | {r.artist} - {r.title} | {r.file_s or '?'} | {r.video_s or '?'} | "
                     f"{_cell(r.file_score)} | {_cell(r.record_score)} | {r.note} |")
    below = [r for r in scored if r.file_score < minimum]
    disagree = [r for r in rows if r.record_score is not None and r.record_score < minimum]
    lines += ["", f"File below threshold: {len(below)}. Record preview below threshold: {len(disagree)}. "
                  f"Unscored: {len(unscored)}."]
    return "\n".join(lines) + "\n"


def _cell(score: float | None) -> str:
    return "-" if score is None else f"{score:.2f}"
```

`cli.py`:

```python
@app.command()
def sweep(out: Path = typer.Option(Path("sweep.md"), "--out", help="Where to write the markdown table")) -> None:
    """Fingerprint every filed track and its Deezer record against the YouTube source; write a report.
    Reads the library, YouTube and Deezer previews; changes nothing in the library."""
    import httpx

    from .store import Store
    from .sweep import render, run

    s = _settings()

    async def go() -> list:
        async with httpx.AsyncClient() as http:
            return await run(Store(s.db_path), s, http, progress=typer.echo)

    rows = asyncio.run(go())
    out.write_text(render(rows, s.lossless_fingerprint_min))
    typer.echo(f"wrote {out}")
```

- [ ] **Step 4: Run** `uv run pytest tests/test_sweep.py tests/test_cli.py -q && uv run lint-imports` — PASS.
- [ ] **Step 5: Commit** on the branch `chore/measurement-tools` (label `chore`). Open the PR after Task 6.

### Task 6: `flackey replay-picks` and the candidate rankers

**Files:**
- Modify: `src/flackey/lossless.py` (ranker signature `(f, ref)`, `_version_agrees`, `rank_*`, `HARD_RULES`, `IDENTITY_RANKERS`, `PEER_RANKERS`, tolerant `PickPolicy.from_dict`; `RULES`/`RANKERS` defaults **unchanged** here)
- Create: `src/flackey/replay.py`
- Modify: `src/flackey/cli.py`
- Test: `tests/test_lossless.py`, `tests/test_replay.py`

**Interfaces:**
- Produces: `lossless.Ranker = Callable[[LosslessFile, Reference], object]`; `lossless.rank_version/rank_duration/rank_title/rank_artist`; `lossless.HARD_RULES`, `lossless.IDENTITY_RANKERS`, `lossless.PEER_RANKERS` (today's five, with the new signature); `lossless.DURATION_BUCKET_S = 5`; `replay.ReplayRow(attempt_id, request_id, text, asked_s, seen, survivors, top: list[tuple[str, int | None, int | None]])`; `replay.run(store, *, rules, rankers, limit=10_000) -> list[ReplayRow]`; `replay.render(rows) -> str`.

- [ ] **Step 1: Failing tests**

`tests/test_lossless.py` (`mk(**kw)` defaults: username "peer", path `Music\\Twisted\\02. Hallucinogen - Orphic Thrench.flac`, length_s 442, has_free_slot True; `REF` = Hallucinogen / Orphic Thrench / Original Mix / 442 s):

```python
from flackey.lossless import HARD_RULES, IDENTITY_RANKERS, PEER_RANKERS, rank_duration, rank_title, rank_version


def test_identity_rankers_order_survivors_instead_of_rejecting_them():
    far = mk(username="far", length_s=600)
    near = mk(username="near", length_s=444)                  # 2 s off: same bucket as exact
    exact_no_slot = mk(username="exact", length_s=442, has_free_slot=False)
    remix = mk(username="rmx", path="x\\Hallucinogen - Orphic Thrench (Twisted Remix).flac", length_s=442)
    report = pick([far, remix, exact_no_slot, near], REF, PickPolicy(), rules=HARD_RULES,
                  rankers=IDENTITY_RANKERS + PEER_RANKERS)
    assert not report.rejections
    assert [f.username for f in report.survivors] == ["near", "exact", "far", "rmx"]


def test_rankers_measure_against_the_reference():
    assert rank_version(mk(), REF) == 0
    assert rank_version(mk(path="x\\Hallucinogen - Orphic Thrench (Live).flac"), REF) == 1
    assert rank_duration(mk(length_s=442), REF) == 0 and rank_duration(mk(length_s=449), REF) == 1
    assert rank_duration(mk(length_s=None), REF) == 10_000
    assert rank_title(mk(), REF) == -10 and rank_title(mk(path="x\\Someone - Else.flac"), REF) > -5


def test_pick_policy_from_dict_ignores_fields_older_reports_carry():
    d = {"lossless_extensions": ["flac"], "duration_tolerance_s": 3, "title_ratio": 90, "require_artist": False,
         "max_queue_length": None, "banned_users": [], "some_future_field": 1}
    p = PickPolicy.from_dict(d)
    assert p.lossless_extensions == frozenset({"flac"}) and p.max_queue_length is None
```

`tests/test_replay.py`:

```python
from pathlib import Path

from flackey.lossless import HARD_RULES, IDENTITY_RANKERS, PEER_RANKERS, PickPolicy, Reference, pick
from flackey.models import RequestKind
from flackey.replay import render, run
from flackey.store import Store
from tests.test_lossless import mk


def test_replay_reruns_stored_reports_with_the_given_rules(tmp_path: Path):
    store = Store(tmp_path / "t.sqlite")
    rid = store.add_request("Hallucinogen - Orphic Thrench", RequestKind.YT_TRACK)
    store.update_request(rid, query_duration_s=442)
    ref = Reference("Hallucinogen", "Orphic Thrench", "Original Mix", 442, None, 442)
    # the stored report: today's rules rejected the 600 s file on duration, nothing survived
    report = pick([mk(username="far", length_s=600)], ref, PickPolicy())
    aid = store.add_attempt(rid, "soulseek", "Hallucinogen Orphic Thrench")
    store.update_attempt(aid, outcome="no_pick", report=report.to_dict())
    rows = run(store, rules=HARD_RULES, rankers=IDENTITY_RANKERS + PEER_RANKERS)
    assert len(rows) == 1 and rows[0].survivors == 1 and rows[0].top[0][1:] == (600, 158) and rows[0].asked_s == 442
    assert "| 158 |" in render(rows)
```

- [ ] **Step 2: Run** — FAIL on imports.

- [ ] **Step 3: Implement in `lossless.py`**

Change the ranker type and today's rankers to take the reference (unused by them):

```python
Ranker = Callable[[LosslessFile, Reference], object]
DURATION_BUCKET_S = 5     # encoding slack between releases of one recording; inside it, peer quality decides


def _version_agrees(f: LosslessFile, ref: Reference) -> bool:
    """rule_version's test, as a fact rather than a rejection: does the file's name claim the version the
    reference is?"""
    title, version = file_title(f.name, ref.artist)
    extra = [t for t in title.split() if t not in set(norm(ref.title).split())]
    words = set(norm(version or "").split()) | set(extra)
    if ref.is_original:
        return not (words & VERSION_WORDS) or "original" in words
    want = [w for w in norm(ref.mix_name).split() if w not in VERSION_WORDS]
    have = set(norm(f"{version or ''} {title}").split())
    return all(w in have for w in want)


def rank_version(f: LosslessFile, ref: Reference) -> int:
    return 0 if _version_agrees(f, ref) else 1


def rank_duration(f: LosslessFile, ref: Reference) -> int:
    """Distance to the nearest length the recording is known by, in buckets. Length is not identity (a
    626 s file was the 392 s video's recording, and two wrong files sat within 2 s of theirs), so it orders
    what to try first and the fingerprint decides (issue #69)."""
    known = ref.durations
    if not known or f.length_s is None:
        return 10_000
    return min(abs(f.length_s - d) for d in known) // DURATION_BUCKET_S


def rank_title(f: LosslessFile, ref: Reference) -> int:
    title, _ = file_title(f.name, ref.artist)
    return -(int(fuzz.token_set_ratio(norm(ref.title), title)) // 10)


def rank_artist(f: LosslessFile, ref: Reference) -> int:
    return 0 if norm(first_artist(ref.artist)) in norm(f.path) else 1


HARD_RULES: list[tuple[str, Rule]] = [
    ("extension", rule_extension), ("has_length", rule_has_length), ("plausible_size", rule_plausible_size),
    ("queue", rule_queue), ("banned_user", rule_banned_user),
]
IDENTITY_RANKERS: list[tuple[str, Ranker]] = [
    ("version", rank_version), ("duration", rank_duration), ("title", rank_title), ("artist", rank_artist),
]
PEER_RANKERS: list[tuple[str, Ranker]] = [
    ("free_slot", lambda f, ref: not f.has_free_slot),
    ("bit_depth", lambda f, ref: {16: 0, 24: 1}.get(f.bit_depth or 0, 2)),
    ("queue_length", lambda f, ref: f.queue_length),
    ("upload_speed", lambda f, ref: -f.upload_speed_bps),
    ("size", lambda f, ref: f.size),
]
RANKERS = PEER_RANKERS          # today's order; Task 13 puts IDENTITY_RANKERS in front and makes HARD_RULES the RULES
```

Check `rule_version`'s real body before writing `_version_agrees` and keep its exact test; the sketch above follows it from memory. In `pick`: `report.survivors.sort(key=lambda f: tuple(fn(f, ref) for _, fn in rankers))`.

`PickPolicy.from_dict`:

```python
    @classmethod
    def from_dict(cls, d: dict) -> PickPolicy:
        """Tolerant of fields an older report carries and this policy no longer has (the replay re-reads
        every stored report)."""
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in known}
        kw["lossless_extensions"] = frozenset(kw.get("lossless_extensions", LOSSLESS_EXTENSIONS))
        kw["banned_users"] = frozenset(kw.get("banned_users", ()))
        return cls(**kw)
```

(`from dataclasses import fields`.) `RULES` stays as it is in this task.

- [ ] **Step 4: Implement `replay.py`**

```python
"""Re-run the stored no-pick reports through a different gate, without the network (issue #69's measurement).
`PickReport.to_dict` keeps every rejected file, so the question "what would rank first now?" is answerable
from the attempt table alone."""
from __future__ import annotations

from dataclasses import dataclass

from .lossless import LosslessFile, PickPolicy, Ranker, Reference, Rule, pick
from .store import Store

TOP = 4


@dataclass(frozen=True)
class ReplayRow:
    attempt_id: int
    request_id: int
    text: str
    asked_s: int | None
    seen: int
    survivors: int
    top: list[tuple[str, int | None, int | None]]     # (file name, length, delta to the asked length)


def run(store: Store, *, rules: list[tuple[str, Rule]], rankers: list[tuple[str, Ranker]],
        limit: int = 10_000) -> list[ReplayRow]:
    rows: list[ReplayRow] = []
    for a in store.list_attempts(limit=limit, outcome="no_pick"):
        if not a.report:
            continue
        rep = a.report
        files = [LosslessFile(**r["file"]) for r in rep["rejections"]] + [LosslessFile(**f) for f in rep["survivors"]]
        ref = Reference(**rep["reference"])
        report = pick(files, ref, PickPolicy.from_dict(rep["policy"]), rules=rules, rankers=rankers)
        try:
            req = store.get_request(a.request_id)
            text, asked = req.raw_text, req.query_duration_s
        except KeyError:
            text, asked = a.query, ref.requested_duration_s
        top = [(f.name, f.length_s, None if asked is None or f.length_s is None else abs(f.length_s - asked))
               for f in report.survivors[:TOP]]
        rows.append(ReplayRow(a.id, a.request_id, text, asked, len(files), len(report.survivors), top))
    return rows


def render(rows: list[ReplayRow]) -> str:
    with_pick = [r for r in rows if r.survivors]
    near = [r for r in with_pick if r.top[0][2] is not None and r.top[0][2] <= 10]
    far = [r for r in with_pick if r.top[0][2] is not None and r.top[0][2] > 60]
    lines = [f"# No-pick replay: {len(rows)} attempts", "",
             f"- attempts with a survivor now: {len(with_pick)}",
             f"- top pick within 10 s of the asked length: {len(near)}",
             f"- top pick more than 60 s off: {len(far)}", "",
             "| attempt | request | asked s | seen | survivors | #1 file | #1 s | delta | #2 | #3 | #4 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cells = [str(r.attempt_id), r.text[:50].replace("|", "/"), str(r.asked_s or "?"), str(r.seen), str(r.survivors)]
        first = r.top[0] if r.top else ("-", None, None)
        cells += [first[0][:60].replace("|", "/"), str(first[1] or "?"), str(first[2] if first[2] is not None else "?")]
        cells += [f"{n[:30]} ({s}s)" for n, s, _ in r.top[1:4]] + ["-"] * (3 - len(r.top[1:4]))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
```

`cli.py`:

```python
@app.command("replay-picks")
def replay_picks(out: Path = typer.Option(Path("replay.md"), "--out")) -> None:
    """Re-run every stored no-pick report through the proposed hard rules and rankers, no network."""
    from .lossless import HARD_RULES, IDENTITY_RANKERS, PEER_RANKERS
    from .replay import render, run
    from .store import Store

    rows = run(Store(_settings().db_path), rules=HARD_RULES, rankers=IDENTITY_RANKERS + PEER_RANKERS)
    out.write_text(render(rows))
    typer.echo(f"{len(rows)} attempts replayed; wrote {out}")
```

Layer line in `pyproject.toml`: `"worker : events : inbox : sweep : replay",` (already written in Task 5; confirm).

- [ ] **Step 5: Run** `uv run pytest tests/test_lossless.py tests/test_replay.py tests/test_worker_lossless.py -q && uv run lint-imports && uv run ruff check src tests` — PASS.
- [ ] **Step 6: Commit and open the measurement-tools PR** (`chore/measurement-tools`: Tasks 5 + 6, label `chore`, "Part of #61").

### Task 7: Run the sweep and the replay, record both, decide the threshold and the ranker order

**Files:**
- Create: `docs/research/2026-MM-DD-fingerprint-sweep.md`, `docs/research/2026-MM-DD-no-pick-replay.md` (date of the run)

- [ ] **Step 1: Run the sweep against the live database, app quit** (`pgrep -fl flackey` empty). This downloads ~110 YouTube audio streams and ~90 Deezer previews (~20 min):

```bash
uv run flackey sweep --out docs/research/$(date +%F)-fingerprint-sweep.md
```

- [ ] **Step 2: Annotate the low scorers.** For every row whose file score is under 0.85, add a column `verdict` with `correct` / `wrong` / `unsure`, using file length vs video length and the audio itself (`open` the file, listen to the middle). Track 101 (retagged in Task 1) should score ≈ 0.94; Hallucinogen – Trancespotter ≈ 0.97; Astral Projection – Searching For UFO's ≈ 0.93. Put the owner's confirmation on every `wrong` before it is called wrong.

- [ ] **Step 3: Compute the threshold** per the spec: `min_correct` = lowest file score among `correct`, `max_wrong` = highest among `wrong` (use 0.72, the Shiva Shidapu score, if the sweep finds no wrong files because Task 1 removed them). Threshold = `round((min_correct + max_wrong) / 2, 2)`, capped at 0.90. If `min_correct - max_wrong < 0.10`, **stop and ask the owner**. Write the numbers and the decision at the top of the report.

- [ ] **Step 4: Check identification agreement.** List every row whose record score is below the threshold while its file is `correct`. Each is a track where audio identification would have refused the record the text chose. Expected: zero, or only tracks whose Deezer preview is a different master. If any certified-correct track disagrees, **stop and ask the owner** before Task 10 ships; record the list in the report either way.

- [ ] **Step 5: Run the replay on a copy of the live database.** `Settings.db_path` is derived (`data_dir / "flackey.sqlite"`) and the settings have no env prefix, so the copy goes into a folder that `DATA_DIR` points at:

```bash
mkdir -p "$TMPDIR/flackey-replay" && cp "$HOME/Library/Application Support/flackey/flackey.sqlite" "$TMPDIR/flackey-replay/"
DATA_DIR="$TMPDIR/flackey-replay" uv run flackey replay-picks --out docs/research/$(date +%F)-no-pick-replay.md
```

- [ ] **Step 6: Decide the ranker order.** At the top of the replay report, write: how many of the 216 attempts now have a survivor; how many top picks are within 10 s; how many are more than 60 s off; and for ten random attempts whose top pick is more than 60 s off, whether a same-recording file plausibly exists (a 1479 s "Italian EP" upload against a 541 s request cannot be). Decision rule: if more than 25 % of top picks are more than 60 s off **and** a nearer file existed in the list, move `duration` ahead of `version` in `IDENTITY_RANKERS`; otherwise keep `version` first. Record the decision and apply it in Task 13.

- [ ] **Step 7: If the threshold changes**, the default of `lossless_fingerprint_min` in `src/flackey/config.py` and the copy in `web/src/screenshot-harness.tsx` (`fingerprint_min`) change in Task 8's PR, not here.

- [ ] **Step 8: Commit both reports** (`docs/`, label `ignore-for-release`), comment on the measurement issue with the summary lines (matched count, below-threshold count, threshold chosen, identification disagreements, replay counts, ranker decision).

---

## Phase 5 — The fingerprint decides (#68)

### Task 8: `fingerprint_unavailable` outcome; `skipped` no longer files; flag removed

**Files:**
- Modify: `src/flackey/models.py` (`ATTEMPT_OUTCOMES`, `MISS_REASON`), `web/src/presentation.ts` (`MISS_REASON`), `src/flackey/worker.py` (`_check_and_convert`, remove `NO_FINGERPRINT_FLAG` and its two uses, `catalog_candidate` docstring), `src/flackey/config.py` + `web/src/screenshot-harness.tsx` (threshold from Task 7, if it changed)
- Test: `tests/test_worker_lossless.py`, `tests/test_worker.py`

- [ ] **Step 1: Failing tests**

Replace `test_fingerprint_skipped_still_files_and_says_so` (`tests/test_worker_lossless.py:311`) with:

```python
async def test_fingerprint_unavailable_ends_the_attempt_and_falls_back(lenv):
    """Issue #68: a file nothing acoustic vouched for is never filed from Soulseek. The reference is missing
    for every pick alike, so the attempt ends rather than trying the next peer."""
    _, store, notifier, provider, fake_check, _ = lenv
    provider.files = [lf("a"), lf("b")]
    fake_check.result = FingerprintResult("skipped", None, None, "video: yt-dlp timed out")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK)
    r = await w.process(rid)
    a = attempt_of(store, rid)
    assert a.outcome == "fingerprint_unavailable" and provider.downloaded == ["a"]
    assert r.flag_reason is None and not hasattr(worker_mod, "NO_FINGERPRINT_FLAG")
    assert "could not be checked acoustically" in notifier.sent[-1][0]
```

(`r.state` after this test is whatever the lossy fallback does — DONE in this task, ERROR after Task 11; assert only the attempt here.) Update `test_a_file_taken_without_a_fingerprint_says_so_on_the_request` (`tests/test_worker_lossless.py:811-822`, asserts `r.flag_reason == worker_mod.NO_FINGERPRINT_FLAG`) so it asserts `r.flag_reason is None` and the attempt outcome `fingerprint_unavailable`, and reword the comment in `test_the_catalog_stand_in_carries_beatport_data_and_no_deezer_id` (`tests/test_worker.py:489-492`) that mentions the flag.

- [ ] **Step 2: Run** — FAIL (`outcome == "filed"`).

- [ ] **Step 3: Implement**

`models.py`:

```python
ATTEMPT_OUTCOMES = ("filed", "no_pick", "queued", "first_byte_timeout", "transfer_timeout", "transfer_failed",
                    "verify_failed", "fingerprint_failed", "fingerprint_unavailable", "convert_failed",
                    "unavailable", "interrupted")
MISS_REASON = {
    ...
    "fingerprint_failed": "the copies offered were a different recording",
    "fingerprint_unavailable": "the recording could not be checked acoustically",
    ...
}
```

`presentation.ts`: add `fingerprint_unavailable: 'the recording could not be checked acoustically',` to `MISS_REASON` (the pytest pin `test_miss_reasons_match_the_ui` reads this file).

`worker.py` `_check_and_convert`, after `rec.event("fingerprint", …)`:

```python
        if fp.status == "failed":
            return None, "fingerprint_failed"
        if fp.status == "skipped":
            # Nothing acoustic vouched for the file. Not a fact about this peer, so not SECOND_PICK_AFTER:
            # the reference is missing for every survivor alike (issue #68).
            return None, "fingerprint_unavailable"
```

Delete `NO_FINGERPRINT_FLAG` (worker.py:119-120); delete `self.store.update_request(req.id, flag_reason=NO_FINGERPRINT_FLAG)` in `_process`; rewrite the last paragraph of `catalog_candidate`'s docstring to: "`deezer_id` stays None. Since issue #67 the fingerprint's reference is the request's own video, so a Beatport stand-in is checked acoustically like any other candidate; without a video or a Deezer id the attempt ends `fingerprint_unavailable` rather than filing on the match alone." Apply the Task 7 threshold to `config.py` and `screenshot-harness.tsx` if it changed.

- [ ] **Step 4: Run** `uv run pytest -q && npm --prefix web test -- --run` — PASS.
- [ ] **Step 5: Commit** on `feat/68-fingerprint-decides` (Tasks 8–11 share the PR, label `enhancement`, "Closes #68").

### Task 9: `decide` auto-accepts without a Beatport record; the "not on Beatport" review goes

**Files:**
- Modify: `src/flackey/match.py` (`decide`), `src/flackey/worker.py:571-577` (`_process`: the `if catalog is None:` review after `_catalog_for`)
- Test: `tests/test_match.py`, `tests/test_worker.py`

- [ ] **Step 1: Failing tests**

`tests/test_match.py`: replace `test_decide_parks_without_catalog` with

```python
def test_decide_auto_files_without_catalog_when_confident():
    d = decide(Q, [cand()], None)
    assert d.auto and "recording check" in d.reason
    assert not decide(Q, [cand(artist="Someone Else", title="Something", duration_s=100)], None).auto
```

`tests/test_worker.py`: rename `test_auto_accepted_edit_missing_from_beatport_parks_for_review` (line 308) to `test_auto_accepted_edit_missing_from_beatport_files_on_the_fingerprint` — it now expects `r.state == RequestState.DONE` after one `process`, the track's `(mix_name, isrc) == ("Album Edit", "EDIT00001")`, and `store.get_request(rid).catalog_track_id < 0` (the fallback catalogue). `test_not_on_beatport_parks` (line 237) becomes `test_not_on_beatport_files_when_confident`: same setup, expects DONE and a negative `catalog_track_id`. Any later test that relied on either to *park* a request must park it another way: give the source a below-threshold candidate (`Candidate(..., artist="Someone Else", title="Something", duration_s=100)`).

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement**

`match.decide`: delete `if catalog is None: return Decision(False, top, "not found on Beatport; information cannot be verified")` and change the final return to:

```python
    if catalog is None:
        return Decision(True, top, f"confidence {top.score}%; not on Beatport, the recording check decides")
    return Decision(True, top, f"confidence {top.score}%")
```

`worker._process`: delete the block

```python
            if catalog is None:
                # the pinned edit/remix has no Beatport record: only the owner may file it on Deezer's word
                reason = f"the {candidate_version(cand)} is not on Beatport; information cannot be verified"
                self._set_state(req, RequestState.AWAITING_REVIEW, flag_reason=reason)
                await self._ask_review(req, saved, reason)
                return
```

- [ ] **Step 4: Run** `uv run pytest tests/test_match.py tests/test_worker.py tests/test_worker_lossless.py tests/test_web.py -q` — PASS.
- [ ] **Step 5: Commit** on the same branch.

### Task 10: Audio picks the record for YouTube requests

**Files:**
- Modify: `src/flackey/reference.py` (`Identification`, `IDENTIFY_MAX`, `identify_record`), `src/flackey/worker.py:534-570` (`_process` from `decision = decide(...)` to `cand = chosen`)
- Test: `tests/test_reference.py`, `tests/test_worker.py`

**Interfaces:**
- Produces: `reference.Identification(chosen: Candidate | None, score: float | None, tried: list[tuple[int, float | None]], reason: str)`; `reference.IDENTIFY_MAX = 5`; `reference.identify_record(reference: AcousticReference, cands: list[Candidate], http, tmp_dir, *, minimum: float, limit: int = IDENTIFY_MAX) -> Identification` (async; never raises; `chosen` is one of the objects in `cands`).
- Consumes: `Worker._video_reference(req)` (Task 4), `fingerprint.compare`, `reference.deezer_needles`.

- [ ] **Step 1: Failing tests**

`tests/test_reference.py`:

```python
from flackey.models import Candidate
from flackey.reference import identify_record

FULL = list(range(100, 400))
VIDEO = AcousticReference("youtube", "abc", [FULL[135:376]], FULL, 135.0, 30.0)


def _cand(deezer_id: int) -> Candidate:
    return Candidate("deezer_bot", f"dz_track:{deezer_id}:send", "A", "B", deezer_id=deezer_id)


async def test_identify_record_picks_the_first_preview_that_is_the_video(tmp_path: Path, monkeypatch):
    previews = {7: [FULL[50:290]],                                  # a slice of the video: same recording
                8: [[v ^ 0xFFFFFFFF for v in FULL[50:290]]],        # every bit differs
                9: [FULL[10:250]]}
    async def fake_needles(deezer_id, http, tmp_dir):
        if deezer_id == 6:
            raise FingerprintError("deezer has no preview for this track")
        return previews[deezer_id]
    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    cands = [_cand(8), _cand(6), _cand(7), _cand(9), Candidate("deezer_bot", "x", "A", "B")]
    got = await identify_record(VIDEO, cands, None, tmp_path, minimum=0.9)
    assert got.chosen is cands[2] and got.score == 1.0
    assert got.tried == [(8, 0.0), (6, None), (7, 1.0)] and "deezer:7" in got.reason


async def test_identify_record_refuses_when_no_preview_matches(tmp_path: Path, monkeypatch):
    async def fake_needles(deezer_id, http, tmp_dir):
        return [[v ^ 0xFFFFFFFF for v in FULL[50:290]]]
    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    cands = [_cand(n) for n in range(1, 9)]
    got = await identify_record(VIDEO, cands, None, tmp_path, minimum=0.9)
    assert got.chosen is None and len(got.tried) == 5 and "none of 5" in got.reason
```

`tests/test_worker.py` (uses `env`, `FakeSource`, `FakeCatalog`, `good_cand`, `CT`; build the worker the way the neighbouring tests do):

```python
async def test_a_youtube_request_files_the_candidate_whose_preview_is_the_video(env, monkeypatch):
    """Issue #68: the text winner is 'Between The Lines' (artist 100, title 44, average 72); the video's
    audio says the second candidate is the track. Audio wins, no review, confidence is the audio score."""
    settings, store, notifier = env
    wrong = good_cand()
    wrong.title, wrong.deezer_id, wrong.source_ref = "Between The Lines", 1, "dz_track:1:send"
    right = good_cand()
    right.title, right.deezer_id, right.source_ref = "Nothing but a Title", 2, "dz_track:2:send"
    async def fake_youtube(url, tmp_dir, *, duration_s=None):
        return AcousticReference("youtube", "abc", [[1, 2, 3]], [1, 2, 3, 4], 0.0, 30.0)
    async def fake_identify(reference, cands, http, tmp_dir, *, minimum, limit=5):
        chosen = next(c for c in cands if c.deezer_id == 2)
        return Identification(chosen, 0.97, [(1, 0.41), (2, 0.97)], "deezer:2 preview matches the video, score 0.97")
    monkeypatch.setattr(worker_mod, "youtube_reference", fake_youtube)
    monkeypatch.setattr(worker_mod, "identify_record", fake_identify)
    w = Worker(store, FakeSource([wrong, right]), FakeCatalog([CT]), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request("New Born - Nothing but a Title", RequestKind.YT_TRACK,
                            source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.DONE and r.confidence == 97
    assert store.get_track(r.track_id).title == "Nothing but a Title"
    assert not [m for m in notifier.sent if m[0].startswith("Review needed")]


async def test_a_youtube_request_with_no_matching_preview_does_not_file_the_text_winner(env, monkeypatch):
    settings, store, notifier = env
    async def fake_youtube(url, tmp_dir, *, duration_s=None):
        return AcousticReference("youtube", "abc", [[1, 2, 3]], [1, 2, 3, 4], 0.0, 30.0)
    async def fake_identify(reference, cands, http, tmp_dir, *, minimum, limit=5):
        return Identification(None, None, [(c.deezer_id, 0.3) for c in cands], "none of 1 Deezer previews is the video's recording")
    monkeypatch.setattr(worker_mod, "youtube_reference", fake_youtube)
    monkeypatch.setattr(worker_mod, "identify_record", fake_identify)
    w = Worker(store, FakeSource([good_cand()]), FakeCatalog([CT]), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.NOT_FOUND and "none of 1" in r.error_message      # Task 12 sends this to Soulseek instead


async def test_without_a_video_reference_text_still_decides(env, monkeypatch):
    settings, store, notifier = env
    async def fake_youtube(url, tmp_dir, *, duration_s=None):
        raise YouTubeError("yt-dlp timed out")
    called = []
    async def fake_identify(*a, **kw):
        called.append(1)
    monkeypatch.setattr(worker_mod, "youtube_reference", fake_youtube)
    monkeypatch.setattr(worker_mod, "identify_record", fake_identify)
    w = Worker(store, FakeSource([good_cand()]), FakeCatalog([CT]), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.DONE and called == [] and store.get_reference(rid)["kind"] == "deezer"
```

(Match the `Worker(...)` constructor call to what the existing tests in `tests/test_worker.py` use — `grep -n "Worker(" tests/test_worker.py | head -3` — including any lossless/provider arguments; `YouTubeError` from `flackey.youtube`, `Identification` from `flackey.reference`.)

- [ ] **Step 2: Run** — FAIL on imports.

- [ ] **Step 3: Implement `identify_record`** in `reference.py` (import `Candidate` from `.models`, `compare` from `.fingerprint`, `logging`):

```python
IDENTIFY_MAX = 5      # previews tried per request; the bot's menu is text-ordered, so the record is in the first few


@dataclass(frozen=True)
class Identification:
    chosen: Candidate | None
    score: float | None
    tried: list[tuple[int, float | None]]     # (deezer_id, best score; None when the preview could not be fetched)
    reason: str


async def identify_record(reference: AcousticReference, cands: list[Candidate], http: httpx.AsyncClient,
                          tmp_dir: Path, *, minimum: float, limit: int = IDENTIFY_MAX) -> Identification:
    """Which Deezer candidate is the recording the owner pointed at (issue #68): the first whose 30 s preview
    is found inside the reference's full fingerprint at or above `minimum`. Candidates are tried in the
    order given (text score, best first). A text score never overrules this: a candidate whose preview does
    not match is not the track, however its title reads. Never raises; a preview that cannot be fetched
    counts as tried with no score."""
    tried: list[tuple[int, float | None]] = []
    for cand in [c for c in cands if c.deezer_id][:limit]:
        try:
            needles = await deezer_needles(cand.deezer_id, http, tmp_dir)
        except FingerprintError as e:
            log.info("identify: deezer:%d preview unavailable: %s", cand.deezer_id, e)
            tried.append((cand.deezer_id, None))
            continue
        score = round(max(compare(n, reference.full)[0] for n in needles), 3)
        tried.append((cand.deezer_id, score))
        if score >= minimum:
            return Identification(cand, score, tried,
                                  f"deezer:{cand.deezer_id} preview matches the video, score {score:.2f}")
    return Identification(None, None, tried, f"none of {len(tried)} Deezer previews is the video's recording")
```

- [ ] **Step 4: Implement the worker change.** Import `Identification, identify_record` from `.reference`. Replace `_process` from `decision = decide(query, cands, catalog)` through `cand = chosen` with:

```python
            # The record: by audio when the request has audio of its own, by text otherwise (issue #68).
            # `decide` runs either way: it scores every candidate (the order the previews are tried in,
            # and what the Choose window shows), and its verdict only counts without a video.
            video, _ = await self._video_reference(req)
            decision = decide(query, cands, catalog)
            ident: Identification | None = None
            if video is not None:
                ordered = sorted(cands, key=lambda c: -(c.score or 0))
                async with self._cpu:
                    ident = await identify_record(video, ordered, self.http, self.settings.tmp_dir,
                                                  minimum=self.settings.lossless_fingerprint_min)
                log.info("req#%d identification: %s (tried %s)", req.id, ident.reason, ident.tried)
            chosen_obj = ident.chosen if ident is not None else decision.chosen
            if chosen_obj is not None and chosen_obj.isrc:
                # The source's recording ID disambiguates equally named Beatport releases. Never
                # substitute a loosely matched release: require the normal search score first.
                matched_catalog = best_match(query, catalog_tracks, chosen_obj.isrc)
                if matched_catalog and matched_catalog.id != (catalog.id if catalog else None):
                    catalog = matched_catalog
                    self.store.upsert_catalog_track(catalog)
                    self.store.update_request(req.id, catalog_track_id=catalog.id)
                    if ident is None:
                        decision = decide(query, cands, catalog)
                        chosen_obj = decision.chosen
            saved = self.store.add_candidates(req.id, cands)
            confidence = (round(ident.score * 100) if ident is not None and ident.score is not None
                          else (chosen_obj.score if chosen_obj else None))
            self.store.update_request(req.id, confidence=confidence)
            if chosen_obj is None:
                reason = ident.reason if ident is not None else decision.reason
                log.info("req#%d: no acceptable candidate among %d: %s", req.id, len(cands), reason)
                self._set_state(req, RequestState.NOT_FOUND, error_message=f"could not identify this track: {reason}")
                await self.notifier.send(f"Not available on Deezer: {req.raw_text}")
                return
            # the chooser returns one of the objects in `cands`; match by identity, not by source_ref
            # (the bot can list the same Deezer id twice)
            chosen = saved[next(i for i, c in enumerate(cands) if c is chosen_obj)]

            dup = find_duplicate(self.store, catalog, chosen)
            if dup:
                await self._mark_duplicate(req, dup.id, dup.path)
                return

            if ident is None and not decision.auto:
                self._set_state(req, RequestState.AWAITING_REVIEW, flag_reason=decision.reason,
                                chosen_candidate_id=chosen.id)
                await self._ask_review(req, saved, decision.reason)
                return
            # persist the choice so a fetch failure resumes here instead of searching (and saving candidates) again
            self.store.update_request(req.id, chosen_candidate_id=chosen.id)
            cand = chosen
```

(The `_catalog_for` call that follows is unchanged; Task 9 already removed the review after it.)

- [ ] **Step 5: Run** `uv run pytest -q && uv run ruff check src tests && uv run lint-imports` — PASS. In `tests/test_worker.py` every request built with `RequestKind.YT_TRACK` and a `source_url` now needs `youtube_reference` faked (the `env` fixture from Task 4 fakes `deezer_reference` only): add to `env` a `fake_youtube` that raises `FingerprintError("no video audio in tests")` so existing tests keep the text path (a permanent failure on purpose: Task 15 makes transient yt-dlp failures retry, and the fixture must stay off that ladder), and override it in the three tests above.
- [ ] **Step 6: Commit** on the same branch.

### Task 11: The lossy fallback is fingerprinted too

**Files:**
- Modify: `src/flackey/worker.py` (`_verify_and_file`, `_record_evidence`)
- Test: `tests/test_worker_lossless.py`

- [ ] **Step 1: Failing tests** (`tests/test_worker_lossless.py`)

```python
async def test_the_lossy_fallback_is_rejected_when_it_is_a_different_recording(lenv):
    _, store, notifier, provider, fake_check, _ = lenv
    provider.files = []                                  # no lossless copy: Deezer's MP3 lands
    fake_check.result = FingerprintResult("failed", 0.61, 3.0, "best score 0.61 below 0.90")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK)
    r = await w.process(rid)
    assert r.state == RequestState.REJECTED and "different recording" in store.get_rejection_for_request(rid).reason
    assert not any((w.settings.tmp_dir).rglob("*"))


async def test_the_lossy_fallback_errors_when_it_cannot_be_checked(lenv):
    _, store, notifier, provider, fake_check, _ = lenv
    provider.files = []
    fake_check.result = FingerprintResult("skipped", None, None, "video: yt-dlp timed out")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK)
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and "yt-dlp timed out" in r.error_message
    assert store.get_request(rid).track_id is None


async def test_the_lossy_fallback_files_with_recording_evidence_when_it_matches(lenv):
    _, store, _, provider, _, _ = lenv
    provider.files = []
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK)
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    kinds = {e.kind for e in store.list_evidence(r.track_id)}
    assert "recording_match" in kinds and "source" not in kinds
```

- [ ] **Step 2: Run** — FAIL (`DONE`).

- [ ] **Step 3: Implement** in `_verify_and_file`, inside `if hit is None:` after the `verdict.passed` check:

```python
            async with self._cpu:
                fp = await fingerprint_check(tmp, acoustic.reference if acoustic else None,
                                             minimum=self.settings.lossless_fingerprint_min,
                                             missing=acoustic.missing if acoustic else "")
            log.info("req#%d lossy copy fingerprint: %s %s", req.id, fp.status, fp.reason)
            if fp.status == "failed":
                reason = f"a different recording: {fp.reason}"
                self.store.add_rejection(req.id, reason, verdict.bitrate_kbps, verdict.cutoff_hz, verdict.spectrogram_path)
                self._set_state(req, RequestState.REJECTED)
                await self.notifier.send(f"Rejected: {cand.artist} – {cand.title}\n{reason}")
                return
            if fp.status == "skipped":
                reason = (f"the recording could not be checked acoustically ({fp.reason}). "
                          f"Use Try again once it can be checked")
                self._set_state(req, RequestState.ERROR, attempts=req.attempts + 1, error_message=reason)
                await self.notifier.send(f"Could not verify: {req.raw_text}\n{reason}")
                return
```

and in the `else:` branch set `fp = hit.fingerprint`. After `track_id = self.store.add_track(...)`, replace `if hit: self._record_evidence(track_id, hit, cand)` with `self._record_evidence(track_id, fp, hit)`, where:

```python
    def _record_evidence(self, track_id: int, fp: FingerprintResult, hit: LosslessHit | None) -> None:
        if hit is not None:
            self.store.add_evidence(track_id, "source", {"provider": hit.provider, "source_fmt": hit.source_fmt,
                                                         "attempt_id": hit.attempt_id})
        self.store.add_evidence(track_id, "recording_match", {"status": fp.status, "score": fp.score,
                                                              "offset_s": fp.offset_s, "reference": fp.reference,
                                                              "reason": fp.reason})
        if fp.track:
            self.store.add_evidence(track_id, "fingerprint", {"frames": fp.track, "fps": FPS})
```

`_replace_with` calls `self._record_evidence(t.id, hit.fingerprint, hit)`.

- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check src tests && uv run lint-imports && npm --prefix web test -- --run && npm --prefix web run build` — PASS.
- [ ] **Step 5: Commit, open the PR** (`feat/68-fingerprint-decides`, label `enhancement`, "Closes #68. Sweep report: docs/research/…").

---

## Phase 6 — Open the gate, rank instead of reject, spellings in sequence (#69)

### Task 12: `query_candidate` — Soulseek on the request's own words

**Files:**
- Modify: `src/flackey/worker.py` (`QUERY_SOURCE` next to `CATALOG_SOURCE`, `query_candidate`, `_reference`, `_process`: the `SourceNotFound` path, the no-candidates branch and the no-record branch after identification; `_fetch_verify_file` no-fallback check; `_no_route` wording), `src/flackey/lossless.py` (`Reference.from_query` field only)
- Test: `tests/test_worker_lossless.py` (add `from flackey.models import Query` and `from flackey.source import SourceNotFound` to its imports), `tests/test_worker.py`

**Interfaces:**
- Produces: `worker.QUERY_SOURCE = "query"`; `worker.query_candidate(query: Query, request_id: int) -> Candidate` (source `QUERY_SOURCE`, `source_ref=f"query:{request_id}"`, `mix_name = query.version or "Original Mix"`, `duration_s = query.duration_s`); `lossless.Reference.from_query: bool = False` (`reference_for` is untouched; the worker sets the flag with `dataclasses.replace` in `Worker._reference(req, cand, catalog) -> Reference`).

- [ ] **Step 1: Failing tests**

```python
async def test_no_record_anywhere_still_searches_soulseek_on_the_request(lenv):
    """Issue #69: 'no Beatport record' means unverified, not unavailable. The parsed words and the video's
    length go to Soulseek and the fingerprint decides; tags come from the request."""
    _, store, _, provider, _, _ = lenv
    w = make(lenv, source=FakeSource(error=SourceNotFound("no results")), catalog=FakeCatalog([]))
    rid = store.add_request("Astral Projection - Into the Void", RequestKind.YT_TRACK,
                            source_url="https://www.youtube.com/watch?v=abc",
                            query=Query(raw="", artist="Astral Projection", title="Into the Void", duration_s=3))
    r = await w.process(rid)
    assert r.state == RequestState.DONE and provider.searches == ["Astral Projection Into the Void"]
    t = store.get_track(r.track_id)
    assert (t.artist, t.title, t.mix_name) == ("Astral Projection", "Into the Void", "Original Mix") and t.catalog_track_id < 0
    assert attempt_of(store, rid).report["reference"]["from_query"] is True


async def test_no_record_anywhere_and_no_lossless_copy_has_no_lossy_fallback(lenv):
    _, store, _, provider, _, _ = lenv
    provider.files = []
    w = make(lenv, source=FakeSource(error=SourceNotFound("no results")), catalog=FakeCatalog([]))
    rid = store.add_request("A - B", RequestKind.YT_TRACK, query=Query(raw="", artist="A", title="B", duration_s=3))
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and "nothing on Soulseek matched" in r.error_message
    assert attempt_of(store, rid).outcome == "no_pick"


async def test_a_bare_artist_request_ends_not_found_with_a_reason(lenv):
    _, store, _, provider, _, _ = lenv
    w = make(lenv, source=FakeSource(error=SourceNotFound("no results")), catalog=FakeCatalog([]))
    rid = store.add_request("Oforia", RequestKind.YT_TRACK, query=Query(raw="Oforia", artist="Oforia"))
    r = await w.process(rid)
    assert r.state == RequestState.NOT_FOUND and "no artist and title" in r.error_message and provider.searches == []


async def test_no_matching_preview_sends_the_request_words_to_soulseek(lenv, monkeypatch):
    """Task 10 ended this in NOT_FOUND; with the gate open the words go to Soulseek and the video decides."""
    _, store, _, provider, _, _ = lenv
    async def fake_youtube(url, tmp_dir, *, duration_s=None):
        return AcousticReference("youtube", "abc", [[1, 2, 3]], [1, 2, 3, 4], 0.0, 30.0)
    async def fake_identify(reference, cands, http, tmp_dir, *, minimum, limit=5):
        return Identification(None, None, [(c.deezer_id, 0.3) for c in cands], "none of 1 Deezer previews is the video's recording")
    monkeypatch.setattr(worker_mod, "youtube_reference", fake_youtube)
    monkeypatch.setattr(worker_mod, "identify_record", fake_identify)
    w = make(lenv, catalog=FakeCatalog([]))
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc",
                            query=Query(raw="", artist="Astral Projection", title="Into the Void", duration_s=3))
    r = await w.process(rid)
    assert r.state == RequestState.DONE and provider.searches == ["Astral Projection Into the Void"]
    assert store.get_track(r.track_id).catalog_track_id < 0
```

`tests/test_worker.py`: `test_a_youtube_request_with_no_matching_preview_does_not_file_the_text_winner` (Task 10) now expects an ERROR from `_no_route` when the worker has no providers, or DONE via Soulseek when it has; re-state it per how the `env` worker is built. The existing test asserting NOT_FOUND "neither Deezer nor Beatport" for a structured request now expects a Soulseek attempt (providers present) or `_no_route`'s ERROR (none). Read each failing test and re-state its expectation per the spec.

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement**

`lossless.Reference`: add `from_query: bool = False` as the last field, with the comment "built from the request's own words rather than a catalogue record or a source candidate; a hit on it tags from the request". Nothing else in `lossless.py` changes.

`worker.py`, next to `CATALOG_SOURCE`:

```python
QUERY_SOURCE = "query"      # a candidate built from the request itself; see query_candidate
```

```python
def query_candidate(query: Query, request_id: int) -> Candidate:
    """What the owner asked for, as a candidate: the parsed artist, title and version plus the video's length.
    Built when no record was chosen (issue #69). `lossless.reference_for` takes the search text from it and
    `_fallback_catalog` the tags; the only thing that vouches for the file is the fingerprint against the
    request's own audio, which is exactly the check that never depended on either catalogue. Never
    persisted: a retry rebuilds it from the row's query columns."""
    return Candidate(source=QUERY_SOURCE, source_ref=f"{QUERY_SOURCE}:{request_id}", artist=query.artist or "",
                     title=query.title or "", mix_name=query.version or "Original Mix", duration_s=query.duration_s)
```

As a `Worker` method, used by `_try_lossless` in place of its direct `reference_for` call:

```python
    def _reference(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> Reference:
        ref = reference_for(catalog, cand, req.query_duration_s)
        return replace(ref, from_query=True) if cand.source == QUERY_SOURCE else ref

    async def _search_on_the_request(self, req: Request, query: Query, catalog: CatalogTrack | None,
                                     why: str) -> None:
        """No record was chosen. Soulseek is the only network that has this library's music, and the
        fingerprint is what proves the file; the owner's own words are enough to ask with (issue #69)."""
        if not (query.artist and query.title):
            self._set_state(req, RequestState.NOT_FOUND, error_message=f"could not identify this track: {why}; "
                            f"no artist and title could be read from the request, so there is nothing to search for")
            await self.notifier.send(f"Could not identify: {req.raw_text}")
            return
        if not self._lossless_allowed(req):
            await self._no_route(req)
            return
        await self._fetch_verify_file(req, query_candidate(query, req.id), catalog)
```

`_process`, the source search and no-candidates branch:

```python
            cands: list[Candidate] = []
            if self.settings.source_enabled:
                try:
                    cands = await self.source.search(query)
                except SourceUnauthorized:
                    raise  # handled in process(): subclass of SourceError, so it must be caught before it
                except SourceNotFound as e:
                    # Not a verdict any more: the request's own words can still reach Soulseek below.
                    log.info("req#%d not found at source: %s", req.id, e)
                except (SourceTimeout, SourceError) as e:
                    if catalog is None:
                        # The source may be back in an hour and would offer a lossy fallback the query-only
                        # path does not have; a retry is worth more than a Soulseek-or-nothing pass now.
                        await self._retry_or_fail(req, f"source error: {e}")
                        return
                    log.info("req#%d source gave nothing (%s); trying the lossless providers on the "
                             "Beatport match alone", req.id, e)

            if not cands:
                if catalog is not None:
                    # `catalog_candidate` explains what this costs: every pick rule runs on Beatport data.
                    if not self._lossless_allowed(req):
                        await self._no_route(req)
                        return
                    await self._fetch_verify_file(req, catalog_candidate(catalog), catalog)
                    return
                await self._search_on_the_request(req, query, None, "no Deezer candidates and no Beatport match")
                return
```

and the no-record branch after identification (Task 10's `if chosen_obj is None:`) becomes:

```python
            if chosen_obj is None:
                reason = ident.reason if ident is not None else decision.reason
                log.info("req#%d: no acceptable candidate among %d: %s", req.id, len(cands), reason)
                await self._search_on_the_request(req, query, catalog, reason)
                return
```

`_fetch_verify_file`: `if hit is None and cand.source in (CATALOG_SOURCE, QUERY_SOURCE):` → `_no_route`. In `_no_route`, the `fallback` sentence when the source is enabled becomes `"Deezer offered nothing to fall back on"` (the old wording assumed the source was down).

- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check src tests` — PASS.
- [ ] **Step 5: Commit, PR** (`feat/69-open-the-gate`, label `enhancement`, "Part of #69 (1/3)").

### Task 13: Rules become rankers; the three settings go

**Files:**
- Modify: `src/flackey/lossless.py` (`RULES = HARD_RULES`, `RANKERS = IDENTITY_RANKERS + PEER_RANKERS`, delete `rule_duration`, `rule_title`, `rule_version`, `rule_artist`, `PickPolicy.duration_tolerance_s/title_ratio/require_artist`, `policy_from_settings`, the `Reference.durations` docstring), `src/flackey/config.py` (delete `lossless_duration_tolerance_s`, `lossless_title_ratio`, `lossless_require_artist`), `src/flackey/web/library.py:165-171`, `web/src/api.ts:46-47`, `web/src/components/SettingsPage.tsx:330-345`, `web/src/screenshot-harness.tsx:34`
- Test: `tests/test_lossless.py` (rule tests for duration/title/version/artist become the ranker tests from Task 6; `policy_from_settings` test), `tests/test_config.py`, `tests/test_web.py` (settings payload), `web/src` vitest snapshots if any

- [ ] **Step 1: Failing tests**

```python
def test_default_gate_is_hard_rules_then_identity_rankers():
    from flackey.lossless import RANKERS, RULES
    assert [n for n, _ in RULES] == ["extension", "has_length", "plausible_size", "queue", "banned_user"]
    assert [n for n, _ in RANKERS][:4] == ["version", "duration", "title", "artist"]


def test_a_far_off_length_survives_and_ranks_last():
    report = pick([mk(username="far", length_s=1479), mk(username="near", length_s=442)], REF, PickPolicy())
    assert not report.rejections and [f.username for f in report.survivors] == ["near", "far"]


def test_policy_from_settings_carries_only_what_is_left():
    p = policy_from_settings(Settings(_env_file=None, lossless_max_queue=3))
    assert p == PickPolicy(max_queue_length=3)
```

(Apply the Task 7 ranker-order decision to the first assertion.) `tests/test_config.py`: any test naming the three settings is deleted. `tests/test_web.py`: the `/api/settings` `ranking` payload becomes `{"max_picks", "max_queue", "fingerprint_min"}`.

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement.** In `lossless.py`: `RULES: list[tuple[str, Rule]] = HARD_RULES`, `RANKERS: list[tuple[str, Ranker]] = IDENTITY_RANKERS + PEER_RANKERS` (with the Task 7 order inside `IDENTITY_RANKERS`); delete the four rule functions; `PickPolicy` keeps `lossless_extensions`, `max_queue_length`, `banned_users`; `policy_from_settings(settings)` returns `PickPolicy(max_queue_length=settings.lossless_max_queue)`; `Reference.durations` docstring: "Every length this recording is known by, newest evidence first. `rank_duration` measures against the nearest; nothing rejects on it (issue #69)." Module docstring: "Rules are the cheap filters that keep files that can never satisfy the goal (lossy, implausible) from being downloaded; identity is ordered by the rankers and proven by the fingerprint after the download."

`config.py`: delete the three fields. `web/library.py`: the `ranking` dict keeps `max_picks`, `max_queue`, `fingerprint_min`. `api.ts`: `export interface Ranking { max_picks:number; max_queue:number|null; fingerprint_min:number }`. `SettingsPage.tsx`: the ranking sentence becomes `Files a peer offers are tried nearest the video's length first{s.ranking.max_queue != null ? ` (queues longer than ${s.ranking.max_queue} are skipped)` : ''}; the recording check decides.` `screenshot-harness.tsx:34`: `ranking: { max_picks: 4, max_queue: null, fingerprint_min: 0.9 }` (use the Task 7 threshold).

- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check src tests && uv run lint-imports && npm --prefix web test -- --run && npm --prefix web run build` — PASS.
- [ ] **Step 5: Commit, PR** (`feat/69-rankers`, label `enhancement`, "Part of #69 (2/3). Replay report: docs/research/…").

### Task 14: Spellings in sequence; tags follow the search when there is no record

**Files:**
- Modify: `src/flackey/worker.py` (`SECOND_SEARCH_AFTER`, `LosslessHit.reference`, `_search_references`, `_try_lossless`, `_check_and_convert` return, `_fetch_verify_file` retag block)
- Test: `tests/test_worker_lossless.py` (`FakeProvider.files_for`)

**Interfaces:**
- Produces: `worker.SECOND_SEARCH_AFTER = {"no_pick", "fingerprint_failed"}`; `LosslessHit.reference: Reference`; `Worker._search_references(req, cand, catalog) -> list[Reference]`.

- [ ] **Step 1: Failing tests.** Extend `FakeProvider.__init__` with `files_for: Callable[[str], list] | None = None` and in `search`: `files = self.files_for(text) if self.files_for else self.files`. Then:

```python
async def test_a_wrong_catalogue_record_is_overruled_by_the_second_search(lenv, tmp_path: Path, monkeypatch):
    """The Asteroids case with no Deezer record: Beatport matched a different act with the same title. The
    catalogue's spelling finds that act's file, the fingerprint rejects it, the request's own words find the
    right one, and the tags come from the request rather than the wrong record."""
    settings, store, _, _, fake_check, _ = lenv
    wrong = _flac(tmp_path / "wrong.flac")
    right = _flac(tmp_path / "right.flac")
    provider = FakeProvider(settings.slskd_downloads, audio={"w": wrong, "r": right},
                            files_for=lambda text: [lf("w", path="x\\Outputmessage - Asteroids.flac")]
                            if text.startswith("Outputmessage") else [lf("r", path="x\\Universal Sound - Asteroids.flac")])
    results = iter([FingerprintResult("failed", 0.6, 1.0, "best score 0.60 below 0.90"),
                    FingerprintResult("matched", 0.97, 1.0, "ok", reference="youtube:abc")])
    async def fake_check_seq(path, reference, *, minimum, missing=""):
        return next(results)
    monkeypatch.setattr(worker_mod, "fingerprint_check", fake_check_seq)
    ct = CatalogTrack(**{**CT3.__dict__, "artist": "Outputmessage", "title": "Asteroids"})
    w = make(lenv, provider=provider, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([ct]))
    rid = store.add_request("Universal Sound - Asteroids", RequestKind.YT_TRACK,
                            query=Query(raw="", artist="Universal Sound", title="Asteroids", duration_s=3))
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    assert provider.searches == ["Outputmessage Asteroids", "Universal Sound Asteroids"]
    t = store.get_track(r.track_id)
    assert (t.artist, t.title) == ("Universal Sound", "Asteroids") and t.catalog_track_id < 0
    assert store.get_request(rid).catalog_track_id == t.catalog_track_id
    assert sorted(a.outcome for a in store.list_attempts(limit=10)) == ["filed", "fingerprint_failed"]


async def test_no_second_search_when_the_words_are_the_same(lenv):
    _, store, _, provider, _, _ = lenv
    provider.files = []
    w = make(lenv, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([CT3]))
    rid = store.add_request("Astral Projection - Into the Void", RequestKind.YT_TRACK,
                            query=Query(raw="", artist="Astral Projection", title="Into the Void", duration_s=3))
    await w.process(rid)
    assert provider.searches == ["Astral Projection Into the Void"]


async def test_a_chosen_record_keeps_its_tags_whichever_spelling_found_the_file(lenv, tmp_path: Path, monkeypatch):
    """With a record the tags are the record's, even when the request's own words found the file."""
    settings, store, _, _, fake_check, _ = lenv
    right = _flac(tmp_path / "right.flac")
    provider = FakeProvider(settings.slskd_downloads, audio={"r": right},
                            files_for=lambda text: [] if text.startswith("Astral") else [lf("r", path="x\\AP - Into the Void.flac")])
    w = make(lenv, provider=provider, catalog=FakeCatalog([]))
    rid = store.add_request("AP - Into the Void", RequestKind.YT_TRACK,
                            query=Query(raw="", artist="AP", title="Into the Void", duration_s=3))
    r = await w.process(rid)
    assert r.state == RequestState.DONE and provider.searches == ["Astral Projection Into the Void", "AP Into the Void"]
    assert store.get_track(r.track_id).artist == "Astral Projection"          # good_cand()'s record, not the request's words
```

- [ ] **Step 2: Run** — FAIL (one search).

- [ ] **Step 3: Implement**

```python
# After these, the next spelling gets a search of its own (issue #69). Both say this spelling found nothing
# that is the recording; neither says anything about the other spellings.
SECOND_SEARCH_AFTER = {"no_pick", "fingerprint_failed"}
```

`LosslessHit` gains `reference: Reference` (last field); `_check_and_convert` returns `LosslessHit(out, verdict, fp, rec.provider, file.extension, rec.id, ref)`.

```python
    def _search_references(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> list[Reference]:
        """What to type into the network, in order: Beatport's spelling (clean, and for a channel-name artist
        such as `ShpongleMusic` the only text that finds anything), then the chosen record's, then the
        request's own words -- each only when its tokens differ from what came before. A wrong Beatport
        record redirects the first search to a different track (`Power Of Celtic` was searched as `The Power
        Of The Dark Side`); the fingerprint rejecting that search is what lets the next spelling run."""
        refs: list[Reference] = []
        if catalog is not None:
            refs.append(self._reference(req, catalog_candidate(catalog), catalog))
        if cand.source not in (CATALOG_SOURCE, QUERY_SOURCE):
            refs.append(self._reference(req, cand, catalog))
        query = req.query()
        if query.artist and query.title:
            refs.append(self._reference(req, query_candidate(query, req.id), None))
        seen: list[set[str]] = []
        out: list[Reference] = []
        for ref in refs:
            tokens = set(norm(search_text(ref)).split())
            if tokens in seen:
                continue
            seen.append(tokens)
            out.append(ref)
        return out or [self._reference(req, cand, catalog)]
```

(`norm` from `.models`.) `_try_lossless(self, req, cand, catalog, acoustic)`:

```python
        try:
            refs = self._search_references(req, cand, catalog)
            policy = policy_from_settings(self.settings)
        except Exception:
            log.exception("req#%d could not build a lossless reference/policy", req.id)
            return None
        for ref in refs:
            query = search_text(ref)
            outcome: str | None = None
            for provider in self.providers:
                rec = None
                try:
                    rec = AttemptRecorder(self.store, self.settings.lossless_raw_dir, req.id, provider.name, query,
                                          clock=self.clock)
                    self.store.update_request(req.id, fetch_source=provider.name)
                    hit = await self._attempt(provider, rec, req, ref, policy, acoustic)
                except Exception as e:
                    ...unchanged...
                finally:
                    self._publish_progress(req.id, None)
                if hit is not None:
                    return hit
                if rec is not None:
                    outcome = self.store.get_attempt(rec.id).outcome
            if outcome not in SECOND_SEARCH_AFTER:
                break
        return None
```

`_fetch_verify_file`, right after `hit = await self._try_lossless(...)`:

```python
        if hit is not None and hit.reference.from_query and catalog is not None and cand.source in (CATALOG_SOURCE, QUERY_SOURCE):
            # No record vouches for this file: Beatport's spelling found nothing the recording check
            # accepted and the owner's own words did. The Beatport record is not this recording, so it must
            # not name the file either. (With a chosen Deezer record the record's tags stand.)
            log.info("req#%d: filing on the request's words; the Beatport match was a different recording", req.id)
            catalog = None
            cand = query_candidate(req.query(), req.id)
            self.store.update_request(req.id, catalog_track_id=None)
```

(`_verify_and_file` then builds `_fallback_catalog(cand)` and writes `catalog_track_id` as it already does.) Note `_lossless_miss_line` and `_no_route` read `get_attempt_for_request` (the latest attempt): after several attempts that is the last spelling's, which is the one the owner should hear about.

- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check src tests` — PASS.
- [ ] **Step 5: Commit, PR** (`feat/69-spelling-sequence`, label `enhancement`, "Closes #69 (3/3)").

---

### Task 15: Retry on our own — wait for Soulseek, retry the video audio

**Files:**
- Modify: `src/flackey/worker.py` (constants after `SLOW_RETRY_OUTCOMES`; `_retry_or_fail`; new `_wait_for_soulseek`; the tail of `_no_route`; `_video_reference` except clauses; the `_video_reference` call in `_process`), `web/src/components/download/RequestRow.tsx` (`RetryCountdown`), `web/src/presentation.ts` (the `queued` + `retry_after` branch)
- Test: `tests/test_worker_lossless.py`, `tests/test_worker.py`, `web/src/components/download/RequestRow.test.tsx`, `web/src/presentation.test.ts`

**Interfaces:**
- Produces: `worker.LONG_RETRY_OUTCOMES`, `worker.LONG_RETRY_EVERY_S = 6 * 3600`, `worker.LONG_RETRY_TIMES = 12`; `Worker._retry_or_fail(req, reason, *, flag=None, wait_s=None, long_after: str | None = None)`; `Worker._wait_for_soulseek(req, why, attempts)`.
- Consumes: `Worker._video_reference` (Task 4), the `_process` identification block (Task 10), `_no_route` (Task 12 wording), `Request.lossless_retry` and `_lossless_allowed` (existing).

Why: Soulseek is a population, not a library. The search for "Space Dwarfs" found nothing at 11:40 on 2026-09-10 and two copies at 14:51. Today the whole budget (3 attempts, 30 s / 120 s, 15 min after a queue or no-pick) is spent within half an hour, then the row waits for a button press. And the video's audio, which now identifies the record, is fetched by yt-dlp, which fails for a minute far more often than for good.

- [ ] **Step 1: Failing tests**

`tests/test_worker_lossless.py` (add `from datetime import UTC, datetime, timedelta` to its imports):

```python
async def test_a_transient_soulseek_miss_waits_and_looks_again(lenv):
    """After the quick ladder, a miss that can change as people come online waits LONG_RETRY_EVERY_S in
    `queued` and really searches again, LONG_RETRY_TIMES times, before it parks."""
    _, store, notifier, provider, _, _ = lenv
    provider.files = []                                                   # no_pick on every pass
    w = make(lenv, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([CT3]))
    rid = store.add_request("A - B", RequestKind.YT_TRACK, query=Query(raw="", artist="A", title="B", duration_s=3))
    store.update_request(rid, attempts=worker_mod.MAX_ATTEMPTS - 1)      # the quick ladder is spent
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.lossless_retry == 1 and r.attempts == worker_mod.MAX_ATTEMPTS
    assert r.flag_reason.startswith("waiting for Soulseek: nothing on Soulseek matched")
    wait = datetime.fromisoformat(r.retry_after) - datetime.now(UTC)
    assert timedelta(hours=5, minutes=59) < wait <= timedelta(hours=6)
    assert notifier.sent[-1][0].startswith("Waiting for Soulseek")
    store.update_request(rid, retry_after=None)                           # 6 h later
    r = await w.process(rid)
    assert len(provider.searches) == 2 and r.state == RequestState.QUEUED
    assert r.attempts == worker_mod.MAX_ATTEMPTS + 1 and r.lossless_retry == 1
    assert not notifier.sent[-1][0].startswith("Waiting for Soulseek")    # said once, when the ladder handed over


async def test_the_long_wait_gives_up_after_its_budget(lenv):
    _, store, notifier, provider, _, _ = lenv
    provider.files = []
    w = make(lenv, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([CT3]))
    rid = store.add_request("A - B", RequestKind.YT_TRACK, query=Query(raw="", artist="A", title="B", duration_s=3))
    store.update_request(rid, attempts=worker_mod.MAX_ATTEMPTS + worker_mod.LONG_RETRY_TIMES - 1, lossless_retry=1)
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and "looked 12 times over 72 h" in r.error_message
    assert "Use Try again" in r.error_message and notifier.sent[-1][0].startswith("Gave up")


async def test_a_wrong_recording_everywhere_skips_the_quick_ladder(lenv):
    """`fingerprint_failed` is a verdict the next minute would repeat: no 30 s retry, straight to the wait."""
    _, store, _, provider, fake_check, _ = lenv
    fake_check.result = FingerprintResult("failed", 0.5, 1.0, "best score 0.50 below 0.90")
    w = make(lenv, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([CT3]))
    rid = store.add_request("A - B", RequestKind.YT_TRACK, query=Query(raw="", artist="A", title="B", duration_s=3))
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == worker_mod.MAX_ATTEMPTS
    assert r.flag_reason.startswith("waiting for Soulseek: the copies offered were a different recording")


async def test_a_permanent_miss_still_parks_at_once(lenv):
    """No reference to check against: waiting changes nothing."""
    _, store, _, provider, fake_check, _ = lenv
    fake_check.result = FingerprintResult("skipped", None, None, "no video and no Deezer id to fingerprint against")
    w = make(lenv, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([CT3]))
    rid = store.add_request("A - B", RequestKind.YT_TRACK, query=Query(raw="", artist="A", title="B", duration_s=3))
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and attempt_of(store, rid).outcome == "fingerprint_unavailable"
```

`tests/test_worker.py`:

```python
async def test_video_audio_failure_retries_before_the_text_path(env, monkeypatch):
    """The video's audio identifies the record, so a yt-dlp blip earns the quick ladder; after it the pass
    goes on by text (checked by the preview on download), so a removed video cannot block the request."""
    settings, store, notifier = env
    calls = []
    async def flaky_youtube(url, tmp_dir, *, duration_s=None):
        calls.append(url)
        raise YouTubeError("HTTP Error 429: Too Many Requests")
    monkeypatch.setattr(worker_mod, "youtube_reference", flaky_youtube)
    w = Worker(store, FakeSource([good_cand()]), FakeCatalog([CT]), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1 and r.flag_reason == "video audio unavailable, will retry"
    assert "Retrying in 30 s" in notifier.sent[-1][0] and "429" in notifier.sent[-1][0]
    store.update_request(rid, retry_after=None)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 2
    store.update_request(rid, retry_after=None)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and len(calls) >= 3 and store.get_reference(rid)["kind"] == "deezer"
```

Update `test_without_a_video_reference_text_still_decides` (Task 10): add `store.update_request(rid, attempts=worker_mod.MAX_ATTEMPTS - 1)` before `w.process(rid)` with the comment `# the quick ladder is spent: the pass goes on by text`.

`web/src/components/download/RequestRow.test.tsx`: next to the existing `retryInSeconds: 61` case, a case with `retryInSeconds: 21600` expecting the text `Retry in 6h 0m`. `web/src/presentation.test.ts`: a queued row with `retry_after` in the future and `flag_reason: 'waiting for Soulseek: nothing on Soulseek matched this track closely enough; 11 more looks, one every 6 h'` expects `v.status` to be `Waiting for Soulseek: nothing on Soulseek matched this track closely enough; 11 more looks, one every 6 h` and `v.retryInSeconds` set.

- [ ] **Step 2: Run** `uv run pytest tests/test_worker_lossless.py tests/test_worker.py -q -k "waits_and_looks or gives_up_after or skips_the_quick_ladder or parks_at_once or retries_before_the_text" && npm --prefix web test -- --run` — FAIL.

- [ ] **Step 3: Implement the worker**

Constants, after `SLOW_RETRY_OUTCOMES`:

```python
# Outcomes whose answer changes only as the people online turn over. Soulseek is a population, not a
# library: the search for "Space Dwarfs" found nothing at 11:40 on 2026-09-10 and two copies at 14:51.
# After the quick ladder (RETRY_BACKOFF_S) a request that ended in one of these waits LONG_RETRY_EVERY_S in
# `queued` and looks again, LONG_RETRY_TIMES times, before it parks in `error`. `fingerprint_failed` is
# here although it is a verdict: every copy offered was a different recording, which says nothing about
# the copies tomorrow's peers will offer. Not here: a missing reference or nothing to search for, which no
# amount of waiting changes.
LONG_RETRY_OUTCOMES = {"no_pick", "fingerprint_failed", "transfer_failed", "first_byte_timeout",
                       "transfer_timeout", "queued", "unavailable", "interrupted"}
LONG_RETRY_EVERY_S = 6 * 3600
LONG_RETRY_TIMES = 12            # 3 days at 6 h
```

`_retry_or_fail` and the new method:

```python
    async def _retry_or_fail(self, req: Request, reason: str, *, flag: str | None = None,
                             wait_s: int | None = None, long_after: str | None = None) -> None:
        """The quick ladder (RETRY_BACKOFF_S) while attempts < MAX_ATTEMPTS, then `error` -- or, when
        `long_after` names why the answer may change, the long wait for Soulseek first."""
        attempts = req.attempts + 1
        if attempts < MAX_ATTEMPTS:
            wait = RETRY_BACKOFF_S[min(attempts, len(RETRY_BACKOFF_S)) - 1] if wait_s is None else wait_s
            retry_after = (datetime.now(UTC) + timedelta(seconds=wait)).isoformat(timespec="seconds")
            self._set_state(req, RequestState.QUEUED, attempts=attempts, flag_reason=flag or reason,
                            retry_after=retry_after)
            await self.notifier.send(f"Attempt {attempts} failed for {req.raw_text}: {reason}\nRetrying in {wait} s.")
        elif long_after:
            await self._wait_for_soulseek(req, long_after, attempts)
        else:
            self._set_state(req, RequestState.ERROR, attempts=attempts, error_message=reason)
            await self.notifier.send(f"Gave up after {attempts} attempts: {req.raw_text}\n{reason}")

    async def _wait_for_soulseek(self, req: Request, why: str, attempts: int) -> None:
        """Park the request in `queued` for LONG_RETRY_EVERY_S with `lossless_retry` granted, so the next
        pass really searches whatever the last outcome was (`_lossless_allowed`); or in `error` once the
        LONG_RETRY_TIMES looks are spent. Told to the owner once, when the quick ladder hands over; the row
        shows the countdown and the reason meanwhile. `attempts` keeps counting past MAX_ATTEMPTS so the
        budget survives a restart: it lives on the row, not in memory."""
        hours = LONG_RETRY_EVERY_S // 3600
        if attempts >= MAX_ATTEMPTS + LONG_RETRY_TIMES:
            reason = (f"{why}; looked {LONG_RETRY_TIMES} times over {LONG_RETRY_TIMES * hours} h. "
                      f"Use Try again in the app to search once more")
            self._set_state(req, RequestState.ERROR, attempts=attempts, error_message=reason)
            await self.notifier.send(f"Gave up: {req.raw_text}\n{reason}")
            return
        retry_after = (datetime.now(UTC) + timedelta(seconds=LONG_RETRY_EVERY_S)).isoformat(timespec="seconds")
        left = MAX_ATTEMPTS + LONG_RETRY_TIMES - attempts
        self._set_state(req, RequestState.QUEUED, attempts=attempts, retry_after=retry_after, lossless_retry=1,
                        flag_reason=f"waiting for Soulseek: {why}; {left} more looks, one every {hours} h")
        if attempts <= MAX_ATTEMPTS:
            await self.notifier.send(f"Waiting for Soulseek: {req.raw_text}\n{why}. Looking again every {hours} h "
                                     f"for {LONG_RETRY_TIMES * hours // 24} days.")
```

The tail of `_no_route`, from `reason = f"no way to fetch this track: ..."` on:

```python
        reason = f"no way to fetch this track: {why}, {fallback}"
        transient = outcome in LONG_RETRY_OUTCOMES
        if self._lossless_allowed(self.store.get_request(req.id)):
            await self._retry_or_fail(req, reason, wait_s=SLOW_BACKOFF_S if outcome in SLOW_RETRY_OUTCOMES else None,
                                      long_after=why if transient else None)
            return
        if transient:
            # A verdict the next minute would only repeat (every copy was a different recording, every peer
            # refused): no quick ladder, straight to the wait for the people online to change.
            await self._wait_for_soulseek(req, why, max(req.attempts + 1, MAX_ATTEMPTS))
            return
        reason += ". Use Try again in the app to search once more"
        self._set_state(req, RequestState.ERROR, attempts=req.attempts + 1, error_message=reason)
        await self.notifier.send(f"Could not fetch: {req.raw_text}\n{reason}")
```

`_video_reference` (Task 4): split the except clause so the two failures read differently:

```python
        except YouTubeError as e:
            log.warning("req#%d: no video reference: %s", req.id, e)
            return None, f"video: {e}"            # transient until proven otherwise: _process retries
        except FingerprintError as e:
            log.warning("req#%d: video audio unreadable: %s", req.id, e)
            return None, f"video audio: {e}"      # the audio came and could not be read: no retry
```

`_process` (Task 10's block), right after `video, _ = await self._video_reference(req)` becomes `video, why = ...`:

```python
            if video is None and why.startswith("video: ") and req.attempts + 1 < MAX_ATTEMPTS:
                # yt-dlp fails for a minute (429, a network blip) far more often than for good, and the
                # video's audio is what identifies the record: a short wait beats choosing by text. After the
                # ladder the pass goes on without it, so a removed video cannot block the request.
                await self._retry_or_fail(req, f"could not fetch the video's audio: {why[7:]}",
                                          flag="video audio unavailable, will retry")
                return
```

- [ ] **Step 4: Implement the web side**

`RequestRow.tsx` `RetryCountdown`:

```tsx
  const label = remaining <= 0 ? 'Retrying now' : remaining >= 3600
    ? `Retry in ${Math.floor(remaining / 3600)}h ${Math.floor((remaining % 3600) / 60)}m`
    : remaining >= 60
      ? `Retry in ${Math.floor(remaining / 60)}m ${remaining % 60}s`
      : `Retry in ${remaining}s`
```

`presentation.ts`, in the `case 'queued':` / `if (r.retry_after)` branch, before `v.status = \`Previous attempt: ${reason}\``:

```ts
        if (/^waiting for Soulseek/i.test(r.flag_reason || '')) {
          v.status = r.flag_reason!.replace(/^waiting/, 'Waiting')
          v.statusTone = 'amber'
          break
        }
```

- [ ] **Step 5: Run** `uv run pytest -q && uv run ruff check src tests && npm --prefix web test -- --run && npm --prefix web run build` — PASS.
- [ ] **Step 6: Commit, PR** (`feat/retry-on-our-own`, label `enhancement`, "Closes <the retry issue from Task 0>").

---

## Phase 7 — Acceptance

### Task 16: Re-queue the stuck requests and report

**Files:**
- Create: `docs/research/2026-MM-DD-requeue-results.md`

- [ ] **Step 1: Tell the owner** it is about to start (34 distinct videos plus requests 71 and 72; real Soulseek traffic, up to four transfers per spelling) and wait for a yes.

- [ ] **Step 2: With the app quit, mark them queued** — one per distinct video, the newest request of each:

```bash
uv run python - <<'EOF'
from flackey.config import load_settings
from flackey.models import RequestState
from flackey.store import Store

store = Store(load_settings().db_path)
stuck = store.list_requests({RequestState.NOT_FOUND, RequestState.CANCELLED, RequestState.ERROR}, limit=100_000)
newest: dict[str, int] = {}
for r in sorted(stuck, key=lambda r: r.id):
    newest[r.source_url or r.raw_text] = r.id
for rid in sorted(newest.values()):
    store.update_request(rid, state=RequestState.QUEUED, attempts=0, retry_after=None, error_message=None,
                         flag_reason=None, chosen_candidate_id=None, catalog_track_id=None, confidence=None,
                         lossless_retry=1)
print(len(newest), "re-queued:", sorted(newest.values()))
EOF
```

- [ ] **Step 3: Start the app** (`uv run flackey start --no-browser` or the installed app) and let the queue drain (`flackey status` until nothing is queued / fetching; expect 30–90 min).

- [ ] **Step 4: Write the report** from the live database:

```bash
sqlite3 "$HOME/Library/Application Support/flackey/flackey.sqlite" "select r.id, r.state, r.raw_text, r.query_duration_s, r.confidence, a.outcome, json_extract(a.fingerprint_json,'$.score'), r.error_message from requests r left join lossless_attempts a on a.id=(select max(id) from lossless_attempts where request_id=r.id) where r.id in (<the ids from step 2>) order by r.state, r.id;"
```

Table columns: request, state, text, video s, confidence (the identification score for YouTube requests), last attempt outcome, fingerprint score, reason. Below it: counts per state; the probe's 16 tracks with whether each filed; every `fingerprint_failed` with the file name that was rejected (from `report_json` / timeline); every `not_found` and `error` with its reason.

- [ ] **Step 5: Commit the report** (`docs/`, label `ignore-for-release`), comment the summary on #61 and on the re-queue issue, close the re-queue issue. Leave #61 open for the owner to close after reading.

- [ ] **Step 6: Open the epic PR to `main`** with `gh pr create --base main --head epic/61-search-correctness --label enhancement`, its body listing every task PR by number and the summary lines from the report. The owner A/B tests the epic build against `main` and merges it with `gh pr merge --merge` (a merge commit, never squash), so each task's squash commit stays revertable on its own.

---

## Self-review (done while writing)

- Spec coverage: the one rule → Tasks 4, 10, 11; #66 → 2; #63 → 3; #67 (reference, persisted, annotate-only) → 4; sweep with both scores + threshold + identification agreement → 5, 7; replay + ranker order → 6, 7, 13; #68 (identification, `fingerprint_unavailable`, review removed, lossy checked) → 10, 8, 9, 11; gate + bare artist → 12; rankers + settings removal → 13; spelling sequence + tags rule → 14; retries (long wait, video audio) → 15; #70 → 1; re-queue → 16; issue rewrite → 0; epic branch → Global Constraints, Task 0 Step 1, Task 16 Step 6; "not built" list → Global Constraints.
- Names used across tasks: `TITLE_FLOOR` (3), `AcousticReference(kind, ref, needles, full, excerpt_start_s, excerpt_s)` (4, 5, 10), `check(path, reference, *, minimum, missing)` (4, 5, 8, 11), `Acoustic` (4, 11, 14), `Worker._video_reference` / `_acoustic_reference` (4, 10), `youtube_reference(url, tmp_dir, *, duration_s)` (4, 5, 10), `deezer_needles` (4, 5, 10), `store.get_reference` / `set_reference` (4, 5, 10), `Identification` / `identify_record` / `IDENTIFY_MAX` (10, 12), `HARD_RULES` / `IDENTITY_RANKERS` / `PEER_RANKERS` (6, 13), `QUERY_SOURCE` / `query_candidate` / `Worker._reference` / `_search_on_the_request` (12, 14), `Reference.from_query` (12, 14), `LosslessHit.reference` (14), `fingerprint_unavailable` (8, 11), `LONG_RETRY_OUTCOMES` / `LONG_RETRY_EVERY_S` / `LONG_RETRY_TIMES` / `_wait_for_soulseek` / `_retry_or_fail(long_after=)` (15), the `video: ` / `video audio: ` prefixes of `_video_reference` (4, 15).
- Placeholders: none. Every code step has its code. The two places that say "check the real body first" (`_version_agrees` in Task 6, the `Worker(...)` constructor in Task 10) name the exact thing to read.
