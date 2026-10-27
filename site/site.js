const release = document.querySelector("#release-line");
// One button per installer. Their hrefs are fixed in the HTML (releases/latest/download/...),
// so they work without this script; the release lookup below only adds the version and size.
const downloads = [
  { link: document.querySelector("#download-mac"), asset: "Flackey.pkg" },
  { link: document.querySelector("#download-windows"), asset: "Flackey-Setup.exe" },
];
const notesList = document.querySelector("#release-notes-list");
const RELEASES_URL = "https://github.com/EyalDelarea/flackey/releases";

const formatReleaseDate = (iso) =>
  new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(iso));

// Turns the small Markdown subset GitHub's generated release notes use into
// markup: headings, bullets, bold/code, markdown links, and bare PR/compare URLs.
// Escapes first, so nothing in a PR title can inject HTML -- markdown syntax
// is only ever matched against already-escaped text, and the whole line is
// scanned in one replace() pass so an inserted <a>/<strong> is never rescanned.
function renderReleaseBody(body) {
  const esc = (s) =>
    s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const inline = (s) =>
    esc(s).replace(
      /`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/(\d+))\b|(https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/(?:compare|commits)\/(\S+))|(https?:\/\/\S+)/g,
      (match, code, bold, linkText, linkUrl, prUrl, prNum, diffUrl, diffRange, anyUrl) => {
        if (code !== undefined) return `<code>${code}</code>`;
        if (bold !== undefined) return `<strong>${bold}</strong>`;
        if (linkUrl)
          return `<a href="${linkUrl}" target="_blank" rel="noopener">${linkText}</a>`;
        if (prUrl)
          return `<a class="release-note-pr" href="${prUrl}" target="_blank" rel="noopener">#${prNum}</a>`;
        if (diffUrl)
          return `<a class="release-note-compare" href="${diffUrl}" target="_blank" rel="noopener">${diffRange}</a>`;
        return `<a href="${anyUrl}" target="_blank" rel="noopener">${anyUrl}</a>`;
      },
    );
  const categoryHeading = (text) => {
    const glyphs = Array.from(text);
    const first = glyphs[0] || "";
    const hasIcon = first && !/[A-Za-z0-9#]/.test(first);
    return hasIcon
      ? `<h5><span class="release-note-icon" aria-hidden="true">${first}</span><span>${inline(glyphs.slice(1).join("").trim())}</span></h5>`
      : `<h5>${inline(text)}</h5>`;
  };
  let html = "";
  let inList = false;
  const closeList = () => {
    if (inList) html += "</ul>";
    inList = false;
  };
  let inComment = false;
  for (const raw of (body || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (inComment) {
      inComment = !line.includes("-->");
      continue;
    }
    if (line.startsWith("<!--")) {
      inComment = !line.includes("-->");
      continue;
    }
    const heading = line.match(/^(#{2,6})\s+(.+)$/);
    if (heading) {
      closeList();
      html += heading[1].length === 2
        ? `<h4>${inline(heading[2])}</h4>`
        : categoryHeading(heading[2]);
    } else if (/^[-*]\s+/.test(line)) {
      if (!inList) {
        html += "<ul>";
        inList = true;
      }
      html += `<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`;
    } else {
      closeList();
      html += `<p>${inline(line)}</p>`;
    }
  }
  closeList();
  return html;
}

// The newest published release that is not a pre-release: a pre-release is a branch build
// handed to a tester, and the in-app update check skips them for the same reason.
//
// Progressive enhancement only. The API allows 60 unauthenticated requests an hour per address,
// so it can fail or hang; the download buttons already work, and the notes fall back to a link.
const RELEASE_LOOKUP_MS = 7000;
const releaseLookup = new AbortController();
const releaseTimer = setTimeout(() => releaseLookup.abort(), RELEASE_LOOKUP_MS);
fetch("https://api.github.com/repos/EyalDelarea/flackey/releases?per_page=10", {
  signal: releaseLookup.signal,
})
  .then((response) => {
    if (!response.ok) throw new Error(`Release lookup failed: ${response.status}`);
    return response.json();
  })
  .then((releases) => {
    const published = Array.isArray(releases)
      ? releases.filter((item) => !item.draft && !item.prerelease)
      : [];
    const data = published[0];
    if (!data) throw new Error("No published release");
    for (const { link, asset: name } of downloads) {
      const asset = data.assets?.find((item) => item.name === name);
      const size = link?.parentElement?.querySelector(".download-size");
      if (size && asset?.size) size.textContent = ` · ${(asset.size / 1e6).toFixed(0)} MB`;
    }
    const version = String(data.tag_name || "").replace(/^v/, "");
    if (release)
      release.textContent = `Version ${version} · ${formatReleaseDate(data.published_at)}`;
    // The Updates tile's banner names the current version rather than going stale in the HTML.
    const wnVersion = document.querySelector("#wn-version");
    if (wnVersion && version) wnVersion.textContent = version;

    if (notesList) {
      const title = `<span class="release-note-title">
            <span>v${version}</span>
            <time datetime="${data.published_at}">${formatReleaseDate(data.published_at)}</time>
          </span>`;
      notesList.innerHTML = `<details class="release-note">
            <summary>
              ${title}
              <span class="release-note-toggle" aria-hidden="true"></span>
            </summary>
            <div class="release-note-body">
              ${renderReleaseBody(data.body)}
              <p class="release-note-link">
                <a href="${data.html_url}" target="_blank" rel="noopener">View release on GitHub</a>
              </p>
            </div>
          </details>
          <p class="release-notes-more">
            <a href="${RELEASES_URL}" target="_blank" rel="noopener">View other releases on GitHub</a>
          </p>`;
    }
  })
  .catch(() => {
    // The static line under the picker already reads fine, so it is left as it is.
    if (notesList)
      notesList.innerHTML = `<p class="release-notes-status">Couldn’t load release notes.
          See <a href="${RELEASES_URL}" target="_blank" rel="noopener">GitHub releases</a>
          for what changed.</p>`;
  })
  .finally(() => clearTimeout(releaseTimer));

// Mac or Windows: the choice shows that OS's download with its install guide.
// A visitor on Windows starts on Windows; a Mac or anything unrecognised starts on Mac.
// Phones, tablets, Linux and ChromeOS can't run either installer, so they get a note
// instead of a download, and can still pick a tab to read the install steps.
const osTabs = [...document.querySelectorAll('.os-picker [role="tab"]')];
const platformNote = document.querySelector("#platform-note");
function chooseOs(chosen) {
  for (const tab of osTabs) {
    const selected = tab === chosen;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected || (!chosen && tab === osTabs[0]) ? 0 : -1;
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    if (panel) panel.hidden = !selected;
  }
  if (platformNote) platformNote.hidden = Boolean(chosen);
}
for (const tab of osTabs) {
  tab.addEventListener("click", () => chooseOs(tab));
  tab.addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const next = osTabs[(osTabs.indexOf(tab) + step + osTabs.length) % osTabs.length];
    chooseOs(next);
    next.focus();
  });
}
function visitorPlatform() {
  const ua = navigator.userAgent;
  // iPadOS Safari reports itself as a Mac; a touch screen gives it away.
  const iPad = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;
  // Android user agents also say Linux, so the mobile checks come first.
  if (iPad || /Android|iPhone|iPad|iPod|Mobile|CrOS|Linux/.test(ua)) return "other";
  if (/Windows/.test(ua)) return "windows";
  return "mac";
}
const platform = visitorPlatform();
if (platform === "windows") chooseOs(document.querySelector("#os-tab-windows"));
else if (platform === "other") chooseOs(null);

// The folder-layout tile cycles through the three choices, with the path each gives.
const layoutTile = document.querySelector("#wn-layout");
if (layoutTile && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const segs = [...layoutTile.querySelectorAll(".wn-seg i")];
  const path = layoutTile.querySelector(".wn-path");
  const today = new Date().toISOString().slice(0, 7);
  const paths = ["Bicep/Bicep - Glue.aiff", `${today}/Bicep - Glue.aiff`, "Bicep - Glue.aiff"];
  let at = 0;
  setInterval(() => {
    at = (at + 1) % segs.length;
    segs.forEach((seg, i) => seg.classList.toggle("on", i === at));
    if (path) path.textContent = paths[at];
  }, 2600);
}

// The product film. It starts muted the first time it is mostly on screen, pauses
// when scrolled away, and plays through once. Visitors who ask for reduced motion
// get the poster and the play button only. The browser's own controls are the
// fallback in the HTML; they come off here in favour of the two on the film.
const filmVideo = document.querySelector("#film-video");
if (filmVideo) {
  const frame = filmVideo.closest(".film-frame");
  const controls = frame.querySelector(".film-controls");
  const playBtn = frame.querySelector(".film-play");
  const soundBtn = frame.querySelector(".film-sound");
  const soundLabel = frame.querySelector(".film-sound-label");
  // Set once someone presses pause, so scrolling back never overrides them.
  let heldByVisitor = false;
  filmVideo.removeAttribute("controls");
  filmVideo.muted = true;
  controls.hidden = false;

  const sync = () => {
    const paused = filmVideo.paused;
    frame.classList.toggle("is-paused", paused);
    frame.classList.toggle("is-unmuted", !filmVideo.muted);
    playBtn.setAttribute("aria-label", paused ? "Play the film" : "Pause the film");
    soundBtn.setAttribute("aria-label", filmVideo.muted ? "Turn sound on" : "Turn sound off");
    soundLabel.textContent = filmVideo.muted ? "Sound on" : "Sound off";
  };
  const play = () => filmVideo.play().catch(sync);
  const toggle = () => {
    if (filmVideo.paused) {
      heldByVisitor = false;
      play();
    } else {
      heldByVisitor = true;
      filmVideo.pause();
    }
  };
  ["play", "pause", "ended", "volumechange"].forEach((e) => filmVideo.addEventListener(e, sync));
  playBtn.addEventListener("click", toggle);
  filmVideo.addEventListener("click", toggle);
  soundBtn.addEventListener("click", () => {
    filmVideo.muted = !filmVideo.muted;
    // Asking for sound is asking to hear it, so a stopped film starts too.
    if (!filmVideo.muted && filmVideo.paused) {
      heldByVisitor = false;
      play();
    }
  });
  sync();

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduceMotion && "IntersectionObserver" in window) {
    new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          // Only a muted film comes back on its own; with sound on it waits for play.
          if (!heldByVisitor && !filmVideo.ended && filmVideo.paused && filmVideo.muted) play();
        } else if (!filmVideo.paused) {
          filmVideo.pause();
        }
      },
      { threshold: 0.4 },
    ).observe(filmVideo);
  }
}

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
        found: "Found it · Deezer has a copy",
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
    // Like the hero waveform, this loop only runs while the demo is on screen.
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
// are queued and three are shown, which is what a list scrolled to the top
// looks like; the counter and the bar cover all nine. The third track finds a lossy
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
  // groupIn; only the first three have a row on the page, but all nine feed the
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

    // Every row is the same record, so they share the single-track demo's
    // sleeve. It arrives when the track is identified, which for a rejected
    // source is the lossy match rather than the lossless one that replaces it.
    const artAt = track.reject ? c.lossy : c.found;
    setStyle(
      row.querySelector(".ad-art img"),
      `a${i}`,
      "opacity",
      String(ramp(lt, artAt, artAt + 0.45)),
    );

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

