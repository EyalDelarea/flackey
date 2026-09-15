const release = document.querySelector("#release-line");
const downloadLink = document.querySelector("#download-link");

// GitHub's /releases/latest endpoint deliberately excludes prereleases. Flackey is
// still in beta, so read the release list and select the newest published build.
fetch("https://api.github.com/repos/EyalDelarea/flackey/releases?per_page=10")
  .then((response) => {
    if (response.status === 404) return null;
    if (!response.ok) throw new Error("Release lookup failed");
    return response.json();
  })
  .then((releases) => {
    const data = Array.isArray(releases)
      ? releases.find((item) => !item.draft)
      : null;
    const asset = data?.assets?.find((item) => item.name === "Flackey.pkg");
    if (!release || !downloadLink || !asset?.browser_download_url) {
      if (release) release.textContent = "The first download is on its way.";
      return;
    }
    downloadLink.href = asset.browser_download_url;
    downloadLink.removeAttribute("aria-disabled");
    downloadLink.classList.remove("unavailable");
    downloadLink.innerHTML =
      'Download Mac installer <span aria-hidden="true">↓</span>';
    const version = String(data.tag_name || "").replace(/^v/, "");
    const size = `${(asset.size / 1e6).toFixed(1)} MB`;
    const date = new Intl.DateTimeFormat("en-GB", {
      day: "numeric",
      month: "long",
      year: "numeric",
    }).format(new Date(data.published_at));
    const status = data.prerelease ? "Beta" : "Stable";
    release.textContent = `Version ${version}v · ${status} installer · ${size} · ${date}`;
  })
  .catch(() => {
    if (release)
      release.textContent = "Release details are temporarily unavailable.";
  });

