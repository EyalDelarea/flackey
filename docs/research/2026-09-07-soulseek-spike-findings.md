# Soulseek spike: live measurements against the library (2026-09-07)

Follow-up to [2026-09-06-soulseek-as-audio-source.md](2026-09-06-soulseek-as-audio-source.md).
That document is desk research; this one is what actually happened when slskd ran on this Mac and
was asked for the 20 tracks already in the library. Scripts and raw output are in
`spikes/soulseek/` (throwaway, not part of the app).

## Setup that was done

- slskd 0.26.0, native arm64 binary, lives in `~/Library/Application Support/Krater/slskd/`
  (binary, `slskd.yml`, `api_key`, `downloads/`, `incomplete/`, `slskd.log`, `data/`). Started by
  hand with `./slskd --app-dir <that folder>`; nothing auto-starts it yet.
- Fresh Soulseek account `krater-dj` (`krater` was already taken: the server answers
  "invalid username or password" for a taken name). Password and API key were generated and exist
  only in `slskd.yml`, which is mode 600 and outside the repo.
- Shares: `~/Music/DJ Library` read-only. slskd scanned it in 90 ms: 14 directories, 23 files.
- Web UI and API bound to `127.0.0.1:5030`, API key restricted to `127.0.0.1/32`.
- Listening port 50300, no router forward. The client still got a distributed-network parent
  (branch level 5) and full search results, so an unforwarded port did not block searching.
- No peer browsed or downloaded from us during the session (0 uploads).

## Search probe

Method: for each library track, query `first artist + title` (mix name appended when it is not an
original mix), fall back to title only; wait until slskd reports the search `Completed`; then
classify every returned file. "Good lossless" means extension flac/wav/aiff, reported length
within 3 s of the Beatport duration, and filename fuzzy-matching artist + title at 80 or better.

