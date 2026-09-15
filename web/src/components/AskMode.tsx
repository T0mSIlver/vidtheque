"use client";

import Link from "next/link";
import { Fragment, useEffect, useRef, useState } from "react";
import { AskEvent, AskFailure, type AskAnswer, type Citation } from "@/lib/api/schemas";
import { badgeWords, channelWord, presentationOf } from "@/lib/group";
import { framingOf, readJsonEvents } from "@/lib/sse";
import { FrameShot } from "./Frame";
import { Receipt } from "./Receipt";
import { AskSwitch, QueryMark, StateCell } from "./SearchBox";
import styles from "./AskMode.module.css";

// Ask mode is one Client Component that owns the question, the stream and
// what it produced, because all three change together while bytes arrive.
// A shared link arrives with the question loaded and does not fire: an
// answer costs a slice of the daily model budget, and only a click spends it.

type Line = { id: number; text: string; result?: string };
type Phase =
  | { kind: "idle" }
  | { kind: "working"; lines: Line[] }
  | { kind: "answered"; lines: Line[]; answer: AskAnswer }
  | { kind: "degraded"; lines: Line[]; body: AskFailure };

// The event kinds this build knows. A kind it does not is a server that has
// grown a new one: skipped, because ignoring an addition is what forward
// compatibility means. One of these arriving malformed is a different thing —
// the answer is not coming — and it says so.
const KNOWN_EVENTS = new Set(["activity", "answer", "error"]);

// The stream ended before the answer did: the connection dropped, the model
// timed out, the proxy cut it. There is nothing to show and nothing to blame,
// so it offers the one thing that helps.
const INTERRUPTED: AskFailure = {
  error: "interrupted",
  reason: "no_terminal_event",
  message: "Answer interrupted.",
  retry_after_s: null,
};

// A known event kind that did not match its shape. Not swallowed: the events
// after it describe an answer we can no longer trust.
const MALFORMED: AskFailure = {
  error: "malformed_stream",
  reason: "bad_event",
  message: "The answer arrived in a shape this page does not understand.",
  retry_after_s: null,
};

// The stream comes over the same POST in one of two framings (demo-site.md
// §3.5). SSE is asked for first and is what the deployment actually uses:
// Cloudflare buffers every proxied response *except* `text/event-stream`, so
// through the tunnel the NDJSON variant arrived as ninety seconds of silence
// and then a burst — the exact failure the stream exists to remove. NDJSON
// stays the second preference for anything in front of a proxy that does not
// care, and plain JSON is what a browser with no reader asks for: the same
// answer, with nothing to watch on the way.
const CAN_STREAM = typeof ReadableStream === "function" && typeof TextDecoder === "function";
const STREAM_ACCEPT = "text/event-stream, application/x-ndjson;q=0.9, application/json;q=0.8";

