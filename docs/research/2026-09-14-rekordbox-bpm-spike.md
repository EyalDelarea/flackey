# Issue #3: brief BPM assessment

Source: [issue and screenshots](https://github.com/EyalDelarea/flackey/issues/3).

The screenshots compare different releases: Beatport's Hallucinogen “L.S.D. Original Mix” on Twisted (1996), 6:43, 82 BPM, D minor; Tunebat's “LSD” on “A Voyage Into Trance Mixed By Paul Oakenfold” (2001), 6:13, 137 BPM, D major. These are not confirmed identical audio. Neither listing establishes the correct BPM of the user's file; 82 and 137 are not a half/double pair. Decimal BPM alone is not evidence of an error.

Rekordbox supports manual BPM entry, tapping, half/double BPM, and grid alignment adjustment. Normal analysis suits relatively consistent tempo; Dynamic suits changing tempo. See the [official 6.7 manual](https://cdn.rekordbox.com/files/20230316171900/rekordbox6.7.0_manual_EN.pdf), pp. 60, 75–76, 205. These documented controls should be checked in the user's installed version.

External XML import is documented by the [developer page](https://rekordbox.com/en/support/developer/). Its [XML specification](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf) separates `AverageBpm` metadata from `TEMPO` grid entries including time position and beat number. Writing a BPM tag alone does not establish a correctly aligned grid. Actual target-version import behavior remains untested.

Local code already retains catalog BPM (`src/flackey/catalog.py`) but deliberately omits BPM/key file tags (`src/flackey/tag.py`, `_values`). The README also deliberately avoids generated rekordbox XML. Automatic overwrite would change existing design decisions.

Recommendation: keep this a validation spike. Compare 5–10 exact audio files against matching release metadata, use Normal analysis for stable-tempo tracks, and verify alignment near the beginning and end. Use catalog BPM as a comparison signal, not unquestioned truth. Test one opt-in XML import on a duplicate track for BPM, grid alignment, and cue preservation before considering automated integration.