// The Choose demo: play the Original Mix, switch to the Extended Mix, keep the
// Original. Silent on purpose - the site has no rights to Deezer's clips.
const chooseDemo = document.querySelector("#choose-demo");
if (chooseDemo) {
  const cards = [...chooseDemo.querySelectorAll(".ch-card")];
  const status = chooseDemo.querySelector(".ch-status");
  const LOOP = 12;
  const clip = (s) => `0:0${Math.min(9, Math.floor(Math.max(0, s)))}`;
  const press = (btn, on) => btn && btn.classList.toggle("press", on);

  const render = (t) => {
    const playing = t >= 1.2 && t < 4.6 ? 0 : t >= 4.6 && t < 8.2 ? 1 : -1;
    const chose = t >= 8.4;
    cards.forEach((c, i) => {
      c.classList.toggle("lit", i === playing);
      c.classList.toggle("chosen", chose && i === 0);
      const start = i === 0 ? 1.2 : 4.6;
      c.querySelector(".elapsed").textContent = clip(t - start);
      const rail = c.querySelector(".ch-rail i");
      if (rail) rail.style.transform = `scaleX(${i === playing ? Math.min(1, (t - start) / 30) : 0})`;
    });
    press(cards[0].querySelector(".play"), t >= 1.0 && t < 1.2);
    press(cards[1].querySelector(".play"), t >= 4.4 && t < 4.6);
    press(cards[0].querySelector(".use"), t >= 8.2 && t < 8.4);
    chooseDemo.classList.toggle("chose", chose);
    status.textContent = chose
      ? "Downloading the Original Mix"
      : "Needs your choice. Three versions matched.";
  };

  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    render(3);
  } else {
    let running = false;
    let start = null;
    const tick = (now) => {
      if (!running) return;
      if (start === null) start = now;
      render(((now - start) / 1000) % LOOP);
      requestAnimationFrame(tick);
    };
    new IntersectionObserver(([e]) => {
      if (e.isIntersecting === running) return;
      running = e.isIntersecting;
      start = null;
      if (running) requestAnimationFrame(tick);
    }).observe(chooseDemo);
  }
}