export function AskMode({
  initialQ,
  examples = [],
  askEnabled = true,
  children,
}: {
  initialQ: string;
  /** The cold page's questions. Copy, so they are the page's and not this
   *  component's; a chip click runs in the mode that is on screen. */
  examples?: readonly string[];
  /** Whether this deployment has an ask at all — the boot call's answer, which
   *  arrives after the box does. A deployment with no key is corrected into
   *  search mode when it lands (`AskSwitch`); every other one never notices. */
  askEnabled?: boolean | Promise<boolean>;
  /** What is in this corpus, listed under the examples. */
  children?: React.ReactNode;
}) {
  const [q, setQ] = useState(initialQ);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const input = useRef<HTMLInputElement>(null);
  // The in-flight request, so a second ask or an unmount cancels the first.
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  async function ask(question: string) {
    if (!question) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    // A reply that lost the race — a second ask, or an unmount — must not
    // land on top of the state the newer one is building.
    const show = (next: Phase) => {
      if (abort.current === controller && !controller.signal.aborted) setPhase(next);
    };

    let lines: Line[] = [];
    show({ kind: "working", lines });
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: CAN_STREAM ? STREAM_ACCEPT : "application/json",
        },
        body: JSON.stringify({ q: question }),
        signal: controller.signal,
      });
      // Everything that fails *before* the model is reached is still a status
      // code with a JSON body: a 429 from the limiter with its `Retry-After`,
      // a 503 with no key. Only a request that got as far as the loop streams.
      if (!res.ok) {
        show({ kind: "degraded", lines, body: await failureBody(res) });
        return;
      }
      // Whichever framing the server picked — or neither, if it answered the
      // plain JSON body to a client that asked for a stream it does not serve.
      // That is the same answer arriving whole, not a degraded pane.
      const framing = framingOf(res.headers.get("content-type"));
      if (!framing || !res.body) {
        const parsed = AskAnswerOf(await res.json().catch(() => null));
        show(
          parsed
            ? { kind: "answered", lines, answer: parsed }
            : { kind: "degraded", lines, body: MALFORMED },
        );
        return;
      }
      // The stream is only finished when it says so. Bytes running out first
      // is a truncated answer, and silence would leave the box looking ready.
      // The terminal event ends the read, too: a trailing `activity` frame —
      // a tool call the server logged after it sent the answer — would put the
      // pane back into `working` over a phase that had already settled, and
      // the button with it. Breaking here is also what closes the body, by way
      // of the generator's `finally`.
      let settled = false;
      for await (const raw of readJsonEvents(res.body, framing)) {
        const parsed = AskEvent.safeParse(raw);
        if (!parsed.success) {
          if (isFutureEvent(raw)) continue;
          show({ kind: "degraded", lines, body: MALFORMED });
          settled = true;
          break;
        }
        const ev = parsed.data;
        if (ev.event === "activity") {
          lines =
            ev.phase === "start"
              ? [...lines, { id: ev.id, text: ev.text ?? "" }]
              : lines.map((l) => (l.id === ev.id ? { ...l, result: ev.result ?? "" } : l));
          show({ kind: "working", lines });
        } else if (ev.event === "answer") {
          show({ kind: "answered", lines, answer: ev.payload });
          settled = true;
          break;
        } else {
          show({ kind: "degraded", lines, body: ev.payload });
          settled = true;
          break;
        }
      }
      if (!settled) show({ kind: "degraded", lines, body: INTERRUPTED });
    } catch (err) {
      if (controller.signal.aborted) return;
      show({ kind: "degraded", lines, body: unreachable(0, err) });
    }
  }

  function run(question: string) {
    input.current?.blur();
    void ask(question);
  }

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    run(q.trim());
  }

  const busy = phase.kind === "working";
  const state = busy ? "reading" : phase.kind === "degraded" ? "refused" : "ready";
  return (
    <div className={styles.ask}>
      {/* One form in both modes, and it is a search landmark in both: the mode
          changes what the answer looks like, not what the box is for. */}
      <form role="search" onSubmit={onSubmit} className={styles.form} aria-busy={busy}>
        {/* A real `<label>` and not an `aria-label` (demo-site.md §6.2): the
            field's name is a thing in the document, and clicking the label
            focuses the field. */}
        <label className={styles.srOnly} htmlFor="q">
          Your question
        </label>
        <div className={styles.bar}>
          <QueryMark className={styles.ic} />
          <input
            id="q"
            ref={input}
            type="search"
            name="q"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="ask a question about AI engineering…"
            enterKeyHint="search"
            spellCheck={false}
            autoComplete="off"
            className={styles.input}
          />
          <StateCell state={state} />
          <button type="submit" className={styles.go} disabled={busy}>
            Ask ✨
          </button>
        </div>
        {/* In ask mode the model picks the channel, so the content-type filter
            has nothing to act on and is not drawn at all. */}
        <div className={styles.controls}>
          <AskSwitch ask q={q} enabled={askEnabled} onLeave={() => abort.current?.abort()} />
        </div>
      </form>

      {phase.kind === "idle" ? (
        <section className={styles.empty} aria-label="Getting started">
          <p className={styles.kick}>
            <s />
            <span>start here</span>
          </p>
          <h2 className={styles.exhead}>Ask one of these</h2>
          {/* A search box teaches itself; a question box does not. Nothing else
              on screen says whether the answer is read out of these talks or
              invented over them, and that distinction is the product. */}
          <p className={styles.exnote}>
            None of these is answered by one talk. The model reads the corpus to build the answer
            and hands back the sentence, the talk and the second it was said.
          </p>
          <ul className={styles.examples}>
            {examples.map((question) => (
              <li key={question}>
                <button
                  type="button"
                  className={styles.example}
                  onClick={() => {
                    setQ(question);
                    run(question);
                  }}
                >
                  {question}
                </button>
              </li>
            ))}
          </ul>
          {children}
        </section>
      ) : (
        <Pane phase={phase} q={q} onRetry={() => void ask(q.trim())} />
      )}
    </div>
  );
}

