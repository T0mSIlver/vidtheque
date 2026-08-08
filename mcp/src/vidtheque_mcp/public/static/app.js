// vidtheque demo — no framework, no build step, no external requests.
// It talks to /api/meta, /api/search and /api/ask; everything it renders is
// text the server already bounded (demo-site.md §2, §6).

const $ = (id) => document.getElementById(id);

const state = {
  contentType: "all",
  askMode: false,
  meta: null,
  query: "",
  offset: 0,
  inFlight: null,
};

// --------------------------------------------------------------------- utils

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const setStatus = (text, notes = []) => {
  const status = $("status");
  status.textContent = text || "";
  for (const note of notes) status.append(el("span", "note", note));
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
  if (hit.thumb) {
    const img = el("img", "hit-thumb");
    img.src = hit.thumb;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
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

const placeholder = (hit) =>
  el("div", "hit-thumb placeholder", PLACEHOLDER_LABEL[hit.source] || "video");

// The whole row is one <a>, so middle-click and "open in new tab" work.
const hitRow = (hit, query, n) => {
  const row = el("a", "hit");
  row.href = hit.link || `https://youtu.be/${hit.video_id}`;
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

async function runSearch(append = false) {
  const q = $("q").value.trim();
  if (!q) {
    $("results").replaceChildren();
    setStatus("");
    return;
  }
  state.query = q;
  syncUrl(q);
  if (!append) state.offset = 0;
  setStatus(append ? "loading more…" : "searching…");

  const params = new URLSearchParams({
    q,
    content_type: state.contentType,
    limit: "10",
    offset: String(state.offset),
  });
  let payload;
  try {
    const response = await fetch(`/api/search?${params}`);
    payload = await response.json();
    if (!response.ok) {
      setStatus(payload.message || "search failed");
      return;
    }
  } catch {
    setStatus("could not reach the server");
    return;
  }

  const results = $("results");
  if (!append) results.replaceChildren();
  document.querySelector(".more")?.remove();

  for (const hit of payload.results) results.append(hitRow(hit, q));

  const page = payload.pagination || {};
  if (!payload.results.length) {
    setStatus(`nothing matched “${q}”`, payload.notes || []);
    return;
  }
  const shown = state.offset + payload.results.length;
  // `has_more` over exact totals: the count probe is bounded, so an
  // "of ~N" that equals what is on screen would be noise, not information.
  const total = page.has_more && page.approx_total > shown ? ` of ~${page.approx_total}` : "";
  setStatus(`${shown} result${shown === 1 ? "" : "s"}${total}`, payload.notes || []);

  if (page.has_more) {
    const more = el("button", "ghost more", "More results");
    more.addEventListener("click", () => {
      state.offset = shown;
      runSearch(true);
    });
    results.after(more);
  }
}

// ----------------------------------------------------------------------- ask

// The answer text is plain prose with [n] markers; each becomes a link into
// the source list below it. Server-side, a marker naming nothing was already
// stripped — this only ever renders citations that exist.
const renderAnswer = (payload) => {
  const pane = $("answer");
  pane.replaceChildren();

  const byNumber = new Map(payload.citations.map((c) => [c.n, c]));
  for (const para of (payload.answer || "").split(/\n{2,}/)) {
    const p = el("p");
    let index = 0;
    for (const match of para.matchAll(/\[(\d{1,2})\]/g)) {
      if (match.index > index) p.append(para.slice(index, match.index));
      const n = Number(match[1]);
      if (byNumber.has(n)) {
        const link = el("a", "cite", `[${n}]`);
        link.href = byNumber.get(n).link || "#";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        p.append(link);
      } else {
        p.append(match[0]);
      }
      index = match.index + match[0].length;
    }
    p.append(para.slice(index));
    pane.append(p);
  }

  if (payload.citations.length) {
    const sources = el("div", "answer-sources");
    for (const c of payload.citations) sources.append(hitRow(c, "", c.n));
    pane.append(sources);
  }
  pane.hidden = false;
};

const renderDegraded = (message) => {
  const pane = $("answer");
  pane.replaceChildren();
  pane.append(el("p", "degraded", message));
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
  const pane = $("answer");
  pane.replaceChildren(el("p", "thinking", "reading the corpus…"));
  pane.hidden = false;
  $("results").replaceChildren();
  document.querySelector(".more")?.remove();
  setStatus("");
  $("go").disabled = true;

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q }),
    });
    const payload = await response.json();
    if (response.status === 429) {
      renderDegraded(
        `${payload.message || "Too many requests."} Try again in ${payload.retry_after_s || 60}s.`,
      );
    } else if (!response.ok) {
      renderDegraded(payload.message || "LLM mode unavailable — use search.");
    } else {
      renderAnswer(payload);
    }
  } catch {
    renderDegraded("LLM mode unavailable — use search.");
  } finally {
    $("go").disabled = false;
  }
}