| # | Track | Query used | First resp | Peers | Files | Lossless | Good lossless | Good MP3-320 | Best lossless (user, slot, queue, speed, format) |
|---|---|---|---|---|---|---|---|---|---|
| 5 | Hallucinogen - L.S.D. (Original Mix) | Hallucinogen L.S.D. | 1.1 s | 72 | 120 | 48 | 2 | 1 | yencz, slot, q0, 814 kB/s, flac 44100/16 |
| 6 | Hallucinogen - Orphic Thrench (Original Mix) | Hallucinogen Orphic Thrench | 1.0 s | 131 | 157 | 63 | 21 | 18 | loginty, slot, q0, 7091 kB/s, flac 44100/16 |
| 7 | Sun Project - Space Dwarfs (Original Mix) | Sun Project Space Dwarfs | 1.5 s | 22 | 32 | 14 | 2 | 2 | lem11, slot, q8, 5378 kB/s, flac 44100/16 |
| 8 | Filteria - Made Out Of Trance (Original Mix) | Filteria Made Out Of Trance | 1.0 s | 16 | 15 | 8 | 6 | 2 | dogfoodwoman, slot, q0, 729 kB/s, flac 44100/16 |
| 10 | Astral Projection - Dreaming (Anything Can Happen) (Original Mix) | Astral Projection Dreaming | 1.0 s | 116 | 600 | 106 | 23 | 27 | SGVsbG8gSHVtYW4hCg==, slot, q0, 5840 kB/s, flac 44100/16 |
| 11 | Astral Projection - Nilaya (Original Mix) | Astral Projection Nilaya | 1.0 s | 115 | 202 | 48 | 10 | 11 | SGVsbG8gSHVtYW4hCg==, slot, q0, 5840 kB/s, flac 44100/16 |
| 12 | Talamasca - A Brief History Of Goa-Trance Etnica (Original Mix) | Talamasca A Brief History Of Goa-Trance Etnica | 2.6 s | 11 | 17 | 4 | 4 | 9 | s4th4n, slot, q14, 5132 kB/s, flac 44100/16 |
| 13 | Talamasca - A Brief History Of Goa-Trance Hallucinogen (Original Mix) | Talamasca A Brief History Of Goa-Trance Hallucinogen | 3.6 s | 11 | 17 | 4 | 4 | 9 | s4th4n, slot, q4, 5132 kB/s, flac 44100/16 |
| 14 | Goasia - Love & Peace (Original Mix) | Goasia Love & Peace | 2.1 s | 18 | 18 | 7 | 3 | 1 | hsetib, slot, q25, 7042 kB/s, flac 44100/16 |
| 15 | Goasia - Space Travellers (Original Mix) | Goasia Space Travellers | 1.5 s | 15 | 13 | 4 | 2 | 1 | hsetib, slot, q25, 7042 kB/s, flac 44100/16 |
| 16 | Mindsphere - Depth of Consciousness (Original Mix) | Mindsphere Depth of Consciousness | 1.0 s | 8 | 12 | 6 | 5 | 0 | web-graffiti, slot, q0, 4075 kB/s, flac 44100/16 |
| 17 | Astral Projection - People Can Fly (Original Mix) | Astral Projection People Can Fly | 1.0 s | 156 | 333 | 61 | 18 | 25 | SGVsbG8gSHVtYW4hCg==, slot, q0, 5840 kB/s, flac 44100/16 |
| 19 | Celestial Intelligence - Inevitable Feelings (Original Mix) | Celestial Intelligence Inevitable Feelings | 1.6 s | 7 | 10 | 5 | 5 | 1 | saintcom, no slot, q379, 422 kB/s, flac 44100/16 |
| 21 | Filteria - Dog Days Bliss (Album Edit) | Filteria Dog Days Bliss Album Edit | 1.1 s | 19 | 19 | 8 | 2 | 0 | hsetib, slot, q30, 7042 kB/s, flac 44100/16 |
| 22 | Hallucinogen In Dub - Angelic Particles (Original Mix) | Hallucinogen In Dub Angelic Particles | 1.0 s | 88 | 117 | 46 | 8 | 2 | OvenmittsForYears, slot, q0, 2453 kB/s, flac 44100/16 |
| 23 | Alien Art, Out of Orbit (Eitan Reiter) - Planet X (Out of Orbit remix) | Alien Art Planet X Out of Orbit remix | 1.0 s | 15 | 18 | 2 | 2 | 13 | GeorgeNu_, slot, q229, 7059 kB/s, flac 44100/16 |
| 24 | Dekel - Magneto (Original Mix) | Dekel Magneto | 1.5 s | 7 | 25 | 2 | 2 | 5 | Goa4life, no slot, q61, 528 kB/s, flac 44100/16 |
| 25 | Dekel - Oasis (Original Mix) | Dekel Oasis | 1.6 s | 2 | 5 | 1 | 1 | 0 | Nq, no slot, q498, 442 kB/s, flac 44100/16 |
| 26 | Ritmo - Organic Rhythm (Original mix) | Ritmo Organic Rhythm | 3.1 s | 16 | 22 | 8 | 7 | 7 | akarny, slot, q0, 1167 kB/s, flac 44100/24 |
| 27 | Rising Dust, Astrix - Universo (Original Mix) | Rising Dust Universo | 2.1 s | 57 | 98 | 22 | 10 | 29 | dogfoodwoman, slot, q0, 729 kB/s, aiff None/None |

Summary:

| Measure | Value |
|---|---|
| Tracks with at least one good lossless file | 20 of 20 |
| Tracks whose best lossless peer has a free slot and an empty queue | 10 of 20 |
| First peer response after posting the search | median 1.5 s, max 3.6 s |
| Search reported Completed (15 s idle after last response) | median 23 s, max 35 s |
| Files returned without a length attribute (excluded by the duration filter) | 423 of 1850 |

Observations:

- Every query, including the small-label 2018 to 2024 releases (Dekel, Ritmo, Alien Art, Rising
  Dust), found a 16-bit/44.1 kHz FLAC with the right duration. Ritmo also had a 24-bit copy.
- Classic Goa (Hallucinogen, Astral Projection) returns hundreds of files from over a hundred
  peers; the newer releases return 2 to 25 files from 2 to 20 peers.