// The pane is a live region and stays `aria-busy` until the answer: without
// that, a screen reader narrates six activity rows and then re-narrates two of
// them as their results land (demo-site.md §6.6).
function Pane({
  phase,
  q,
  onRetry,
}: {
  phase: Exclude<Phase, { kind: "idle" }>;
  q: string;
  onRetry: () => void;
}) {
  const busy = phase.kind === "working";
  return (
    <section className={styles.answer} aria-live="polite" aria-busy={busy} aria-label="Answer">
      {busy ? (
        <>
          <WorkLog lines={phase.lines} />
          {/* Exactly one spinner is on screen at any moment: the idle line
              steps aside while a tool call is running, because the honest place
              for it is whichever line is the current work. It steps aside *in
              the flow* — hiding it outright shortened the document by a line at
              the start of every call and grew it back at the end, and a reader
              parked near the bottom had their scroll clamped down and anchored
              back up on each one (Tom, 2026-08-11). */}
          <p
            className={`${styles.thinking} ${
              phase.lines.some((l) => l.result === undefined) ? styles.parked : ""
            }`}
          >
            reading the corpus…
          </p>
        </>
      ) : null}
      {phase.kind === "answered" ? <Answer answer={phase.answer} lines={phase.lines} /> : null}
      {phase.kind === "degraded" ? (
        <Degraded body={phase.body} q={q} lines={phase.lines} onRetry={onRetry} />
      ) : null}
    </section>
  );
}

function unreachable(status: number, err?: unknown): AskFailure {
  return {
    error: "unreachable",
    reason: status ? `http_${status}` : err instanceof Error ? err.name : "network",
    message: "Could not reach the server.",
    retry_after_s: null,
  };
}

// The body of a reply that never became a stream. The limiter's envelope has
// no `reason`; §3.4's degraded body does. Both carry the sentence to show and,
// between the body and the header, when to come back — the body first, because
// a header is the half anything on the way can rewrite. A 429 with neither
// waits the limiter's own minute rather than retrying into the same refusal.
async function failureBody(res: Response): Promise<AskFailure> {
  const header = Number(res.headers.get("retry-after")) || null;
  const floor = res.status === 429 ? 60 : null;
  const parsed = AskFailure.safeParse(await res.json().catch(() => null));
  if (!parsed.success) {
    return {
      ...unreachable(res.status),
      message: res.status === 429 ? "Too many questions for now." : "Ask unavailable.",
      retry_after_s: header ?? floor,
    };
  }
  return { ...parsed.data, retry_after_s: parsed.data.retry_after_s ?? header ?? floor };
}

// A plain-JSON answer, from a server that does not stream or a client that did
// not ask. Shape-checked like every other payload; a body that is not one is a
// stream that stopped making sense, not an answer.
function AskAnswerOf(raw: unknown): AskAnswer | null {
  const parsed = AskEvent.safeParse({ event: "answer", payload: raw });
  return parsed.success && parsed.data.event === "answer" ? parsed.data.payload : null;
}

// An event kind this build has never heard of, which is the one case worth
// skipping past. Anything else — no `event` field, a kind we know arriving
// wrong — is a stream that has stopped making sense.
function isFutureEvent(raw: unknown): boolean {
  if (typeof raw !== "object" || raw === null) return false;
  const kind = (raw as { event?: unknown }).event;
  return typeof kind === "string" && !KNOWN_EVENTS.has(kind);
}

