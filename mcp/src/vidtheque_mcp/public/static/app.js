// vidtheque demo — no framework, no build step, no external requests.
// It talks to /api/meta, /api/videos, /api/search and /api/ask; everything it
// renders is text the server already bounded (demo-site.md §2, §6).
//
// Two rules hold everywhere below:
//   1. Nothing from the corpus, the model or the URL is ever inserted as HTML.
//      Every string arrives as a DOM text node. OCR text in particular is
//      whatever happened to be on someone's screen — treat it as hostile.
//   2. Every URL that reaches an href or a src goes through `safeUrl` first,
//      so a `javascript:` or `data:` string in a payload cannot become a link.

const $ = (id) => document.getElementById(id);

// One request, one screen of results — hits, which the page then groups into a
// card per video. What the skeleton reserves is that *shape*, not this number.
const PAGE_SIZE = 10;

const state = {
  contentType: "all",
  askMode: false,
  meta: null,
  offset: 0,
  seq: 0, // a stale response must never overwrite a newer one
  abort: null,
  countdown: null,
  lastShape: null, // moments per video card, the last time results landed
  cards: new Map(), // video_id -> the card on screen, so page 2 merges into it
};

// --------------------------------------------------------------------- utils

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

// Only http(s) survives. Everything else becomes null and the caller falls
// back — a link is the one place where corpus data would become code.
const safeUrl = (value) => {
  if (typeof value !== "string" || !value) return null;
  try {
    const url = new URL(value, location.href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
};

const stopCountdown = () => {
  if (state.countdown) clearInterval(state.countdown);
  state.countdown = null;
};

const setStatus = (text, notes = []) => {
  const status = $("status");
  status.replaceChildren();
  if (text) status.append(text);
  for (const note of notes) status.append(el("span", "note", note));
};

// "10 results of ~38", notes and all, kept aside while a *second* page is in
// flight: if that page fails, the count of what is still on screen is the
// truth, not "loading more…" frozen forever.
const statusNodes = () => Array.from($("status").childNodes, (node) => node.cloneNode(true));

const restoreStatus = (nodes) => $("status").replaceChildren(...(nodes || []));

// A ticking "try again in Ns" beats a frozen number: the wait is short and
// seeing it move is the difference between "broken" and "busy".
const setCountdown = (seconds, render) => {
  stopCountdown();
  let left = Math.max(1, Math.ceil(Number(seconds) || 1));
  render(left);
  state.countdown = setInterval(() => {
    left -= 1;
    if (left <= 0) {
      stopCountdown();
      render(0);
      return;
    }
    render(left);
  }, 1000);
};

const showEmptyState = (on) => {
  $("empty").hidden = !on;
};

// Reserve the space the results will occupy, so nothing below them moves when
// they land. Results are cards — a video header and its moments — so the shape
// to reserve is a list of moment counts, one entry per card.
//
// What to expect is a guess, and a full page of one-moment cards is the wrong
// one on a small corpus: reserving ten headers for a query that returns ten
// moments across three videos shifts everything below it. The best evidence
// available is what the *last* search actually returned, so that is the shape
// the next one reserves; the first search of a session guesses this, which is
// a ten-hit page spread over three talks — the demo corpus's usual answer.
const DEFAULT_SHAPE = [4, 3, 3];

const showSkeleton = (shape = state.lastShape || DEFAULT_SHAPE) => {
  const results = $("results");
  results.replaceChildren();
  results.setAttribute("aria-busy", "true");
  for (const moments of shape) {
    const card = el("div", "vcard skel");
    const head = el("div", "vcard-head");
    head.append(el("div", "hit-thumb skel-block"));
    const id = el("div", "vcard-id");
    id.append(el("div", "skel-line skel-block w-70"));
    id.append(el("div", "skel-line skel-block w-40"));
    head.append(id);
    card.append(head);
    const list = el("div", "moments");
    for (let i = 0; i < moments; i += 1) {
      list.append(el("div", "moment-skel skel-line skel-block w-90"));
    }
    card.append(list);
    results.append(card);
  }
};

const clearResults = () => {
  stopCountdown();
  $("results").replaceChildren();
  $("results").removeAttribute("aria-busy");
  $("results-foot").replaceChildren();
  state.cards.clear();
};

// ----------------------------------------------------------------- in flight

// One in-flight discipline for both modes (§6.1). A request aborts the one
// before it and takes the next sequence number; a reply renders only if it is
// still the newest **and** the mode it was issued in is still the mode on
// screen. The mode half is load-bearing: an ask can run for 90s, which is
// plenty of time to give up and search, and a late answer that reopens the
// answer pane over those results reads as the page being broken.
const beginRequest = () => {
  stopCountdown();
  state.abort?.abort();
  const abort = new AbortController();
  state.abort = abort;
  const seq = (state.seq += 1);
  const askMode = state.askMode;
  return {
    signal: abort.signal,
    // Called bare after an await, or with the rejection an abort produced.
    stale: (error) =>
      error?.name === "AbortError" || seq !== state.seq || askMode !== state.askMode,
  };
};

// Switching mode cancels whatever the other mode had in flight — and takes its
// half-drawn loading state with it, because the skeleton belongs to a search
// that is now never going to land.
const cancelInFlight = () => {
  stopCountdown();
  state.abort?.abort();
  state.abort = null;
  state.seq += 1;
  const results = $("results");
  results.removeAttribute("aria-busy");
  for (const row of results.querySelectorAll(".skel")) row.remove();
  $("go").disabled = false;
};

// Mark query terms in a snippet. Built with DOM nodes, never innerHTML: the
// text is a transcript from the corpus and the query is whatever was typed.
const highlight = (text, query) => {
  const frag = document.createDocumentFragment();
  const terms = (query || "")
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((t) => t.length > 2);
  if (!terms.length) {
    frag.append(text);
    return frag;
  }
  const lower = text.toLowerCase();
  let i = 0;
  while (i < text.length) {
    let best = -1;
    let bestLen = 0;
    for (const term of terms) {
      const at = lower.indexOf(term, i);
      if (at !== -1 && (best === -1 || at < best)) {
        best = at;
        bestLen = term.length;
      }
    }
    if (best === -1) {
      frag.append(text.slice(i));
      break;
    }
    if (best > i) frag.append(text.slice(i, best));
    frag.append(el("mark", null, text.slice(best, best + bestLen)));
    i = best + bestLen;
  }
  return frag;
};

// -------------------------------------------------------------- provenance
//
// Three sources are three different kinds of evidence, and the page says which
// one it is showing rather than letting the snippet imply it:
//
//   transcript      someone *said* this — a verbatim quotation of speech.
//   ocr             this was *on the screen* — text that was visible, not said.
//   frame           the *image* matched. There is no quotable text at all; any
//                   text carried along is what happened to be visible in it.
//   transcript+ocr  both channels agreed; the text is whichever was longer, so
//                   it is presented as neither a quote nor screen text.
//
// The word carries the meaning. Colour and border only reinforce it, so the
// badges still read on a monochrome screen and for a colour-blind visitor.
const SOURCE_BADGES = {
  transcript: ["spoken"],
  ocr: ["on-screen"],
  frame: ["frame"],
  "transcript+ocr": ["spoken", "on-screen"],
};

const BADGE_CLASS = {
  spoken: "badge badge-spoken",
  "on-screen": "badge badge-screen",
  frame: "badge badge-frame",
};

// An unknown source (a fourth leg, one day) still gets a badge with its own
// name: dropping the provenance silently is the one thing that must not happen.
const badgesFor = (source) => {
  const frag = document.createDocumentFragment();
  for (const label of SOURCE_BADGES[source] || (source ? [source] : [])) {
    frag.append(el("span", BADGE_CLASS[label] || "badge", label));
  }
  return frag;
};

const SNIPPET_CLASS = {
  transcript: "snip snip-spoken",
  ocr: "snip snip-screen",
  frame: "snip snip-frame",
  "transcript+ocr": "snip snip-mixed",
};

// The snippet, presented as what it is evidence of. A frame hit whose text the
// server dropped (there was none — the match was visual) gets no snippet at
// all rather than a sentence standing in for one.
const snippetFor = (hit, query) => {
  if (!hit.text) return null;
  const node = el("div", SNIPPET_CLASS[hit.source] || "snip");
  node.append(highlight(hit.text, query));
  return node;
};

// A frame hit *is* its image: the picture is the evidence, so it is rendered
// bigger than the decorative thumbnail a transcript hit carries.
const isFrameHit = (hit) => hit.source === "frame";

const thumbFor = (hit) => {
  const src = safeUrl(hit.thumb);
  if (src) {
    const wide = isFrameHit(hit);
    const img = el("img", "hit-thumb");
    img.src = src;
    // The row's own link text says the title and the timestamp, so a verbose
    // alt would be read twice. It still names both for anyone landing on the
    // image alone, e.g. when the images are still loading.
    img.alt = `Frame from ${hit.title || hit.video_id} at ${hit.timestamp || "0:00"}`;
    img.loading = "lazy";
    img.decoding = "async";
    // Mirrors the CSS box at 2x, so the row keeps its height before the bytes
    // arrive — the wide box for a frame hit included.
    img.width = wide ? 320 : 160;
    img.height = wide ? 180 : 90;
    // A frame that will not load takes its enlarge button with it: a control
    // that opens a broken image is worse than no control.
    img.addEventListener("error", () =>
      (img.closest(".hit-shot") || img).replaceWith(placeholder(hit)),
    );
    return img;
  }
  return placeholder(hit);
};

// No keyframe for this hit (or the image failed to load): say which channel it
// came from rather than showing a broken frame.
const placeholder = (hit) => {
  const box = el("div", "hit-thumb placeholder");
  box.append(el("span", null, (SOURCE_BADGES[hit.source] || [])[0] || "video"));
  box.setAttribute("aria-hidden", "true");
  return box;
};

// -------------------------------------------------------------- the lightbox
//
// A thumbnail is 96 or 160 CSS pixels of a slide: enough to recognise, never
// enough to read. Clicking one opens the frame at `thumb_large` (a width the
// *server* picked and clamped — the page cannot ask for a size of its own).
//
// The native <dialog> does the work: `showModal()` brings Esc, the inert
// background, the focus trap and the modal semantics with it. The two things
// it does not reliably do are click-outside and returning focus to the exact
// element that opened it, so those are here — the trigger is remembered rather
// than inferred, because clicking a button does not focus it on every browser.
const DIALOG_WORKS = typeof HTMLDialogElement !== "undefined" &&
  typeof HTMLDialogElement.prototype.showModal === "function";

let shotTrigger = null;

const openShot = (hit, trigger) => {
  const src = safeUrl(hit.thumb_large) || safeUrl(hit.thumb);
  if (!src) return;
  const dialog = $("shot");
  const image = $("shot-img");
  image.src = src;
  image.alt = `Frame from ${hit.title || hit.video_id} at ${hit.timestamp || "0:00"}`;
  const where = [hit.title || hit.video_id, hit.channel, hit.timestamp || "0:00"]
    .filter(Boolean)
    .join(" · ");
  $("shot-caption").textContent = where;
  const link = $("shot-link");
  link.href =
    safeUrl(hit.link) || `https://youtu.be/${encodeURIComponent(hit.video_id || "")}`;
  link.textContent = `open at ${hit.timestamp || "0:00"} on YouTube ↗`;
  shotTrigger = trigger || null;
  dialog.showModal();
  // `showModal()` focuses the first focusable thing in the dialog, which is the
  // YouTube link — so the first Enter after opening would leave the page. Close
  // is the safe landing, and the `autofocus` in the markup says the same thing
  // for anyone reading the HTML.
  $("shot-close").focus();
};

if (DIALOG_WORKS) {
  const dialog = $("shot");
  $("shot-close").addEventListener("click", () => dialog.close());
  // The dialog itself is only the backdrop: `.shot-inner` covers every pixel of
  // the box, so a click that lands on the dialog element landed outside it.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    // Nothing to release the image for otherwise: a 960px frame would stay
    // decoded until the next one replaced it.
    $("shot-img").removeAttribute("src");
    if (shotTrigger?.isConnected) shotTrigger.focus();
    shotTrigger = null;
  });
}

