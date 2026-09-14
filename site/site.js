const release = document.querySelector("#release-line");
const command = document.querySelector("#xattr-command");
const copy = document.querySelector("#copy-command");

fetch("https://api.github.com/repos/EyalDelarea/flackey/releases/latest")
  .then((response) => (response.ok ? response.json() : Promise.reject()))
  .then((data) => {
    const asset = data.assets?.find((item) => item.name === "Flackey.zip");
    if (!release || !asset) return;
    const version = String(data.tag_name || "").replace(/^v/, "");
    const size = `${(asset.size / 1e6).toFixed(1)} MB`;
    const date = new Intl.DateTimeFormat("en-GB", {
      day: "numeric",
      month: "long",
      year: "numeric",
    }).format(new Date(data.published_at));
    release.textContent = `Version ${version} · ${size} · ${date}`;
  })
  .catch(() => {});

copy?.addEventListener("click", async () => {
  const text = command?.textContent || "";
  try {
    await navigator.clipboard.writeText(text);
    copy.textContent = "Copied";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(command);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    copy.textContent = "Selected";
  }
  window.setTimeout(() => {
    copy.textContent = "Copy";
  }, 1600);
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

const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
const finePointer = window.matchMedia("(pointer: fine)");
const hero = document.querySelector(".hero");
const vinyl = document.querySelector(".vinyl");
hero?.addEventListener("pointermove", (event) => {
  if (motion.matches || !finePointer.matches || !vinyl) return;
  const bounds = hero.getBoundingClientRect();
  vinyl.style.setProperty(
    "--ry",
    `${((event.clientX - bounds.left) / bounds.width - 0.5) * 5}deg`,
  );
  vinyl.style.setProperty(
    "--rx",
    `${((event.clientY - bounds.top) / bounds.height - 0.5) * -3}deg`,
  );
});
hero?.addEventListener("pointerleave", () => {
  vinyl?.style.setProperty("--rx", "0deg");
  vinyl?.style.setProperty("--ry", "0deg");
});