// "Show its work" (§6.6): one line per tool call, the one still running
// marked. Every line is derived from the call, so this is a log, not a
// progress bar.
//
// A fixed box of six lines, reserved before it is needed. Every tool call used
// to append a row, the pane grew 46px, and the connect band and the footer
// under it moved by that much four to eight times an ask. A longer run scrolls
// inside the box, and the box scrolls itself to the newest line — the one
// worth reading. This is the one auto-scroll on the surface and it moves a
// 169px box, never the document.
function WorkLog({ lines, folded = false }: { lines: Line[]; folded?: boolean }) {
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [lines.length]);

  return (
    <ol
      ref={list}
      className={`${styles.log} ${folded ? styles.logFolded : ""}`}
      aria-label="What the model is doing"
    >
      {lines.map((l) => (
        <li key={l.id} className={l.result === undefined ? styles.running : undefined}>
          <span className={styles.logText}>{l.text}</span>
          {/* The arrow is a text node and not a `::before`: a log a visitor
              copies out of the page should still read as a log. */}
          {l.result ? <span className={styles.logResult}>{` → ${l.result}`}</span> : null}
        </li>
      ))}
    </ol>
  );
}

// The trail, after the fact. `<details>` because the platform's disclosure is
// the whole widget — keyboard, semantics, the open/closed state — and a demo
// does not need a second one.
function WorkDisclosure({ lines }: { lines: Line[] }) {
  if (!lines.length) return null;
  return (
    <details className={styles.disclosure}>
      <summary>Show its work</summary>
      <WorkLog lines={lines} folded />
    </details>
  );
}

function Answer({ answer, lines }: { answer: AskAnswer; lines: Line[] }) {
  const byNumber = new Map(answer.citations.map((c) => [c.n, c]));
  return (
    <>
      {/* The prose is one column of the pane, not the pane: above the demo's
          own breakpoint the sources sit beside it. */}
      <div className={styles.prose}>
        {answer.answer.split(/\n{2,}/).map((para, i) => (
          <p key={i} className={styles.para}>
            <Cited text={para} byNumber={byNumber} />
          </p>
        ))}
      </div>
      {answer.citations.length > 0 ? (
        <div className={styles.sources}>
          <h2 className={styles.label}>Sources</h2>
          <ol>
            {answer.citations.map((c) => (
              <Source key={c.n} c={c} />
            ))}
          </ol>
        </div>
      ) : null}
      {/* Sources are the evidence; the log is how it was found. Under both. */}
      <WorkDisclosure lines={lines} />
      {answer.model ? <p className={styles.foot}>model · {answer.model}</p> : null}
    </>
  );
}