// Flackey inspecting a file, in the lossless check section. One short story per
// run, about six seconds: the file drops in, its waveform unfolds into a
// spectrogram, a loupe reads the compression grid, the three checks tick in and
// a verdict lands. Plays the real file then the fake once when it first comes
// into view; the chips and Replay run it again. Everything is drawn from one
// synthetic spectrum, so the only differences between the two files are the
// two signs the check looks for: the empty top band and the regular grid.
// Illustrative, not an analysis of a real file - the caption says so.
const inspect = document.querySelector("#inspect");
if (inspect) {
  const q = (sel) => inspect.querySelector(sel);
  const stage = q(".ix-stage");
  const canvas = q(".ix-canvas");
  const ctx = canvas.getContext("2d");
  const chips = [...inspect.querySelectorAll(".ix-file")];
  const rows = [...inspect.querySelectorAll(".ix-check")];
  const stamp = q(".ix-stamp");
  const replay = q(".ix-replay");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const NYQUIST = 22050;
  const CEILING = 16000;
  // Which way each check comes out, per file: real passes all three; the fake
  // fails the top end and the MP3 grid, and has no Opus or AAC traces - an MP3
  // leaves an MP3 grid, not theirs.
  const OUTCOME = { real: ["ok", "ok", "ok"], fake: ["bad", "bad", "ok"] };

  // ---- Timeline, in seconds from the start of a run ----
  const T = {
    wave: 0.35, // waveform draws across
    unfold: 1.15, // it sinks and the spectrogram rises out of it
    rise: 1.0, // how long one column takes to fill
    spread: 0.32, // left-to-right lag across the panel
    ceiling: 2.45, // the dashed ceiling draws in (fake)
    row1: 2.55,
    loupe: 2.7, // loupe travels out from its patch
    loupeIn: 3.2,
    grid: 3.3, // cells inside it light up
    row2: 4.0,
    row3: 4.4,
    stamp: 4.85,
    keep: 5.35,
    end: 5.8,
  };
  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
  const span = (t, a, b) => clamp01((t - a) / (b - a));
  const outCubic = (x) => 1 - Math.pow(1 - x, 3);
  const inOutCubic = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
  const outBack = (x) => 1 + 2.70158 * Math.pow(x - 1, 3) + 1.70158 * Math.pow(x - 1, 2);
  const lerp = (a, b, f) => a + (b - a) * f;

  // ---- Colour: dark ground through the site's blue to white ----
  const RAMP = [
    [0.0, [6, 8, 14]],
    [0.25, [11, 42, 94]],
    [0.5, [10, 92, 196]],
    [0.72, [47, 168, 255]],
    [0.88, [159, 224, 255]],
    [1.0, [255, 255, 255]],
  ];
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
  const rgb = (v, a = 1) => {
    const i = (clamp01(v) * 255) | 0;
    return `rgba(${LUT[i * 3]},${LUT[i * 3 + 1]},${LUT[i * 3 + 2]},${a})`;
  };

  // ---- The music: tones as lines, drums as stripes, a noise bed ----
  const VOICES = [
    { root: 82.41, partials: 16, gain: 1.0, rate: 0.37 },
    { root: 164.81, partials: 11, gain: 0.62, rate: 0.55 },
    { root: 246.94, partials: 9, gain: 0.44, rate: 0.28 },
    { root: 329.63, partials: 8, gain: 0.34, rate: 0.83 },
  ];
  const SEMITONES = [0, 3, 5, 7, 10, 7, 5, 3];
  const STEP = 0.125;
  const KICK = [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0];
  const SNARE = [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1];
  const HAT = [1, 0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1, 1];
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
  const spectrum = (freq, t, binHz, env) => {
    let v = 0.008 * (0.3 + 0.7 / (1 + freq / 4000));
    v += 0.012 * (0.8 + 0.2 * Math.sin(t * 1.7)) * Math.exp(-Math.pow((freq - 16000) / 7000, 2));
    v += env.kick * Math.exp(-freq / 170);
    v += env.snare * 0.45 * Math.exp(-Math.pow((freq - 2200) / 2600, 2));
    v += env.snare * 0.1 * Math.exp(-Math.pow((freq - 7000) / 4000, 2));
    v += env.hat * 0.34 * Math.exp(-Math.pow((freq - 14500) / 5000, 2));
    for (const voice of VOICES) {
      const step = SEMITONES[Math.floor(t * voice.rate) % SEMITONES.length];
      const root = voice.root * Math.pow(2, step / 12);
      for (let n = 1; n <= voice.partials; n++) {
        const pf = root * n;
        if (pf > NYQUIST) break;
        const width = Math.max(1.6 * binHz, 24 + pf * 0.014);
        v += (voice.gain / Math.pow(n, 1.22)) * Math.exp(-Math.pow((freq - pf) / width, 2));
      }
    }
    return v;
  };
  const SEC_PER_COL = 0.03;
  const T0 = 600; // the voices index patterns with %, so keep model time positive

  // ---- Pictures, built once per size ----
  // The spectrogram is computed on a coarse grid and scaled up with smoothing,
  // which is both cheap and soft enough to read as light. A blurred copy is the
  // bloom: drawn additively over the image, so the loud parts glow.
  let W = 0;
  let H = 0;
  let dpr = 1;
  const pics = { real: null, fake: null };
  const blooms = { real: null, fake: null };
  const amp = []; // waveform amplitude per CSS pixel column, 0..1

  const offscreen = (w, h) => {
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    return c;
  };
  const build = () => {
    const rect = canvas.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    if (w === W && h === H) return false;
    W = canvas.width = w;
    H = canvas.height = h;
    const gw = Math.max(120, Math.min(560, Math.round(rect.width / 1.6)));
    const gh = 150;
    const binHz = NYQUIST / gh;
    const real = new ImageData(gw, gh);
    const fake = new ImageData(gw, gh);
    for (let x = 0; x < gw; x++) {
      const t = T0 + x * SEC_PER_COL;
      const env = envelopes(t);
      for (let y = 0; y < gh; y++) {
        const freq = (1 - y / (gh - 1)) * NYQUIST;
        const m = spectrum(freq, t, binHz, env);
        const o = (y * gw + x) * 4;
        for (const [img, mm] of [
          [real, m],
          [fake, freq > CEILING ? m * 0.012 : m],
        ]) {
          const level = clamp01((Math.log10(mm) * 20 + 45) / 53);
          const i = (level * 255) | 0;
          img.data[o] = LUT[i * 3];
          img.data[o + 1] = LUT[i * 3 + 1];
          img.data[o + 2] = LUT[i * 3 + 2];
          img.data[o + 3] = 255;
        }
      }
    }
    for (const [name, img] of [
      ["real", real],
      ["fake", fake],
    ]) {
      const c = offscreen(gw, gh);
      c.getContext("2d").putImageData(img, 0, 0);
      pics[name] = c;
      // Bloom: down to an eighth and back, which blurs it for free
      const small = offscreen(Math.max(1, gw >> 3), Math.max(1, gh >> 3));
      const sc = small.getContext("2d");
      sc.imageSmoothingQuality = "high";
      sc.drawImage(c, 0, 0, small.width, small.height);
      blooms[name] = small;
    }
    amp.length = 0;
    const cols = Math.round(rect.width);
    for (let x = 0; x < cols; x++) {
      const env = envelopes(T0 + (x / cols) * gw * SEC_PER_COL);
      amp.push(clamp01(0.18 + 0.75 * env.kick + 0.35 * env.snare + 0.1 * env.hat));
    }
    return true;
  };

  // A smooth, fixed wobble per column, so the rising front looks like energy
  // and not like a progress bar.
  const wobble = (x) => 0.5 + 0.3 * Math.sin(x * 0.021) + 0.2 * Math.sin(x * 0.067 + 1.3);
  // How far past full height each column aims: a little more where the drums
  // hit, so the front comes up spiky with the music rather than as a smooth hill
  const lift = (x) => 1.03 + 0.09 * (amp[Math.min(amp.length - 1, Math.round(x))] || 0);
  const delayOf = (fx, x) => T.spread * fx + 0.08 * wobble(x);
  const frontOf = (t, fx, x) => outCubic(span(t, T.unfold + delayOf(fx, x), T.unfold + delayOf(fx, x) + T.rise));

  // ---- One frame ----
  let file = "real";
  const draw = (t) => {
    ctx.clearRect(0, 0, W, H);
    const fake = file === "fake";
    const capH = fake ? H * (CEILING / NYQUIST) : H;
    const ceilY = H - capH;

    // Faint frequency guides
    ctx.fillStyle = "rgba(255,255,255,0.045)";
    for (const k of [5000, 10000, 15000, 20000]) ctx.fillRect(0, Math.round(H * (1 - k / NYQUIST)), W, dpr);

    // 1. The waveform draws across, then sinks to the floor as the spectrum rises
    const sink = inOutCubic(span(t, T.unfold, T.unfold + 0.55));
    if (t > T.wave && sink < 1) {
      const reach = outCubic(span(t, T.wave, T.wave + 0.75)) * W;
      const mid = lerp(H * 0.55, H, sink);
      const scale = H * 0.3 * (1 - sink);
      const step = 4 * dpr;
      ctx.save();
      ctx.shadowColor = "rgba(40,150,255,0.9)";
      ctx.shadowBlur = 14 * dpr;
      ctx.fillStyle = `rgba(150,210,255,${0.95 * (1 - sink * 0.7)})`;
      for (let x = 0; x < reach; x += step) {
        const a = amp[Math.min(amp.length - 1, Math.round(x / dpr))] || 0;
        // the leading edge grows in rather than appearing at full height
        const edge = clamp01((reach - x) / (40 * dpr));
        const hgt = Math.max(dpr, a * scale * edge);
        ctx.fillRect(x, mid - hgt, step - dpr, hgt * 2);
      }
      ctx.restore();
    }

    // 2. The spectrogram fills upward out of it, column by column
    if (t > T.unfold) {
      const step = 3 * dpr;
      const tops = [];
      for (let x = 0; x <= W + step; x += step) {
        const fx = x / W;
        const f = frontOf(t, fx, x / dpr);
        // A touch of overshoot on the real file, so energy visibly reaches the top
        const want = f * H * lift(x / dpr);
        tops.push([x, H - Math.min(want, capH)]);
      }
      const path = new Path2D();
      path.moveTo(0, H);
      for (const [x, y] of tops) path.lineTo(x, y);
      path.lineTo(W, H);
      path.closePath();
      ctx.save();
      ctx.clip(path);
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(pics[file], 0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";
      ctx.globalAlpha = 0.3;
      ctx.drawImage(blooms[file], 0, 0, W, H);
      ctx.restore();

      // The glowing front while it is still moving
      const settle = span(t, T.unfold + T.rise + T.spread, T.unfold + T.rise + T.spread + 0.5);
      const frontAlpha = 0.85 * (1 - settle);
      if (frontAlpha > 0.01) {
        ctx.save();
        ctx.strokeStyle = `rgba(175,225,255,${frontAlpha})`;
        ctx.lineWidth = 1.5 * dpr;
        ctx.shadowColor = "rgba(40,150,255,1)";
        ctx.shadowBlur = 16 * dpr;
        ctx.beginPath();
        tops.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.stroke();
        ctx.restore();
      }

      // 3. The fake hits a ceiling: each column flashes as it slams into it
      if (fake) {
        ctx.save();
        ctx.shadowColor = "rgba(255,255,255,0.9)";
        ctx.shadowBlur = 10 * dpr;
        for (let x = 0; x <= W; x += step) {
          const fx = x / W;
          const reachAt = capH / (H * lift(x / dpr));
          const hitT = T.unfold + delayOf(fx, x / dpr) + (1 - Math.cbrt(1 - reachAt)) * T.rise;
          if (t < hitT) continue;
          const k = Math.exp(-(t - hitT) * 4.5);
          if (k < 0.02) continue;
          ctx.fillStyle = `rgba(220,240,255,${0.9 * k})`;
          ctx.fillRect(x, ceilY - 1.5 * dpr, step, 3 * dpr);
        }
        ctx.restore();
      }
    }

    // 4. The loupe: out of a patch of the spectrum, a close look at the grid
    if (t > T.loupe) drawLoupe(t, fake);
  };

  const noise = (i, j, k) => {
    const x = Math.sin(i * 127.1 + j * 311.7 + k * 74.7) * 43758.5453;
    return x - Math.floor(x);
  };
  const drawLoupe = (t, fake) => {
    const travel = inOutCubic(span(t, T.loupe, T.loupeIn));
    const grow = outBack(span(t, T.loupe, T.loupeIn));
    const R = Math.min(84 * dpr, H * 0.26);
    const px = W * 0.5;
    const py = H * (1 - 7500 / NYQUIST);
    const patch = 13 * dpr;
    const cx = Math.min(W - R - 14 * dpr, px + Math.max(R * 1.9, W * 0.24));
    // Kept clear of the 16 kHz ceiling and its label, on both files, so the
    // loupe sits in the same place whichever one is playing
    const below = H * (1 - CEILING / NYQUIST) + R + 10 * dpr;
    const cy = Math.min(H - R - 6 * dpr, Math.max(below, py - R * 0.55));
    const x = lerp(px, cx, travel);
    const y = lerp(py, cy, travel);
    const r = Math.max(0, R * grow);
    if (r < 1) return;
    const a = clamp01(travel * 1.5);

    // The patch it reads, and the two lines that tie it to the loupe
    ctx.save();
    ctx.strokeStyle = `rgba(255,255,255,${0.75 * a})`;
    ctx.lineWidth = 1.2 * dpr;
    ctx.strokeRect(px - patch, py - patch, patch * 2, patch * 2);
    ctx.strokeStyle = `rgba(255,255,255,${0.28 * a})`;
    ctx.beginPath();
    ctx.moveTo(px + patch, py - patch);
    ctx.lineTo(x - r * 0.2, y - r * 0.98);
    ctx.moveTo(px + patch, py + patch);
    ctx.lineTo(x - r * 0.2, y + r * 0.98);
    ctx.stroke();

    // Glass
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(5,7,12,0.94)";
    ctx.shadowColor = "rgba(0,0,0,0.6)";
    ctx.shadowBlur = 24 * dpr;
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.clip();

    // Cells: organic noise in both; in the fake, a regular lattice lights up
    const n = 9;
    const cell = (2 * r) / n;
    const gap = Math.max(1, cell * 0.16);
    const shimmer = Math.floor(t * 9);
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const lx = x - r + i * cell;
        const ly = y - r + j * cell;
        let v = 0.12 + 0.38 * Math.pow(noise(i, j, shimmer + ((i * 7 + j * 3) % 5)), 1.6);
        if (fake && i % 2 === 1 && j % 2 === 1) {
          const on = outBack(span(t, T.grid + (i + j) * 0.035, T.grid + (i + j) * 0.035 + 0.28));
          v = lerp(v, 0.9, clamp01(on));
          if (on > 0) {
            ctx.shadowColor = "rgba(60,160,255,0.9)";
            ctx.shadowBlur = 10 * dpr * clamp01(on);
          }
        }
        ctx.fillStyle = rgb(v);
        const inset = gap / 2;
        ctx.fillRect(lx + inset, ly + inset, cell - gap, cell - gap);
        ctx.shadowBlur = 0;
      }
    }
    ctx.restore();

    // Rim
    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(220,238,255,0.85)";
    ctx.lineWidth = 1.5 * dpr;
    ctx.shadowColor = "rgba(60,160,255,0.8)";
    ctx.shadowBlur = 14 * dpr;
    ctx.stroke();
    ctx.restore();
  };

  // ---- The page around the canvas: classes the CSS animates ----
  const setFile = (next) => {
    file = next;
    inspect.dataset.file = next;
    chips.forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.file === next)));
    rows.forEach((row, i) => {
      const res = row.querySelector(".ix-check-result");
      res.textContent = res.dataset[next];
      row.classList.toggle("ok", OUTCOME[next][i] === "ok");
      row.classList.toggle("bad", OUTCOME[next][i] === "bad");
    });
    stamp.textContent = stamp.dataset[next];
  };
  const STEPS = [
    ["ix-dropped", 0],
    ["ix-ceiling-on", T.ceiling],
    ["ix-row1", T.row1],
    ["ix-row2", T.row2],
    ["ix-row3", T.row3],
    ["ix-landed", T.stamp],
    ["ix-thud", T.stamp + 0.27], // the window takes the stamp's weight
    ["ix-keep-on", T.keep],
  ];
  const syncClasses = (t) => {
    for (const [name, at] of STEPS) inspect.classList.toggle(name, t >= at);
  };
  const clearClasses = () => {
    for (const [name] of STEPS) inspect.classList.remove(name);
    void inspect.offsetWidth; // so the next run's transitions start from scratch
  };

  // ---- Playback: an elapsed clock that only advances while on screen ----
  let elapsed = T.end;
  let playing = false;
  let visible = false;
  let frame = 0;
  let last = 0;
  let queued = null; // { file, at }: the fake, after the real one, on first view
  let pending = null; // { file, at }: the next run, once the last one has faded out

  const loop = (now) => {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (playing) {
      elapsed = Math.min(T.end, elapsed + dt);
      syncClasses(elapsed);
      draw(elapsed);
      if (elapsed >= T.end) {
        playing = false;
        if (queued) queued.at = performance.now() + queued.hold;
      }
    } else if (pending && now >= pending.at) {
      const next = pending.file;
      pending = null;
      start(next);
    } else if (queued && queued.at && now >= queued.at) {
      const next = queued.file;
      queued = null;
      run(next);
    }
    frame = playing || queued || pending ? requestAnimationFrame(loop) : 0;
  };
  const kick = () => {
    if (frame || !visible) return;
    last = performance.now();
    frame = requestAnimationFrame(loop);
  };
  const start = (next) => {
    setFile(next);
    clearClasses();
    inspect.classList.remove("ix-fading");
    elapsed = 0;
    playing = true;
    draw(0);
  };
  const run = (next) => {
    chips.forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.file === next)));
    if (reduce) {
      setFile(next);
      elapsed = T.end;
      syncClasses(elapsed);
      draw(elapsed);
      return;
    }
    // Whatever is on screen fades out first, so a new run never starts on a cut
    const shown = inspect.classList.contains("ix-dropped");
    playing = false;
    if (shown) inspect.classList.add("ix-fading");
    pending = { file: next, at: performance.now() + (shown ? 320 : 0) };
    kick();
  };

  chips.forEach((chip) =>
    chip.addEventListener("click", () => {
      queued = null;
      run(chip.dataset.file);
    }),
  );
  replay.addEventListener("click", () => {
    queued = null;
    run(file);
  });
  if (reduce) replay.hidden = true;

  build();
  window.addEventListener("resize", () => {
    if (build()) draw(elapsed);
  });

  if (reduce || !("IntersectionObserver" in window)) {
    // The finished picture, nothing moving; the chips swap between the two
    run(reduce ? "fake" : "real");
  } else {
    setFile("real");
    draw(0);
    let seen = false;
    new IntersectionObserver(
      ([entry]) => {
        visible = entry.isIntersecting;
        if (!visible) {
          cancelAnimationFrame(frame);
          frame = 0;
          return;
        }
        if (!seen && entry.intersectionRatio >= 0.45) {
          seen = true;
          queued = { file: "fake", hold: 1400, at: 0 };
          run("real");
        } else {
          kick();
        }
      },
      { threshold: [0, 0.45] },
    ).observe(stage);
  }
}