// The app-walkthrough demo. Every value below is interpolated per frame rather
// than stepped between a handful of states: the percentage, the arc that draws
// it, the byte counter and the speed all move continuously, which is what keeps
// this from reading as a recording of a few screenshots.
const appDemo = document.querySelector("#app-demo");
if (appDemo) {
  const el = (sel) => appDemo.querySelector(sel);
  const urlText = el(".ad-url");
  const statusText = el(".ad-status");
  const xferText = el(".ad-xfer");
  const artImg = el(".ad-art img");
  const platter = el(".ad-platter");
  const arc = el(".ad-arc");
  const spindle = el(".ad-spindle");
  const pctText = el(".ad-pct");
  const verified = el(".ad-verified");
  const group = el(".ad-group");
  const row = el(".ad-row");
  const steps = [...appDemo.querySelectorAll(".ad-step")];
  const checks = [...appDemo.querySelectorAll(".ad-check")];

  const URL_TEXT = "music.youtube.com/watch?v=KrbGBz8MlXo";
  const PLACEHOLDER = "Paste a YouTube, YouTube Music, or Spotify link";
  const CIRC = 2 * Math.PI * 19; // matches Platter.tsx's geometry
  const SIZE_MB = 61.0;

  // Cue sheet, in seconds. The gaps are the point: a phase that changes nothing
  // for a beat is what makes the next change read as a step forward.
  const T = {
    typeStart: 0.5,
    typeEnd: 1.9,
    press: 2.15,
    rowIn: 2.4,
    found: 3.9,
    dlStart: 4.7,
    dlEnd: 9.5,
    verify: 10.1,
    check1: 10.9,
    check2: 11.4,
    done: 12.1,
    fadeOut: 15.2,
    loop: 16.2,
  };

  // Written through a cache because these are strings: reassigning identical
  // text every frame would dirty layout 60 times a second for no visible change.
  const last = {};
  const put = (node, key, value) => {
    if (last[key] === value) return;
    last[key] = value;
    node.textContent = value;
  };
  const toggle = (node, cls, on) => node.classList.toggle(cls, on);

  const clamp01 = (n) => (n < 0 ? 0 : n > 1 ? 1 : n);
  // Between two cues, 0 → 1 on the app's own easing curve.
  const ramp = (t, from, to) => clamp01((t - from) / (to - from));

  let speed = 0;
  let prevDone = 0;
  let prevT = 0;

  const render = (t) => {
    const typing = t >= T.typeStart && t < T.press;
    const typed = Math.round(ramp(t, T.typeStart, T.typeEnd) * URL_TEXT.length);
    // Before the first keystroke the field shows its placeholder; after the
    // submit it is empty again, the way the real one clears itself.
    const empty = t >= T.press;
    put(
      urlText,
      "url",
      empty ? "" : typed === 0 ? PLACEHOLDER : URL_TEXT.slice(0, typed),
    );
    toggle(urlText, "placeholder", !empty && typed === 0);
    toggle(appDemo, "typing", typing);
    toggle(appDemo, "pressing", t >= T.press && t < T.press + 0.18);

    // The row arrives on a short rise and leaves on a plain fade. The card it
    // sits in fades with it, because an empty bordered box waiting for content
    // reads as a bug rather than as an app that has nothing to show yet.
    const rowIn = ramp(t, T.rowIn, T.rowIn + 0.45);
    const rowOut = 1 - ramp(t, T.fadeOut, T.fadeOut + 0.6);
    group.style.opacity = String(rowIn * rowOut);
    row.style.transform = `translateY(${((1 - rowIn) * 6).toFixed(2)}px)`;

    artImg.style.opacity = String(ramp(t, T.found, T.found + 0.45));

    const phase =
      t >= T.done
        ? "done"
        : t >= T.verify
          ? "verify"
          : t >= T.dlStart
            ? "download"
            : t >= T.found
              ? "found"
              : "search";

    put(
      statusText,
      "status",
      {
        search: "Working out what this is…",
        found: "Found on Beatport · Deezer has a copy",
        download: "Downloading from Deezer",
        verify: "Checking the file…",
        done: "Saved to your library",
      }[phase],
    );

    const stage = { search: 0, found: 1, download: 1, verify: 2, done: 4 }[phase];
    steps.forEach((step, i) => {
      toggle(step, "done", i < stage);
      toggle(step, "current", i === stage);
    });

    // Progress: decelerating, with a little speed jitter that fades out as the
    // transfer settles. A perfectly linear sweep is the tell that it is a fake.
    const s = ramp(t, T.dlStart, T.dlEnd);
    const pct =
      phase === "download"
        ? clamp01(
            1 - Math.pow(1 - s, 1.75) + Math.sin(s * 11.3) * 0.014 * (1 - s),
          ) * 100
        : null;
    const showPlatter = t >= T.rowIn && t < T.done;
    platter.style.opacity = String(showPlatter ? 1 : 0);
    toggle(appDemo, "waiting", pct === null);

    const swept = pct === null ? CIRC * 0.22 : (CIRC * pct) / 100;
    arc.setAttribute("stroke-dasharray", `${swept.toFixed(2)} ${CIRC.toFixed(2)}`);
    spindle.style.opacity = pct === null ? "0.5" : "0";
    put(pctText, "pct", pct === null ? "" : String(Math.round(pct)));

    // Speed is read off the counter rather than invented, so the number and the
    // arc always tell the same story. Scaled down because the demo compresses a
    // real transfer into five seconds, and the unscaled rate that falls out of
    // that is about 13 MB/s - a figure no one should read off a marketing page.
    const doneMb = ((pct ?? 0) / 100) * SIZE_MB;
    const dt = t - prevT;
    if (dt > 0 && dt < 0.2 && phase === "download") {
      const instant = ((doneMb - prevDone) / dt) * 0.51;
      speed = speed ? speed + (instant - speed) * 0.08 : instant;
    }
    prevDone = doneMb;
    prevT = t;

    put(
      xferText,
      "xfer",
      phase === "found"
        ? "Connecting to Deezer…"
        : phase === "download"
          ? `${doneMb.toFixed(1)} of ${SIZE_MB.toFixed(1)} MB · ${speed.toFixed(1)} MB/s`
          : phase === "verify"
            ? "Comparing against the source"
            : "",
    );

    [T.check1, T.check2].forEach((cue, i) => {
      const p = ramp(t, cue, cue + 0.35);
      checks[i].style.opacity = String(p);
      checks[i].style.transform = `translateY(${((1 - p) * 3).toFixed(2)}px)`;
    });

    const v = ramp(t, T.done, T.done + 0.35);
    verified.style.opacity = String(v);
    verified.style.transform = `translateY(${((1 - v) * 2).toFixed(2)}px)`;
  };

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    // The site's reduced-motion rules already kill every CSS animation in here.
    // This loop is the only moving part left, so it renders the finished state
    // once instead: the same information, arrived at rather than performed.
    render(T.done + 1);
  } else {
    let start = null;
    let running = false;
    let frame = 0;
    const tick = (now) => {
      if (!running) return;
      if (start === null) start = now;
      render(((now - start) / 1000) % T.loop);
      frame = requestAnimationFrame(tick);
    };
    // The hero curve already runs a permanent rAF loop; this one only runs while
    // the demo is actually on screen.
    new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting === running) return;
        running = entry.isIntersecting;
        if (running) {
          start = null;
          prevT = 0;
          frame = requestAnimationFrame(tick);
        } else {
          cancelAnimationFrame(frame);
        }
      },
      { threshold: 0.15 },
    ).observe(appDemo);
    render(0);
  }
}

