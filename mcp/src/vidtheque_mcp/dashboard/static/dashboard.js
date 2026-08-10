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
const shotLines = document.getElementById("shot-lines");

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
  return Array.from(panel.querySelectorAll(".ocrline")).flatMap((line, index) => {
    const parts = (line.dataset.box || "").split(",").map(Number);
    if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return [];
    const [x0, y0, x1, y1] = parts;
    return [
      {
        x0,
        y0,
        x1,
        y1,
        index,
        text: (line.querySelector(".ocrtext")?.textContent || "").trim(),
        conf: (line.querySelector(".conf")?.textContent || "").trim(),
      },
    ];
  });
}

/** Light the box and the line that share an index, in whichever container is
 *  passed. One function for the grid and for the dialog, because the pairing is
 *  the same fact in both and a second implementation is a second thing to get
 *  out of step. */
function lightPair(scope, index, on) {
  for (const node of scope.querySelectorAll(`[data-line="${index}"]`)) {
    node.classList.toggle("is-lit", on);
  }
}

/** Both halves of the enlarged frame: the boxes over the still at full size,
 *  and the frame's own lines beside them. Every string is cloned as a text node
 *  from the `.ocrline` rows the server already rendered — demo-site.md §6.2,
 *  and the reason the dialog needs no second request. */
function drawBoxes(frameId) {
  boxes.replaceChildren();
  if (shotLines) shotLines.replaceChildren();
  const lines = boxesFor(frameId);

  for (const box of lines) {
    const mark = document.createElement("span");
    mark.className = "ocrbox";
    mark.dataset.line = String(box.index);
    mark.style.left = (box.x0 * 100).toFixed(3) + "%";
    mark.style.top = (box.y0 * 100).toFixed(3) + "%";
    mark.style.width = ((box.x1 - box.x0) * 100).toFixed(3) + "%";
    mark.style.height = ((box.y1 - box.y0) * 100).toFixed(3) + "%";
    // A title, not a label: the text is listed beside the frame, and a text
    // node here would sit on top of the thing it describes.
    mark.title = box.text;
    boxes.appendChild(mark);
  }

  if (!shotLines) return;
  for (const box of lines) {
    const li = document.createElement("li");
    li.className = "ocrline";
    li.dataset.line = String(box.index);

    const text = document.createElement("span");
    text.className = "ocrtext";
    text.textContent = box.text;
    li.append(text);

    if (box.conf) {
      const conf = document.createElement("span");
      conf.className = "conf";
      conf.textContent = box.conf;
      li.append(conf);
    }
    shotLines.append(li);
  }
  shotLines.hidden = lines.length === 0;
}

// The full-size half of the linkage. At 1152px a detection box is a target you
// can point at, which is exactly why this interaction lives here and not over a
// 512px thumbnail: hovering a box scrolls its line into view and lights it,
// hovering a line lights its box. Delegated, so it survives `replaceChildren`
// on every open.
if (dialog) {
  const pair = (event, on) => {
    const node = event.target.closest?.("[data-line]");
    if (!node) return;
    const index = node.dataset.line;
    lightPair(dialog, index, on);
    if (!on || !shotLines) return;
    // Only a box brings a line into view; a line hovering itself is already
    // where the reader is looking.
    if (!node.classList.contains("ocrbox")) return;
    const line = shotLines.querySelector(`[data-line="${index}"]`);
    if (line) line.scrollIntoView({ block: "nearest" });
  };
  dialog.addEventListener("pointerover", (event) => pair(event, true));
  dialog.addEventListener("pointerout", (event) => pair(event, false));
}

// The same pairing in the grid, in the one direction that is usable there: a
// line lights its box. The boxes themselves are `pointer-events: none` at this
// size — a detection box on a 512px still is a few millimetres of screen, and
// the honest answer to "I want to point at that box" is the enlarged frame.
//
// This is an enhancement, and what it enhances is *pointing*: every line, its
// text, its confidence and its box are server-rendered and complete with this
// file blocked. It is delegated at the document, so it costs one listener
// whatever the slide holds — and it works for every line rather than the first
// eight a stylesheet could enumerate, which is why it moved out of CSS.
for (const [type, on] of [["pointerover", true], ["pointerout", false], ["focusin", true], ["focusout", false]]) {
  document.addEventListener(type, (event) => {
    const line = event.target.closest?.(".ocrline");
    const frame = line?.closest("[data-ocrframe]");
    if (!frame) return;
    lightPair(frame, line.dataset.line, on);
  });
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
    if (shotLines) {
      shotLines.replaceChildren();
      shotLines.hidden = true;
    }
  });
}

