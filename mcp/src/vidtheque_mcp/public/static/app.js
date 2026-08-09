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

// One request, one screen of results. The skeleton reserves exactly this many.
const PAGE_SIZE = 10;

const state = {
  contentType: "all",
  askMode: false,
  meta: null,
  offset: 0,
  seq: 0, // a stale response must never overwrite a newer one
  abort: null,
  countdown: null,
  lastRows: 0, // how tall the last page of results actually was
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
// they land: one row per expected result, each the height of a typical hit
// (thumbnail box, title, meta, two lines of snippet).
//
// How many to expect is a guess, and a full page is the wrong one on a small
// corpus: reserving ten for a query that returns three shifts everything below
// by seven rows, which on a three-video demo is the common case and not the
// edge. The best evidence available is what the last search actually returned,
// so that is what the next one reserves — the first search of a session still
// guesses a full page.
const showSkeleton = (rows = state.lastRows || PAGE_SIZE) => {
  const results = $("results");
  results.replaceChildren();
  results.setAttribute("aria-busy", "true");
  for (let i = 0; i < rows; i += 1) {
    const row = el("div", "hit skel");
    row.append(el("div", "hit-thumb skel-block"));
    const body = el("div", "hit-body");
    for (const width of ["w-70", "w-40", "w-90", "w-60"]) {
      body.append(el("div", `skel-line skel-block ${width}`));
    }
    row.append(body);
    results.append(row);
  }
};

const clearResults = () => {
  stopCountdown();
  $("results").replaceChildren();
  $("results").removeAttribute("aria-busy");
  $("results-foot").replaceChildren();
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

const thumbFor = (hit) => {
  const src = safeUrl(hit.thumb);
  if (src) {
    const img = el("img", "hit-thumb");
    img.src = src;
    // The row's own link text says the title and the timestamp, so a verbose
    // alt would be read twice. It still names both for anyone landing on the
    // image alone, e.g. when the images are still loading.
    img.alt = `Frame from ${hit.title || hit.video_id} at ${hit.timestamp || "0:00"}`;
    img.loading = "lazy";
    img.decoding = "async";
    img.width = 160;
    img.height = 90;
    img.addEventListener("error", () => img.replaceWith(placeholder(hit)));
    return img;
  }
  return placeholder(hit);
};

// No keyframe for this hit (or the image failed to load): say which channel it
// came from rather than showing a broken frame.
const PLACEHOLDER_LABEL = {
  transcript: "spoken",
  "transcript+ocr": "spoken",
  ocr: "screen",
  frame: "frame",
};

const placeholder = (hit) => {
  const box = el("div", "hit-thumb placeholder");
  box.append(el("span", null, PLACEHOLDER_LABEL[hit.source] || "video"));
  box.setAttribute("aria-hidden", "true");
  return box;
};

// The whole row is one <a>, so middle-click and "open in new tab" work.
const hitRow = (hit, query, n) => {
  const row = el("a", "hit");
  row.href = safeUrl(hit.link) || `https://youtu.be/${encodeURIComponent(hit.video_id || "")}`;
  row.target = "_blank";
  row.rel = "noopener noreferrer";
  if (n) row.append(el("span", "cite-n", `[${n}]`));
  row.append(thumbFor(hit));

  const body = el("div", "hit-body");
  body.append(el("div", "hit-title", hit.title || hit.video_id));

  const meta = el("div", "hit-meta");
  if (hit.source) meta.append(el("span", "hit-source", hit.source));
  meta.append(document.createTextNode(hit.channel || "unknown"));
  meta.append(document.createTextNode(" · "));
  meta.append(el("span", "at", hit.timestamp || "0:00"));
  meta.append(document.createTextNode(" · youtu.be ↗"));
  body.append(meta);

  if (hit.text) {
    const snippet = el("div", "hit-text");
    snippet.append(highlight(hit.text, query));
    body.append(snippet);
  }
  row.append(body);
  return row;
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
    box.append(
      el(
        "p",
        "notice-detail",
        "This instance has an empty corpus, so no query can match. Index a " +
          "video and the same search will work.",
      ),
    );
    $("results").replaceChildren(box);
    return;
  }
  box.append(el("p", "notice-title", `Nothing matched “${q}”.`));
  const tips = el("ul", "notice-tips");
  if (state.contentType !== "all") {
    const li = el("li");
    li.append(`You are searching only ${CHANNEL_NAME[state.contentType]}. `);
    const widen = el("button", "linky", "Search all three channels");
    widen.addEventListener("click", () => selectContentType("all", true));
    li.append(widen);
    tips.append(li);
  }
  tips.append(
    el("li", null, "Try fewer words: two or three carry further than a sentence."),
  );
  tips.append(
    el(
      "li",
      null,
      "The corpus has three channels — the spoken transcript, the text that was " +
        "on screen, and the frames. A phrase from a slide will not be in the words.",
    ),
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
      detail.textContent = `The demo allows a burst then a steady trickle. Try again in ${left}s.`;
    } else {
      detail.textContent = "You can search again now.";
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
      "The demo may be restarting, or the connection dropped.",
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
  if (!append) results.replaceChildren();

  const hits = payload.results || [];
  if (!append && !hits.length) {
    setStatus("", payload.notes || []);
    renderNoResults(q, payload.data_status);
    return;
  }
  if (!append) state.lastRows = hits.length;
  for (const hit of hits) results.append(hitRow(hit, q));

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

// The answer text is plain prose with [n] markers; each becomes a link into
// the source list below it. Server-side, a marker naming nothing was already
// stripped — this only ever renders citations that exist.
const renderAnswer = (payload) => {
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
  if (payload.model) {
    pane.append(el("p", "answer-foot", `Answered from the corpus by ${payload.model}.`));
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
      wait.textContent = left > 0 ? `Ask is back in ${left}s.` : "You can ask again now.";
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

async function runAsk() {
  const q = $("q").value.trim();
  if (!q) return;
  const { signal, stale } = beginRequest();
  showEmptyState(false);
  const pane = $("answer");
  pane.replaceChildren(el("p", "thinking", "reading the corpus…"));
  pane.hidden = false;
  pane.setAttribute("aria-busy", "true");
  clearResults();
  setStatus("");
  syncUrl(q);
  $("go").disabled = true;

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q }),
      signal,
    });
    const payload = await response.json().catch(() => ({}));
    // The visitor may have switched to search — or asked something else —
    // while this was upstream. Every render below reopens the answer pane, so
    // a stale one would plant it over whatever is on screen now.
    if (stale()) return;
    if (response.status === 429) {
      renderDegraded(
        payload.message || "Too many questions for now — search still works.",
        Number(payload.retry_after_s) || Number(response.headers.get("Retry-After")) || 60,
      );
    } else if (!response.ok) {
      renderDegraded(
        payload.message || "LLM mode unavailable — use search.",
        Number(payload.retry_after_s) || 0,
      );
    } else {
      renderAnswer(payload);
    }
  } catch (error) {
    if (stale(error)) return;
    renderDegraded("Could not reach the server. Search still works once it is back.", 0);
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

for (const example of document.querySelectorAll(".example")) {
  example.addEventListener("click", () => {
    $("q").value = example.textContent.trim();
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