// Scroll reveal for the lossless check's numbered points and verdict cards: each
// group fades up in a short stagger (--i in the markup). Skipped entirely under
// reduced motion, which leaves them where they are.
const reveals = document.querySelectorAll("[data-reveal]");
if (
  reveals.length &&
  "IntersectionObserver" in window &&
  !window.matchMedia("(prefers-reduced-motion: reduce)").matches
) {
  document.documentElement.classList.add("js-reveal");
  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("in");
        io.unobserve(entry.target);
      }
    },
    { threshold: 0.2, rootMargin: "0px 0px -8% 0px" },
  );
  reveals.forEach((el) => io.observe(el));
}

// Ambient sound-wave motifs: a track waveform under the hero, and a small
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

const trackWave = document.querySelector("#track-wave");
if (trackWave) {
  const reduceMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;
  const ctx = trackWave.getContext("2d");
  const wave = getComputedStyle(document.documentElement)
    .getPropertyValue("--wave")
    .trim();
  const sweepSeconds = 40;
  let width = 0;
  let height = 0;
  let heights = [];

  // A fixed "track" loudness profile: quiet intro, a build, the body, a short
  // outro. Bar count follows the width so bars keep the same spacing on phones.
  const profile = (count) =>
    Array.from({ length: count }, (_, i) => {
      const u = i / count;
      const outro = u > 0.88 ? (1 - u) * 8 : 1;
      const shape = 0.25 + 0.75 * Math.min(1, u * 4) * outro;
      const noise = Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1;
      return Math.max(0.08, shape * (0.45 + 0.55 * noise));
    });

  const resize = () => {
    const dpr = window.devicePixelRatio || 1;
    width = trackWave.clientWidth;
    height = trackWave.clientHeight;
    trackWave.width = width * dpr;
    trackWave.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    heights = profile(Math.max(40, Math.round(width / 6.5)));
  };

  const render = (playhead) => {
    ctx.clearRect(0, 0, width, height);
    const gap = width / heights.length;
    const barWidth = Math.max(1.5, gap * 0.5);
    heights.forEach((v, i) => {
      const barHeight = v * height;
      ctx.fillStyle =
        i / heights.length < playhead
          ? `rgba(${wave}, 0.7)`
          : "rgba(16, 17, 22, 0.12)";
      ctx.beginPath();
      ctx.roundRect(
        i * gap,
        (height - barHeight) / 2,
        barWidth,
        barHeight,
        barWidth / 2,
      );
      ctx.fill();
    });
  };

  let running = false;
  let playhead = 0.38;
  let last = null;
  const tick = (now) => {
    if (!running) return;
    if (last !== null) playhead = (playhead + (now - last) / 1000 / sweepSeconds) % 1;
    last = now;
    render(playhead);
    requestAnimationFrame(tick);
  };

  new ResizeObserver(() => {
    resize();
    render(playhead);
  }).observe(trackWave);

  if (!reduceMotion) {
    new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting === running) return;
      running = entry.isIntersecting;
      last = null;
      if (running) requestAnimationFrame(tick);
    }).observe(trackWave);
  }
}