// The thumbnail cell: a button when there is a frame to enlarge, the plain
// image or the placeholder when there is not. It is *inside* the row rather
// than inside the row's link, because a button inside an <a> is neither valid
// nor operable — which is what makes the row a `<div>` with its own link.
const thumbCell = (hit) => {
  const image = thumbFor(hit);
  if (!DIALOG_WORKS || image.tagName !== "IMG") return image;
  const button = el("button", "hit-shot");
  button.type = "button";
  // The button owns the accessible name, so the image inside it is decorative
  // — otherwise a screen reader reads the frame twice.
  button.setAttribute(
    "aria-label",
    `Enlarge the frame from ${hit.title || hit.video_id} at ${hit.timestamp || "0:00"}`,
  );
  image.alt = "";
  button.append(image);
  button.addEventListener("click", () => openShot(hit, button));
  return button;
};

// A link into the corpus, as a real <a href>: middle-click and "open in new
// tab" are two of the three things a search result has to do.
const momentLink = (hit, className) => {
  const link = el("a", className);
  link.href =
    safeUrl(hit.link) || `https://youtu.be/${encodeURIComponent(hit.video_id || "")}`;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
};

// The row *was* one <a> around everything. It is now a row containing two
// controls — the thumbnail button and the link over the text — because the
// enlarge button cannot live inside the anchor. Everything the anchor used to
// cover except the image still opens the video, so middle-click is intact.
const hitRow = (hit, query, n) => {
  const row = el("div", isFrameHit(hit) ? "hit is-frame" : "hit");
  if (n) row.append(el("span", "cite-n", `[${n}]`));
  row.append(thumbCell(hit));

  const link = momentLink(hit, "hit-link");
  const body = el("div", "hit-body");
  body.append(el("div", "hit-title", hit.title || hit.video_id));

  const meta = el("div", "hit-meta");
  meta.append(badgesFor(hit.source));
  meta.append(document.createTextNode(hit.channel || "unknown"));
  meta.append(document.createTextNode(" · "));
  meta.append(el("span", "at", hit.timestamp || "0:00"));
  meta.append(document.createTextNode(" · youtu.be ↗"));
  body.append(meta);

  const snippet = snippetFor(hit, query);
  if (snippet) body.append(snippet);
  link.append(body);
  row.append(link);
  return row;
};

