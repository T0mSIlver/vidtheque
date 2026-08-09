// vidtheque dashboard — the interactive bits, and only those.
//
// The pages are documents: real links, real forms, a page per route, every
// filter in the URL. Everything here is an enhancement over markup that
// already works with JavaScript off — the shot timeline navigates, the pagers
// page, the filters filter. What this adds is reading a slide without leaving
// the page.
//
// Two rules, both asserted in test_dashboard.py, both from demo-site.md §6.2:
// every string becomes a DOM text node (no innerHTML, no insertAdjacentHTML,
// no document.write, no eval), and every URL that reaches an href or a src
// passes safeUrl(). The corpus is adversarial by construction: OCR text is
// whatever was on someone's screen, and a slide that says <script> is a
// normal slide.

const dialog = document.getElementById("shot");
const image = document.getElementById("shot-img");
const boxes = document.getElementById("shot-boxes");
const caption = document.getElementById("shot-caption");
const link = document.getElementById("shot-link");
const closeButton = document.getElementById("shot-close");
const showBoxes = document.getElementById("shot-showboxes");

/** Only http(s) ever reaches an href or a src. A `javascript:` URL in a
 *  payload becomes nothing at all rather than a live one. */
function safeUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.href);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

/** The OCR boxes this frame has, read from the page's own OCR panel.
 *
 *  The server already rendered them there, normalised 0–1, so the lightbox
 *  needs no second request and the page needs no JSON blob in a script tag. */
function boxesFor(frameId) {
  const panel = document.getElementById("ocr-" + frameId);
  if (!panel) return [];
  return Array.from(panel.querySelectorAll(".ocrline")).flatMap((line) => {
    const parts = (line.dataset.box || "").split(",").map(Number);
    if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return [];
    const [x0, y0, x1, y1] = parts;
    return [{ x0, y0, x1, y1, text: line.textContent.trim() }];
  });
}

function drawBoxes(frameId) {
  boxes.replaceChildren();
  for (const box of boxesFor(frameId)) {
    const mark = document.createElement("span");
    mark.className = "ocrbox";
    mark.style.left = (box.x0 * 100).toFixed(3) + "%";
    mark.style.top = (box.y0 * 100).toFixed(3) + "%";
    mark.style.width = ((box.x1 - box.x0) * 100).toFixed(3) + "%";
    mark.style.height = ((box.y1 - box.y0) * 100).toFixed(3) + "%";
    // A title, not a label: the text is already listed under the frame, and a
    // text node here would sit on top of the thing it describes.
    mark.title = box.text;
    boxes.appendChild(mark);
  }
}

function openFrame(button) {
  if (!dialog || typeof dialog.showModal !== "function") return false;
  const large = safeUrl(button.dataset.large);
  if (!large) return false;
  const frameId = button.dataset.frame || "";

  image.src = large;
  image.alt = button.querySelector("img")?.alt || "";
  caption.textContent = button.dataset.caption || frameId;

  const deep = safeUrl(button.dataset.link);
  if (deep) {
    link.href = deep;
    link.hidden = false;
  } else {
    link.removeAttribute("href");
    link.hidden = true;
  }

  drawBoxes(frameId);
  boxes.hidden = !(showBoxes && showBoxes.checked);
  dialog.showModal();
  return true;
}

document.addEventListener("click", (event) => {
  const button = event.target.closest(".framebtn");
  if (!button) return;
  if (openFrame(button)) event.preventDefault();
});

if (showBoxes) {
  showBoxes.addEventListener("change", () => {
    boxes.hidden = !showBoxes.checked;
  });
}

if (closeButton && dialog) {
  closeButton.addEventListener("click", () => dialog.close());
  // The dialog element is the backdrop: `.shot-inner` covers every pixel of
  // the box, so a click that landed on the dialog itself landed outside the
  // picture. That is what makes click-to-dismiss two lines instead of a hit
  // test. Esc, the focus trap and the modal semantics are the platform's.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  // Releasing the bytes on close: a 1280px JPEG per frame opened adds up over
  // a browsing session, and nothing needs it once the dialog is shut.
  dialog.addEventListener("close", () => {
    image.removeAttribute("src");
    boxes.replaceChildren();
  });
}

// Hovering a shot lifts its frames, and hovering a frame lifts its shot: the
// timeline and the strip are two views of one thing, and the link between them
// is otherwise invisible.
const timeline = document.querySelector(".timeline");
if (timeline) {
  const mark = (shotId, on) => {
    for (const card of document.querySelectorAll(".framecard")) {
      if (card.dataset.shot === shotId) card.classList.toggle("is-linked", on);
    }
    for (const shot of timeline.querySelectorAll(".shotbar")) {
      if (shot.dataset.shot === shotId) shot.classList.toggle("is-linked", on);
    }
  };
  const bind = (element) => {
    const shotId = element.dataset.shot;
    if (!shotId) return;
    element.addEventListener("pointerenter", () => mark(shotId, true));
    element.addEventListener("pointerleave", () => mark(shotId, false));
    element.addEventListener("focusin", () => mark(shotId, true));
    element.addEventListener("focusout", () => mark(shotId, false));
  };
  timeline.querySelectorAll(".shotbar").forEach(bind);
  document.querySelectorAll(".framecard").forEach(bind);
}
