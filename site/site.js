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

const preview = document.querySelector("#preview-dialog");
document
  .querySelector("#preview-button")
  ?.addEventListener("click", () => preview?.showModal());
preview
  ?.querySelector(".close")
  ?.addEventListener("click", () => preview.close());
preview?.addEventListener("click", (event) => {
  if (event.target !== preview) return;
  const bounds = preview.getBoundingClientRect();
  if (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  )
    preview.close();
});

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