- The artist + title query worked for every track once the search was allowed to complete. The
  earlier "title only" fallbacks in run 1 were an artefact of the wait bug below, not of the
  network.
- One fuzzy-match caveat: for "Dreaming (Anything Can Happen)" the best pick was the album cut
  "Still Dreaming (Anything Can Happen)", same duration. Token-set fuzzy matching at 80 accepts an
  extra word; the duration filter is what kept it plausible here. Later the same day the
  Chromaprint check scored this file 0.98 against the Deezer preview and Rekordbox analysed it to
  the same key and BPM as the MP3: it is the same recording under its album title, so accepting
  it was right.

## Download probe

Six of the best picks were enqueued at once (five with a free slot and empty queue, one with 26
in queue), then polled every 2 s. Each finished file was run through `krater.verify.verify`.

| # | Track | Peer | Queue | Transfer states (s) | Done in | Speed | verify | fmt/bitrate/cutoff | reason |
|---|---|---|---|---|---|---|---|---|---|
| 6 | Hallucinogen - Orphic Thrench | loginty | q0 | InProgress@3.9 -> Completed, Succeeded@18.2 | 18.2 s | 3297 kB/s | PASS | flac/927/22050 | lossless, content to 22050 Hz |
| 8 | Filteria - Made Out Of Trance | dogfoodwoman | q0 | Initializing@3.5 -> InProgress@11.7 -> Requested@13.7 -> Queued, Remotely@15.8 -> Queued, Locally@52.4 -> InProgress@60.5 -> Completed, Succeeded@201.7 | 201.7 s | 566 kB/s | PASS | flac/949/22050 | lossless, content to 22050 Hz |
| 10 | Astral Projection - Dreaming (Anything Can Happen) | SGVsbG8gSHVtYW4hCg== | q0 | Initializing@3.2 -> InProgress@5.3 -> Completed, Succeeded@50.1 | 50.1 s | 1060 kB/s | PASS | flac/811/22050 | lossless, content to 22050 Hz |
| 14 | Goasia - Love & Peace | hsetib | q26 | InProgress@2.9 -> Completed, Succeeded@29.4 | 29.4 s | 2142 kB/s | PASS | flac/958/22050 | lossless, content to 22050 Hz |
| 16 | Mindsphere - Depth of Consciousness | web-graffiti | q0 | Queued, Remotely@2.4 -> InProgress@4.4 -> Completed, Succeeded@47.2 | 47.2 s | 1505 kB/s | PASS | flac/1011/22050 | lossless, content to 22050 Hz |
| 26 | Ritmo - Organic Rhythm | akarny | q0 | Initializing@2.0 -> InProgress@4.1 -> Completed, Succeeded@73.2 | 73.2 s | 1461 kB/s | PASS | flac/1604/22050 | lossless, content to 22050 Hz |

Observations:

- Five of six transfers were moving within 5 s of enqueueing, and the "26 in queue" peer started
  in 3 s, so the queue length in a search response is not a reliable predictor of waiting.
- One peer that advertised a free slot bounced the request into its queue for a minute before
  serving it. A 60 s "time to first byte" cap would have dropped that one and taken the MP3.
- A verified 320 MP3 of a 7 to 10 minute track is 17 to 25 MB; these FLACs are 48 to 101 MB.
- All six passed the existing verify with a 22 050 Hz cutoff, which is the Nyquist limit, so none
  was a transcoded fake. The downloaded files are still in slskd's `downloads/` folder and have not
  been filed into the library.

## Integration rules learned (things the desk research got wrong or did not say)

- `searchTimeout` in `POST /api/v0/searches` is milliseconds, whatever the source comment says.
  `15` ends the search instantly with zero results; `15000` works.
- `GET /api/v0/searches/{id}/responses` returns file lists only after the search state contains
  `Completed`. While `InProgress` it returns the responses with empty file arrays, even when
  `responseCount` is in the hundreds. Poll `GET /searches/{id}` until `Completed`, then fetch.
