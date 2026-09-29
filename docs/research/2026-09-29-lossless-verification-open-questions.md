# Lossless verification: the open questions from issue #97

Research date: 2026-09-29. Branch `audio-verification` at `d1920b8` (draft detector `2.0`). No code changed.
This builds on [primary sources](2026-09-22-audio-verification-primary-sources.md) and
[detector methods](2026-09-22-lossless-detector-methods.md) and does not repeat them.

Three kinds of statement are kept apart:
- **Source fact**: from the codec, standard or vendor, linked inline.
- **Measured**: run by us on an M3 Pro with FFmpeg 9.0.2 and LAME 4.0. The scripts are in the session scratchpad.
- **Recommendation**: our reasoning.

## Answers in short

1. **21 kHz.** The number barely matters. At 44.1 kHz the draft never searches above 21,050 Hz, so any edge it finds counts as suspicious. The real problem: genuine masters and LAME 320 or Opus transcodes all end near 20–21 kHz. Decide on the whole file instead: suspicious if an edge exists and **no window reaches more than 500 Hz above it**. Give MP3 its own edge threshold at 19.5 kHz, not 18 kHz.
2. **`inconclusive`: this depends on answer 1.** Under the draft's voting, every real-music transcode we made landed in `inconclusive`, including LAME 128, so it must keep rejecting. With the whole-file rule those become `suspicious`. Then *(product recommendation)*: on Soulseek, try the other picks first and file the best inconclusive copy with a flag. On the Deezer MP3 path, accept with a flag.
3. **Keep the full decode.** The draft's flags miss FLAC bit flips. Use `crccheck`, check the MD5 and sample count in the same pass, and compare MP3 length against its Info header. Cost: 0.2–2.7 s against a 18–202 s download.
4. **Corpus.** Use the owner's purchased lossless files plus MUSDB18-HQ (non-commercial terms). FMA and MTG-Jamendo are MP3 only. Take about 100 masters with about 20 recipes each, split by master, and store measurements, not audio.
5. **Tests.** 4 existing worker tests **fail on the branch**: a mono 64 kbps transcode now reads `clear`. CI's last run on main skipped 223 tests, and we could not confirm the reason. Check that before adding the 12 generated fixtures in section 5.

## 1. Is 21 kHz right?

### Source fact: encoder lowpass at high settings, 44.1 kHz stereo

