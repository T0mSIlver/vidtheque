// The console's two requests, both same-origin to Python: `GET /api/search`
// and the streamed `POST /api/ask` (demo-site.md §2.1, §3.5).
import { RETRY_FALLBACK, SEARCH_PAGE, type SearchOutcome } from "@/lib/api/outcome";
import {
  AskEvent,
  AskFailure,
  PartialErrorEnvelope,
  SearchResponse,
  type AskAnswer,
  type ContentType,
} from "@/lib/api/schemas";
import { framingOf, readJsonEvents } from "@/lib/sse";

export async function fetchSearch(
  params: { q: string; type: ContentType; offset: number; tags?: string },
  signal: AbortSignal,
): Promise<SearchOutcome> {
  const query = new URLSearchParams({
    q: params.q,
    content_type: params.type,
    limit: String(SEARCH_PAGE),
    offset: String(params.offset),
  });
  if (params.tags) query.set("tags", params.tags);
  let response: Response;
  try {
    response = await fetch(`/api/search?${query}`, { signal });
  } catch (err) {
    if (signal.aborted) throw err;
    return { kind: "unreachable" };
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = PartialErrorEnvelope.safeParse(body);
    const data = envelope.success ? envelope.data : {};
    if (response.status === 429) {
      const header = Number(response.headers.get("retry-after")) || null;
      return { kind: "rate_limited", retryAfter: data.retry_after_s ?? header ?? RETRY_FALLBACK };
    }
    return {
      kind: "refused",
      message: data.message || "Search failed.",
      next: data.next || undefined,
    };
  }
  const parsed = SearchResponse.safeParse(body);
  return parsed.success
    ? { kind: "ok", page: parsed.data }
    : { kind: "refused", message: "The corpus answered in a shape this page cannot read." };
}

export type Line = { id: number; text: string; result?: string };

export type AskPhase =
  | { kind: "idle" }
  | { kind: "working"; lines: Line[] }
  | { kind: "answered"; lines: Line[]; answer: AskAnswer }
  | { kind: "degraded"; lines: Line[]; body: AskFailure };

// SSE first: Cloudflare buffers every proxied response except
// `text/event-stream`. Plain JSON is the same answer with nothing to watch.
const CAN_STREAM = typeof ReadableStream === "function" && typeof TextDecoder === "function";
const STREAM_ACCEPT = "text/event-stream, application/x-ndjson;q=0.9, application/json;q=0.8";
const KNOWN_EVENTS = new Set(["activity", "answer", "error"]);

const INTERRUPTED: AskFailure = {
  error: "interrupted",
  reason: "no_terminal_event",
  message: "Answer interrupted.",
  retry_after_s: null,
};

const MALFORMED: AskFailure = {
  error: "malformed_stream",
  reason: "bad_event",
  message: "The answer arrived in a shape this page does not understand.",
  retry_after_s: null,
};

/** Streams one ask, reporting every phase. Resolves once a terminal phase is
 *  reported; stays silent after an abort. */
export async function streamAsk(
  params: { q: string; tags?: string },
  signal: AbortSignal,
  report: (phase: AskPhase) => void,
): Promise<void> {
  let lines: Line[] = [];
  report({ kind: "working", lines });
  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: CAN_STREAM ? STREAM_ACCEPT : "application/json",
      },
      body: JSON.stringify({ q: params.q, ...(params.tags ? { tags: params.tags } : {}) }),
      signal,
    });
    // A refusal before the model is reached is a status code with a JSON body.
    if (!res.ok) {
      report({ kind: "degraded", lines, body: await failureBody(res) });
      return;
    }
    const framing = framingOf(res.headers.get("content-type"));
    if (!framing || !res.body) {
      const answer = answerOf(await res.json().catch(() => null));
      report(
        answer ? { kind: "answered", lines, answer } : { kind: "degraded", lines, body: MALFORMED },
      );
      return;
    }
    // Only a terminal event finishes the stream; it also ends the read, so a
    // trailing activity frame cannot reopen a settled pane.
    for await (const raw of readJsonEvents(res.body, framing)) {
      if (signal.aborted) return;
      const parsed = AskEvent.safeParse(raw);
      if (!parsed.success) {
        if (isFutureEvent(raw)) continue;
        report({ kind: "degraded", lines, body: MALFORMED });
        return;
      }
      const ev = parsed.data;
      if (ev.event === "answer") {
        report({ kind: "answered", lines, answer: ev.payload });
        return;
      }
      if (ev.event === "error") {
        report({ kind: "degraded", lines, body: ev.payload });
        return;
      }
      lines =
        ev.phase === "start"
          ? [...lines, { id: ev.id, text: ev.text ?? "" }]
          : lines.map((l) => (l.id === ev.id ? { ...l, result: ev.result ?? "" } : l));
      report({ kind: "working", lines });
    }
    if (!signal.aborted) report({ kind: "degraded", lines, body: INTERRUPTED });
  } catch (err) {
    if (signal.aborted) return;
    report({ kind: "degraded", lines, body: unreachable(0, err) });
  }
}

function unreachable(status: number, err?: unknown): AskFailure {
  return {
    error: "unreachable",
    reason: status ? `http_${status}` : err instanceof Error ? err.name : "network",
    message: "Could not reach the server.",
    retry_after_s: null,
  };
}

// The body's delay before the header's: anything on the way can rewrite a
// header. A 429 with neither waits the limiter's minute.
async function failureBody(res: Response): Promise<AskFailure> {
  const header = Number(res.headers.get("retry-after")) || null;
  const floor = res.status === 429 ? RETRY_FALLBACK : null;
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

function answerOf(raw: unknown): AskAnswer | null {
  const parsed = AskEvent.safeParse({ event: "answer", payload: raw });
  return parsed.success && parsed.data.event === "answer" ? parsed.data.payload : null;
}

/** A kind this build has never heard of is skipped; a known one arriving
 *  malformed is not. */
function isFutureEvent(raw: unknown): boolean {
  if (typeof raw !== "object" || raw === null) return false;
  const kind = (raw as { event?: unknown }).event;
  return typeof kind === "string" && !KNOWN_EVENTS.has(kind);
}