- `responseLimit` (default 100) ends the search early with state `Completed, ResponseLimitReached`.
  That is the fastest path for popular tracks (1.6 s to 250 peers). For the app, a lower
  `searchTimeout` (5000 ms idle) plus the default response limit gives a 5 to 25 s search.
- slskd's API allows one search creation at a time and answers 429 to a concurrent POST, so the
  client must serialise search creation or retry on 429.
- Enqueue with `POST /api/v0/transfers/downloads/{username}` and a body of
  `[{"filename": <exact path with backslashes>, "size": <exact bytes>}]`. The response carries the
  transfer id. Transfer state strings seen: `Initializing`, `Requested`, `Queued, Remotely`,
  `Queued, Locally`, `InProgress`, `Completed, Succeeded`. Read one transfer via
  `GET /transfers/downloads/{username}/{id}`; the object carries `bytesTransferred`, `size`,
  `averageSpeed`.
- Result attributes match the protocol doc: MP3 files carry `bitRate`, `length`,
  `isVariableBitRate`; FLAC files carry `sampleRate`, `bitDepth`, `length` and no bitrate. About a
  quarter of files carry no `length` at all and must be excluded or matched by other means.
- Completed downloads land in `<app-dir>/downloads/<remote folder name>/<file>`; the app would
  move the file out of there into its own tmp dir before verify.

## Conversion and Rekordbox check (same day, later)

Question: does converting the downloaded FLAC to AIFF (the filing format the owner chose) and tagging it
with krater's own tag code harm the audio, and does Rekordbox treat the result like the MP3s it
already knows?

Method (throwaway script, scratchpad only): for each of the six downloaded FLACs, decode to raw PCM and
hash it, convert with `ffmpeg -vn -map 0:a -c:a pcm_s16be` (`pcm_s24be` for the one 24-bit master),
write Beatport tags plus artwork through `krater.tag.write_tags`, then decode and hash again.
One track was also written as WAV. The 13 files were staged in `~/Music/Krater Soulseek Test`
and imported into Rekordbox 7 by hand; the results were read back from Rekordbox's `master.db` with
pyrekordbox, next to the library MP3s of the same six tracks.

### Audio is untouched

| # | Track | bits | FLAC samples | AIFF samples | PCM hash FLAC | PCM hash AIFF (after tags) | WAV | tags read back | sizes FLAC/AIFF MB |
|---|---|---|---|---|---|---|---|---|---|
| 6 | Hallucinogen - Orphic Thrench | 16 | 19496904 | 19496904 | 18a4d8af63 | 18a4d8af63 SAME | same | ok | 51/78 |
| 8 | Filteria - Made Out Of Trance | 16 | 29720205 | 29720205 | f02cad420b | f02cad420b SAME | - | ok | 80/119 |
| 10 | Astral Projection - Dreaming (Anything Can Happen) | 16 | 20709360 | 20709360 | 765053009d | 765053009d SAME | - | ok | 48/83 |
| 14 | Goasia - Love & Peace | 16 | 22002960 | 22002960 | 219b4c1185 | 219b4c1185 SAME | - | ok | 60/88 |
| 16 | Mindsphere - Depth of Consciousness | 16 | 21954744 | 21954744 | 1f2fcac223 | 1f2fcac223 SAME | - | ok | 63/88 |
| 26 | Ritmo - Organic Rhythm | 24 | 22245300 | 22245300 | d69c632e0e | d69c632e0e SAME | - | ok | 101/134 |

Decoded PCM is byte-identical and the sample count is unchanged for every track, before and after
tagging. AIFF is about 1.5x the FLAC size.

### Rekordbox reads the same thing from every format

