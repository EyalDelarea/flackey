# Replacing Beatport tags with Deezer and Discogs: 2026-10-14 (issue #146)

I looked up every Beatport record already stored in the live database on Discogs, MusicBrainz and Deezer,
then compared what came back with Beatport's own fields. The database was opened read-only
(`file:…/flackey.sqlite?mode=ro`). No production code changed and nothing was written back.

This is revision 2. A reviewer found real defects in the first version: loose Discogs matching,
circular Deezer figures, and a wrong ISRC claim. All figures below come from the corrected run. The
**Review** section lists what changed.

The scripts and every raw API response are cached outside the repo. A rerun reads the cache and sends
nothing to the network:

```bash
R=/private/tmp/claude-501/-Users-delarea-Desktop-code-cratedigger/6abf5fa5-0183-4871-88d7-d5f6a88b7ae8/scratchpad/research146
# beatport_rows.json   the 213 Beatport rows + their request query + chosen Deezer candidate (read-only export)
# fetch_all.py         lookups (paced: MusicBrainz 1.1 s, Discogs 2.6 s, Deezer 0.15 s between requests)
# analyse.py           matching rules (v2: strict Discogs comparator + 15 s duration gate)
# report.py, report_v2.py, label_rule.py   the figures below
# cache/<svc>/*.json   raw responses with status and rate-limit headers
# analyse_v1.py, results_*_v1.json         the first (superseded) run, kept for comparison
PY=$PWD/.venv/bin/python   # from the repo root; needs rapidfuzz
(cd $R && OFFLINE=1 $PY analyse.py beatport && OFFLINE=1 $PY analyse.py prod && $PY report_v2.py && $PY report.py beatport --table)
```

## Decision: **implement partly.** Use the Deezer album first and Discogs second. Skip MusicBrainz for now.