// The album variant. Same recreation one link further on: an album or playlist
// link queues every track at once, so the rows run the same state machine
// staggered in time rather than each carrying its own cue sheet. Nine tracks
// are queued and four are shown, which is what a list scrolled to the top looks
// like; the counter and the bar cover all nine. The third track finds a lossy
// source first, so it is rejected and the search carries on - the one thing a
// batch shows that a single track cannot.
const plDemo = document.querySelector("#playlist-demo");
if (plDemo) {
  const urlText = plDemo.querySelector(".ad-url");
  const summary = plDemo.querySelector(".ad-summary");
  const barFill = plDemo.querySelector(".ad-groupbar-fill");
  const moreLine = plDemo.querySelector(".ad-more");
  const groupSec = plDemo.querySelector(".ad-groupsec");
  const rows = [...plDemo.querySelectorAll(".ad-row")];

  const URL_TEXT = "music.youtube.com/playlist?list=OLAK5uy_nq8bhlHsmgmKdzp5ZH5dkdv9x0_XGfQ3s";
  const PLACEHOLDER = "Paste a YouTube, YouTube Music, or Spotify link";
  const CIRC = 2 * Math.PI * 19; // matches Platter.tsx's geometry

  // Master cue sheet, in seconds.
  const T = {
    typeStart: 0.5,
    typeEnd: 2.0,
    press: 2.25,
    groupIn: 2.5,
    fadeOut: 15.4,
    loop: 16.6,
  };

  // The album, in order. `at` is when the track's row starts relative to
  // groupIn; only the first four have a row on the page, but all nine feed the
  // counter and the bar. `reject` marks the one whose first source is lossy.
  const TRACKS = [
    { at: 0.0, mb: 42.8, source: "Deezer" },
    { at: 0.5, mb: 51.3, source: "Qobuz" },
    { at: 1.0, mb: 47.6, source: "Qobuz", reject: true },
    { at: 1.5, mb: 39.2, source: "Deezer" },
    { at: 2.0, mb: 55.1, source: "Qobuz" },
    { at: 2.5, mb: 44.9, source: "Deezer" },
    { at: 3.0, mb: 61.4, source: "Qobuz" },
    { at: 3.5, mb: 38.7, source: "Deezer" },
    { at: 4.0, mb: 49.5, source: "Qobuz" },
  ];

  // Row-local cue sheets, in seconds after that row's own start. A rejected
  // source costs the track about a second and a half of extra searching.
  const PLAIN = { found: 1.35, dlStart: 1.85, dlEnd: 5.6, verify: 6.0, done: 6.7 };
  const RETRY = {
    lossy: 1.35,
    rejected: 1.95,
    found: 2.85,
    dlStart: 3.35,
    dlEnd: 7.0,
    verify: 7.4,
    done: 8.1,
  };

  const last = {};
  const put = (node, key, value) => {
    if (last[key] === value) return;
    last[key] = value;
    node.textContent = value;
  };
  const setStyle = (node, key, prop, value) => {
    if (last[key] === value) return;
    last[key] = value;
    node.style[prop] = value;
  };
  const toggle = (node, cls, on) => node.classList.toggle(cls, on);
  const clamp01 = (n) => (n < 0 ? 0 : n > 1 ? 1 : n);
  const ramp = (t, from, to) => clamp01((t - from) / (to - from));

  const cuesFor = (track) => (track.reject ? RETRY : PLAIN);

  // Phase is worked out for all nine tracks, including the five with no row on
  // the page, so the counter and the bar describe the whole album.
  const phaseOf = (i, t) => {
    const track = TRACKS[i];
    const c = cuesFor(track);
    const lt = t - T.groupIn - track.at;
    if (lt >= c.done) return "done";
    if (lt >= c.verify) return "verify";
    if (lt >= c.dlStart) return "download";
    if (lt >= c.found) return "found";
    if (track.reject && lt >= c.rejected) return "rejected";
    if (track.reject && lt >= c.lossy) return "lossy";
    return "search";
  };

  const renderRow = (row, i, t) => {
    const track = TRACKS[i];
    const c = cuesFor(track);
    const lt = t - T.groupIn - track.at; // negative until this track is reached
    const phase = phaseOf(i, t);

    const rowIn = ramp(lt, 0, 0.4);
    setStyle(row, `o${i}`, "opacity", String(rowIn));
    setStyle(row, `y${i}`, "transform", `translateY(${((1 - rowIn) * 5).toFixed(2)}px)`);

    const s = ramp(lt, c.dlStart, c.dlEnd);
    // Same decelerating curve as the single-track demo, so both read as the
    // same app rather than as two different fakes.
    const pct =
      phase === "download"
        ? clamp01(1 - Math.pow(1 - s, 1.75) + Math.sin(s * 11.3 + i) * 0.014 * (1 - s)) * 100
        : null;
    const doneMb = ((pct ?? 0) / 100) * track.mb;

    const statusEl = row.querySelector(".ad-status");
    put(
      statusEl,
      `s${i}`,
      {
        search: "Working out what this is…",
        lossy: "Best match is 320 kbps",
        rejected: "Not lossless — looking for another source",
        found: `Found on ${track.source}`,
        download: `Downloading from ${track.source} · ${doneMb.toFixed(1)} of ${track.mb.toFixed(1)} MB`,
        verify: "Checking the file…",
        done: "Saved to your library",
      }[phase],
    );
    toggle(statusEl, "amber", phase === "lossy" || phase === "rejected");
    toggle(row, "washed", phase === "lossy" || phase === "rejected");

    // A track still hunting for a source has finished Search and is not yet
    // downloading, so Download is the step in progress throughout.
    const stage = {
      search: 0,
      lossy: 1,
      rejected: 1,
      found: 1,
      download: 1,
      verify: 2,
      done: 4,
    }[phase];
    [...row.querySelectorAll(".ad-step")].forEach((step, n) => {
      toggle(step, "done", n < stage);
      toggle(step, "current", n === stage);
    });

    const showPlatter = lt >= 0 && phase !== "done";
    setStyle(row.querySelector(".ad-platter"), `p${i}`, "opacity", showPlatter ? "1" : "0");
    toggle(row, "waiting", pct === null);

    const swept = pct === null ? CIRC * 0.22 : (CIRC * pct) / 100;
    row
      .querySelector(".ad-arc")
      .setAttribute("stroke-dasharray", `${swept.toFixed(2)} ${CIRC.toFixed(2)}`);
    setStyle(row.querySelector(".ad-spindle"), `n${i}`, "opacity", pct === null ? "0.5" : "0");
    put(row.querySelector(".ad-pct"), `c${i}`, pct === null ? "" : String(Math.round(pct)));

    const v = ramp(lt, c.done, c.done + 0.35);
    const badge = row.querySelector(".ad-verified");
    setStyle(badge, `v${i}`, "opacity", String(v));
    setStyle(badge, `vt${i}`, "transform", `translateY(${((1 - v) * 2).toFixed(2)}px)`);
  };

  const render = (t) => {
    const typing = t >= T.typeStart && t < T.press;
    const typed = Math.round(ramp(t, T.typeStart, T.typeEnd) * URL_TEXT.length);
    const empty = t >= T.press;
    put(urlText, "url", empty ? "" : typed === 0 ? PLACEHOLDER : URL_TEXT.slice(0, typed));
    toggle(urlText, "placeholder", !empty && typed === 0);
    toggle(plDemo, "typing", typing);
    toggle(plDemo, "pressing", t >= T.press && t < T.press + 0.18);

    const inP = ramp(t, T.groupIn, T.groupIn + 0.4);
    const outP = 1 - ramp(t, T.fadeOut, T.fadeOut + 0.6);
    setStyle(groupSec, "grp", "opacity", String(inP * outP));

    rows.forEach((row, i) => renderRow(row, i, t));

    const saved = TRACKS.filter((_, i) => phaseOf(i, t) === "done").length;
    const queued = TRACKS.length - rows.length;
    put(summary, "sum", `${TRACKS.length} tracks · ${saved} lossless`);
    put(moreLine, "more", `+ ${queued} more below`);
    // The bar steps as tracks land, the way the app's own does; the width
    // transition in CSS is what smooths it between steps.
    setStyle(barFill, "bf", "width", `${((saved / TRACKS.length) * 100).toFixed(1)}%`);
  };

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    // Reduced motion gets the finished album instead of the run: every track
    // saved, which is where the sequence ends up anyway.
    render(T.groupIn + TRACKS[TRACKS.length - 1].at + RETRY.done + 1);
  } else {
    let start = null;
    let running = false;
    let frame = 0;
    const tick = (now) => {
      if (!running) return;
      if (start === null) start = now;
      render(((now - start) / 1000) % T.loop);
      frame = requestAnimationFrame(tick);
    };
    new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting === running) return;
        running = entry.isIntersecting;
        if (running) {
          start = null;
          frame = requestAnimationFrame(tick);
        } else {
          cancelAnimationFrame(frame);
        }
      },
      { threshold: 0.15 },
    ).observe(plDemo);
    render(0);
  }
}

