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