1. **When a Deezer candidate exists, take the tags from its Deezer album.** That means label, release
   date, ISRC, duration and the 1400 px cover. It is one `GET /album/{id}`, because the candidate
   already carries the `deezer_id`. That covers 135 of the 195 production requests (69 %). Prefer
   Deezer's label even when Discogs also has one: it is the same kind of label the store already holds
   (Beatport's, 89 % agreement), so the library stays consistent.
2. **Otherwise, use Discogs' CC0 fields:** label, catalogue number and the earliest year. Use them only
   when the track passes the strict comparator *and* the 15 s duration gate. Production-keyed, that is
   **169/195 (87 %)**.
3. **Write Discogs' first style to sub-genre.** Map it into Beatport's vocabulary for the genre tag
   (Goa Trance → Psy-Trance), so Rekordbox keeps one genre list. Do this whenever Discogs matches,
   including when step 1 supplied the other tags.
4. **Skip MusicBrainz for now.**
   - Its genres are mostly "electronic" and are licensed CC BY-NC-SA.
   - Only 20 of 211 ISRCs resolve on it.
   - It would add only for the tracks Discogs misses: it finds 31 of the 36 Beatport-keyed misses,
     but Deezer finds 35 of the same 36.
5. **Ship Discogs unauthenticated first.** Leave the app-key decision until Discogs support answers
   the terms questions in Q2.

**The weak spot is plain.** Discogs is strong on the 1990s and goa catalogue: 94 % of pre-2010 tracks
pass. It is weak on recent and small-label releases: 68 % of tracks dated 2010 or later pass, and 61 %
outside the five biggest labels. Those are the tracks most likely to have no Deezer candidate either.

## The numbers

**Set.** 213 rows of `catalog_tracks` with `id > 0`, the real Beatport records. 188 are linked from
`tracks`. 211 have an ISRC. By genre: Psy-Trance 128, Electronica 44, Trance (Main Floor) 25, Pop 11,
other 5. 141 rows come from five labels: Suntrip 33, S.U.N. Project 32, Twisted 28, Trust In Trance 24,
HOMmega 24.

Two lookups were run on every row:

- **Beatport-keyed (the ceiling).** The lookup uses Beatport's artist, title, mix and ISRC. The
  duration gate measures against Beatport's length.
- **Production-keyed.** The lookup uses what Flackey actually holds once Beatport is gone: the
  request's `query_artist` and `query_title`, and the chosen Deezer candidate's ISRC (135 rows). The
  gate measures against the candidate's length, or else the video's. 195 rows have a request.

Agreement is always judged against the Beatport row.

### Discogs (headline: strict match + 15 s gate)

| measure | Beatport-keyed | production-keyed |
|---|---|---|
| searches with any result | 207/213 | 188/195 |
| found, v1 loose rule (superseded) | 193/213 (91 %) | 182/195 (93 %) |
| found, strict comparator | 188/213 (88 %) | 179/195 (92 %) |
| **found, strict + duration within 15 s** | **177/213 (83 %)** | **169/195 (87 %)** |

Agreement below is measured on the gated set, so the denominator is the gated count: 177
Beatport-keyed and 169 production-keyed.

| measure | Beatport-keyed | production-keyed |
|---|---|---|
| label agrees | 73/177 (41 %) | 65/169 (38 %) |
| catalogue number agrees | 54/177 (31 %) | 54/169 (32 %) |
| year exact | 84/177 (47 %) | 90/169 (53 %) |
| year ±1 | 89/177 (50 %) | 92/169 (54 %) |
| earliest year in the search results equals Beatport's year | 81/177 (46 %) | 86/169 (51 %) |
| genre: first style equals Beatport's genre | 35/177 (20 %) | 27/169 (16 %) |
| genre: first style, mapped to Beatport's vocabulary, equals Beatport's genre | **112/177 (63 %)** | 98/169 (58 %) |
| genre: any style in Beatport's genre family | 144/177 (81 %) | 127/169 (75 %) |
| largest image | 600 px for 173 of 177 | same |

Two notes on these figures:

- **The figure I first quoted for the reviewer was 167/213.** That was v1's found set with the gate
  applied. v2 is higher because the strict loop tries up to five releases and keeps the first one that
  passes the gate, instead of stopping at the first title hit.
- **I spot-checked every gated pick whose title differs from Beatport's** (17 of them). All 17 are
  spelling variants ("The Power Of The Darkside", "LSD" for "L.S.D.") or the right named version, such
  as "Casio-Paya (1998 Unreleased Mix)" on *Secret Mixes 1996-2000*. None is a wrong track.

### Deezer: the circular figure and the one that matters

**The Beatport-keyed Deezer figures are circular.**

- 209 of the 212 Beatport-keyed finds came through Beatport's ISRC.
- In 131 rows, Beatport's row was itself chosen through the Deezer candidate's ISRC
  (`pipeline._identify`, second `best_match`).
- So "Deezer agrees with Beatport 89 % on label" partly measures Flackey agreeing with itself.

Production-keyed, the two Deezer routes come out like this:

| Deezer route | n | label agrees | year exact | duration > 15 s off |
|---|---|---|---|---|
| chosen candidate's ISRC (step 1 of the decision) | 135 | 120 (89 %) | 116 (86 %) | 1 |
| **text search, no candidate (not circular)** | **51** | **39 (76 %)** | **25 (49 %)** | **5** |

The 51-row text-search slice is the figure that applies when there is no Deezer candidate. It is no
better than Discogs on year, which is why step 2 uses Discogs and not a Deezer text search.

**Year direction.** Beatport-keyed, Deezer's year differs from Beatport's in 50 rows. Deezer is earlier
in 38 and later in 12. Beatport's own date is sometimes the original even on a reissue label:
*Trancespotter* is dated 1996 by Beatport and 2015 by Deezer. **Taking the year from Deezer will
therefore stamp a reissue year on some tracks.** Discogs' earliest search-result year is earlier than
Beatport's in 92 of the 177 gated rows.

### MusicBrainz (measured, not recommended)

These figures come from the first run. Its matching rules were not changed in v2.

- **Found:** 205/213 Beatport-keyed and 189/195 production-keyed, with artist ≥ 80 and title
  `token_set_ratio` ≥ 75. **This rule is loose**, the same defect the reviewer found in v1's Discogs
  matching. 42 of 201 found lengths are more than 15 s off Beatport's.
- **Label:** 33 % agreement on the picked release.
- **Genre:** the top genre is "electronic" for 133 of 205; strict agreement is 8 %.
- **ISRC:** 20 of 211 resolve.

### Method

**Discogs.** The lookup sends `GET /database/search?type=release&artist=<first artist>&track=<title>`.
If that returns nothing, it falls back to `q=<first artist> <title>`. Searches were unauthenticated, at
2.6 s intervals. Among the search results:

- **Pick order:** non-compilation first, then the earliest year, then the lowest id.
- **For each of the first five releases**, the lookup fetches `/releases/{id}` and checks each
  tracklist entry with three tests:
  - **Title:** `fuzz.ratio` ≥ 90 on `models.norm` titles, after removing bracketed version text.
  - **Version:** an original must not meet a named mix or remix, and a named mix must match the
    listed version (`token_set_ratio` ≥ 80) when the listing names one.
  - **Artist:** the track artist, or else the release artist with the `(N)` suffix removed, matches the
    first artist (`token_set_ratio` ≥ 80).
- **Gate:** the first entry passing all three is "found". It is "gated" when its tracklist length is
  within 15 s of the reference length; otherwise the loop tries the next release.
- **The rule rejects every false positive the reviewer listed:**

  | Beatport track | wrong v1 match |
  |---|---|
  | Rain | "Don't Stop The Rain" |
  | Police 106 | DJ Goblin Remix |
  | T8 (Melo Mix) | "Original-Mix" |
  | Land Of Freedom | "'Tribute To MWNN'" |
  | Cream (2023 Remaster) | "Two Places At The Same Time" |
  | Brief History… | "– Astral Projection" |
  | Let There Be Light (Filteria Remix) | Outsiders Remix |

  It still accepts "Lunar Civilization (Filteria's Higher Remix)" and "T8 (Melo-Mix)".
- **Genre tag:** the release's first style, because Discogs lists styles in entry order, not ranked.
- **Mapping for the comparison:**
  - Goa Trance, Psy-Trance and Progressive Trance → Psy-Trance
  - Trance → Trance (Main Floor)
  - Downtempo, Ambient, IDM and Dub → Electronica

**Labels.** Both names are lower-cased with `norm`. A Discogs `(N)` suffix is removed, along with the
words Records, Recordings, Ltd, Music, Productions and similar. They agree when the names are equal or
`token_set_ratio` ≥ 90.

**Genre families** were fixed before the results were seen:

- **Psy-Trance:** a style containing psy-trance, goa trance or psytrance.
- **Trance (Main Floor):** any style containing trance.
- **Electronica:** Downtempo, Ambient, IDM, Experimental, Leftfield, Dub and similar.
- **Pop:** the Discogs genre Pop.

**Ambiguity.** 145 of 207 Discogs searches with results (70 %) span more than one label, and 156
(75 %) span more than one year. The median search returns 7 releases. **The track is usually there.
The hard question is which release to tag from.**

**Deezer.** The lookup sends `/track/isrc:{isrc}`, then `/search?q=<artist> <title>`. It accepts a
result when the title scores ≥ 75 and the artist ≥ 80. Label, year and genres come from `/album/{id}`.
The advanced syntax `artist:"…" track:"…"` returned 0 results for every query, even
`artist:"eminem" track:"without me"`, on 2026-10-14.

**MusicBrainz.** Each request waited 1.1 s and sent the User-Agent
`Flackey-research/0.1 (https://github.com/EyalDelarea/flackey)`. 41 requests returned 503 against 1,322
cached responses, and a 30 s back-off always recovered.

- **ISRC first:** `/ws/2/isrc/{isrc}`. Then a phrase query, then an unquoted query.
- **Per recording:** `/recording/{id}?inc=artist-credits+genres+tags+isrcs` and
  `/release?recording={id}&inc=labels+release-groups+genres+tags+media`.

### Breakdowns (Beatport-keyed, Discogs strict + gate)

| slice | n | Discogs found + gated | label agrees | year ±1 | genre family | Deezer label agrees (circular, see above) |
|---|---|---|---|---|---|---|
| Psy-Trance | 128 | 108 (84 %) | 37 % | 44 % | 100 % | 94 % |
| Electronica | 44 | 31 (70 %) | 81 % | 48 % | **35 %** | 70 % |
| Trance (Main Floor) | 25 | 24 (96 %) | 25 % | 100 % | 100 % | 92 % |
| Pop | 11 | 11 (100 %) | 0 % | 0 % | 0 % | 100 % |
| top-5 labels | 141 | 133 (94 %) | 44 % | 53 % | 85 % | 99 % |
| **all other labels** | 72 | **44 (61 %)** | 34 % | 41 % | 70 % | 70 % |
| Beatport date before 2010 | 123 | 116 (94 %) | 28 % | 47 % | 72 % | 96 % |
| **Beatport date 2010 or later** | 90 | **61 (68 %)** | 66 % | 57 % | 100 % | 80 % |
| linked from `tracks` | 188 | 157 (84 %) | 43 % | 55 % | 80 % | 90 % |

**Where the 36 misses are.**

- **By label:** Record Union, a self-release distributor, accounts for 10. Then Twisted 4, Tip 3,
  Classic Goa Trax 3, and 1–2 each from a long tail.
- **Elsewhere:** Deezer finds 35 of the 36 and MusicBrainz 31, though MusicBrainz's matching is loose.

### What "disagreement" actually is

The full table is at the end: 133 gated rows where label, year ±1 or genre family differ. Four
patterns cover nearly all of them.

1. **Original release vs later release on another label.** This is most of the label misses:

   | Beatport | Discogs | rows |
   |---|---|---|
   | S.U.N. Project (the artist's own label) | Spirit Zone Recordings | 19 |
   | S.U.N. Project | Sector | 10 |
   | Twisted Records Ltd | Dragonfly Records | 12 |
   | EastWest U.K. | Perfecto | 11 |
   | Trust In Trance | TIP Records | 8 |
   | Trust In Trance | Ganesha | 7 |
   | HOMmega Productions | Phonokol | 8 |

   Beatport's *label* is usually the current digital rights holder. Its *date*, however, is mixed:
   sometimes the original (Trancespotter 1996), sometimes the reissue. So "Beatport sells the reissue"
   is true of labels, not reliably of years. A DJ library arguably wants Discogs' original label; the
   recommendation still prefers Deezer's label, for consistency with the 188 tracks already filed.
2. **"Goa Trance" vs "Psy-Trance".** Discogs' first style is Goa Trance for 132 of 177 gated picks and
   Psy-Trance for 33. Hence 20 % strict genre agreement but 63 % after mapping, and 100 % in family on
   Psy-Trance. For 1990s goa, Discogs is more specific, which is why it goes to sub-genre.
3. **Beatport genre errors.**
   - All 11 "Pop" rows are Man With No Name's *Earth Moving the Sun*. Discogs has Perfecto 1998, Goa
     Trance.
   - Whole goa albums are filed as "Electronica": Afgin, *Astral Experience*; Goasia, *From Other
     Spaces*. That is why Electronica family agreement is 35 %.
4. **Versions without a Discogs entry of their own.** The gate now holds most of these back.
   - Of 50 non-original Beatport mixes, 37 pass the gate.
   - The mix name appears in Discogs' track title for 15 of those 37. Examples are the S.U.N. Project
     "Unreleased Mix" tracks on *Secret Mixes 1996-2000*, "Moon Remix" and "Disco Valley Mix".
   - "Remastered 2024" tracks pass the gate (16 of 18) but carry no remaster marker. They take the
     original album's year and label.

## Q4: per-track vs per-release genre

**Measured:**

- **Compilations are rare among the picks.** Of the 177 gated picks, only 5 are compilations, all 5 in
  family. Among the 172 non-compilations, 139 (81 %) are in family.
- **Compilations do not cause the mislabels in this library.** The misses are the Electronica and Pop
  albums above, where Beatport is the odd one out.
- **What a release-level style cannot express.** Beatport's own data has 6 releases that mix genres:
  - S.U.N. Project *Drosophila*, *Paranormal* and *Macrophage*: one Electronica track each among 7–9
    Psy-Trance tracks.
  - Astral Projection *Another World*, *Dancing Galaxy* and *Trust In Trance Vol. 3*: one Electronica
    track each among Trance tracks.
- **How Discogs handles them.** On the S.U.N. albums the release also lists Downtempo. On the Astral
  Projection releases Goa Trance is the only style, so the ambient track gets Goa Trance.

**Inference:**

- **The per-track cost is small.** It touches 6 of 213 tracks (3 %), always an ambient track on a
  trance album.
- **Multi-style releases need a rule.** 74 of the 177 gated picks have more than one style, and 9 have
  seven or more. "First style" is the simplest rule; mapping into Beatport's list absorbs most of the
  noise.

## Q5: what Beatport feeds besides tags, and who can replace it

| use (code) | what it reads from the catalog record | Discogs | MusicBrainz | Deezer album |
|---|---|---|---|---|
| Tags: `tag._values` | `display_title` (title + mix), artist, album = `release_name`, genre, label, catalog no., date, ISRC, mix | title, release, label, catalog no., year, styles; no mix field; the version is in the track title on 15 of 37 gated non-original mixes | title, release, label, catalog no., date; ISRC on 13 % of matched recordings | title, album, label, date, ISRC; coarse genres ("Electro", "Dance") |
| Comment `beatport {id}`: `tag.comment_for`; `catalog_tracks.id > 0` is a Beatport id | the id | needs a new id namespace | needs a new id namespace | needs a new id namespace |
| Artwork: `filing._file_track` → `fetch_artwork(artwork_url)`, 1400 px | `artwork_url` | 600 px max, and Restricted Data: **don't write it into files** | CAA front on 186/205, 1200 px on 124 | 1000 px `cover_xl`; 1400 px by editing the URL (tested once) |
| File name and folder: `library.final_path`, `refile_path` | `artist.split(",")[0]`, `display_title` | names carry `(N)` disambiguators ("Moonchild (3)", "Ultravibe (4)") and "Various"; strip them | clean names | clean names |
| Duplicate check: `library.find_duplicate` | `catalog.isrc`, then artist/title/mix/duration | no ISRC | ISRC sometimes | ISRC and duration |
| Pick the catalog record: `catalog.best_match`, `_length_pins_a_version` | mix name, duration and ISRC across results | tracklist durations (97 % present) | recording length | duration, ISRC |
| Score source candidates: `match.score_candidate`, `decide` | reference artist and title; duration only when the query has none; equal ISRC scores 100 | artist, title | artist, title | artist, title, ISRC |
| Disambiguate by ISRC: `pipeline._identify` (second `best_match`) | catalog ISRCs | – | 20 of 211 resolve | yes |
| Re-match for a remix: `pipeline._catalog_for` | mix name | partial | weak | per-track |
| Catalog-only path when the Deezer bot gives nothing: `records.catalog_candidate` → `lossless.reference_for` → Soulseek `search_text`, `rank_version`, `rank_duration` | artist, title, mix, duration | could stand in, with gated durations | loose | n/a (Deezer is the source that failed) |
| Telegram "Done" line: `filing` | genre / label | yes | yes | yes |

**Inference.** Matching and duplicate checks already work without a catalog record: `catalog is None`
is a supported path (`_fallback_catalog`, `query_candidate`, issue #69). The version comes from the
Deezer candidate's own title (`identify.parse_version` → `candidate_version`) and from
`query.version`. So losing Beatport degrades tags and the catalog-only Soulseek route, not
identification.

Durations for ranking should keep coming from the Deezer candidate or the video, and not from a
catalogue release.

## Q2: Discogs API terms

**Sources.** The API docs at `discogs.com/developers`, and the API Terms of Use at
`support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use` ("Last Updated: May 27th, 2025").
Both live pages return a Cloudflare 403 to scripts. They were read from Wayback copies dated
2026-09-19 (docs) and 2026-05-30 (terms), and the reviewer confirmed the terms text from the archive.

**Search auth.**

- The docs: "Authentication (as any user) is required."
- **Measured on 2026-10-14:** unauthenticated search returns 200 with `X-Discogs-Ratelimit: 25`. All
  316 searches and about 225 release fetches in this study ran unauthenticated, with no 429.
- **Inference:** this is undocumented behaviour and can be withdrawn.

**Rate limit.**

- The docs: 60 per minute authenticated and 25 per minute unauthenticated, as a moving 60 s average.
- **Most likely per source IP.** Discogs' own OpenAPI spec says requests are "throttled by the server by
  source IP" (mirror:
  https://repo.rock-n-code.com/rock-n-code/discogs-service/blame/tag/v0.6.0/Sources/DiscogsService/openapi.yaml).
  The live page no longer says so.
- The terms forbid "creating additional API keys to overcome these limits".
- At about 2.5 requests per track, 25 per minute is enough for one user's queue (10 tracks a minute).

**Images.**

- The docs say image URLs need a key and secret. **Measured:** unauthenticated `/releases/{id}`
  returned signed `i.discogs.com` URLs, and they downloaded.
- The largest image is 600 px.
- Images are **Restricted Data**: they may not be transferred "to any third party" or used "for any
  commercial purposes". **Do not write Discogs artwork into files.**

**Data licence.**

- **CC0:** release titles, dates, track listings, identifiers, credits, and label names and catalogue
  data.
- **Genres and styles are not in the CC0 list**, nor in the Restricted list.
- The 6-hour clause: "You may not display … the Content if it is more than six (6) hours older…".
  Storing data is limited to "longer than is necessary to provide a service".
- **Inference:** label, catalogue number and year written into tags are CC0 and safe. A style written
  into a tag is the unclear part.

**Shipping a key.**

- The terms are silent. The docs say "You should not share your consumer secret".
- beets ships a Discogs consumer key in its source (`beetsplug/discogs/__init__.py`). **That is
  precedent, not permission.**

**Attribution.**

- The application must state: "This application uses Discogs' API but is not affiliated with…".
- "Data provided by Discogs." must appear next to the data, with a followed link to the discogs.com
  page.

## Q3: MusicBrainz and the Cover Art Archive

**Rate limit and User-Agent.**

- The limit is per source IP, on average one request per second, answered with 503 over the limit.
- A meaningful User-Agent is required. No key is needed.
- Measured: 41 responses of 503 at one request per 1.1 s.

**Licence.**

- **Core data is CC0:** titles, credits, durations, ISRCs, dates, labels and catalogue numbers.
- **Tags, including genre associations, are CC BY-NC-SA 3.0.**
- The web service is free for non-commercial use. MetaBrainz has a **$0 non-profit tier "for … authors
  of open-source software using our data"** (https://metabrainz.org/supporters/account-type).

**ISRC lookup.**

- `/ws/2/isrc/{isrc}` exists.
- Measured: `inc=genres` is rejected on that resource ("genres is not a valid inc parameter for the isrc
  resource"), even though the docs say it takes the same `inc` values as a recording lookup.

**Cover Art Archive.**

- No rate limit is in force.
- Thumbnails come in 250, 500 and 1200 px, plus the original. Images redirect to archive.org.
- No licence grant: "Use the images at your own risk."
- Measured: a front cover for 186 of 205 matched releases, with a 1200 px thumbnail on 124.

## Deezer terms

https://developers.deezer.com/termsofuse is undated.

- **A developer account is required:** "Provide true and complete information to DEEZER when creating
  a Developer account". **Flackey's current unauthenticated calls to `api.deezer.com` (`deezer.py`,
  `reference.py`) have no such account**, and this proposal adds more of them.
- Use is "strictly limited for a non-commercial purpose".
- Content use is limited to "a strictly private use within a family scope".
- Images are among the reserved "elements associated with the Content". Nothing addresses writing
  metadata or covers into files.
- The 1400 px cover comes from editing the size in the `cover_xl` URL. That is undocumented and was
  tested on one image.

## Recommendation

**Implement partly**, in this order:

1. **Deezer album when a Deezer candidate exists** (69 % of production requests). Take label, date,
   ISRC, duration and the 1400 px cover. Prefer Deezer's label for consistency with the labels already
   stored.
2. **Discogs when there is no candidate.** Take the CC0 fields: label, catalogue number and earliest
   year. Accept only with the strict comparator and the 15 s gate; that passes 87 % production-keyed,
   and 61–68 % on small-label and post-2010 releases.
3. **Discogs' first style goes to sub-genre**, and is mapped into Beatport's genre list for the genre
   tag. Do this whenever Discogs matches.
4. **No MusicBrainz** for now.
5. **Discogs unauthenticated first.** The key decision waits for Discogs support.
6. **Keep `_fallback_catalog`** ("Unknown") as the last resort.

**Open risks.**

- **Discogs terms:**
  - Undocumented unauthenticated access.
  - Styles are outside CC0.
  - The 6-hour clause.
  - Attribution needs a place in the UI.
  - The key decision.
- **Deezer terms:** a developer account is required and the use is "private use within a family
  scope". This already applies to today's code.
- **Reissue years from Deezer:** Deezer is later than Beatport in 12 of 50 year disagreements. And
  remasters take the original album's year from Discogs.
- **Coverage where it matters least well:** recent, small-label psytrance with no Deezer candidate. 68 %
  (post-2010) and 61 % (outside the top five labels) of such tracks pass Discogs' gate.
- **Genre mapping:** Goa Trance → Psy-Trance is a product choice. Modern full-on that Discogs files
  under Goa Trance would be mislabelled in sub-genre.

## Review (2026-10-14): what changed from v1

1. **Discogs matching was too loose.** `token_set_ratio ≥ 75` scores a token subset 100, which
   produced false finds (Rain → "Don't Stop The Rain", Cream → "Two Places At The Same Time", the
   Talamasca pair) and wrong versions (Police 106, Land Of Freedom, Let There Be Light, T8).
   - Replaced with `ratio ≥ 90` on version-stripped titles, a version-compatibility check and a 15 s
     duration gate.
   - The headline moved from "found 193/213 (91 %)" to **gated 177/213 (83 %)**, and 169/195 (87 %)
     production-keyed.
   - Label, year and genre agreement were recomputed on the gated set.
2. **Deezer agreement was partly circular.** 209 of 212 Beatport-keyed Deezer finds used Beatport's
   ISRC, and 131 Beatport rows were themselves chosen through the Deezer candidate's ISRC. The doc now
   reports the non-circular text-search slice (51 rows: label 76 %, year 49 %, 5 more than 15 s off)
   as the figure for requests with no candidate.
3. **Removed the "27 doubtful rows (file ISRC ≠ Beatport ISRC)" claim** and its breakdown row.
   `filing.py:193` writes `catalog.isrc or cand.isrc`, so 25 of those rows simply have no linked file,
   2 have no Beatport ISRC, and none actually differ.
4. **Year direction corrected.**
   - Beatport's date is sometimes the original (Trancespotter 1996, Deezer 2015). Of 50 Deezer year
     disagreements, Deezer is earlier in 38 and later in 12.
   - "Beatport sells the reissue" now applies to labels only.
   - The risk of reissue years from Deezer is stated.
5. **Denominators stated** in every headline table (207 searches with results, 177 gated, and so on).
6. **Terms updated:**
   - Discogs images are Restricted Data, so no Discogs art goes into files.
   - Discogs genres and styles are outside CC0.
   - The Discogs rate limit is most likely per source IP (OpenAPI spec).
   - beets ships a key: precedent, not permission.
   - MusicBrainz has a $0 tier for open-source authors.
   - Deezer requires a developer account, which Flackey does not have.
7. **Recommendation simplified** to the reviewer's version: Deezer album, then Discogs CC0 fields,
   style to sub-genre, no MusicBrainz, Discogs unauthenticated first. The re-run supports it. It also
   adds one caveat: Discogs coverage drops to 61–68 % for small-label and recent releases.

## Not verified

- **Discogs live pages.** Read from archive copies.
- **Discogs rate-limit scope.** Per source IP rests on Discogs' OpenAPI spec, not the live docs.
- **Discogs images with a key:** whether an authenticated key gets anything larger than 600 px.
- **Deezer terms on storing metadata or covers in files**, and the 1400 px cover URL edit (one image).
- **Precision of "found".** Measured through the duration gate and a read of the 17 differing titles.
  Nobody listened to the audio.
- **MusicBrainz matching** was not re-run with the strict comparator.

## Appendix: Discogs disagreements on the gated set (Beatport-keyed)

Bold marks the field that differs: label, year beyond ±1, or style outside the Beatport genre family.

| # | artist – title | Beatport label / year / genre | Discogs release, label / year / styles | MusicBrainz label / year / genres | note |
|---|---|---|---|---|---|
| 1 | Hallucinogen – Trancespotter | Twisted Records Ltd / 1996 / Psy-Trance | Spacepussy: **Dragonfly Records** / 1996 / Goa Trance | Dragonfly Records / 1996 / electronic, goa trance |  |
| 2 | Space Tribe – The Ultraviolet Catastrophie | Space Tribe Music / 1997 / Psy-Trance | The Ultraviolet Catastrophe: Space Tribe Music / **2006** / Goa Trance | Spirit Zone Recordings / 1997 / electronic, goa trance |  |
| 3 | Hallucinogen – LSD | Liquid Sound / 2005 / Psy-Trance | Alpha Centauri / LSD: **Dragonfly Records** / **1994** / Goa Trance | Dragonfly Records / 1994 / electronic, goa trance |  |
| 4 | Hallucinogen – Mi-loony-um! | Etnicanet Records / 2000 / Psy-Trance | Mi-Loony-Um!: **Twisted Records** / 2000 / Goa Trance, Progressive Trance | EFA / 2000 / electronic, trance |  |
| 5 | Afgin – Old Is Gold | Kagdila Records / 2007 / Trance (Main Floor) | OLD is GOLD: **OLD is GOLD** / 2006 / Goa Trance | Old is Gold / 2006 / electronic, goa trance |  |
| 6 | Goasia – Pray for Rain | Cosmicleaf Records / 2005 / Breaks / Breakbeat / UK Bass | From Other Spaces: **Suntrip Records** / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 7 | Shiva Shidapu – The Power Of The Dark Side | Reality Bites Records / 2009 / Psy-Trance | Hovek Olam: **Hovek Olam** / **1998** / Goa Trance | None / 1999 /  | Discogs pick is a compilation |
| 8 | Hallucinogen – L.S.D. | Twisted Records Ltd / 1996 / Trance (Main Floor) | Twisted: **Dragonfly Records** / 1995 / Goa Trance | Dragonfly Records / 1994 / electronic, goa trance |  |
| 9 | Hallucinogen – Orphic Thrench | Twisted Records Ltd / 1996 / Trance (Main Floor) | Twisted: **Dragonfly Records** / 1995 / Goa Trance | Dragonfly Records / 1995 / goa trance, electronic |  |
| 10 | Sun Project – Tribolus | S.U.N. Project / 2009 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 11 | Sun Project – Fatal Error | S.U.N. Project / 2009 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 12 | Sun Project – At The Edge Of Time | S.U.N. Project / 2009 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 13 | Sun Project – Drosophila | S.U.N. Project / 2009 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 14 | Sun Project – The Awakening | S.U.N. Project / 2009 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 15 | Sun Project – Sexdrugs & Acidtrance | S.U.N. Project / 2009 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1998 / downtempo, dub |  |
| 16 | Sun Project – T.O.T.C. | S.U.N. Project / 2009 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 17 | Sun Project – Transformation | S.U.N. Project / 2009 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 18 | Sun Project – The Norb | S.U.N. Project / 2009 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 19 | Sun Project – Going With The Flow | S.U.N. Project / 2009 / Electronica | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 20 | Sun Project – Subsonic Overdrive | S.U.N. Project / 2009 / Psy-Trance | Zwork: **Velvet Inc.** / **1999** / Psy-Trance, Ambient | Velvet Inc. / 1999 / electronic, ambient |  |
| 21 | Sun Project – Computer Breath | S.U.N. Project / 2009 / Psy-Trance | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / downtempo, electronic |  |
| 22 | Sun Project – Luna | S.U.N. Project / 2009 / Psy-Trance | T8 / Luna / Casio-Paya (Remix): **M.A.S.H.** / **1999** / Goa Trance, Progressive Trance | M.A.S.H. / 2000 / electronic, goa trance |  |
| 23 | Sun Project – Out Of My Brain | S.U.N. Project / 2009 / Psy-Trance | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / psytrance, electronic |  |
| 24 | Sun Project – Thats A Trap | S.U.N. Project / 2009 / Psy-Trance | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / psytrance, downtempo |  |
| 25 | Sun Project – Under Control | S.U.N. Project / 2009 / Psy-Trance | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / psytrance, downtempo |  |
| 26 | Sun Project – Simplicity | S.U.N. Project / 2009 / Psy-Trance | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / downtempo, electronic |  |
| 27 | Sun Project – Paranormal | S.U.N. Project / 2009 / Psy-Trance | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / downtempo, electronic |  |
| 28 | Sun Project – Chapachoolka | S.U.N. Project / 2009 / Psy-Trance | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / downtempo, electronic |  |
| 29 | Sun Project – T8 | S.U.N. Project / 2009 / Psy-Trance | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / downtempo, electronic |  |
| 30 | Sun Project – Solitude | S.U.N. Project / 2009 / Electronica | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / psychedelic, psytrance |  |
| 31 | Sun Project – Out Of My Brain | S.U.N. Project / 2009 / Psy-Trance | Paranormal: **Sector** / **2000** / Psy-Trance, Downtempo, Progressive Trance | Sector / 2000 / psytrance, electronic |  |
| 32 | Sun Project – Frisco Machines | S.U.N. Project / 2009 / Psy-Trance | Macrophage: **Spirit Zone Recordings** / **1998** / Goa Trance, Downtempo, Dub, Progressive Trance, Tribal, Industrial, Psy-Trance | Spirit Zone Recordings / 1998 / downtempo, dub |  |
| 33 | Sun Project – Looking For You | S.U.N. Project / 2009 / Psy-Trance | Sexperimental: **Radiosonic Records** / **2002** / Goa Trance | Spirit Zone Recordings / 2003 / electronic, goa trance |  |
| 34 | Sun Project – Space Dwarfs | S.U.N. Project / 2009 / Psy-Trance | Macrophage: **Spirit Zone Recordings** / **1998** / Goa Trance, Downtempo, Dub, Progressive Trance, Tribal, Industrial, Psy-Trance | Spirit Zone Recordings / 1998 / downtempo, dub |  |
| 35 | Sun Project – Spaceships & Spacepeople | S.U.N. Project / 2009 / Psy-Trance | Macrophage: **Spirit Zone Recordings** / **1998** / Goa Trance, Downtempo, Dub, Progressive Trance, Tribal, Industrial, Psy-Trance | Spirit Zone Recordings / 1998 / trance |  |
| 36 | Sun Project – Dance Of The Witches | S.U.N. Project / 2009 / Psy-Trance | Macrophage: **Spirit Zone Recordings** / **1998** / Goa Trance, Downtempo, Dub, Progressive Trance, Tribal, Industrial, Psy-Trance | Spirit Zone Recordings / 1998 / downtempo, dub |  |
| 37 | Sun Project – I Feel | S.U.N. Project / 2009 / Psy-Trance | I Feel / Slap: **Spirit Zone Recordings** / **1997** / Goa Trance | Spirit Zone Recordings / 1997 / electronic, goa trance |  |
| 38 | Sun Project – The Saw | S.U.N. Project / 2009 / Psy-Trance | Macrophage: **Spirit Zone Recordings** / **1998** / Goa Trance, Downtempo, Dub, Progressive Trance, Tribal, Industrial, Psy-Trance | Spirit Zone Recordings / 1998 / downtempo, dub |  |
| 39 | Sun Project – Sexdrugs & Acidtrance II | S.U.N. Project / 2009 / Psy-Trance | Macrophage: **Spirit Zone Recordings** / **1998** / Goa Trance, Downtempo, Dub, Progressive Trance, Tribal, Industrial, Psy-Trance | Spirit Zone Recordings / 1998 / downtempo, dub |  |
| 40 | Sun Project – Energia Magica | S.U.N. Project / 2009 / Psy-Trance | Macrophage: **Spirit Zone Recordings** / **1998** / Goa Trance, Downtempo, Dub, Progressive Trance, Tribal, Industrial, Psy-Trance | YoYo Records / 1998 / ambient, electronic |  |
| 41 | Sun Project – From Dusk Till Dawn | S.U.N. Project / 2009 / Electronica | Macrophage: **Spirit Zone Recordings** / **1998** / Goa Trance, Downtempo, Dub, Progressive Trance, Tribal, Industrial, Psy-Trance | Spirit Zone Recordings / 1998 / downtempo, dub |  |
| 42 | Afgin – Astral Experience | Suntrip Records / 2009 / Electronica | Astral Experience: Suntrip Records / 2009 / **Goa Trance** | Suntrip Records / 2009 / electronic, goa trance |  |
| 43 | Afgin – Old is Gold | Suntrip Records / 2009 / Electronica | OLD is GOLD: **OLD is GOLD** / **2006** / **Goa Trance** | Old is Gold / 2006 / electronic, goa trance |  |
| 44 | Afgin – Journey Through Acid | Suntrip Records / 2009 / Electronica | Astral Experience: Suntrip Records / 2009 / **Goa Trance** | Suntrip Records / 2009 / electronic, goa trance |  |
| 45 | Afgin – Dreams in Motion | Suntrip Records / 2009 / Electronica | Astral Experience: Suntrip Records / 2009 / **Goa Trance** | Suntrip Records / 2009 / electronic, goa trance |  |
| 46 | Afgin – Emotions | Suntrip Records / 2009 / Electronica | Astral Experience: Suntrip Records / 2009 / **Goa Trance** | Suntrip Records / 2009 / electronic, goa trance |  |
| 47 | Afgin – Heaven's Tears | Suntrip Records / 2009 / Electronica | Astral Experience: Suntrip Records / 2009 / **Goa Trance** | Suntrip Records / 2009 / electronic, goa trance |  |
| 48 | Afgin – Northern Exposure | Suntrip Records / 2009 / Electronica | Astral Experience: Suntrip Records / 2009 / **Goa Trance** | Suntrip Records / 2009 / electronic, goa trance |  |
| 49 | Afgin – Aden Prayers | Suntrip Records / 2009 / Electronica | Astral Experience: Suntrip Records / 2009 / **Goa Trance** | Suntrip Records / 2009 / electronic, goa trance |  |
| 50 | Filteria – Unfiltered | Suntrip Records / 2009 / Electronica | Heliopolis: Suntrip Records / **2006** / Goa Trance, Downtempo | Suntrip Records / 2006 / ambient, electronic |  |
| 51 | Goasia – Intro | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Psychedelic Records / 2007 / electronic, goa trance |  |
| 52 | Goasia – Love & Peace | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 53 | Goasia – Space Travellers | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 54 | Goasia – From Other Spaces | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 55 | Goasia – Octagon | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 56 | Goasia – Liquid Soul | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 57 | Goasia – Taboo | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 58 | Goasia – Per Aspera ad Astra | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 59 | Goasia – Sunrise | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 60 | Goasia – Pray for Rain | Suntrip Records / 2009 / Electronica | From Other Spaces: Suntrip Records / **2007** / **Goa Trance** | Suntrip Records / 2007 / electronic, goa trance |  |
| 61 | Astral Projection – Trance Dance | Trust In Trance / 1999 / Trance (Main Floor) | Another World: **Transient Records** / 1999 / Goa Trance | Why Not Records / 1999 / electronic, psytrance |  |
| 62 | Astral Projection – Still On Mars | Trust In Trance / 1999 / Electronica | Another World: Trust In Trance Records / 1999 / **Goa Trance** | Why Not Records / 1999 / electronic, trance |  |
| 63 | Astral Projection – Dancing Galaxy | Trust In Trance / 1997 / Trance (Main Floor) | Dancing Galaxy: **Ganesha** / 1997 / Goa Trance | Trust in Trance Records / 1997 / electronic, goa trance |  |
| 64 | Astral Projection – Soundform | Trust In Trance / 1997 / Trance (Main Floor) | Dancing Galaxy: **Ganesha** / 1997 / Goa Trance | Trust in Trance Records / 1997 / electronic, goa trance |  |
| 65 | Astral Projection – Flying Into a Star | Trust In Trance / 1997 / Trance (Main Floor) | Dancing Galaxy: **Ganesha** / 1997 / Goa Trance | Trust in Trance Records / 1997 / trance |  |
| 66 | Astral Projection – No One Ever Dreams | Trust In Trance / 1997 / Trance (Main Floor) | Dancing Galaxy: **Ganesha** / 1997 / Goa Trance | Trust in Trance Records / 1997 / electronic, goa trance |  |
| 67 | Astral Projection – Life On Mars | Trust In Trance / 1997 / Trance (Main Floor) | Dancing Galaxy: **Ganesha** / 1997 / Goa Trance | Trust in Trance Records / 1997 / electronic, goa trance |  |
| 68 | Astral Projection – Liquid Sun | Trust In Trance / 1997 / Trance (Main Floor) | Dancing Galaxy: **Ganesha** / 1997 / Goa Trance | Trust in Trance Records / 1997 / electronic, goa trance |  |
| 69 | Astral Projection – Ambient Galaxy | Trust In Trance / 1997 / Electronica | Dancing Galaxy: **Ganesha** / 1997 / **Goa Trance** | Trust in Trance Records / 1997 / electronic, goa trance |  |
| 70 | Astral Projection – Kabalah | Trust In Trance / 1996 / Trance (Main Floor) | Trust In Trance: **TIP Records** / 1996 / Goa Trance | Phokol / 1996 / electronic, goa trance |  |
| 71 | Astral Projection – Enlightened Evolution | Trust In Trance / 1996 / Trance (Main Floor) | Enlightened Evolution: **TIP Records** / 1996 / Goa Trance | Phokol / 1996 / electronic, psytrance |  |
| 72 | Astral Projection – The Feelings | Trust In Trance / 1996 / Trance (Main Floor) | Trust In Trance: **TIP Records** / 1996 / Goa Trance | Phokol / 1996 / electronic, goa trance |  |
| 73 | Astral Projection – Utopia | Trust In Trance / 1996 / Trance (Main Floor) | Trust In Trance: **TIP Records** / 1996 / Goa Trance | Phokol / 1996 / electronic, trance |  |
| 74 | Astral Projection – Black & White | Trust In Trance / 1996 / Trance (Main Floor) | Trust In Trance: **TIP Records** / 1996 / Goa Trance | Phokol / 1996 / electronic, goa trance |  |
| 75 | Astral Projection – People Can Fly | Trust In Trance / 1996 / Trance (Main Floor) | Trust In Trance: **TIP Records** / 1996 / Goa Trance | Phokol / 1996 / electronic, ambient |  |
| 76 | Astral Projection – Radial Blur | Trust In Trance / 1996 / Trance (Main Floor) | Trust In Trance: **TIP Records** / 1996 / Goa Trance | Phokol / 1996 / electronic, goa trance |  |
| 77 | Astral Projection – Aurora Borealis | Trust In Trance / 1996 / Trance (Main Floor) | Trust In Trance: **TIP Records** / 1996 / Goa Trance | Phokol / 1996 / electronic, goa trance |  |
| 78 | Power Source – Skywalker | Suntrip Records / 2014 / Psy-Trance | Cosmic Waves: **Melodia Records (2)** / **1999** / Goa Trance, Trance | Suntrip Records / 2014 / ambient, downtempo |  |
| 79 | Astral Projection – Soundform | Transient Records / 2017 / Psy-Trance | Dancing Galaxy: **Ganesha** / **1997** / Goa Trance | Trust in Trance Records / 1997 / electronic, goa trance |  |
| 80 | Shpongle – Star Shpongled Banner | Twisted Records Ltd / 2018 / Electronica | Tales Of The Inexpressible: Twisted Records / **2001** / Psy-Trance, Tribal, Downtempo, Dub, Ambient | Twisted Records / 2008 / downtempo, electronic |  |
| 81 | Astrix – On Fire | Shamanic Tales / 2004 / Psy-Trance | Artcore: **HOM-Mega Productions** / 2004 / Downtempo, Psy-Trance | Shamanic Tales / 2004 / psytrance, electronic |  |
| 82 | Prana – Kiba | Classic Goa Trax / 2019 / Psy-Trance | Cyclone: **Matsuri Productions** / **1996** / Goa Trance | Matsuri Productions / 1995 / electronic, goa trance |  |
| 83 | Raja Ram, Riktam, Space Cat – Snorkel Blaster | HOMmega Productions / 2000 / Psy-Trance | Shapes Of Sound: HOM-Mega Productions / **0** / Psy-Trance, Goa Trance | not found |  |
| 84 | Space Cat – Spacecat | HOMmega Productions / 2000 / Psy-Trance | Shapes Of Sound: **YoYo Records (2)** / 2000 / Psy-Trance, Goa Trance | not found |  |
| 85 | Simon Posford, Space Cat – Invasion | HOMmega Productions / 1999 / Psy-Trance | Beam Me Up: **Avatar Records** / **2009** / Psy-Trance, Goa Trance | Trancelucent Productions / 2014 / psytrance |  |
| 86 | Orion – Nazca Spider  | memo604 / 2020 / Psy-Trance | Futuristic Poetry: **Symbiosis Records** / **1997** / Goa Trance, Tribal | Symbiosis Records / 1997 / electronic, goa trance |  |
| 87 | Children Of The Doc – Police 106 | Goahunter / 1999 / Psy-Trance | Police 106 EP: GoaHunter Records / **2021** / Psy-Trance | Melodia Records / 1998 / goa trance, psytrance |  |
| 88 | Sun Project – At The Edge of Time | Classic Goa Trax / 2021 / Psy-Trance | Sexdrugs & Acidtrance / At The Edge Of Time: **Spirit Zone Recordings** / **1997** / Goa Trance | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 89 | Sun Project – Energia Magica | Classic Goa Trax / 2021 / Psy-Trance | Macrophage: **Spirit Zone Recordings** / **1998** / Goa Trance, Downtempo, Dub, Progressive Trance, Tribal, Industrial, Psy-Trance | YoYo Records / 1998 / ambient, electronic |  |
| 90 | Sun Project – Going With the Flow | Classic Goa Trax / 2021 / Psy-Trance | Drosophila: **Spirit Zone Recordings** / **1997** / Goa Trance, Acid, Tribal, Downtempo | Spirit Zone Recordings / 1997 / downtempo, electronic |  |
| 91 | Filteria – Unfiltered | Record Union / 2022 / Psy-Trance | Heliopolis: **Suntrip Records** / **2006** / Goa Trance, Downtempo | Suntrip Records / 2006 / ambient, electronic |  |
| 92 | Filteria – Dog Days Bliss | Record Union / 2022 / Psy-Trance | Lost In The Wild: **Suntrip Records** / **2013** / Goa Trance | Suntrip Records / 2013 / electronic, goa trance |  |
| 93 | Filteria – Life Never Sleeps | Record Union / 2022 / Psy-Trance | Lost In The Wild: **Suntrip Records** / **2013** / Goa Trance | Suntrip Records / 2013 / electronic, goa trance |  |
| 94 | Ott – One Day I Wish To Have This Kind Of Time | DistroKid / 2011 / Electronica | Mir: **Ottsonic Music** / 2011 / Dub, Downtempo, Dubstep, Ambient | Ottsonic Music / 2011 / electronic, ambient |  |
| 95 | Hallucinogen – LSD | Twisted Records Ltd / 2024 / Psy-Trance | Alpha Centauri / LSD: **Dragonfly Records** / **1994** / Goa Trance | Dragonfly Records / 1994 / electronic, goa trance |  |
| 96 | Hallucinogen – Orphic Thrench | Twisted Records Ltd / 2024 / Psy-Trance | Twisted: **Dragonfly Records** / **1995** / Goa Trance | Dragonfly Records / 1995 / goa trance, electronic |  |
| 97 | Hallucinogen – Alpha Centauri | Twisted Records Ltd / 2024 / Psy-Trance | Twisted: **Dragonfly Records** / **1995** / Goa Trance | Dragonfly Records / 1995 / goa trance, electronic |  |
| 98 | Hallucinogen – Dark Magus | Twisted Records Ltd / 2024 / Psy-Trance | Twisted: **Dragonfly Records** / **1995** / Goa Trance | Dragonfly Records / 1995 / goa trance, electronic |  |
| 99 | Hallucinogen – Shamanix | Twisted Records Ltd / 2024 / Psy-Trance | Twisted: **Dragonfly Records** / **1995** / Goa Trance | TIP Records / 1995 / electronic, goa trance |  |
| 100 | Hallucinogen – Snarling Black Mabel | Twisted Records Ltd / 2024 / Psy-Trance | Twisted: **Dragonfly Records** / **1995** / Goa Trance | Dragonfly Records / 1995 / goa trance, electronic |  |
| 101 | Hallucinogen – Fluoro Neuro Sponge | Twisted Records Ltd / 2024 / Psy-Trance | Twisted: **Dragonfly Records** / **1995** / Goa Trance | Dragonfly Records / 1995 / electronic, goa trance |  |
| 102 | Hallucinogen – Solstice | Twisted Records Ltd / 2024 / Psy-Trance | Twisted: **Dragonfly Records** / **1995** / Goa Trance | Dragonfly Records / 1995 / goa trance, electronic |  |
| 103 | Hallucinogen – Demention | Twisted Records Ltd / 2024 / Psy-Trance | The Lone Deranger: Twisted Records / **1997** / Goa Trance | Twisted Records / 1997 / electronic, goa trance |  |
| 104 | Hallucinogen – Snakey shaker | Twisted Records Ltd / 2024 / Psy-Trance | The Lone Deranger: Twisted Records / **1997** / Goa Trance | Twisted Records / 1997 / electronic, goa trance |  |
| 105 | Hallucinogen – Trancespotter | Twisted Records Ltd / 2024 / Psy-Trance | Spacepussy: **Dragonfly Records** / **1996** / Goa Trance | Dragonfly Records / 1996 / electronic, goa trance |  |
| 106 | Hallucinogen – Horrorgram | Twisted Records Ltd / 2024 / Psy-Trance | The Lone Deranger: Twisted Records / **1997** / Goa Trance | Twisted Records / 1997 / electronic, goa trance |  |
| 107 | Hallucinogen – Deranger | Twisted Records Ltd / 2024 / Psy-Trance | Deranger: Twisted Records / **1996** / Goa Trance | Twisted Records / 1996 / electronic, goa trance |  |
| 108 | Hallucinogen – Jiggle Of The Sphinx | Twisted Records Ltd / 2024 / Psy-Trance | The Lone Deranger: Twisted Records / **1997** / Goa Trance | Twisted Records / 1997 / electronic, goa trance |  |
| 109 | Mandra Gora – Wicked Warp  | memo604 / 2021 / Psy-Trance | Mandrake–EP: **Gaia Tonträger** / **1994** / Goa Trance | Dragonfly Records / 1993 / electronic, goa trance |  |
| 110 | Deedrah – Self Oscillation | HOMmega Productions / 1997 / Psy-Trance | Self Oscillation: **Distance** / 1997 / Psy-Trance, Goa Trance, Breakbeat | Substance / 1997 / breakbeat, electronic |  |
| 111 | Bamboo Forest – Breath | Bamboodelic Recordings / 2003 / Psy-Trance | Bamboo Forest: **Vision Quest** / 2003 / Psy-Trance, Downtempo | Vision Quest / 2003 / electronic, psytrance |  |
| 112 | Tandu – Alien Pump | HOMmega Productions / 1997 / Psy-Trance | Multimoods: **Phonokol** / 1997 / Goa Trance, Psy-Trance | פונוקול / 1997 / electronic, goa trance |  |
| 113 | Tandu – The System | HOMmega Productions / 1997 / Psy-Trance | Multimoods: **Phonokol** / 1997 / Goa Trance, Psy-Trance | פונוקול / 1997 / electronic, goa trance |  |
| 114 | Tandu – Naughty Moves | HOMmega Productions / 1997 / Psy-Trance | Multimoods: **Phonokol** / 1997 / Goa Trance, Psy-Trance | פונוקול / 1997 / electronic, goa trance |  |
| 115 | Tandu – New Aura | HOMmega Productions / 1997 / Psy-Trance | Multimoods: **Phonokol** / 1997 / Goa Trance, Psy-Trance | פונוקול / 1997 / electronic, goa trance |  |
| 116 | Tandu – Orca | HOMmega Productions / 1997 / Psy-Trance | Multimoods: **Phonokol** / 1997 / Goa Trance, Psy-Trance | פונוקול / 1997 / electronic, goa trance |  |
| 117 | Tandu – Visually Distorted | HOMmega Productions / 1997 / Psy-Trance | Multimoods: **Phonokol** / 1997 / Goa Trance, Psy-Trance | פונוקול / 1997 / electronic, goa trance |  |
| 118 | Tandu – Rodeo | HOMmega Productions / 1997 / Psy-Trance | Multimoods: **Phonokol** / 1997 / Goa Trance, Psy-Trance | פונוקול / 1997 / electronic, goa trance |  |
| 119 | Tandu – Interstealer Dawn | HOMmega Productions / 1997 / Psy-Trance | Multimoods: **Phonokol** / 1997 / Goa Trance, Psy-Trance | פונוקול / 1997 / electronic, goa trance |  |
| 120 | Oforia – Maximiser | HOMmega Productions / 1998 / Psy-Trance | Delirious: **Dragonfly Records** / 1998 / Goa Trance, Psy-Trance | Dragonfly Records / 1998 / electronic, goa trance |  |
| 121 | Oforia – Cream | HOMmega Productions / 1998 / Psy-Trance | Delirious: **Dragonfly Records** / 1998 / Goa Trance, Psy-Trance | Dragonfly Records / 1998 / trance |  |
| 122 | Man With No Name – The Breech | EastWest U.K. / 2004 / Pop | Earth Moving The Sun: **Perfecto** / **1998** / **Goa Trance** | Kinetic Records / 1998 / goa trance, electronic |  |
| 123 | Man With No Name – Tarantula | EastWest U.K. / 2004 / Pop | Earth Moving The Sun: **Perfecto** / **1998** / **Goa Trance** | Kinetic Records / 1998 / goa trance, electronic |  |
| 124 | Man With No Name – Spaghettification | EastWest U.K. / 2004 / Pop | Earth Moving The Sun: **Perfecto** / **1998** / **Goa Trance** | Kinetic Records / 1998 / electronic, goa trance |  |
| 125 | Man With No Name – Parallel Universe | EastWest U.K. / 2004 / Pop | Earth Moving The Sun: **Perfecto** / **1998** / **Goa Trance** | Kinetic Records / 1998 / electronic, goa trance |  |
| 126 | Man With No Name – Possessed | EastWest U.K. / 2004 / Pop | The First Day (Horizon): **Perfecto** / **1998** / **Trance, Goa Trance** | Kinetic Records / 1998 / goa trance, electronic |  |
| 127 | Man With No Name – Treacle | EastWest U.K. / 2004 / Pop | Earth Moving The Sun: **Perfecto** / **1998** / **Goa Trance** | Kinetic Records / 1998 / electronic, goa trance |  |
| 128 | Man With No Name – The First Day | EastWest U.K. / 2004 / Pop | The First Day (Horizon): **Perfecto** / **1998** / **Trance, Goa Trance** | Kinetic Records / 1998 / electronic, goa trance |  |
| 129 | Man With No Name – Own the World | EastWest U.K. / 2004 / Pop | Vavoom! / Own The World: **Perfecto** / **1998** / **Trance, Goa Trance** | Kinetic Records / 1998 / goa trance, electronic |  |
| 130 | Man With No Name – Camouflage | EastWest U.K. / 2004 / Pop | Earth Moving The Sun: **Perfecto** / **1998** / **Goa Trance** | Kinetic Records / 1998 / goa trance, electronic |  |
| 131 | Man With No Name – Seratonin Sunrise | EastWest U.K. / 2004 / Pop | Earth Moving The Sun: **Perfecto** / **1998** / **Goa Trance** | EastWest Japan / 1998 / goa trance, electronic |  |
| 132 | Power Source – Vorlan | Melodia Records / 1999 / Dance / Pop | Cosmic Waves: Melodia Records (2) / 1999 / **Goa Trance, Trance** | Melodia Records / 1999 / electronic, goa trance |  |
| 133 | Man With No Name – Vavoom! | EastWest U.K. / 2004 / Pop | Earth Moving The Sun: **Perfecto** / **1998** / **Trance, Goa Trance** | Perfecto Records / 1998 / electronic, goa trance |  |
