# Audio verification: primary-source research

Research date: 2026-09-22. Scope: `src/flackey/verify.py`. No application code changed. **Source facts** below come from codec authors, standards, or tool documentation. **Audit deductions and recommendations** are our reasoning about the implementation, not claims that these sources have validated this detector.

## What the formats actually tell us

| Format | Source-backed distinction | Implication for this app |
|---|---|---|
| FLAC | Lossless audio coding preserves the supplied audio; its stream metadata contains sample rate and a checksum of unencoded audio. [Xiph overview](https://www.xiph.org/flac/documentation_format_overview.html) | Can establish representation and integrity; cannot establish what happened before encoding. |
| WAV | WAVEFORMATEX distinguishes PCM, IEEE float, and other registered coding formats. Sample rate, channel count, and sample size are separate fields. [Microsoft WAVEFORMATEX](https://learn.microsoft.com/en-us/windows/win32/api/mmreg/ns-mmreg-waveformatex) | The container name or extension alone does not establish losslessness. |
| AIFF | FFmpeg exposes AIFF as a file format, separately from its audio codecs. [FFmpeg supported formats/codecs](https://www.ffmpeg.org/general.html) | Read the actual codec; do not identify containers from PCM endianness. |
| ALAC | Apple's lossless codec supports 16/20/24/32-bit samples and multiple sample rates and channel counts. [Apple implementation](https://github.com/macosforge/alac) | Legitimate lossless encoding missing from the current allowlist; extension alone is insufficient. |
| MP3 | LAME documents irreversible encoding and CBR, ABR, and quality-targeted VBR modes. [LAME FAQ](https://lame.sourceforge.io/lame-faq.en.php), [official usage](https://svn.code.sf.net/p/lame/svn/trunk/lame/USAGE) | 320 kbps describes a current encoding setting, not its ancestry. VBR below 320 is not intrinsically fraudulent. |
| AAC | FFmpeg's AAC encoder provides a selectable cutoff; when unspecified, it can adjust cutoff dynamically. Its tools include perceptual noise substitution. [FFmpeg codec documentation](https://www.ffmpeg.org/ffmpeg-codecs.html) | A single fixed spectral edge is not a universal signature of lossy coding. |
| Opus | The standard explicitly separates encoded audio bandwidth and decoder sample rate; a 48 kHz decoder can decode narrowband audio. Fullband bandwidth is 20 kHz. [RFC 6716, §2.1.3](https://www.rfc-editor.org/rfc/rfc6716.html#section-2.1.3) | A 48 kHz decoded stream need not contain audio to 24 kHz, and high-frequency coverage does not prove losslessness. |

**Audit deduction:** Codec, container, sample rate, bit depth, bitrate, audible quality, integrity, and provenance answer different questions. A decoder output sample format is not necessarily an original recording bit depth. Larger stored dimensions cannot certify a better source. For integer PCM, raw payload rate is sample rate × channels × bits/sample; compressed bitrate follows a different relationship. Microsoft documents the equivalent byte-rate relationship for PCM in WAVEFORMATEX.

## Why a cutoff cannot prove provenance

**Source facts:** LAME lets callers set lowpass cutoff and transition width independently; its documentation distinguishes VBR quality settings from average bitrate. It recommends lossless encoding for archival preservation because MP3 changes its input. [Official LAME usage](https://svn.code.sf.net/p/lame/svn/trunk/lame/USAGE)

**Source facts:** FFmpeg's resampler performs sample-rate conversion, rematrixing, and sample-format conversion. It exposes filtering, cutoff, and dithering parameters; these operations themselves change the analyzed samples and spectrum. [FFmpeg resampler documentation](https://www.ffmpeg.org/ffmpeg-resampler.html)

**Audit deductions:**

- A sharp lowpass can be deliberately applied to an original master before lossless storage. That has no lossy codec in its history. Therefore the code comment that music never has such a cliff is an invalid premise.
- A lossy-derived file can acquire high-frequency noise, dither, or processing later. Its spectral edge can disappear without restoration of the discarded information.
- Even a perfect detector of lowpass filtering would detect filtering, not uniquely identify its cause. Mapping an edge to an exact prior bitrate is underdetermined.
- Two histories may produce the same decoded samples: a PCM sequence saved directly to FLAC, or that same sequence obtained from a decoder and then saved to FLAC. A function of the final samples alone cannot distinguish the histories. Trusted provenance or a trusted reference is required for stronger claims.
- Frequency coverage is not a perceptual score. Noise can increase coverage; sparse or intentionally filtered musical material can reduce it.

The defensible output is **“spectral evidence consistent with prior bandwidth limiting; source history unverified”**, with measured evidence and limitations. No edge means **“no edge detected”**, not **“genuine lossless”**.

## Concrete implementation concerns

These are code-inspection findings, not empirical accuracy estimates:

1. The analysis always converts to mono, 44.1 kHz, signed 16-bit PCM. This discards channel differences, alters high-resolution samples, and may introduce a resampling edge. Opposite-polarity stereo can cancel in the mono mix. Analyze channels separately at native rate with floating-point decode.
2. A single middle 60-second window cannot establish properties of the rest of the track. Sample several active windows, preserve results per window, and abstain on insufficient coverage or silence.
3. `spectral_cutoff_hz()` returns 22050 when it finds no cliff. Silence also has no cliff. That sentinel is presented as evidence of content to Nyquist even though no such energy is required.
4. `band_levels_db()` averages power over time, then logs each FFT bin and averages the logarithms within each band. This is not average band power in dB. Sum or average linear power within bands, normalize the estimator, then convert to dB; document the reference and noise floor.
5. Taking the highest detected cliff can select a small high-frequency feature beyond the meaningful audio band. Examine sustained attenuation, active-frame consistency, and energy above the edge rather than a single adjacent-band difference.
6. The absolute 20 kHz lossless threshold cannot be appropriate for a legitimate lossless source whose native Nyquist limit is below 20 kHz. Tie measurable bands to native rate and mark out-of-range evidence unavailable.
7. PCM codec mapping includes only signed 16/24-bit LE/BE variants; legitimate float or 32-bit PCM is unsupported. Container and codec should be stored independently.
8. Stream bitrate falling back to total container bitrate and rounding before a strict comparison can blur a policy threshold. Unknown bitrate should remain unknown; frame-level evidence is appropriate when enforcing a specific MP3 encoding policy.

## Integrity and identity are useful independent checks

**Source facts:** `flac -t` detects stream errors and mismatch between decoded audio and stored MD5, even when the bitstream is otherwise valid. [Xiph FLAC tool](https://www.xiph.org/flac/documentation_tools_flac.html)

**Deduction:** This establishes consistency with the file's checksum, not authenticity of the master. A transcoded source can be encoded into a perfectly valid, checksum-correct FLAC. Report checksum unavailable distinctly from checksum verified or mismatch.

**Source facts:** Chromaprint targets near-identical audio identification, duplicates, and long-stream monitoring; it trades some precision/robustness for search performance. [Project README](https://github.com/acoustid/chromaprint/blob/master/README.md)

**Deduction:** Fingerprints can help verify the intended recording or compare candidates, but a match cannot certify sample identity or lossless ancestry. A trusted reference decoded-PCM comparison is stronger, provided alignment, channels, sample rate, gain, and mastering version are controlled. Byte or PCM hashes identify equality under the chosen representation; their evidential value depends on trust in the reference.

## Recommended order of improvements

1. **Correct the verdict semantics first.** Separate technical validity, format-policy compliance, spectral suspicion, and source confidence. Use explicit unknown/inconclusive states. Reserve definitive failure for supported facts such as decode failure or actual policy mismatch.
2. **Fix observability.** Record actual codec/container, native rate, channels, depth where meaningful, bitrate source/mode, analyzed windows, energy/noise floor, and detector version. Use full-file decode for integrity, independently of spectral sampling.
3. **Improve the measurement.** Native-rate floating-point, per-channel analysis, multiple active windows, linear-power aggregation, sustained-edge measures, and plots showing what drove the warning. Increased complexity should earn its place in held-out evaluation.
4. **Build ground truth before classifiers.** Include known original masters, clean filtered masters, low-rate original PCM, silence, tones, anti-phase stereo, transient/sparse music, different encoders and settings, lossy-to-lossless transcodes, repeated lossy encodes, noise-added transcodes, corruption, and truncation. Keep masters/artists separated between training and evaluation to avoid leakage.
5. **Report meaningful performance.** False rejection of legitimate originals, missed transcodes, and abstention/coverage, stratified by source and codec. Do not publish confidence percentages unless calibrated on representative held-out data. Synthetic fixtures establish counterexamples; they do not establish real-music accuracy.
6. **Add stronger evidence when available.** Trusted vendor/source history, lossless-reference PCM comparison, and fingerprint identity. Consider additional forensic features or learned models only after establishing this baseline; label resulting evidence probabilistically and retain abstention.

**Product decision to clarify:** Is success compliance with an explicit delivery format (e.g. MP3 CBR 320), detection of suspicious ancestry, archival preservation, or listening suitability? These require different policies and should not share one undifferentiated pass/fail flag.