| Encoder | Lowpass | Source |
|---|---|---|
| LAME CBR 128 / 192 / 256 / 320 | 17.0 / 18.6 / 19.7 / 20.5 kHz | `freq_map` in [lame.c 3.100](https://sourceforge.net/p/lame/svn/HEAD/tree/tags/RELEASE__3_100/lame/libmp3lame/lame.c) |
| LAME `-V0` / `-V2` | none / 18.5 kHz | Same file. The V0 table value is 24 kHz, clamped to Nyquist, so no filter is built. |
| FFmpeg `aac` 256 / 320 | 21.2 / 22.0 kHz | [psymodel.h](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/psymodel.h) plus the ×1.15 factor in [aacenc.c](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/aacenc.c) (our arithmetic) |
| FDK AAC, CBR ≥ 192 stereo / VBR 5 | 17.0 / 19.3 kHz | [bandwidth.cpp](https://github.com/mstorsjo/fdk-aac/blob/master/libAACenc/src/bandwidth.cpp) |
| Apple AAC 256 | not published | [Apple Digital Masters](https://www.apple.com/apple-music/apple-digital-masters/docs/apple-digital-masters.pdf) says only "VBR", target 256 kbps |
| libvorbis q5 / q6+ | 20.1 kHz / none | [psych_44.h](https://github.com/xiph/vorbis/blob/master/lib/modes/psych_44.h) |
| Opus, any bitrate | 20 kHz | [RFC 6716 §2](https://www.rfc-editor.org/rfc/rfc6716.html#section-2): "Opus never codes audio above 20 kHz" |

### Source fact: what limits genuine 44.1 kHz masters

- **ADC decimation filters.** The passband ends at 0.454–0.47 fs, which is 20.0–20.7 kHz at 44.1 kHz. The stopband starts near 0.58 fs, above Nyquist. Sources: [TI PCM1808](https://www.ti.com/lit/ds/symlink/pcm1808.pdf), [Cirrus CS5381](https://statics.cirrus.com/pubs/proDatasheet/CS5381_F3.pdf), [AKM AK5572](https://www.akm.com/content/dam/documents/products/audio/audio-adc/ak5572en/ak5572en-en-datasheet.pdf).
- **Resamplers**, when a 48 or 96 kHz master is delivered at 44.1 kHz:

  | Resampler | Edge at 44.1 kHz | Source |
  |---|---|---|
  | SoX default | 95 %, about 21 kHz | [sox.1](https://github.com/chirlu/sox/blob/master/sox.1) |
  | libsoxr | 0.913, 0 dB point, 20.1 kHz | [soxr.h](https://github.com/chirlu/soxr/blob/master/src/soxr.h) |
  | FFmpeg swr | 0.97, −6 dB point, 21.4 kHz | [ffmpeg-resampler](https://ffmpeg.org/ffmpeg-resampler.html) |

- Apple asks for native high-rate masters and then applies its own SRC ([PDF](https://www.apple.com/apple-music/apple-digital-masters/docs/apple-digital-masters.pdf)). A resampled 44.1 kHz master is therefore normal.

**Deduction:** genuine edges between 20.0 and 21.4 kHz overlap LAME 320 at 20.5 kHz and Opus at 20 kHz. No single frequency separates them.

### Measured

`lame --verbose -b N` prints these transition bands:

| Setting | Transition band |
|---|---|
| 128 | 16,538–17,071 Hz |
| 192 | 18,671–19,205 Hz |
| 256 | 19,383–19,916 Hz |
| 320 | 20,094–20,627 Hz |
| `-V0` | "polyphase lowpass filter disabled" |

Then the draft `analyze()` on the test files:
- Synthetic: 6-minute pink noise.
- Real: two library AIFFs, decoded, encoded and written back to FLAC 16/44.1 in the scratchpad (`gen.sh`, `genr.sh`, `matrix.py`, `rule.py`).
- Edges are reported at 250 Hz band starts.

| File | Pink noise | Real music (2 tracks) |
|---|---|---|
| Genuine 16/44.1, 24/96, and 44.1 → 96 k upsample | clear | clear |
| Genuine 96 → 44.1 k, swr default / swr cutoff 0.95 | clear / clear | — |
| **Genuine 96 → 44.1 k, swr cutoff 0.91** (−6 dB at 20.07 kHz) | **suspicious, edge 20,500** | — |
| LAME 128 / 192 / 256 / 320 → FLAC | suspicious at 16,750 / 18,750 / 19,500 / 20,250 | **all inconclusive**, 2–4 of 6 votes |
| LAME 320 plus −60 dB noise, or upsampled to 96 k | suspicious | — |
| Opus 256 → FLAC | suspicious at 20,250 | inconclusive |
| **LAME V0; FFmpeg AAC 256; Apple AAC 256 and 320 (`aac_at`) → FLAC** | **clear** | **clear**, bandwidth 21.25–22.05 kHz |

What this shows:
- **Voting fails on real music.** An edge only shows in windows with high-frequency content. The draft catches real transcodes only because it *rejects inconclusive*.
- **Bandwidth analysis is blind to AAC ≥ 256 and to V0.** Catching those needs option C (quantization) from the [methods note](2026-09-22-lossless-detector-methods.md).
- **libsoxr is not in this FFmpeg build**, so the soxr and ADC cases above are *not measured*. A native 44.1 kHz ADC rolls off through Nyquist, which may give a slope rather than a 20 dB step.

**The owner's library**, all 213 files, read-only (`lib.py`, 76 s):
- 11 of 190 AIFFs (5.8 %) are `inconclusive`. The draft would reject them, although all of them passed today's detector.
- Some show single low edges in 1 window of 6, while other windows reach 22 kHz. Examples: Hallucinogen *LSD* (10 kHz), *Bamboo Forest* (15 kHz).
- Others show 20.0–20.5 kHz edges in 2 of 6 windows. Examples: Astral Projection, Filteria, Mandra Gora. That fits a 320 source. We have no ground truth.

**Code fact.** The draft skips edges where `hz[i] > sr / 2 - 1000`, so at 44.1 kHz only a 21,000 Hz edge could clear `MIN_LOSSLESS_CUTOFF = 21_000`. At 96 kHz the threshold does separate files: a CD master upsampled to 96 kHz is clear, and a 320 upsampled to 96 kHz is flagged. Both were measured.

### Recommendation

- **Lossless path: the whole-file rule.** A file is suspicious when some window has an edge *and* no window's bandwidth exceeds the highest edge + 500 Hz.
  - On the same files (`rule.py`) it:
    - turns all 10 real-music transcodes, LAME 128 through 320 and Opus, into `suspicious`;
    - clears 10 of the 11 library inconclusives (Mandra Gora stays flagged: edge 20,500, bandwidth 20,750);
    - keeps every synthetic suspicious result, except the 320 upsampled to 96 k, whose resampling residue reaches 25 kHz.
  - This result is **in-sample**. Section 4 is how to test it properly.
  - It still falsely rejects genuine masters that were resampled with a steep filter ending ≤ 20.5 kHz. It still misses AAC ≥ 256, V0, Vorbis q6+, and anything noise-filled above the edge.
- **MP3 path: whole-file edge ≤ 19,500 Hz is suspicious.**
  - A real LAME 320 made from a lossless source still carries LAME's 20.5 kHz lowpass.
  - The 19 library MP3s Flackey filed as 320 (tag `flackey: verified 320 kbps`, presumably from the Deezer bot): 7 show an edge, always at 20,000–20,250, and one is tagged `LAME3.99r`. One of them, *Playing Games*, also has a 17,500 edge in one window. The draft calls it inconclusive; the whole-file rule clears it.
  - The draft's 18 kHz threshold passed 320 re-encodes of LAME 192 (edge 18,750) and 256 (19,500). The 19.5 kHz rule catches them, but the margin is one 250 Hz band.
  - A 320 re-encoded from V0 or AAC gets LAME's own 20.5 kHz lowpass, so bandwidth cannot see it.

## 2. Should `inconclusive` reject?

**Code fact: what a reject costs.**
- **Soulseek.** A failed verdict returns `verify_failed`. That is in `SECOND_PICK_AFTER`, so the next survivor is tried, up to `lossless_max_picks = 4` within budget. After the last pick:
  - a Deezer candidate falls back to the MP3;
  - catalogue-only and query-only candidates go to `_no_route`, and the request ends in `ERROR`.
- **Deezer MP3.** In `_verify_and_file` a failed verdict is **terminal** (`REJECTED`), and the file is not kept. A `VerifyError`, such as a failed decode, reaches the generic handler, and the request ends in `ERROR`.

**Recommendation (product, conditional on section 1):**
- **Draft voting unchanged:** keep rejecting inconclusive. Under it, even real LAME 128 transcodes are inconclusive, so accepting would let them in.
- **Whole-file rule adopted:** inconclusive then mostly means "not enough high-frequency content to judge".
  - **Soulseek:** hold the file and try the remaining picks. File the first clear one. If none is clear, file the best inconclusive copy with an "unverified" flag and its spectrogram, and don't drop to the MP3. This needs a pick-loop change: keep one `LosslessHit` and don't delete its `tmp`.
  - **Deezer MP3:** reject only `policy_rejected` and `suspicious`. Accept inconclusive with a flag, because a reject loses the track.

## 3. Is the full-decode integrity check worth it?

### Source fact

- **FLAC** has three checks ([RFC 9639](https://www.rfc-editor.org/rfc/rfc9639.html)):
  - a CRC-8 on each frame header (§9.1.8);
  - a CRC-16 on each whole frame (§9.3);
  - an MD5 of the decoded audio plus total samples in STREAMINFO (§8.2). Either can be 0, meaning "unknown".
- **`flac -t`** checks all three ([docs](https://xiph.org/flac/documentation_tools_flac.html)).
- **FFmpeg:**
  - `err_detect` defaults to 0 ([options_table.h](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/options_table.h), line 136).
  - [flacdec.c](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/flacdec.c) checks the CRC-16 only under `crccheck`, `compliant` or `aggressive`. The header CRC-8 is always checked.
  - FFmpeg **never checks the MD5** ([flac.c](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/flac.c) skips it).
- **MP3's CRC** is opt-in (LAME `-p`, [USAGE](https://svn.code.sf.net/p/lame/svn/trunk/lame/USAGE), read via a GitHub mirror). It covers only the header and side info ([mpegaudiodec_template.c](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/mpegaudiodec_template.c) `handle_crc`). ISO 11172-3 is paywalled.

### Measured

Test command: `ffmpeg -v error <flags> -i f -map 0:a:0 -vn -f null -` (`integ.sh`). Test files: a 6-minute FLAC 16/44.1 and a LAME 320. Each was damaged three ways: 1 byte flipped mid-file, 10 bytes flipped, and truncated to 90 %.

| Damage | Draft: `-xerror -err_detect explode` | `-xerror -err_detect crccheck+explode` |
|---|---|---|
| FLAC, 1 or 10 bytes flipped | **exit 0, silent** | exit 183, "CRC error" |
| FLAC, truncated | exit 183 | exit 183 |
| MP3, 1 byte flipped | exit 183 | exit 183 |
| MP3, 10 bytes flipped, or truncated | **exit 0** | **exit 0** |

- Without `-xerror` everything exits 0, and `_run` ignores stderr.
- `md5chk.py` decodes at native depth (`-c:a pcm_s16le|pcm_s24le -f data -`) and compares against STREAMINFO. It caught all three damaged FLACs.
- The truncated MP3 still reports 360 s from its `Info` header but decodes to 324 s. All 4 MP3s we inspected have an `Info` header, and none has a CRC.

Timings from `timing.sh` and `real.py` on 6–9 minute tracks:

| Step | Time |
|---|---|
| Full decode | 0.15–0.9 s |
| Draft `verify()` in total | 0.5–1.2 s |
| MD5 decode | 0.5 s at 16/44.1, 2.7 s at 24/96 |
| `fpcalc -length 0`, which runs after verify anyway | 0.2–1.1 s |

A Soulseek transfer takes 18–202 s ([spike findings](2026-09-07-soulseek-spike-findings.md)).

### Recommendation

- Keep the check, as **one** decode pass with `-xerror -err_detect crccheck+explode`, piping native-depth PCM into an MD5 and a sample counter.
- Report "MD5 absent" when the stored value is 0.
- For MP3, compare the decoded duration with the Info/Xing frame count.
- The added cost is about 1–3 s per track, about 1–15 % of a transfer. Verify runs under `self._cpu`, so this cost serialises across parallel downloads.
- A failure is a fact about the file. `verify_failed` already moves on to the next pick.

## 4. Labelled corpus

**Source fact: sources of genuine lossless.**

| Source | Format | Terms |
|---|---|---|
| Owner's purchases (Bandcamp, Beatport) | FLAC/WAV/AIFF | His own files. [Bandcamp](https://get.bandcamp.help/en/articles/15263285-which-audio-format-should-i-download) keeps the upload's depth and rate, but only [advises against](https://get.bandcamp.help/hc/en-us/articles/23020723948951-How-and-why-should-I-upload-lossless-files) lossy uploads and does not say it rejects them. Beatport pages returned 403. |
| [MUSDB18-HQ](https://zenodo.org/records/3338373) | WAV 44.1, 150 tracks | "educational purposes only … not … for any commercial purpose" |
| [MedleyDB](https://medleydb.weebly.com) | WAV 16/44.1, 196 multitracks | CC BY-NC-SA, research only, access on request |
| [FMA](https://github.com/mdeff/fma), [MTG-Jamendo](https://github.com/MTG/mtg-jamendo-dataset) | **MP3 only** | Usable only as lossy input |

The 2L test bench is "currently not available". The Internet Archive's licences and formats vary per item. The library AIFFs are **not ground truth**.

**Recommendation.**
- **Masters.** About 100: at least 60 from the owner's electronic purchases, the rest from MUSDB18-HQ, for internal evaluation only.
  - Split 70/30 **by master**.
  - With 0 false rejects on 100 genuine files, the rule of three bounds the rate below 3 % at 95 % confidence.
- **Recipes per master.**
  - **Genuine, must stay clear:** native; 96 → 44.1 via swr default, via swr 0.91, and via soxr where available; 16 → 24-bit padding (expect the padding warning only); −3 dB gain; 44.1 → 96 k.
  - **Transcodes back to FLAC:** LAME 128, 192, 256, 320, V0 and V2; FFmpeg AAC 256; `aac_at` 256 (macOS only); Opus 160 and 256; Vorbis q6 if libvorbis is available.
  - **Hard transcodes:** 320 plus −60 dB noise; 320 upsampled to 96/24; a 128 → 320 chain; one channel lossy; the middle third lossy.
- **Size.** About 2,000 files, at roughly 2.5 s each here, so about 1.5 h. Keep JSONL measurements and delete the audio.
- **Script outline:**

  ```text
  for each master, then each recipe:
      encode to temp -> FLAC -> analyze()
      append {master, recipe, label, analysis}
      delete temp
  tune on the 70 %
  report false rejects, misses and abstentions per recipe on the 30 %
  ```

## 5. Tests

**Measured on the branch** (`PYTHONPATH=<worktree>/src pytest -p no:cacheprovider --ignore=tests/live`): 791 passed, **4 failed**. The failures:
- `test_verify_failure_tries_the_second_pick`
- `…falls_back_without_a_rejection_row`
- `test_second_pick_needs_budget`
- `test_a_transfer_that_stopped_moves_to_the_next_survivor…`

All four are in `tests/test_worker_lossless.py`. Their fixture `_fake_flac` is 3 s of *mono* LAME 64 kbps. Its edge is only about 22 dB deep: −42 dB up to 16.5 kHz, then −64 dB. That misses the per-frame "≥ 20 dB in 75 % of frames" support, so the draft calls it `clear`. Main's mean-spectrum check rejected it.

The existing tests use `requires_ffmpeg` (a `skipif` in `tests/conftest.py`), and they generate `anoisesrc` audio with ffmpeg. There are no binary fixtures. The last CI run on main was `uv run pytest -q` on `ubuntu-latest`: 654 passed, **223 skipped**. Locally nothing skips. `-q` hides the reasons, so we could not confirm that ffmpeg is missing on CI. Add `-rs` or install ffmpeg in `checks.yml`, or none of the tests below run in CI. Use 20–30 s signals, and avoid `aac_at`.

| # | Generated fixture | Pins |
|---|---|---|
| 1 | PCM in AIFF and in WAV; `pcm_f32le` WAV | Format comes from the container. Float PCM is unsupported; the library holds one such file. |
| 2 | Monkeypatched ffprobe JSON (duration 0) and `tool_path` returning None | `VerifyError` messages |
| 3 | Silence; noise lowpassed below 8 kHz | `inconclusive`, and no padding warning |
| 4 | Numpy brick-wall at 16 kHz; a 12 kHz notch that recovers | Edge found / no edge |
| 5 | Mono LAME 64 → FLAC (today's `_fake_flac`) | Shallow edges must still be caught. This is the regression above. |
| 6 | Anti-phase stereo (L = −R) LAME 128 → FLAC | `suspicious`. The old mono downmix cancelled to silence. |
| 7 | Lossy in one channel only; lossy in the middle third only | `inconclusive` under the draft; change if the whole-file rule lands |
| 8 | 16-bit noise in 24-bit FLAC, versus true 24-bit | Padding warning on the first only |
| 9 | Genuine 96 kHz; CD upsampled to 96 kHz; LAME 320 upsampled to 96 kHz | clear, clear, suspicious |
| 10 | LAME 192 and 256 re-encoded to 320 MP3 | MP3-path threshold |
| 11 | FLAC with a flipped byte; truncated FLAC; truncated MP3 | Integrity. `xfail` the first and third until `crccheck` and the length check land. |
| 12 | 96 → 44.1 k with swr cutoff 0.91; a 192 kbps MP3 through `verify()` | Known false positive (`xfail`); `policy_rejected`, a spectrogram, and `analysis["version"] == "2.0"` |

## Community tools

- **[Fakin' The Funk](https://fakinthefunk.net):** proprietary GUI for Windows and macOS. It gives a bitrate/frequency estimate and a "defective file" check (header length vs scanned frames). It warns there "MIGHT be false positives".
- **Lossless Audio Checker:** the site did not resolve from here, so its CLI and licence are unverified.
- **Open source, MIT or Apache, not reviewed:** [flaccheck](https://github.com/dasunNimantha/flaccheck), [FLAC_Detective](https://github.com/Guillain-RDCDE/FLAC_Detective), [FLAD](https://github.com/Sg4Dylan/FLAD). Their accuracy figures are self-reported.

## Not verified

- **Unreachable first-party pages:** Beatport and Juno formats, Cambridge-MT and Musopen terms, losslessaudiochecker.com.
- **Unpublished:** Apple AAC's lowpass. We only measured `aac_at`.
- **Not in this FFmpeg build:** FDK, libvorbis and libsoxr. Those rows come from source code only.
- **Not tested:** ADC-style roll-off.
- **Not read:** how the MP3 demuxer treats a truncated last frame.
- **Unknown:** the reason for CI's 223 skips, and ground truth for the 11 inconclusive library files.