// ------------------------------------------------------ results, by video
//
// Ten flat rows are usually three talks. Grouped, the page answers the question
// a visitor actually has — *which videos cover this, and where* — instead of
// making them read the same title four times.
//
// One card per video: a header (frame, title, channel, moment count) and under
// it that video's moments, each a timestamp, its source badges and one line of
// snippet, each linking to youtu.be at that second. Grouping is per *page*: the
// server ranks and paginates, and re-ordering across pages here would mean a
// video quietly climbing the list because page 2 arrived.

const groupByVideo = (hits) => {
  const order = [];
  const byId = new Map();
  for (const hit of hits) {
    const key = hit.video_id || "";
    let group = byId.get(key);
    if (!group) {
      group = { key, hits: [] };
      byId.set(key, group);
      order.push(group);
    }
    group.hits.push(hit);
  }
  return order;
};

// The video itself, not the moment: the same link with the `?t=` taken off.
// A hit with no deep link (a source that is not YouTube) has no honest video
// URL either, and gets a title that is text rather than a link that guesses.
const videoUrl = (hit) => {
  const link = safeUrl(hit.link);
  if (!link) return null;
  const url = new URL(link);
  url.search = "";
  return url.href;
};

const momentCount = (n) => `${n} moment${n === 1 ? "" : "s"}`;