// The spectrogram comparison in the verification section. Both panels are drawn
// from one synthetic spectrum, so the only difference between them is the thing
// the check actually looks for: the lossy panel discards everything above its
// cutoff. Illustrative, not an analysis of a real file - the caption says so.
const spectra = document.querySelector("#spectra");
if (spectra) {
  const NYQUIST = 22050;
  const MS_PER_COLUMN = 16; // one column per ~16ms, so the scroll speed does not
  // depend on whether the display runs at 60 or 120Hz

  // Dark ground through the site's blue to white, so the loud parts read as heat
  // and the panel still belongs to the page around it.
  const RAMP = [
    [0.0, [5, 7, 13]],
    [0.25, [11, 42, 94]],
    [0.5, [10, 92, 196]],
    [0.72, [47, 168, 255]],
    [0.88, [159, 224, 255]],
    [1.0, [255, 255, 255]],
  ];
  // 256 steps, baked once: a spectrogram asks for a colour per pixel per column,
  // and interpolating the ramp each time is the one thing here that would cost.
  const LUT = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const v = i / 255;
    let k = 0;
    while (k < RAMP.length - 2 && v > RAMP[k + 1][0]) k++;
    const [p0, c0] = RAMP[k];
    const [p1, c1] = RAMP[k + 1];
    const f = (v - p0) / (p1 - p0);
    for (let c = 0; c < 3; c++) LUT[i * 3 + c] = c0[c] + (c1[c] - c0[c]) * f;
  }

  // A spectrogram of real music is three things at once: sustained tones as
  // horizontal lines, transients as vertical stripes, and a noise bed that thins
  // out with frequency. Modelling those three is enough to look like music.
  const VOICES = [
    { root: 82.41, partials: 16, gain: 1.0, rate: 0.37 },
    { root: 164.81, partials: 11, gain: 0.62, rate: 0.55 },
    { root: 246.94, partials: 9, gain: 0.44, rate: 0.28 },
    { root: 329.63, partials: 8, gain: 0.34, rate: 0.83 },
  ];
  const SEMITONES = [0, 3, 5, 7, 10, 7, 5, 3];

  // A sixteenth-note grid, so the transients land in a pattern instead of on a
  // metronome - an evenly spaced tick every beat reads as a test signal.
  const STEP = 0.125;
  const KICK = [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0];
  const SNARE = [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1];
  const HAT = [1, 0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1, 1];

  // Seconds since this drum last fired. Decays are short, the way percussion
  // actually behaves: a slow one smears every hit into a full-height band.
  const age = (pattern, t) => {
    const now = Math.floor(t / STEP);
    for (let k = 0; k < 16; k++) {
      const i = now - k;
      if (pattern[((i % 16) + 16) % 16]) return t - i * STEP;
    }
    return 99;
  };
  const envelopes = (t) => ({
    kick: Math.exp(-age(KICK, t) * 24),
    snare: Math.exp(-age(SNARE, t) * 17),
    hat: Math.exp(-age(HAT, t) * 60),
  });

  // `binHz` is how much frequency one pixel row covers. Partials narrower than a
  // row fall between the rows being sampled and flicker in and out instead of
  // drawing a line, so they are widened to straddle one.
  const spectrum = (freq, t, binHz, env) => {
    let v = 0.008 * (0.3 + 0.7 / (1 + freq / 4000)); // noise bed
    // Cymbal wash: the sustained top end that a lossy encoder throws away, and
    // the reason the two panels differ at all. Breathes slightly so the upper
    // band looks recorded rather than painted on.
    v +=
      0.012 *
      (0.8 + 0.2 * Math.sin(t * 1.7)) *
      Math.exp(-Math.pow((freq - 16000) / 7000, 2));
    v += env.kick * 1.0 * Math.exp(-freq / 170);
    v += env.snare * 0.45 * Math.exp(-Math.pow((freq - 2200) / 2600, 2));
    v += env.snare * 0.1 * Math.exp(-Math.pow((freq - 7000) / 4000, 2));
    // Narrow in frequency, or every hit is a stripe up the whole panel.
    v += env.hat * 0.34 * Math.exp(-Math.pow((freq - 14500) / 5000, 2));
    for (const voice of VOICES) {
      const step = SEMITONES[Math.floor(t * voice.rate) % SEMITONES.length];
      const root = voice.root * Math.pow(2, step / 12);
      for (let n = 1; n <= voice.partials; n++) {
        const pf = root * n;
        if (pf > NYQUIST) break;
        const width = Math.max(1.6 * binHz, 24 + pf * 0.014);
        v +=
          (voice.gain / Math.pow(n, 1.22)) *
          Math.exp(-Math.pow((freq - pf) / width, 2));
      }
    }
    return v;
  };

  // Starts well above zero so that pre-filling a panel with history still asks
  // the model for positive times.
  let clock = 600;

  const panels = [...spectra.querySelectorAll(".spec-canvas")].map((canvas) => ({
    canvas,
    ctx: canvas.getContext("2d", { alpha: false }),
    cutoff: Number(canvas.dataset.cutoff) || 0,
    column: null,
    w: 0,
    h: 0,
  }));

  const drawColumn = (panel, t) => {
    const { ctx, h, cutoff, column } = panel;
    const data = column.data;
    const binHz = NYQUIST / h;
    const env = envelopes(t); // once per column, not once per pixel
    for (let y = 0; y < h; y++) {
      const freq = (1 - y / (h - 1)) * NYQUIST;
      let m = spectrum(freq, t, binHz, env);
      // What a lossy encoder actually leaves behind: not pure silence, but a
      // floor far below anything musical.
      if (cutoff && freq > cutoff) m *= 0.015;
      // Decibels over a 48 dB window, the way an analyser shows it: linear
      // magnitude spends almost its whole range on the loudest few percent and
      // renders as a solid wash instead of as structure.
      const level = Math.max(0, Math.min(1, (Math.log10(m) * 20 + 45) / 45));
      const i = (level * 255) | 0;
      const o = y * 4;
      data[o] = LUT[i * 3];
      data[o + 1] = LUT[i * 3 + 1];
      data[o + 2] = LUT[i * 3 + 2];
      data[o + 3] = 255;
    }
    // Scroll by one pixel, then stamp the new column at the right edge. `copy`
    // so the shifted image replaces rather than blends with what is under it.
    ctx.globalCompositeOperation = "copy";
    ctx.drawImage(ctx.canvas, -1, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.putImageData(column, panel.w - 1, 0);
  };

  const resize = () => {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    for (const panel of panels) {
      const rect = panel.canvas.getBoundingClientRect();
      const w = Math.max(1, Math.round(rect.width * dpr));
      const h = Math.max(1, Math.round(rect.height * dpr));
      if (w === panel.w && h === panel.h) continue;
      panel.canvas.width = w;
      panel.canvas.height = h;
      panel.w = w;
      panel.h = h;
      panel.column = panel.ctx.createImageData(1, h);
      // Fill the panel with history so it opens as a spectrogram already
      // running, rather than as a black rectangle wiping itself in. Counted
      // forward from a positive base: the voices index a pattern with `%`, and
      // a negative time indexes off the front of it.
      for (let x = 0; x < w; x++)
        drawColumn(panel, clock - (w - x) * (MS_PER_COLUMN / 1000));
    }
  };

  resize();
  window.addEventListener("resize", resize);

  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    let carry = 0;
    let previous = 0;
    let running = false;
    let frame = 0;
    const tick = (now) => {
      if (!running) return;
      // Clamped: coming back to a backgrounded tab should resume, not redraw
      // every column that would have been dropped while it was away.
      carry += Math.min(160, now - previous);
      previous = now;
      while (carry >= MS_PER_COLUMN) {
        carry -= MS_PER_COLUMN;
        clock += MS_PER_COLUMN / 1000;
        for (const panel of panels) drawColumn(panel, clock);
      }
      frame = requestAnimationFrame(tick);
    };
    new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting === running) return;
        running = entry.isIntersecting;
        if (running) {
          previous = performance.now();
          carry = 0;
          frame = requestAnimationFrame(tick);
        } else {
          cancelAnimationFrame(frame);
        }
      },
      { threshold: 0.1 },
    ).observe(spectra);
  }
}

