"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { AskDegraded, AskEvent, type AskAnswer, type Citation } from "@/lib/api/schemas";
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
  | { kind: "degraded"; lines: Line[]; body: AskDegraded };

export function AskMode({ initialQ }: { initialQ: string }) {
  const [q, setQ] = useState(initialQ);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  // The in-flight request, so a second ask or an unmount cancels the first.
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  async function ask(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = q.trim();
    if (!question) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;

    let lines: Line[] = [];
    setPhase({ kind: "working", lines });
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ q: question }),
        signal: controller.signal,
      });
      if (!res.body || !res.headers.get("content-type")?.startsWith("text/event-stream")) {
        // A 429, a 503 before the stream opened, or a proxy error: one body.
        const body = AskDegraded.safeParse(await res.json().catch(() => null));
        setPhase({ kind: "degraded", lines, body: body.success ? body.data : unreachable(res.status) });
        return;
      }
      for await (const raw of readJsonEvents(res.body)) {
        const ev = AskEvent.parse(raw);
        if (ev.event === "activity") {
          lines =
            ev.phase === "start"
              ? [...lines, { id: ev.id, text: ev.text ?? "" }]
              : lines.map((l) => (l.id === ev.id ? { ...l, result: ev.result ?? "" } : l));
          setPhase({ kind: "working", lines });
        } else if (ev.event === "answer") {
          setPhase({ kind: "answered", lines, answer: ev.payload });
        } else {
          setPhase({ kind: "degraded", lines, body: ev.payload });
        }
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      setPhase({ kind: "degraded", lines, body: unreachable(0, err) });
    }
  }

  const busy = phase.kind === "working";
  return (
    <div className={styles.ask}>
      <form onSubmit={ask} className={styles.form} aria-busy={busy}>
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
        <Link href={q.trim() ? `/?q=${encodeURIComponent(q.trim())}` : "/"}>search instead →</Link>
      </p>

      {phase.kind !== "idle" ? <WorkLog lines={phase.lines} live={busy} /> : null}
      {phase.kind === "answered" ? <Answer answer={phase.answer} /> : null}
      {phase.kind === "degraded" ? <Degraded body={phase.body} q={q} /> : null}
    </div>
  );
}

function unreachable(status: number, err?: unknown): AskDegraded {
  return {
    error: "unreachable",
    reason: status ? `http_${status}` : err instanceof Error ? err.name : "network",
    message: "The corpus could not be reached — use search.",
    retry_after_s: null,
  };
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
      {live && lines.length === 0 ? <li className={styles.running}>Reading the question…</li> : null}
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

function Degraded({ body, q }: { body: AskDegraded; q: string }) {
  return (
    <div className={styles.degraded} role="status">
      <p>{body.message}</p>
      {body.retry_after_s ? <p className={styles.mono}>try again in {body.retry_after_s}s</p> : null}
      <p>
        <Link href={q.trim() ? `/?q=${encodeURIComponent(q.trim())}` : "/"}>Search instead</Link>
      </p>
    </div>
  );
}