// One moment: when, from which channel, and what it says. The snippet is
// clamped to a line — this is a list to skim, and the whole hit is one click
// away in the video itself.
const momentRow = (hit, query) => {
  const item = el("li", isFrameHit(hit) ? "moment is-frame" : "moment");
  // A frame hit carries its own picture into the list: the image is the
  // evidence, and the card's header frame is a different second of the talk.
  if (isFrameHit(hit) && hit.thumb) item.append(thumbCell(hit));

  const link = momentLink(hit, "moment-link");
  link.append(el("span", "at", hit.timestamp || "0:00"));
  link.append(badgesFor(hit.source));
  const snippet = snippetFor(hit, query);
  if (snippet) link.append(snippet);
  item.append(link);
  return item;
};

const videoCard = (group) => {
  const node = el("div", "vcard");
  const head = el("div", "vcard-head");
  // Source-agnostic: whichever moment of this video has a frame behind it. The
  // header says *which talk*, so it does not care which leg found it.
  const cover = group.hits.find((hit) => hit.thumb) || group.hits[0];
  head.append(thumbCell(cover));

  const id = el("div", "vcard-id");
  const title = el("h3", "vcard-title");
  const href = videoUrl(cover);
  if (href) {
    const link = el("a", null, cover.title || cover.video_id);
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    title.append(link);
  } else {
    title.append(cover.title || cover.video_id);
  }
  id.append(title);

  const meta = el("div", "vcard-meta");
  meta.append(document.createTextNode(cover.channel || "unknown"));
  meta.append(document.createTextNode(" · "));
  const count = el("span", "vcard-count", momentCount(0));
  meta.append(count);
  id.append(meta);
  head.append(id);
  node.append(head);

  const moments = el("ol", "moments");
  node.append(moments);
  return { node, moments, count, shown: 0 };
};

// Render a page of hits into cards. A second page appends into the card a video
// already has rather than opening a duplicate header — the merge is one Map
// lookup, and a page 2 that repeats a title reads as the list restarting.
const renderGroups = (hits, query) => {
  const results = $("results");
  const shape = [];
  for (const group of groupByVideo(hits)) {
    let card = state.cards.get(group.key);
    if (!card) {
      card = videoCard(group);
      state.cards.set(group.key, card);
      results.append(card.node);
    }
    for (const hit of group.hits) card.moments.append(momentRow(hit, query));
    card.shown += group.hits.length;
    card.count.textContent = momentCount(card.shown);
    shape.push(group.hits.length);
  }
  return shape;
};

// -------------------------------------------------------------------- search

// A search is a URL, so a result is shareable and the back button works.
const syncUrl = (q) => {
  const url = new URL(location.href);
  if (q) {
    url.searchParams.set("q", q);
    if (state.contentType !== "all") url.searchParams.set("type", state.contentType);
    else url.searchParams.delete("type");
  } else {
    url.searchParams.delete("q");
    url.searchParams.delete("type");
  }
  history.replaceState(null, "", url);
};

const CHANNEL_NAME = {
  transcript: "the transcript",
  ocr: "on-screen text",
  frame: "frames",
};

// Nothing matched. Say what was searched, and offer the widening that is
// actually available — the other two channels are the usual reason a phrase
// misses (it was on a slide, not in the words).
const renderNoResults = (q, dataStatus) => {
  const box = el("div", "notice");
  // "Nothing matched" is a lie when there is nothing to match: an instance
  // that has not indexed anything yet says so instead of blaming the query.
  // `data_status` is the search tool's own word for it, forwarded by /api.
  if (dataStatus === "empty") {
    box.append(el("p", "notice-title", "Nothing is indexed yet."));
    $("results").replaceChildren(box);
    return;
  }
  box.append(el("p", "notice-title", `Nothing matched “${q}”.`));
  const tips = el("ul", "notice-tips");
  if (state.contentType !== "all") {
    const li = el("li");
    li.append(`${CHANNEL_NAME[state.contentType]} only · `);
    const widen = el("button", "linky", "Search all");
    widen.addEventListener("click", () => selectContentType("all", true));
    li.append(widen);
    tips.append(li);
  }
  tips.append(
    el("li", null, "Try fewer words."),
  );
  box.append(tips);
  $("results").replaceChildren(box);
};

// `append` is the whole difference between a hiccup and a wipe. A failed
// "More results" must not cost the visitor the ten rows they already have: the
// notice goes into the foot, under the list, where the button that asked for
// page 2 was. The next attempt clears the foot before it starts, so a retry
// cannot layer new rows *below* a stale error box either.
const renderError = (title, detail, retry, append = false) => {
  const box = el("div", "notice notice-bad");
  box.append(el("p", "notice-title", title));
  if (detail) box.append(el("p", "notice-detail", detail));
  if (retry) {
    const button = el("button", "ghost", "Try again");
    button.addEventListener("click", retry);
    box.append(button);
  }
  if (append) $("results-foot").replaceChildren(box);
  else $("results").replaceChildren(box);
  return box;
};