// The answer is plain prose with `[n]` markers, and each becomes a link into
// the moment it cites. Server-side, a marker naming nothing was already
// stripped, so this only ever renders citations that exist — and a marker that
// somehow names nothing here stays the text it was rather than becoming a dead
// link.
function Cited({ text, byNumber }: { text: string; byNumber: Map<number, Citation> }) {
  const out: React.ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(/\[(\d{1,2})\]/g)) {
    if (match.index > cursor) out.push(text.slice(cursor, match.index));
    const n = Number(match[1]);
    const cited = byNumber.get(n);
    out.push(
      cited?.link ? (
        <a
          key={`${n}-${match.index}`}
          className={styles.cite}
          href={cited.link}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Source ${n}: ${cited.title} at ${cited.timestamp}`}
        >
          [{n}]
        </a>
      ) : (
        match[0]
      ),
    );
    cursor = match.index + match[0].length;
  }
  out.push(text.slice(cursor));
  return (
    <>
      {out.map((node, i) => (
        <Fragment key={i}>{node}</Fragment>
      ))}
    </>
  );
}

// A citation is one moment, so this list keeps the flat row a search result
// has — the same provenance badges, the same receipt (demo-site.md §6.3).
//
// The title goes back to the talk, which is where the flat row put it before
// `/videos/{id}` existed and where it goes again since that page was removed
// (2026-09-07). A citation with no deep link falls back to the video's own
// `youtu.be` URL, as the demo's `app.js` did.
// The four ways a snippet is set, one per provenance — the same four a result
// row uses, because a citation *is* a result row (`ResultGroup`'s `SNIPPET`).
const SNIPPET: Record<string, string> = {
  spoken: styles.snipSpoken,
  screen: styles.snipScreen,
  frame: styles.snipFrame,
  mixed: styles.snipMixed,
};

function Source({ c }: { c: Citation }) {
  const kinds = badgeWords(c.source ?? "");
  return (
    <li className={styles.source}>
      <span className={styles.n}>[{c.n}]</span>
      <div className={styles.sourceShot}>
        <FrameShot shot={c} alt="" label={channelWord(c.source ?? "")} />
      </div>
      <div className={styles.sourceText}>
        <a
          href={c.link ?? `https://youtu.be/${encodeURIComponent(c.video_id)}`}
          target="_blank"
          rel="noopener noreferrer"
          className={styles.sourceTitle}
        >
          {c.title}
        </a>
        <span className={styles.sourceMeta}>
          {kinds.map((kind) => (
            <span
              key={kind}
              className={`${styles.badge} ${kind === "on-screen" ? styles.seen : ""}`}
            >
              {kind}
            </span>
          ))}
          {c.channel} · <span className={styles.mono}>{c.timestamp}</span>
        </span>
        {/* Presented as what it is evidence of, exactly as a result row is
            (demo-site.md §6.3): a Sources list that sets a slide's text as
            speech is the one place the page can lie about provenance. */}
        {c.text ? (
          <span className={`${styles.snippet} ${SNIPPET[presentationOf(c.source ?? "")]}`}>
            {c.text}
          </span>
        ) : null}
        {/* The filled slab, where there are three of them and they are the
            payoff (demo-site.md §6.5). */}
        {c.link ? <Receipt href={c.link} size="lg" className={styles.receipt} /> : null}
      </div>
    </li>
  );
}

/** A wait that moves. The floor and the ceiling are `RetryIn`'s, for the same
 *  reason: `retry_after_s` is a float, and a 0.4 rendered raw says "go" on a
 *  bucket that is still empty. */
function useCountdown(seconds: number | null | undefined): number {
  const [left, setLeft] = useState(() =>
    seconds ? Math.max(1, Math.ceil(Number(seconds) || 1)) : 0,
  );
  useEffect(() => {
    if (left <= 0) return;
    const id = setTimeout(() => setLeft(left - 1), 1000);
    return () => clearTimeout(id);
  }, [left]);
  return left;
}

// Ask is the mode that is allowed to be unavailable: search always works, and
// the pane says so with the link that gets you there. The retry wears the
// submit button's style because it is the same act, and it is gated until the
// wait is over — a retry that fires into a refusal is one more refusal.
function Degraded({
  body,
  q,
  lines,
  onRetry,
}: {
  body: AskFailure;
  q: string;
  lines: Line[];
  onRetry: () => void;
}) {
  const left = useCountdown(body.retry_after_s);
  return (
    <div className={styles.degraded} role="status">
      <p className={styles.degradedMessage}>{body.message}</p>
      {body.retry_after_s ? (
        <p className={styles.wait}>{left > 0 ? `Try again in ${left}s.` : "Try again."}</p>
      ) : null}
      <p className={styles.degradedActions}>
        <button
          type="button"
          onClick={onRetry}
          disabled={!q.trim() || left > 0}
          className={styles.ghost}
        >
          Try again
        </button>{" "}
        <Link
          className={styles.ghost}
          href={q.trim() ? `/demo?ask=0&q=${encodeURIComponent(q.trim())}` : "/demo?ask=0"}
        >
          Search instead
        </Link>
      </p>
      <WorkDisclosure lines={lines} />
    </div>
  );
}