// Click a shot and it is *selected into evidence* (Tom, 2026-08-10), not opened:
// the strip and the OCR panel scroll to that moment and the frame is marked in
// both. A modal on the first click would answer a question the operator has not
// asked yet — where in this video is that — and the second click, on the frame
// they can now see, is the one that opens it.
//
// With this file blocked the bar is still a real link to `#frame-N`, still
// carries its own offset when the frame is on another page of the strip, and
// `:target` still marks the frame it lands on. The interception only happens
// when the frame is already here, so a bar pointing off-page always navigates.
function selectFrame(ord) {
  const card = document.getElementById("frame-" + ord);
  if (!card) return false;
  for (const node of document.querySelectorAll(".is-selected")) {
    node.classList.remove("is-selected");
  }
  card.classList.add("is-selected");
  card.scrollIntoView({ block: "center", behavior: "smooth" });

  // Its OCR figure, if the frame carries on-screen text: the two panels are one
  // moment seen twice, and a selection that marked only half of it would send
  // the reader hunting for the other.
  const button = card.querySelector(".framebtn");
  const frameId = button && button.dataset.frame;
  const figure = frameId && document.getElementById("ocr-" + frameId);
  if (figure) figure.classList.add("is-selected");
  if (button) button.focus({ preventScroll: true });
  return true;
}

document.addEventListener("click", (event) => {
  const anchor = event.target.closest?.(".shotbar a");
  if (!anchor) return;
  const hash = (anchor.getAttribute("href") || "").split("#")[1] || "";
  const ord = hash.startsWith("frame-") ? hash.slice(6) : "";
  if (!ord) return;
  if (selectFrame(ord)) event.preventDefault();
});

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

// The transcript scrollbox. The "Next N cues" button is gone (Tom,
// 2026-08-10): a click that reloaded the page to move a hundred rows threw the
// strip, the OCR panel and the scroll position away with it. Nearing the end of
// the box asks the server for the next batch and appends it, and the sticky
// line above always says which cues these are out of how many.
//
// Everything here is an enhancement over a page that already works: the server
// rendered the first batch, the pager under the box is a pair of real links,
// and a fetch that fails simply gives them back. Every string assigned below
// was formatted by `views.cues_json` — the same formatters the template used —
// so this file carries no clock, no chunk label and no rounding of its own.
const cuebox = document.querySelector(".cuebox");
if (cuebox) {
  const list = cuebox.querySelector("[data-cuelist]");
  const loading = cuebox.querySelector("[data-field='cue-loading']");
  const range = document.querySelector("[data-field='cue-range']");
  const pager = document.querySelector("[data-cue-pager]");
  const source = safeUrl(cuebox.dataset.cues);
  const size = Math.max(1, Number(cuebox.dataset.cuePage) || 50);
  const first = Number(cuebox.dataset.cueFirst) || 0;
  const videoId = cuebox.dataset.video || "";
  let next = Number(cuebox.dataset.cueNext) || 0;
  let more = cuebox.dataset.cueMore === "yes";
  let busy = false;

  // The pager is the no-JavaScript path and the fallback if a fetch dies. Once
  // the appender is bound it is the appender's job, so it comes off the page —
  // two ways to advance the same list is one way too many.
  if (source && more && pager) pager.hidden = true;

  function addCue(cue) {
    if (cue.chunk) {
      const mark = document.createElement("li");
      mark.className = "chunkmark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = cue.chunk;
      list.append(mark);
    }
    const li = document.createElement("li");
    li.className = cue.in_chunk ? "cue in-chunk" : "cue";

    const at = document.createElement("a");
    at.className = "at";
    at.textContent = cue.at;
    const deep = safeUrl(`https://youtu.be/${videoId}?t=${cue.t}`);
    if (deep) {
      at.href = deep;
      at.target = "_blank";
      at.rel = "noopener noreferrer";
    }
    li.append(at);

    const text = document.createElement("span");
    text.className = "cuetext";
    text.textContent = cue.text;
    li.append(text);

    if (cue.speaker) {
      const speaker = document.createElement("span");
      speaker.className = "muted speaker";
      speaker.textContent = cue.speaker;
      li.append(speaker);
    }
    if (cue.conf) {
      const conf = document.createElement("span");
      conf.className = "conf";
      conf.textContent = cue.conf;
      li.append(conf);
    }
    list.append(li);
  }

  async function loadMore() {
    if (busy || !more || !source || !list) return;
    busy = true;
    if (loading) loading.hidden = false;
    try {
      const url = new URL(source, window.location.href);
      url.searchParams.set("offset", String(next));
      url.searchParams.set("limit", String(size));
      const response = await fetch(url.href, {
        credentials: "same-origin",
        headers: { accept: "application/json" },
      });
      if (!response.ok) throw new Error("refused");
      const payload = await response.json();
      for (const cue of payload.cues || []) addCue(cue);
      next += (payload.cues || []).length;
      more = Boolean(payload.has_more);
      if (range) range.textContent = `${first + 1}–${next}`;
    } catch {
      // A refusal or an offline tab: stop asking and give the reader the links
      // back. The batch already on the page stays on it.
      more = false;
      if (pager) pager.hidden = false;
    } finally {
      busy = false;
      if (loading) loading.hidden = true;
    }
  }

  // "Nearing the end" is one boxful short of it, which is the distance at which
  // the next batch has to already be arriving for the scroll not to stop. A
  // scroll handler rather than an observer, because a sentinel would live
  // inside the scroller whose content this same handler appends to.
  cuebox.addEventListener("scroll", () => {
    if (cuebox.scrollTop + cuebox.clientHeight * 2 >= cuebox.scrollHeight) loadMore();
  });
}