// 429 from the limiter: the honest answer is "in N seconds", counted down.
const renderRateLimited = (payload, headerRetry, retry, append = false) => {
  const seconds = Number(payload?.retry_after_s) || Number(headerRetry) || 60;
  const box = renderError("Too many requests.", "…", retry, append);
  const detail = box.querySelector(".notice-detail");
  const button = box.querySelector("button");
  if (button) button.disabled = true;
  setCountdown(seconds, (left) => {
    if (left > 0) {
      detail.textContent = `Try again in ${left}s.`;
    } else {
      detail.textContent = "Try again.";
      if (button) button.disabled = false;
    }
  });
};

async function runSearch(append = false) {
  const q = $("q").value.trim();
  if (!q) {
    clearResults();
    setStatus("");
    showEmptyState(true);
    syncUrl("");
    return;
  }
  syncUrl(q);
  showEmptyState(false);
  if (!append) state.offset = 0;

  const { signal, stale } = beginRequest();
  // What the count line said before "loading more…" replaced it.
  const priorStatus = append ? statusNodes() : null;

  setStatus(append ? "loading more…" : "searching…");
  if (!append) showSkeleton();
  $("results-foot").replaceChildren();

  const params = new URLSearchParams({
    q,
    content_type: state.contentType,
    limit: String(PAGE_SIZE),
    offset: String(state.offset),
  });
  // A failed page is a failed *page*: in append mode the list and its count
  // survive it, and only the foot changes.
  const failed = () => (append ? restoreStatus(priorStatus) : setStatus(""));

  let response;
  let payload;
  try {
    response = await fetch(`/api/search?${params}`, { signal });
    payload = await response.json();
  } catch (error) {
    if (stale(error)) return;
    failed();
    $("results").removeAttribute("aria-busy");
    renderError(
      "Could not reach the server.",
      "",
      () => runSearch(append),
      append,
    );
    return;
  }
  if (stale()) return;
  $("results").removeAttribute("aria-busy");

  if (response.status === 429) {
    failed();
    renderRateLimited(
      payload,
      response.headers.get("Retry-After"),
      () => runSearch(append),
      append,
    );
    return;
  }
  if (!response.ok) {
    failed();
    renderError(
      payload?.message || "Search failed.",
      payload?.next || "",
      () => runSearch(append),
      append,
    );
    return;
  }

  const results = $("results");
  if (!append) {
    results.replaceChildren();
    // The cards on screen are the ones a *later* page merges into; a fresh
    // search has none, and keeping the old map would merge page 1 of this
    // search into a card that is no longer in the document.
    state.cards.clear();
  }

  const hits = payload.results || [];
  if (!append && !hits.length) {
    setStatus("", payload.notes || []);
    renderNoResults(q, payload.data_status);
    return;
  }
  const shape = renderGroups(hits, q);
  // What the next search reserves: the shape this one actually had.
  if (!append) state.lastShape = shape.length ? shape : null;

  const page = payload.pagination || {};
  const shown = state.offset + hits.length;
  // `has_more` over exact totals: the count probe is bounded, so an
  // "of ~N" that equals what is on screen would be noise, not information.
  const total = page.has_more && page.approx_total > shown ? ` of ~${page.approx_total}` : "";
  setStatus(`${shown} result${shown === 1 ? "" : "s"}${total}`, payload.notes || []);

  if (page.has_more) {
    const more = el("button", "ghost more", "More results");
    more.addEventListener("click", () => {
      state.offset = shown;
      more.disabled = true;
      runSearch(true);
    });
    $("results-foot").replaceChildren(more);
  }
}

// ----------------------------------------------------------------------- ask

// ------------------------------------------------------------- the work log
//
// An ask can sit ninety seconds behind one static line while the model works
// the corpus. Every tool call it makes arrives as an activity event (§3.5) and
// becomes one line here: what it asked for, then what came back. The server
// builds the words — they are counted from real results — and the page's only
// job is to put them on screen as text, like every other corpus string.
//
// The answer itself is *not* streamed. It lands whole, and when it does the log
// folds into a disclosure under it: the evidence trail stays inspectable, and
// the answer owns the pane.

const workLog = () => {
  const list = el("ol", "worklist");
  const lines = new Map();
  return {
    node: list,
    // A line the model has started. It is the current work, so it carries the
    // one spinner on screen — see `runAsk`, which parks the idle one while it.
    start(id, text) {
      const item = el("li", "work is-running");
      item.append(el("span", "work-what", text || ""));
      lines.set(id, item);
      list.append(item);
    },
    // …and what it found. Never a guess: the server counted it. The arrow is a
    // text node rather than a `::before`, so the line still reads as a line
    // when it is copied out of the page or the stylesheet never arrives.
    done(id, result) {
      const item = lines.get(id);
      if (!item) return;
      item.classList.remove("is-running");
      if (result) item.append(el("span", "work-result", ` → ${result}`));
    },
    get empty() {
      return !lines.size;
    },
  };
};

