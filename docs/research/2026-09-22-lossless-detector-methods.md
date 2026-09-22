# Implementable lossless-detector methods and established practice

Research date: 2026-09-22. Bounded primary-source review; application code unchanged. Recommendations and cautions are our deductions unless explicitly attributed.

## Was the original premise right?

Partly: spectral analysis is an established way to investigate suspicious files. A bandwidth boundary is useful evidence. The unsupported leap is treating one boundary as conclusive proof of a lossy source, or treating its absence as conclusive proof of an original master. The sources reviewed offer different tools for different questions; none establishes a universal standardized certification procedure for unknown-source lossless authenticity. This is a scoped research finding, not proof that no relevant standard exists anywhere.

## How existing workflows divide the problem

- **Visual inspection:** Spek generates spectrograms, displays codec/signal parameters, and permits channel, FFT-window and display-range selection. It is an inspection tool, not reference-based authentication. It is GPLv3. [Spek manual](https://github.com/alexkay/spek/blob/master/MANUAL.md), [project information](https://www.spek.cc/about)
- **File integrity:** FLAC test mode checks stream validity and decoded-audio MD5 against the stored value. That does not reveal pre-encoding history. [FLAC tool documentation](https://www.xiph.org/flac/documentation_tools_flac.html)
- **Reference matching for CD rips:** AccurateRip compares extracted tracks with a database of other rips. This addresses extraction correctness relative to matching discs, not whether a studio used lossy material. Third-party database access requires agreement; commercial access additionally requires a commercial license. [AccurateRip](https://accuraterip.com/), [access conditions](https://www.accuraterip.com/3rdparty-access.htm)
- **Blind forensic inference:** auCDtect and Lossless Audio Checker infer past processing from measured signal features. Their output depends on method assumptions and training/evaluation scope.

## Lossless Audio Checker research

The 2015 AES convention paper separates bit-depth expansion, sample-rate expansion, and lossy transcoding. It checks used quantization levels for the first, high-band STFT energy for the second, and codec re-quantization traces for the third. Its AAC procedure searches 1,024 offsets, four window configurations, and two stereo schemes, samples candidate scalefactors, and evaluates residual statistics; its stated transcoding scope is at most 48 kHz. The abstract reports 100% for upscaling/transcoding and 91.3% for upsampling **on its evaluation database**. AES says the abstract/precis were reviewed, while the complete manuscript was not. [Paper 9416](https://www.aes.org/e-lib/download.cfm/17972.pdf?ID=17972), [AES abstract](https://secure.aes.org/forum/pubs/conventions/?elib=17972)

The later 2019 JAES article describes a non-machine-learning quantization-error method for MPEG-AAC. Its abstract reports tests on 1,576 original and iTunes-AAC-transcoded files, with zero false positives and low or zero false negatives depending on computation settings; it also discusses truncation and gain robustness. These are experiment-specific results, not coverage of every codec, mastering chain, or adversarial modification. Only the abstract was accessible in this review. [Derrien 2019, DOI 10.17743/jaes.2019.0002](https://aes2.org/publications/elibrary-page/?id=19892)

Access limitation: the direct 2015 download returned HTTP 403 locally; relevant paper sections were available through the web search index. Consequently this note does not claim a full equation-by-equation reproduction of that paper.

## auCDtect: useful architecture, unavailable turnkey reproduction

Its author describes per-channel Fourier analysis across time segments, estimated boundary frequencies and statistical features, followed by a specially trained neural network and a maximum-likelihood disc-level decision. Its downloads describe the console program as freeware for 16-bit/44.1 kHz WAV/CD material. The public prose is not a complete model implementation with weights or a permissive source license. [Author's algorithm description](https://tausoft.org/ru/true-audio-checker-%D0%BE%D0%BF%D0%B8%D1%81%D0%B0%D0%BD%D0%B8%D0%B5-%D0%B0%D0%BB%D0%B3%D0%BE%D1%80%D0%B8%D1%82%D0%BC%D0%B0/), [downloads and scope](https://tausoft.org/en/true-audio-checker-%D0%B7%D0%B0%D0%B3%D1%80%D1%83%D0%B7%D0%BA%D0%B8/)

**Recommendation:** Adopt the transparent measurement ideas—time distribution, channel separation, statistics—without claiming an auCDtect implementation or importing its reported accuracy. A newly fitted classifier needs our own training data and held-out validation.

## Inspected open-source implementation: FlacCompagnon

Source inspected at commit `4f5164a44ad56a472ccd1e3e68d72d7466e0ff48`, cloned outside the repo to `/tmp/cratedigger-flaccompagnon-research`. Its [MIT license](https://github.com/craft-and-code/FlacCompagnon/blob/4f5164a44ad56a472ccd1e3e68d72d7466e0ff48/LICENSE) permits reuse subject to preserving its notice. No code was copied here.

### Option A: exact padding detection, minimal dependencies

The [bit-depth implementation](https://github.com/craft-and-code/FlacCompagnon/blob/4f5164a44ad56a472ccd1e3e68d72d7466e0ff48/core/src/analysis/bitdepth.rs) ORs the decoded integer samples, masks to declared width, and counts trailing zero bits. This detects a sample lattice consistent with exact zero-padding, such as a 16-bit integer sequence shifted into a 24-bit representation.

**Our implementation guidance:** Stream original integer samples without resampling, mixing, normalization, or dithering. Account for FFmpeg's left alignment if output is widened to 32 bits. Record analyzed sample count, nonzero count, padding-bit count, and channel. Report silence as insufficient evidence. Use wording such as “lowest eight stored bits are unused,” not “fake 24-bit master.” Nonzero low bits may be dither/noise and do not establish true source resolution. Gain or processing can destroy the exact lattice. This is a sample-precision measurement, not an analog effective-number-of-bits measurement.

### Option B: stronger spectral measurements, existing NumPy sufficient

The [spectrum implementation](https://github.com/craft-and-code/FlacCompagnon/blob/4f5164a44ad56a472ccd1e3e68d72d7466e0ff48/core/src/analysis/spectrum.rs) measures a boundary and the region above it; the [decision layer](https://github.com/craft-and-code/FlacCompagnon/blob/4f5164a44ad56a472ccd1e3e68d72d7466e0ff48/core/src/analysis/detections.rs) combines several signals with tuned thresholds.

**Our implementation guidance:** Keep native sample rate; decode floating-point channels separately; use overlapping windowed FFTs on active regions. Aggregate linear power before dB conversion. Estimate both edge depth and sustained energy beyond the edge, then retain boundary consistency across windows. Track sparse/silent windows and sample-rate limits. A repeated narrow boundary merits a warning. Neither one high-frequency bin nor broad noise should prove genuine content. Do not adopt fixed high-frequency quality rankings or upstream categorical ancestry labels without validation.

### Option C: AAC quantization-grid detector, meaningful but substantially more work

The inspected [re-quantization code](https://github.com/craft-and-code/FlacCompagnon/blob/4f5164a44ad56a472ccd1e3e68d72d7466e0ff48/core/src/analysis/requant.rs) implements an AAC-specific search. It maps MDCT coefficient magnitudes through a 3/4 power to expose quantization lattices, searches frame onset/window/stereo representations and long/short blocks, scans scalefactors, and scores repeated small residuals over bands. It includes empty-band guards and refines promising offsets over higher-energy frames. The implementation runs only for 44.1/48 kHz in its analyzer. Its comments claim 24/24 recall on a local calibration set; this review did not rerun that benchmark and does not treat it as general accuracy.

**Our implementation guidance:** A NumPy prototype could precompute windows and transforms, batch offset searches, and expose raw scores. A performant production version may warrant a compiled helper. Preserve the sample sequence: do not resample before this test. Validate transform normalization, AAC scale-factor bands, transition windows, alignment after cropping, stereo modes, encoder variants, and subsequent gain/dither/resampling. Test separately against lossless masters and MP3/Opus/Vorbis material. Do not label it generic lossy detection or substitute AAC's MDCT for MP3's hybrid analysis filterbank. A negative result means no supported AAC evidence found.

## What to implement first

1. Separate integrity, delivery-policy compliance, suspicious spectral evidence, unused stored bits, and source-history confidence.
2. Implement Options A and B with transparent numeric evidence and inconclusive states. These fit the existing FFmpeg/NumPy stack.
3. Add regression fixtures covering silence, exact padding, native lower-rate PCM, anti-phase stereo, clean lowpass masters, noise-added transcodes, real high-bitrate transcodes, and malformed/truncated files.
4. Evaluate on diverse known masters and real encoder outputs before setting thresholds. Split by original master, not randomly by derivative file, to prevent train/test leakage.
5. Treat Option C as a separately validated optional detector. It is a stronger research direction than further tuning a lone frequency threshold, but it does not replace provenance or trusted-reference comparison.

These recommendations improve the method's discrimination and honesty. They do not imply that all source histories are identifiable from decoded samples alone.