// Ambient sound-wave motifs: a breathing EQ curve behind the hero title, and a small
// pulsing EQ meter next to "How we verify audio". Purely decorative (aria-hidden).
const eqBars = document.querySelector("#eq-bars");
if (eqBars) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < 20; i++) {
    const bar = document.createElement("i");
    bar.style.height = `${30 + Math.abs(Math.sin(i * 1.3 + 2)) * 65}%`;
    bar.style.animationDelay = `${(i * 0.07) % 1.6}s`;
    bar.style.animationDuration = `${1.1 + (i % 5) * 0.18}s`;
    frag.appendChild(bar);
  }
  eqBars.appendChild(frag);
}

const eqCurveSvg = document.querySelector("#eq-curve-svg");
if (eqCurveSvg) {
  const reduceMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;
  const width = 1000;
  const height = 340;
  const baselineY = height * 0.84;
  const ns = "http://www.w3.org/2000/svg";
  const bands = [
    {
      peakX: 0.5,
      sigma: 0.24,
      baseAmp: 150,
      ampVariance: reduceMotion ? 0 : 26,
      speed: 0.35,
      phase: 0,
      driftAmt: 0.03,
      wiggle: reduceMotion ? 0 : 0.09,
      fillClass: "eq-fill-a",
      strokeClass: "eq-stroke-a",
    },
    {
      peakX: 0.28,
      sigma: 0.28,
      baseAmp: 70,
      ampVariance: reduceMotion ? 0 : 16,
      speed: 0.27,
      phase: 1.4,
      driftAmt: 0.04,
      wiggle: reduceMotion ? 0 : 0.12,
      fillClass: "eq-fill-b",
      strokeClass: "eq-stroke-b",
    },
  ];

  for (let g = 1; g < 4; g++) {
    const line = document.createElementNS(ns, "line");
    line.setAttribute("class", "eq-grid-line");
    const x = (width / 4) * g;
    line.setAttribute("x1", x);
    line.setAttribute("x2", x);
    line.setAttribute("y1", 0);
    line.setAttribute("y2", height);
    eqCurveSvg.appendChild(line);
  }
  const baseline = document.createElementNS(ns, "line");
  baseline.setAttribute("class", "eq-baseline");
  baseline.setAttribute("x1", 0);
  baseline.setAttribute("x2", width);
  baseline.setAttribute("y1", baselineY);
  baseline.setAttribute("y2", baselineY);
  eqCurveSvg.appendChild(baseline);

  const built = bands.map((band) => {
    const fillPath = document.createElementNS(ns, "path");
    fillPath.setAttribute("class", band.fillClass);
    const strokePath = document.createElementNS(ns, "path");
    strokePath.setAttribute("class", band.strokeClass);
    eqCurveSvg.appendChild(fillPath);
    eqCurveSvg.appendChild(strokePath);
    return { band, fillPath, strokePath };
  });

  const hillPoints = (peakXFrac, sigmaFrac, ampPx, wiggle, t) => {
    const steps = 90;
    const pts = [];
    for (let i = 0; i <= steps; i++) {
      const xFrac = i / steps;
      const x = xFrac * width;
      const d = (xFrac - peakXFrac) / sigmaFrac;
      const bump = Math.exp(-(d * d));
      const ripple = 1 + wiggle * Math.sin(xFrac * 14 + t * 1.8) * bump;
      pts.push([x, baselineY - ampPx * bump * ripple]);
    }
    return pts;
  };

  const render = (tMs) => {
    const t = tMs / 1000;
    for (const b of built) {
      const { band } = b;
      const wobble =
        Math.sin(t * band.speed + band.phase) * 0.55 +
        Math.sin(t * band.speed * 2.6 + band.phase * 1.8) * 0.3 +
        Math.sin(t * band.speed * 5.7 + band.phase * 0.6) * 0.15;
      const amp = band.baseAmp + band.ampVariance * wobble;
      const peakDrift =
        band.peakX +
        band.driftAmt * Math.sin(t * band.speed * 0.4 + band.phase);
      const pts = hillPoints(peakDrift, band.sigma, amp, band.wiggle, t);
      const coords = pts.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`);
      b.fillPath.setAttribute(
        "d",
        `M0 ${baselineY.toFixed(1)} L${coords.join(" L")} L${width} ${baselineY.toFixed(1)} Z`,
      );
      b.strokePath.setAttribute("d", `M${coords.join(" L")}`);
    }
  };

  if (reduceMotion) {
    render(0);
  } else {
    const loop = (t) => {
      render(t);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
}