// The trail, after the fact. `<details>` because the platform's disclosure is
// the whole widget — keyboard, semantics, the open/closed state — and a demo
// does not need a second one.
const workDisclosure = (log) => {
  const box = el("details", "worklog");
  box.append(el("summary", null, "Show its work"));
  box.append(log.node);
  return box;
};

// The answer text is plain prose with [n] markers; each becomes a link into
// the source list below it. Server-side, a marker naming nothing was already
// stripped — this only ever renders citations that exist.
const renderAnswer = (payload, log) => {
  const pane = $("answer");
  pane.replaceChildren();

  const byNumber = new Map((payload.citations || []).map((c) => [c.n, c]));
  for (const para of (payload.answer || "").split(/\n{2,}/)) {
    const p = el("p");
    let index = 0;
    for (const match of para.matchAll(/\[(\d{1,2})\]/g)) {
      if (match.index > index) p.append(para.slice(index, match.index));
      const n = Number(match[1]);
      const cited = byNumber.get(n);
      if (cited) {
        const link = el("a", "cite", `[${n}]`);
        link.href = safeUrl(cited.link) || "#";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.setAttribute("aria-label", `Source ${n}: ${cited.title || ""} at ${cited.timestamp || ""}`);
        p.append(link);
      } else {
        p.append(match[0]);
      }
      index = match.index + match[0].length;
    }
    p.append(para.slice(index));
    pane.append(p);
  }

  if (payload.citations?.length) {
    const sources = el("div", "answer-sources");
    sources.append(el("h2", "sources-title", "Sources"));
    for (const c of payload.citations) sources.append(hitRow(c, "", c.n));
    pane.append(sources);
  }
  // Sources are the evidence; the log is how it was found. Under both, folded.
  if (log && !log.empty) pane.append(workDisclosure(log));
  if (payload.model) {
    pane.append(el("p", "answer-foot", `Model · ${payload.model}`));
  }
  pane.hidden = false;
};

// Ask is the mode that is allowed to be unavailable: search always works, and
// the pane says so with the button that gets you there.
const renderDegraded = (message, retryAfter) => {
  const pane = $("answer");
  pane.replaceChildren();
  pane.append(el("p", "degraded", message));
  const wait = el("p", "degraded-wait");
  if (retryAfter) {
    pane.append(wait);
    setCountdown(retryAfter, (left) => {
      wait.textContent = left > 0 ? `Try again in ${left}s.` : "Try again.";
    });
  }
  const fallback = el("button", "ghost", "Search instead");
  fallback.addEventListener("click", () => {
    setAskMode(false);
    runSearch();
  });
  pane.append(fallback);
  pane.hidden = false;
};

// The stream comes over the same POST in one of two framings (§3.5). SSE is
// asked for first and is what the deployment actually uses: Cloudflare buffers
// every proxied response *except* `text/event-stream`, so through the tunnel
// the NDJSON variant arrived as ninety seconds of silence and then a burst —
// which is the exact failure the stream exists to remove. NDJSON stays as the
// second preference for anything in front of a proxy that does not care.
//
// Either way this is `fetch` plus a reader, not `EventSource`: the ask is a
// POST with a body, and `EventSource` is GET-only. SSE here is a wire format.
const SSE = "text/event-stream";
const NDJSON = "application/x-ndjson";
const CAN_STREAM =
  typeof ReadableStream === "function" && typeof TextDecoder === "function";

// One JSON object per event in both framings, so the difference is only where
// it sits on the wire: a bare line in NDJSON, the `data:` field of a
// blank-line-terminated block in SSE. `json.dumps` escapes newlines server-side,
// so an event is always exactly one line of JSON — which is what lets one
// reader handle both by looking for the payload rather than counting frames.
const parseFrame = (chunk) => {
  for (const raw of chunk.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    // SSE's own lines: `event:` is redundant (the payload names itself) and a
    // `:` comment is the server keeping the connection flowing. Both skipped.
    if (line.startsWith(":") || line.startsWith("event:") || line.startsWith("id:")) continue;
    // `data: {…}` — the single space after the colon is optional and stripped
    // by the spec, which `trim()` already does. NDJSON has no prefix at all.
    const payload = line.startsWith("data:") ? line.slice(5).trim() : line;
    if (!payload) continue;
    // A payload that does not parse is a truncated stream, not an event. It is
    // skipped; with no terminal event the caller falls through to the degraded
    // pane, which is the honest end for a stream that stopped.
    try {
      return JSON.parse(payload);
    } catch {
      return null;
    }
  }
  return null;
};

// Read the body frame by frame, handing each complete one to `onEvent`. A chunk
// boundary can land mid-frame and mid-character, which is what the buffer and
// `{stream: true}` are for. `stale()` is checked per event, not only per fetch:
// a mode switch aborts the request, and nothing it sends afterwards may draw.
//
// The separator is the framing's: a newline for NDJSON, a blank line for SSE.
// Both are found by the same scan, because a single "\n" never terminates an
// SSE block and "\n\n" never appears inside an NDJSON line.
const readEvents = async (response, stale, onEvent) => {
  const sse = (response.headers.get("content-type") || "").includes(SSE);
  const separator = sse ? "\n\n" : "\n";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let cut;
  for (;;) {
    const { value, done } = await reader.read();
    buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
    while ((cut = buffer.indexOf(separator)) !== -1) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + separator.length);
      if (!chunk.trim()) continue;
      if (stale()) {
        await reader.cancel();
        return;
      }
      const event = parseFrame(chunk);
      if (event) onEvent(event);
    }
    if (done) return;
  }
};

