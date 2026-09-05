"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { AskEvent, AskFailure, type AskAnswer, type Citation } from "@/lib/api/schemas";
import { receipt } from "@/lib/format";
import { readJsonEvents } from "@/lib/sse";
import { Frame } from "./Frame";
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
  message: "The answer stopped before it finished.",
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

export function AskMode({ initialQ }: { initialQ: string }) {
  const [q, setQ] = useState(initialQ);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
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
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ q: question }),
        signal: controller.signal,
      });
      if (!res.body || !res.headers.get("content-type")?.startsWith("text/event-stream")) {
        // A 429, a 503 before the stream opened, or a proxy error: one body.
        // Whatever the sender, it said something — that sentence is the one
        // the visitor gets, not a stand-in about being unreachable.
        show({ kind: "degraded", lines, body: await failureBody(res) });
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
      for await (const raw of readJsonEvents(res.body)) {
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

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask(q.trim());
  }

  const busy = phase.kind === "working";
  return (
    <div className={styles.ask}>
      <form onSubmit={onSubmit} className={styles.form} aria-busy={busy}>
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="ask the talks a question…"
          aria-label="Your question"
          maxLength={400}
          className={styles.input}
        />
        <button type="submit" className={styles.go} disabled={busy}>
          {busy ? "reading the corpus…" : "ask"}
        </button>
      </form>
      <p className={styles.switch}>
        <Link href={q.trim() ? `/demo?q=${encodeURIComponent(q.trim())}` : "/demo"}>
          search instead →
        </Link>
      </p>

      {phase.kind !== "idle" ? <WorkLog lines={phase.lines} live={busy} /> : null}
      {phase.kind === "answered" ? <Answer answer={phase.answer} /> : null}
      {phase.kind === "degraded" ? (
        <Degraded body={phase.body} q={q} onRetry={() => void ask(q.trim())} />
      ) : null}
    </div>
  );
}

function unreachable(status: number, err?: unknown): AskFailure {
  return {
    error: "unreachable",
    reason: status ? `http_${status}` : err instanceof Error ? err.name : "network",
    message: "The corpus could not be reached — use search.",
    retry_after_s: null,
  };
}

// The body of a reply that never became a stream. The limiter's envelope has
// no `reason`; §3.4's degraded body does. Both carry the sentence to show and,
// between the body and the header, when to come back.
async function failureBody(res: Response): Promise<AskFailure> {
  const header = Number(res.headers.get("retry-after")) || null;
  const parsed = AskFailure.safeParse(await res.json().catch(() => null));
  if (!parsed.success) return { ...unreachable(res.status), retry_after_s: header };
  return { ...parsed.data, retry_after_s: parsed.data.retry_after_s ?? header };
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
function WorkLog({ lines, live }: { lines: Line[]; live: boolean }) {
  return (
    <ol className={styles.log} aria-live="polite" aria-label="What the model is doing">
      {lines.map((l) => (
        <li key={l.id} className={l.result === undefined ? styles.running : undefined}>
          <span className={styles.logText}>{l.text}</span>
          {l.result !== undefined ? <span className={styles.logResult}>{l.result}</span> : null}
        </li>
      ))}
      {live && lines.length === 0 ? (
        <li className={styles.running}>Reading the question…</li>
      ) : null}
    </ol>
  );
}

function Answer({ answer }: { answer: AskAnswer }) {
  return (
    <section className={styles.answer} aria-label="Answer">
      {answer.answer.split(/\n{2,}/).map((para, i) => (
        <p key={i} className={styles.para}>
          {para}
        </p>
      ))}
      {answer.citations.length > 0 ? (
        <>
          <h2 className={styles.label}>Sources</h2>
          <ol className={styles.sources}>
            {answer.citations.map((c) => (
              <Source key={c.n} c={c} />
            ))}
          </ol>
        </>
      ) : null}
      {answer.model ? <p className={styles.foot}>model · {answer.model}</p> : null}
    </section>
  );
}

function Source({ c }: { c: Citation }) {
  return (
    <li className={styles.source}>
      <span className={styles.n}>[{c.n}]</span>
      <Frame src={c.thumb} alt="" />
      <div className={styles.sourceText}>
        <Link href={`/videos/${c.video_id}`} className={styles.sourceTitle}>
          {c.title}
        </Link>
        <span className={styles.sourceMeta}>
          {c.channel} · <span className={styles.mono}>{c.timestamp}</span>
        </span>
        {c.text ? <span className={styles.snippet}>{c.text}</span> : null}
        {c.link ? (
          <a href={c.link} rel="noreferrer" className={styles.receipt}>
            {receipt(c.link)}
          </a>
        ) : null}
      </div>
    </li>
  );
}

// What stopped the ask, in the sender's own words, and the two ways out: ask
// again, or search. The retry wears the submit button's style because it is
// the same act, and a second look for it would be a second thing to learn.
function Degraded({ body, q, onRetry }: { body: AskFailure; q: string; onRetry: () => void }) {
  return (
    <div className={styles.degraded} role="status">
      <p>{body.message}</p>
      {body.retry_after_s ? (
        <p className={styles.mono}>try again in {body.retry_after_s}s</p>
      ) : null}
      <p>
        <button type="button" onClick={onRetry} disabled={!q.trim()} className={styles.go}>
          Try again
        </button>
      </p>
      <p>
        <Link href={q.trim() ? `/demo?q=${encodeURIComponent(q.trim())}` : "/demo"}>
          Search instead
        </Link>
      </p>
    </div>
  );
}