| Title | ext | type | kbps | Hz | bits | BPM | key | analysed | len s | artist | album | label | genre | art |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Depth of Consciousness | aiff | 12 | 1411 | 44100 | 16 | 140.00 | Dm | 105 | 497 | Mindsphere | Blacklight Moments | Suntrip Records | Psy-Trance | yes |
| Depth of Consciousness | flac | 5 | 0 | 44100 | 16 | 140.00 | Dm | 105 | 497 | Mindsphere | Blacklight Moments | Suntrip Records | Psy-Trance | yes |
| Depth of Consciousness | mp3 | 1 | 320 | 44100 | 16 | 140.00 | Dm | 105 | 497 | Mindsphere | Blacklight Moments | Suntrip Records | Psy-Trance | yes |
| Dreaming (Anything Can Happen) | aiff | 12 | 1411 | 44100 | 16 | 100.04 | F#m | 105 | 469 | Astral Projection | Trust In Trance Vol. 3 | Trust In Trance | Electronica | yes |
| Dreaming (Anything Can Happen) | flac | 5 | 0 | 44100 | 16 | 100.04 | F#m | 105 | 469 | Astral Projection | Trust In Trance Vol. 3 | Trust In Trance | Electronica | yes |
| Dreaming (Anything Can Happen) | mp3 | 1 | 320 | 44100 | 16 | 99.88 | F#m | 105 | 471 | Astral Projection | Trust In Trance Vol. 3 | Trust In Trance | Electronica | yes |
| Love & Peace | aiff | 12 | 1411 | 44100 | 16 | 145.00 | Am | 105 | 498 | Goasia | From Other Spaces | Suntrip Records | Electronica | yes |
| Love & Peace | flac | 5 | 0 | 44100 | 16 | 145.00 | Am | 105 | 498 | Goasia | From Other Spaces | Suntrip Records | Electronica | yes |
| Love & Peace | mp3 | 1 | 320 | 44100 | 16 | 145.00 | Am | 105 | 499 | Goasia | From Other Spaces | Suntrip Records | Electronica | yes |
| Made Out Of Trance | aiff | 12 | 1411 | 44100 | 16 | 144.00 | Dbm | 105 | 673 | Filteria | Live With The Lag | Suntrip Records | Psy-Trance | yes |
| Made Out Of Trance | flac | 5 | 0 | 44100 | 16 | 144.00 | Dbm | 105 | 673 | Filteria | Live With The Lag | Suntrip Records | Psy-Trance | yes |
| Made Out Of Trance | mp3 | 1 | 320 | 44100 | 16 | 144.00 | Dbm | 105 | 673 | Filteria | Live With The Lag | Suntrip Records | Psy-Trance | yes |
| Organic Rhythm | aiff | 12 | 2116 | 44100 | 24 | 140.00 | F#m | 105 | 504 | Ritmo | Organic Rhythm | Iboga Records | Psy-Trance | yes |
| Organic Rhythm | flac | 5 | 0 | 44100 | 24 | 140.00 | F#m | 105 | 504 | Ritmo | Organic Rhythm | Iboga Records | Psy-Trance | yes |
| Organic Rhythm | mp3 | 1 | 320 | 44100 | 16 | 140.00 | F#m | 105 | 504 | Ritmo | Organic Rhythm | Iboga Records | Psy-Trance | yes |
| Orphic Thrench | aiff | 12 | 1411 | 44100 | 16 | 138.99 | Dm | 105 | 442 | Hallucinogen | Twisted | Twisted Records Ltd | Trance (Main Floor) | yes |
| Orphic Thrench | flac | 5 | 0 | 44100 | 16 | 138.99 | Dm | 105 | 442 | Hallucinogen | Twisted | Twisted Records Ltd | Trance (Main Floor) | yes |
| Orphic Thrench | mp3 | 1 | 320 | 44100 | 16 | 138.99 | Dm | 105 | 442 | Hallucinogen | Twisted | Twisted Records Ltd | Trance (Main Floor) | yes |
| Orphic Thrench | wav | 11 | 1411 | 44100 | 16 | 138.99 | Dm | 105 | 442 | Hallucinogen | Twisted | None | Psytrance - Goa | no |

- BPM, key and length are identical for the AIFF, the FLAC and the MP3 of the same master.
- Astral Projection differs from the MP3 (100.04 vs 99.88 BPM, 469 vs 471 s) because the Soulseek file
  is a different master of the track, not because of the conversion: the FLAC and its AIFF agree exactly.
  The 1.4 s gap sits inside the 3 s duration tolerance.
