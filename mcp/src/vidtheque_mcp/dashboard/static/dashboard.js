// vidtheque dashboard — the interactive bits, and only those.
//
// The pages are documents: real links, real forms, a page per route, every
// filter in the URL. Everything here is an enhancement over markup that
// already works with JavaScript off — the shot timeline navigates, the pagers
// page, the filters filter. What this adds is reading a slide without leaving
// the page, and previewing a shot without going to it.
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

// The scrub preview. Point anywhere along the shot timeline and the shot under
// the pointer shows its own first keyframe, the way a video player previews a
// seek — except that the unit here is a shot, because that is the unit the
// band is made of and the only one the index has a frame for.
//
// Everything below is an enhancement over markup that already works: with this
// file blocked the bars are still real links to `#frame-N`, still carry their
// `title`, and still describe themselves to a screen reader. The server sends
// no preview markup with content in it — the box is empty and `hidden` — so a
// page with no JavaScript has nothing to explain.
const scrub = document.getElementById("scrub");
if (timeline && scrub) {
  const shotImage = scrub.querySelector(".scrubshot img");
  const shotSpan = scrub.querySelector(".scrubspan");
  const shotMeta = scrub.querySelector(".scrubmeta");
  const bars = Array.from(timeline.querySelectorAll(".shotbar"));

  // The bars' true geometry, read once from the percentages the server wrote.
  // Used only when a hit test lands in a gap between bars: `min-width: 3px`
  // means a rendered bar can be wider than its share of the runtime, so where
  // the two disagree the pointer wins — you are pointing at a bar you can see.
  const geometry = bars.map((bar) => {
    const left = parseFloat(bar.style.left) || 0;
    return { bar, left, right: left + (parseFloat(bar.style.width) || 0) };
  });

  // Frames already fetched. A sweep across two hundred shots must not be two
  // hundred requests, so a new frame waits out a short pause before it is
  // asked for — and a frame that has been asked for once is set immediately,
  // because the cost of the second time is a cache lookup.
  const fetched = new Set();
  const SETTLE_MS = 70;
  let pending = 0;
  let current = null;

  // The bar under a pointer at `fraction` of the band, hit test first.
  function barAt(target, fraction) {
    const hit = target instanceof Element ? target.closest(".shotbar") : null;
    if (hit) return hit;
    const at = fraction * 100;
    let nearest = null;
    let distance = Infinity;
    for (const entry of geometry) {
      if (at >= entry.left && at <= entry.right) return entry.bar;
      const gap = at < entry.left ? entry.left - at : at - entry.right;
      if (gap < distance) {
        distance = gap;
        nearest = entry.bar;
      }
    }
    return nearest;
  }

  // Horizontal: clamp the box inside the band, so a shot at either end is
  // previewed without the box hanging off the page. Vertical: above the band,
  // never over the bars it is describing — unless the band has been scrolled
  // near the top of the viewport and there is no room up there, in which case
  // it goes under. Measured, not guessed: `offsetHeight` is only real once the
  // box is visible, which is why `show()` unhides before it calls this.
  function place(x) {
    const width = scrub.offsetWidth;
    const band = timeline.getBoundingClientRect();
    const half = width / 2;
    const clamped =
      band.width <= width ? band.width / 2 : Math.min(Math.max(x, half), band.width - half);
    scrub.classList.toggle("is-below", band.top < scrub.offsetHeight + 16);
    scrub.style.left = Math.round(clamped) + "px";
  }

  function show(bar, x) {
    if (!bar) return hide();
    scrub.hidden = false;
    place(x);
    if (bar === current) return;
    current = bar;
    // Text nodes, never markup — demo-site.md §6.2, the same rule the lightbox
    // caption keeps. `data-span` and `data-kept` are the server's own strings.
    shotSpan.textContent = bar.dataset.span || "";
    shotMeta.textContent = "shot " + (bar.dataset.shot || "?") +
      (bar.dataset.kept ? " · " + bar.dataset.kept : "");
    clearTimeout(pending);
    const url = safeUrl(bar.dataset.preview);
    if (!url) {
      shotImage.removeAttribute("src");
      return;
    }
    if (fetched.has(url)) {
      shotImage.src = url;
      return;
    }
    // Drop the previous shot's frame rather than leaving it under the new
    // shot's caption: an empty box is honest, a stale one is not.
    shotImage.removeAttribute("src");
    pending = setTimeout(() => {
      fetched.add(url);
      shotImage.src = url;
    }, SETTLE_MS);
  }

  function hide() {
    clearTimeout(pending);
    current = null;
    scrub.hidden = true;
    shotImage.removeAttribute("src");
  }

  // A native tooltip under a real preview is the same sentence told twice and
  // a second late. It stays in the markup for the no-JavaScript case and comes
  // off here, which is the only place that knows the preview exists.
  for (const bar of bars) {
    const link = bar.querySelector("a");
    if (link) link.removeAttribute("title");
  }

  timeline.addEventListener("pointermove", (event) => {
    // A tap is a navigation, not a hover: on a touch screen the bar's own link
    // is the whole interaction and a preview would only be in front of it.
    if (event.pointerType === "touch") return;
    const rect = timeline.getBoundingClientRect();
    if (!rect.width) return;
    const x = event.clientX - rect.left;
    show(barAt(event.target, x / rect.width), x);
  });
  timeline.addEventListener("pointerleave", hide);

  // Focus is the keyboard's pointer. The bar's own centre is where the preview
  // goes, so tabbing and scrubbing put the same box in the same place.
  timeline.addEventListener("focusin", (event) => {
    const bar = event.target.closest(".shotbar");
    if (!bar) return;
    const rect = timeline.getBoundingClientRect();
    const box = bar.getBoundingClientRect();
    show(bar, box.left - rect.left + box.width / 2);
  });
  timeline.addEventListener("focusout", (event) => {
    if (!timeline.contains(event.relatedTarget)) hide();
  });

  // Arrow keys step shots. The anchors were already focusable and already the
  // navigation, so this moves focus between them rather than inventing a
  // selection model of its own: whatever the arrows land on, Enter follows.
  const STEPS = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 1, ArrowUp: -1 };
  timeline.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "Escape") return hide();
    const bar = event.target.closest(".shotbar");
    if (!bar) return;
    let next = null;
    if (event.key === "Home") next = bars[0];
    else if (event.key === "End") next = bars[bars.length - 1];
    else if (event.key in STEPS) {
      const index = bars.indexOf(bar) + STEPS[event.key];
      next = bars[Math.min(Math.max(index, 0), bars.length - 1)];
    }
    if (!next) return;
    event.preventDefault();
    const link = next.querySelector("a");
    if (link) link.focus();
  });
}