async function runAsk() {
  const q = $("q").value.trim();
  if (!q) return;
  const { signal, stale } = beginRequest();
  showEmptyState(false);
  const pane = $("answer");
  const log = workLog();
  // Two things wait here: the log, which fills as the model works, and one
  // idle line under it. Exactly one spinner is on screen at any moment — the
  // idle line steps aside while a tool call is running, because the honest
  // place for it is whichever line is the current work.
  const idle = el("p", "thinking", "reading the corpus…");
  pane.replaceChildren(log.node, idle);
  pane.hidden = false;
  // The pane is a live region; `aria-busy` holds the announcement until the
  // answer lands rather than reading out every line as it arrives.
  pane.setAttribute("aria-busy", "true");
  clearResults();
  setStatus("");
  syncUrl(q);
  $("go").disabled = true;

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: CAN_STREAM
          ? `${SSE}, ${NDJSON};q=0.9, application/json;q=0.8`
          : "application/json",
      },
      body: JSON.stringify({ q }),
      signal,
    });
    // The visitor may have switched to search — or asked something else —
    // while this was upstream. Every render below reopens the answer pane, so
    // a stale one would plant it over whatever is on screen now.
    if (stale()) return;

    // Everything that fails *before* the model is reached is still a status
    // code with a JSON body: a 429 from the limiter with its `Retry-After`, a
    // 503 with no key. Only a request that got as far as the loop streams.
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      if (stale()) return;
      renderDegraded(
        payload.message ||
          (response.status === 429
            ? "Too many questions for now."
            : "Ask unavailable."),
        Number(payload.retry_after_s) ||
          Number(response.headers.get("Retry-After")) ||
          (response.status === 429 ? 60 : 0),
      );
      return;
    }

    // Whichever framing the server picked — or neither, if it answered the
    // plain JSON body to a client that asked for a stream it does not serve.
    const type = response.headers.get("content-type") || "";
    const streamed = type.includes(SSE) || type.includes(NDJSON);
    if (!streamed || !response.body) {
      const payload = await response.json().catch(() => ({}));
      if (stale()) return;
      renderAnswer(payload);
      return;
    }

    let answer = null;
    let failure = null;
    await readEvents(response, stale, (event) => {
      if (event.event === "activity") {
        if (event.phase === "start") {
          log.start(event.id, event.text);
          idle.hidden = true;
        } else {
          log.done(event.id, event.result);
          idle.hidden = false;
        }
      } else if (event.event === "answer") {
        answer = event.payload || {};
      } else if (event.event === "error") {
        failure = event.payload || {};
      }
    });
    if (stale()) return;
    // No partial answer is ever shown: either the whole thing arrived, or the
    // pane degrades. A stream that stopped without a terminal event is the
    // second case, and reads as the server going away — which it did.
    if (answer) renderAnswer(answer, log);
    else if (failure) {
      renderDegraded(
        failure.message || "Ask unavailable.",
        Number(failure.retry_after_s) || 0,
      );
    } else {
      renderDegraded("Answer interrupted.", 0);
    }
  } catch (error) {
    if (stale(error)) return;
    renderDegraded("Could not reach the server.", 0);
  } finally {
    // Only the newest request owns the controls: an aborted one must not
    // un-busy the pane the request that replaced it is still filling.
    if (!stale()) {
      pane.removeAttribute("aria-busy");
      $("go").disabled = false;
    }
  }
}

// -------------------------------------------------------------------- wiring

function setAskMode(on) {
  // The switch itself ends whatever the mode being left had in flight. Without
  // this an ask and a search overlap, and the slower one lands in a page that
  // is no longer showing its mode.
  if (on !== state.askMode) cancelInFlight();
  state.askMode = on;
  for (const mode of document.querySelectorAll("#modes .chip")) {
    const selected = (mode.dataset.mode === "ask") === on;
    mode.classList.toggle("is-on", selected);
    mode.setAttribute("aria-pressed", String(selected));
  }
  // In ask mode the model picks the channel, so the content-type filter has
  // nothing to act on: hide it rather than leave a control that does nothing.
  $("chips").hidden = on;
  $("go").textContent = on ? "Ask" : "Search";
  $("q").setAttribute(
    "placeholder",
    on ? "ask a question about these talks…" : "kv cache, nvidia-smi, ontology…",
  );
  if (!on) {
    stopCountdown();
    $("answer").hidden = true;
  }
  const url = new URL(location.href);
  if (on) url.searchParams.set("ask", "1");
  else url.searchParams.delete("ask");
  history.replaceState(null, "", url);
}