- AIFF carries title, artist, album, label, genre, year, artwork and the krater comment into
  Rekordbox exactly like the MP3. The WAV lost label, artwork and comment and got a genre from elsewhere:
  Rekordbox reads RIFF INFO from WAV, not the ID3 chunk. This confirms AIFF over WAV.
- Rekordbox shows FLAC as "VBR" with bitrate 0 (cosmetic) and the 24-bit AIFF as 2116 kbps; both
  analyse normally.

Open point for the spec: the Ritmo master is 24-bit and was filed as a 24-bit AIFF. Decide whether to
keep the source bit depth or normalise to 16-bit; keeping it is lossless and Rekordbox handles it.

## Same-recording check with Chromaprint (same day, later)

Question: can we prove a peer's file is the recording we asked for, without trusting its name or tags?

Method (throwaway `fp_test.py`): fetch Deezer's 30 s preview for each of the six matched tracks
(trusted: it is Deezer's own copy of the recording the match step already chose), fingerprint it and
the Soulseek FLAC with `fpcalc -raw` (Chromaprint 1.6.1 from Homebrew), then slide the preview's
frames along the track's frames and keep the best bit agreement. Four sub-frame trims of the preview
(0, 31, 62, 93 ms) are tried and the best kept, because Chromaprint frames are 124 ms apart.
Each preview was scored against all six FLACs and against the library MP3 of its own track.

| preview \ FLAC | Orphic Thrench | Made Out Of Tr | Dreaming (Anyt | Love & Peace | Depth of Consc | Organic Rhythm | vs own MP3 |
|---|---|---|---|---|---|---|---|
| Orphic Thrench | **0.98 @48s** | 0.71 | 0.67 | 0.62 | 0.76 | 0.68 | 0.98 @48s |
| Made Out Of Tr | 0.60 | **0.99 @323s** | 0.64 | 0.53 | 0.61 | 0.62 | 0.99 @323s |
| Dreaming (Anyt | 0.65 | 0.74 | **0.98 @48s** | 0.56 | 0.65 | 0.65 | 0.99 @48s |
| Love & Peace | 0.61 | 0.66 | 0.59 | **0.97 @48s** | 0.61 | 0.58 | 0.98 @48s |
| Depth of Consc | 0.63 | 0.57 | 0.58 | 0.55 | **0.98 @48s** | 0.58 | 0.98 @48s |
| Organic Rhythm | 0.66 | 0.68 | 0.65 | 0.59 | 0.61 | **0.99 @48s** | 0.98 @48s |

- Correct pairs score 0.97 to 0.99; wrong tracks 0.53 to 0.76. Random bits would give 0.50; the
  psytrance pairs sit above that because the genre shares rhythmic structure. A 0.90 threshold has a
  wide margin on both sides.
- The 320 kbps MP3 and the FLAC score the same against the preview, so lossy encoding does not
  disturb the fingerprint. The Astral Projection FLAC is a different master from the MP3 (1.4 s and
  0.16 BPM apart) and still scores 0.98: the check identifies the recording, not the exact master.
- Deezer starts most previews at 48 s into the track; Filteria's starts at 323 s. The offset the
  check finds is a useful side product for the report.
- Cost: one 400 kB preview download plus about 0.5 s of `fpcalc` per file.

Conclusion: adopt this as an always-on check after verify, threshold 0.90, with the score, offset
and both fingerprints stored on the attempt row. Spec section 16 was updated accordingly.

## Recommendation

The network answers in about a second, has lossless copies of everything in this library, and the
existing verify layer rejects nothing genuine and would catch a fake. The measured behaviour
supports the design already discussed: keep the Deezer flow as the baseline, add a Soulseek
upgrade step right before the fetch, cap time to first byte at 60 s and total transfer at 10 min,
and fall back to the Deezer MP3 on any failure. Still open before building: router port forward
(searching worked without it; upload reachability was not tested), whether slskd should be
launched by `crate start` or stay a separately managed process, and how a filed FLAC is reported.
