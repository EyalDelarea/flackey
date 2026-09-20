# Search quality review — issue #61 and what it leaves out

Reviewed against the live database (207 rows / 143 distinct requests), live Beatport probes, and a live
Soulseek probe through the running slskd instance. Counts below are **distinct requests**; row counts
appear in parentheses where they differ.

## Verdict on #61

Keep it. The evidence is real and the two headline fixes are correct. But #61 is a **precision** issue:
it fixes 2 wrong files. The measured loss in this library is **recall** — 27 distinct requests that
produced no file at all. Same goal, ~13x the volume, and #61 does not touch it.

Two corrections to its content, both measured below:
- Its **"measured, then cut"** call on the parser fixes is **right**, and now confirmed on the leg it
  never tested (Beatport, not just Deezer). Keep them cut.
- Its **#1 title floor is half a fix.** The same defect exists mirror-imaged on the artist side, and the
  floor does not block it. Confirmed live on one of the six wrong matches #61 lists as its own evidence.

---

## 1. The dominant failure is recall, and the cause is a gate

| outcome | distinct | rows |
|---|---|---|
| `done` | — | 110 |
| `not_found` | **17** | 50 |
| `cancelled` (owner skipped a review) | **10** | 27 |
| wrong file filed (#61's subject) | **2** | 2 |

Every one of the 50 `not_found` rows carries `error_message = 'no Deezer results'`. In `worker.py` that
message is only reachable when **Beatport also returned nothing**. 26 of the 27 cancellation rows read
`not found on Beatport; information cannot be verified` — four of them at confidence 100.

Two independent queries agree on the mechanism: 50 `not_found` + 26 Beatport-less cancellations = 76,
which is exactly the `catalog_track_id IS NULL` row count. Beatport has no match for **29 of 143
distinct requests (20%)**.

This library is 1990s Goa/psytrance. Deezer and Beatport are weak on it by nature. The pipeline makes
both a mandatory gate in front of the only source that actually has the music.

### The probe that settles it

I ran the 16 distinct never-fetched tracks against the live Soulseek network, taking the **closest**
lossless file by duration in each case. All 16 returned lossless files with length metadata:

| request (declared "not found") | closest lossless | asked | Δ |
|---|---|---|---|
| Sheyba – Ganesh | 431 | 431 | **0** |
| Slinky Wizard – Funkus Munkus | 522 | 522 | **0** |
| Infinity Project – Hyperspaced [Doof Remix] | 431 | 431 | **0** |
| Tufáan – Probe (Green Nuns Rmx) | 487 | 487 | **0** |
| Cydonia – Animals | 416 | 416 | **0** |
| Johann Bley – Stranded (The Delta remix) | 491 | 491 | **0** |
| Witchcraft – Magic Frequencies | 459 | 460 | 1 |
| New Born – Circus Is Open | 282 | 283 | 1 |
| Psycho Train (Red Headed) | 557 | 556 | 1 |
| Slinky Wizard – Sacred Fist | 508 | 511 | 3 |
| Universal Sound – Asteroids | 382 | 386 | 4 |
| Man With No Name – Silicon Trip | 412 | 416 | 4 |
| Space Tribe – Flipout the Dolphin (Voodoo Edit) | 440 | 446 | 6 |
| Duvdev & Shidapu – Devil Rmx | 560 | 566 | 6 |
| Etnica – "The Italian EP" | 541 | 1479 | 938 |
| Oforia (bare artist, no title) | 506 | 599 | 93 |

**Within 3 s: 10/16. Within 10 s: 14/16.**

The two misses are not matching failures. *The Italian EP* is a 24-minute full-EP upload — a request
that is not a track at all, and deserves its own handling. *Oforia* is a bare artist name with no title
(the parser took the channel as the artist); there is nothing to match against.

The music was there every time. The app never asked, because a metadata broker said no first.

---

## 2. The tolerance regime changes when the gate opens — #61's negative result does not transfer

#61 has a section titled **"Negative result: don't widen the lossless duration tolerance"**
(`4-10s: 0` rejections, "attempts whose closest lossless file was within 10s: 0"). That measurement is
sound **in its own regime** and must not be carried into this one.

Those attempts all passed the catalog gate, so the reference was a Beatport track length — which #61
itself notes is accurate to 0–1 s. Open the gate and the reference becomes the **YouTube duration**,
which carries intro/outro and sits 4–6 s off routinely (the table above). At
`PickPolicy.duration_tolerance_s = 3` that costs 4 of the 14 reachable tracks.

So: keep 3 s when the reference is a catalog duration; a video-derived reference needs its own window
(~10 s). The existing `Reference.durations` two-window design already accommodates exactly this — each
duration keeps its own tolerance rather than widening into a range.

---

## 3. The identity check that would justify dropping the gate is switched off

`fingerprint.check` (Chromaprint vs the Deezer 30 s preview) is the only thing in the pipeline that
proves *this audio is the audio asked for*. Its record:

- `matched` **45** — scores 0.91–1.00, clean separation
- `skipped` **45** — **all 45 for the same reason: `no deezer id for this request`**
- `failed` **0** — it has never once rejected a file

It works, and it is dark on exactly the half of downloads that come from the catalog-only Soulseek path
(89 of 110 filed tracks are Soulseek AIFF). The pipeline gates hard on weak evidence (catalog text
similarity) and treats strong evidence (audio fingerprint) as optional and post-hoc.

`youtube.py` runs yt-dlp with `skip_download: True` — metadata only. yt-dlp is already a dependency, so
the request's own audio can become the fingerprint reference. That reference needs no Deezer id and no
Beatport record, and exists for **every** YouTube request, including all 17 currently declared not-found.

**Implementation constraint:** `fingerprint.compare` returns `0.0` when `len(hay) < len(needle)`. A
531 s video against a 506 s file is needle > haystack and fails instantly. Take a ~30 s excerpt from the
middle of the YouTube audio as the needle, mirroring the Deezer preview's shape.

---

## 4. #61's title floor is half a fix — measured

#61 diagnoses "a weighted average lets a component at 100 carry a component at ~0". True, and both bad
files are artist=100 / title≈0. But the **mirror case is equally live**, and the title floor lets it
through. Live Beatport probe, `Universal Sound - Asteroids`, duration applied, returned by `best_match`:

```
avg=71.4  artistAgr= 42.9  titleAgr=100.0   Outputmessage - Asteroids   (dur 388 vs 386 -> no penalty)
```

A title floor of 75–80 does not touch it. The artist is a different act entirely.

On the count: of the six wrong matches #61 lists, only two still return a match at all under live
probing with duration applied (the rest now fall below 70 on their own). The title floor blocks one
(`Cydonia → Animal People`, titleAgr 60) and lets the other through (the one above). `Sheyba → SOHIER`
is **not** an artist-side escape — SOHIER is 277 s against a 431 s request, and the duration penalty
already rejects it today. The floor is still worth having; it is simply not sufficient alone.

**Caution before writing an artist floor.** `best_match` deliberately uses `token_sort_ratio` for artist
so "Hallucinogen In Dub" ≠ "Hallucinogen". That comparator scores the *correct* record
`Tryambaka, Shiva Shidapu` vs `Shiva Shidapu` at only **72.2**. A floor at 75 would reject a correct
collaboration credit. The observed gap is 42.9 (wrong) vs 72.2 (right), so something near 60 fits — but
this needs the same distribution check #61 ran for titles, not a guessed number.

### A second, quieter mechanism

On `Shiva Shidapu - Power Of Celtic` the **correct** Beatport record exists and ranks *first* on
artist+title (86.1). It loses because the video is 531 s and the release is 506 s — a 25 s gap costs the
full 25-point duration penalty, while the wrong record (529 s) pays nothing.

I tested the obvious fix and it is **wrong**: dropping the duration penalty makes this case worse
(`Marusha - Celtic` then wins on titleAgr=100 / artistAgr=50). Duration is load-bearing *because*
nothing currently constrains the artist. Fix the artist floor first, then re-measure duration.

---

## What I'd actually do

**Ship #61 as written, plus the artist floor.** #1 and #2 are correct and #1 blocks both bad files. Add
the artist-side floor in the same change — measure the distribution first, expect ~60 — or #61 ships a
fix for half of a symmetric defect and the other half gets rediscovered. Keep the parser fixes cut:
re-probing Beatport with corrected queries rescued **0 of 11** tracks (the one apparent "rescue" was
`Universal Sound → Outputmessage`, itself a wrong match).

**Then stop gating acquisition on the catalogs.** This is worth more than all of #61. Today "Beatport
has no record" is terminal; it should mean *unverified*, not *unavailable*. Search Soulseek anyway on
the parsed artist/title plus the YouTube duration, with a video-reference tolerance of ~10 s. That
reaches 14 of the 16 tracks that are provably sitting there right now, and it converts the 26
"cannot be verified" cancellations into real candidates.

**Give the fingerprint a reference that always exists.** Fingerprint a 30 s excerpt of the request's own
YouTube audio instead of the Deezer preview. It removes the 45 skips, it gives a real identity gate for
the un-cataloged tracks above so relaxing the gate stays safe, and it makes #53's preview clips a
confirmation step rather than the primary evidence.

Sequencing: artist floor with #61 → YouTube fingerprint reference → then open the gate, with the
fingerprint already in place to catch what the catalogs no longer filter.