function selectContentType(type, rerun) {
  state.contentType = type;
  for (const chip of document.querySelectorAll("#chips .chip")) {
    const selected = chip.dataset.type === type;
    chip.classList.toggle("is-on", selected);
    chip.setAttribute("aria-pressed", String(selected));
  }
  if (rerun && $("q").value.trim() && !state.askMode) runSearch();
}

$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  // Enter (or the button) is the only thing that spends a request: no
  // search-as-you-type against a shared rate limit.
  $("q").blur();
  state.askMode ? runAsk() : runSearch();
});

for (const mode of document.querySelectorAll("#modes .chip")) {
  mode.addEventListener("click", () => setAskMode(mode.dataset.mode === "ask"));
}

for (const chip of document.querySelectorAll("#chips .chip")) {
  chip.addEventListener("click", () => selectContentType(chip.dataset.type, true));
}

// An example may name the channel it needs: `owl:FunctionalProperty` is only
// on a slide, and a query about what a frame *looks like* has to reach the
// frame leg. It sets the chip row rather than smuggling a parameter past it,
// so the pin is visible, it lands in the shared URL, and the widening tip in
// the empty state ("you are searching only on-screen text") already knows how
// to undo it.
//
// An example with no `data-type` resets to `all`, and that is not a default —
// it is the fix for the sequence that otherwise looks like a broken corpus:
// click the on-screen example, then click a spoken one, and without the reset
// the second query runs pinned to OCR and finds nothing it was ever going to.
for (const example of document.querySelectorAll(".example")) {
  example.addEventListener("click", () => {
    $("q").value = example.textContent.trim();
    selectContentType(example.dataset.type || "all", false);
    state.askMode ? runAsk() : runSearch();
  });
}

$("copy").addEventListener("click", async () => {
  const url = $("mcp-url").textContent;
  let said;
  try {
    await navigator.clipboard.writeText(url);
    said = "Copied";
  } catch {
    said = "Select it";
    const range = document.createRange();
    range.selectNodeContents($("mcp-url"));
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }
  $("copy").textContent = said;
  $("copy-note").textContent = said === "Copied" ? "Endpoint copied to the clipboard." : "";
  setTimeout(() => {
    $("copy").textContent = "Copy";
    $("copy-note").textContent = "";
  }, 1600);
});

// What is actually in here, listed on the cold page — the fastest way to
// understand a corpus is to see the two talks it is made of.
const renderCorpus = (videos) => {
  if (!videos?.length) return;
  const list = $("corpus-list");
  list.replaceChildren();
  for (const video of videos.slice(0, 6)) {
    const item = el("li");
    const link = el("a", null, video.title || video.video_id);
    link.href = safeUrl(video.link) || `https://youtu.be/${encodeURIComponent(video.video_id || "")}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    item.append(link);
    if (video.channel) item.append(el("span", "muted", ` · ${video.channel}`));
    list.append(item);
  }
  $("corpus").hidden = false;
};

(async function boot() {
  const params = new URLSearchParams(location.search);
  const q = params.get("q");
  const type = params.get("type");
  if (type && CHANNEL_NAME[type]) selectContentType(type, false);
  if (q) {
    $("q").value = q;
    // Synchronous, before the first await: a shared result link must not
    // flash the teaching state on its way to the results.
    showEmptyState(false);
  }

  try {
    const meta = await (await fetch("/api/meta")).json();
    state.meta = meta;
    $("mcp-url").textContent = meta.mcp_url;
    $("cli-line").textContent = `claude mcp add --transport http vidtheque ${meta.mcp_url}`;
    const repo = safeUrl(meta.repo);
    if (repo) $("repo").href = repo;
    // No key configured — hide the switch rather than offer a mode that 503s.
    $("modes").hidden = !meta.ask_enabled;
    // The way into the browsable corpus, shown only when the server says the
    // route group is there. `safeUrl` is the wrong tool here — it resolves
    // against the current page and would happily accept an absolute URL on
    // another host — so this is a same-origin *path* check instead: one
    // leading slash, no second one (`//host` is cross-origin), nothing but the
    // characters a route prefix is made of.
    if (typeof meta.browse === "string" && /^\/[a-z0-9][a-z0-9/_-]*$/i.test(meta.browse)) {
      $("browse").href = meta.browse;
      $("browse").hidden = false;
    }
    if (!q && meta.videos) {
      setStatus(`${meta.videos} video${meta.videos === 1 ? "" : "s"} indexed.`);
    }
  } catch {
    setStatus("could not reach the server");
    // The endpoint is the server's to state (it is the same string the OAuth
    // `resource` uses), so an unreachable server gets no guessed URL — and a
    // copy button with nothing to copy is disabled rather than lying.
    $("mcp-url").textContent = "unavailable — reload the page";
    $("copy").disabled = true;
  }

  // `?ask=1` arrives in ask mode with the question loaded, but does not fire:
  // an answer costs a slice of the daily model budget, so a shared link must
  // not spend it on page load. One click does.
  if (params.get("ask") && state.meta?.ask_enabled) setAskMode(true);
  else if (q) runSearch();

  if (!q) {
    showEmptyState(true);
    try {
      const listing = await (await fetch("/api/videos?limit=6")).json();
      renderCorpus(listing.videos);
    } catch {
      /* the examples above are enough on their own */
    }
  }
})();
