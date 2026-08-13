// The jobs view's 2 s tick — dashboard.md §5.4.
//
// Polling, not SSE, and the argument is in the contract: a long-lived
// connection per open tab, against a single-process server that also holds the
// only SQLite writer, for a page watched for minutes a week, is a lifecycle
// problem bought with nothing.
//
// Everything below is an *enhancement*. The page it runs on is complete
// server-rendered HTML: the filters filter, the pager pages, the countdown is
// already a number of seconds printed by the server, and every value this
// script assigns was rendered by the same formatter on the same request. With
// JavaScript off the page is a snapshot, which is what a document is.
//
// The two rules from demo-site.md §6.2 hold here as everywhere: every string
// becomes a DOM text node (no innerHTML, no insertAdjacentHTML, no
// document.write, no eval), and every URL reaching a fetch or an href passes
// safeUrl(). Job event messages are yt-dlp's own strings.

const root = document.querySelector("[data-poll]");

/** Only http(s) is ever fetched or linked. */
function safeUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.href);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

/** The same shape `render.span()` prints, for the seconds between two ticks.
 *  The server sends every other duration as text precisely so that this is the
 *  only formatter on this side. */
function span(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${String(total % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

/** Recognised state words → the tone the stylesheet knows, exactly as
 *  `render.tone()` maps them. Anything unrecognised stays neutral rather than
 *  being coloured by guesswork. */
const TONES = {
  queued: "wait",
  running: "work",
  done: "ok",
  failed: "bad",
  skipped: "wait",
  cancelled: "neutral",
};

function setText(scope, field, value) {
  if (value === undefined || value === null) return;
  for (const node of scope.querySelectorAll(`[data-field="${field}"]`)) {
    node.textContent = value;
  }
}

function setState(scope, field, word) {
  for (const node of scope.querySelectorAll(`[data-field="${field}"]`)) {
    node.textContent = word;
    for (const name of [...node.classList]) {
      if (name.startsWith("tone-")) node.classList.remove(name);
    }
    node.classList.add(`tone-${TONES[word] || "neutral"}`);
  }
}

/** The countdown. `not_before` arrives as a number of seconds still to run;
 *  between polls this ticks it down locally, and every poll resets it, so a
 *  page left open never drifts and never invents a wait that has expired. */
function setDefer(scope, seconds) {
  for (const node of scope.querySelectorAll("[data-field='job-defer']")) {
    node.dataset.defer = String(Math.max(0, seconds));
    node.hidden = seconds <= 0;
  }
  setText(scope, "job-defer-clock", span(seconds));
}

function tickDeferrals() {
  for (const node of document.querySelectorAll("[data-defer]")) {
    const left = Math.max(0, Number(node.dataset.defer || 0) - 1);
    node.dataset.defer = String(left);
    node.hidden = left <= 0;
    for (const clock of node.querySelectorAll("[data-field='job-defer-clock']")) {
      clock.textContent = span(left);
    }
  }
}

/** The other second-by-second arithmetic on a number the server sent: a live
 *  job's wall clock, counting up.
 *
 *  This is the whole of "the page feels alive" and it is not decoration — it is
 *  the one motion DESIGN.md's table sanctions with nothing but "counts are
 *  counts". `data-wall` is present only on a job the server called live, so a
 *  finished job's clock is a measurement and stays one; every poll resets the
 *  number, so a tab left open overnight never drifts. */
function tickWallClocks() {
  for (const node of document.querySelectorAll("[data-wall]")) {
    if (node.dataset.wall === "") continue;
    const seconds = Number(node.dataset.wall) + 1;
    if (!Number.isFinite(seconds)) continue;
    node.dataset.wall = String(seconds);
    node.textContent = span(seconds);
  }
}

/** The one thing this script cannot patch is a row that is not on the page —
 *  a job queued since it rendered, or the panels a finished job makes stale.
 *  Both reveal a note the server already wrote; neither invents a sentence. */
function showStale() {
  for (const note of document.querySelectorAll("[data-field='job-stale']")) {
    note.hidden = false;
  }
}

function applyJob(scope, job) {
  setState(scope, "job-state", job.state);
  setText(scope, "job-progress", job.text.progress);
  setText(scope, "job-counts", job.text.counts);
  // The breakdown behind the percentage, so a hint held open while a job
  // advances is patched with the same numbers the bar beside it moved to.
  setText(scope, "job-tally", job.text.tally);
  setText(scope, "job-basis", job.text.basis);
  setText(scope, "job-wall", job.text.wall);
  setText(scope, "job-ran", job.text.ran);
  // The clock a job acquires by ending. Until then the server sent the em
  // dash, and this assigns the server's string either way — the rule this file
  // keeps everywhere: no formatter on this side but the two counters.
  setText(scope, "job-finished", job.text.finished);
  // The clock the ticker counts up between polls, re-synced to the server's
  // own number on every one of them.
  for (const node of scope.querySelectorAll("[data-wall]")) {
    node.dataset.wall = job.live && job.wall_s !== null ? String(job.wall_s) : "";
  }
  for (const bar of scope.querySelectorAll("[data-field='job-meter']")) {
    bar.style.width = `${job.progress}%`;
  }
  // "Working" is `running` and nothing else: claimed by the runner, a stage
  // executing. Queued, deferred, finished and failed all draw a bar that does
  // not move, because none of them is a machine doing anything.
  for (const box of scope.querySelectorAll("[data-field='job-meter-box']")) {
    box.classList.toggle("is-working", job.state === "running");
  }
  setDefer(scope, job.defer_s);
}

function applyItems(items) {
  for (const item of items) {
    const row = document.querySelector(`[data-item="${item.item_id}"]`);
    if (!row) continue;
    setState(row, "item-state", item.state);
    setText(row, "item-stage", item.text.stage);
    setText(row, "item-attempts", item.text.attempts);
    setText(row, "item-took", item.text.took);
  }
}

/** New events, prepended as elements — never as markup. The event log is the
 *  only record a non-rate-limit deferral has, so watching one arrive is the
 *  point of the page being live at all.
 *
 *  The list is now a digest: the newest few are in `[data-events]` and the rest
 *  are in a second list behind a `<details>`. A new event still goes to the top
 *  of the first list, where the reader is looking — but "already on the page"
 *  has to be asked of the *whole* panel, or every poll would re-prepend the
 *  events that have scrolled into the expander. */
function applyEvents(events) {
  const list = document.querySelector("[data-events]");
  if (!list) return;
  const known = new Set(
    [...document.querySelectorAll("[data-event]")].map((li) => li.dataset.event)
  );
  for (const event of [...events].reverse()) {
    if (known.has(String(event.id))) continue;
    const li = document.createElement("li");
    li.className = "event is-new";
    li.dataset.event = String(event.id);

    const at = document.createElement("span");
    at.className = "at";
    at.textContent = event.at_text;
    li.append(at);

    const level = document.createElement("span");
    level.className = `pill pill-small tone-${TONES[event.level] || "neutral"}`;
    level.textContent = event.level;
    li.append(level);

    if (event.stage) {
      const stage = document.createElement("code");
      stage.textContent = event.stage;
      li.append(stage);
    }
    const text = document.createElement("span");
    text.className = event.message ? "eventtext" : "eventtext muted";
    text.textContent = event.message || "message not published on this instance";
    li.append(text);

    list.prepend(li);
  }
}

if (root) {
  const url = safeUrl(root.dataset.poll);
  const interval = Math.max(1000, Number(root.dataset.interval) || 2000);
  let live = root.dataset.live === "yes";
  let timer = null;
  let ticker = null;

  const stop = () => {
    if (timer) clearInterval(timer);
    if (ticker) clearInterval(ticker);
    timer = null;
    ticker = null;
  };

  async function poll() {
    let payload;
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { accept: "application/json" },
      });
      // A refusal is a reason to stop, not to retry harder: 429 means this tab
      // is already the load, and 401 means the session went away.
      if (!response.ok) return stop();
      payload = await response.json();
    } catch {
      return stop(); // offline, or the server went away. The page stays honest.
    }

    if (payload.job) {
      applyJob(document, payload.job);
      applyItems(payload.items || []);
      applyEvents(payload.events || []);
    }
    for (const job of payload.jobs || []) {
      const row = document.querySelector(`[data-job="${job.job_id}"]`);
      if (row) applyJob(row, job);
      else showStale(); // a job queued since this page rendered
    }

    if (!payload.live && live) {
      // Everything terminal: nothing will change again, and the parts of the
      // page this script does not patch (the degraded list, the stage table)
      // are now the stale ones. Say so once, then stop.
      live = false;
      showStale();
      stop();
    }
  }

  if (url && live) {
    timer = setInterval(poll, interval);
    ticker = setInterval(() => {
      tickDeferrals();
      tickWallClocks();
    }, 1000);
    // The bar's width transition is exactly one poll interval long, so a stage
    // that moved between two measurements is drawn advancing rather than
    // jumping. It is the server's own cadence, not a chosen duration.
    document.documentElement.style.setProperty("--poll-ms", `${interval}ms`);
    // The countdown is wrong the moment the page was cached or the reader came
    // back to the tab, so re-sync on the way back rather than on a timer.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && timer) poll();
    });
  }
}