// -------------------------------------------------------------------- wiring

function setAskMode(on) {
  state.askMode = on;
  for (const mode of document.querySelectorAll("#modes .chip")) {
    mode.classList.toggle("is-on", (mode.dataset.mode === "ask") === on);
  }
  // In ask mode the model picks the channel, so the content-type filter has
  // nothing to act on: hide it rather than leave a control that does nothing.
  $("chips").hidden = on;
  $("go").textContent = on ? "Ask" : "Search";
  if (!on) $("answer").hidden = true;
  const url = new URL(location.href);
  if (on) url.searchParams.set("ask", "1");
  else url.searchParams.delete("ask");
  history.replaceState(null, "", url);
}

$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  state.askMode ? runAsk() : runSearch();
});

for (const mode of document.querySelectorAll("#modes .chip")) {
  mode.addEventListener("click", () => setAskMode(mode.dataset.mode === "ask"));
}

for (const chip of document.querySelectorAll("#chips .chip")) {
  chip.addEventListener("click", () => {
    for (const other of document.querySelectorAll("#chips .chip")) {
      other.classList.remove("is-on");
    }
    chip.classList.add("is-on");
    state.contentType = chip.dataset.type;
    if ($("q").value.trim() && !state.askMode) runSearch();
  });
}

$("copy").addEventListener("click", async () => {
  const url = $("mcp-url").textContent;
  try {
    await navigator.clipboard.writeText(url);
    $("copy").textContent = "Copied";
  } catch {
    $("copy").textContent = "Select it";
  }
  setTimeout(() => ($("copy").textContent = "Copy"), 1600);
});

(async function boot() {
  try {
    const meta = await (await fetch("/api/meta")).json();
    state.meta = meta;
    $("mcp-url").textContent = meta.mcp_url;
    $("cli-line").textContent = `claude mcp add --transport http vidtheque ${meta.mcp_url}`;
    if (meta.repo) $("repo").href = meta.repo;
    // No key configured — hide the switch rather than offer a mode that 503s.
    $("modes").hidden = !meta.ask_enabled;
    if (meta.videos) {
      setStatus(`${meta.videos} video${meta.videos === 1 ? "" : "s"} indexed.`);
    }
  } catch {
    setStatus("could not reach the server");
  }

  const params = new URLSearchParams(location.search);
  const type = params.get("type");
  if (type) {
    for (const chip of document.querySelectorAll("#chips .chip")) {
      chip.classList.toggle("is-on", chip.dataset.type === type);
      if (chip.dataset.type === type) state.contentType = type;
    }
  }
  const q = params.get("q");
  if (q) $("q").value = q;
  // `?ask=1` arrives in ask mode with the question loaded, but does not fire:
  // an answer costs a slice of the daily model budget, so a shared link must
  // not spend it on page load. One click does.
  if (params.get("ask") && state.meta?.ask_enabled) setAskMode(true);
  else if (q) runSearch();
})();
