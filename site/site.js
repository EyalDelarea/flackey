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
  const PLACEHOLDER = "Paste a YouTube or YouTube Music link";
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
