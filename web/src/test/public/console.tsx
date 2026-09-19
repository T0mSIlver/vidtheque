// Shared fixtures for the console's tests. Under `test/` rather than beside
// the console: it imports `vitest`, which no shipped module may.
import { render } from "@testing-library/react";
import { vi } from "vitest";
import { navigateTo, resetNavigation } from "@/test/next";
import type { SearchOutcome } from "@/lib/api/outcome";
import type { Hit, SearchResponse } from "@/lib/api/schemas";
import { Console, type ConsoleProps } from "@/components/public/console/Console";
import { serializeSnapshot } from "@/components/public/console/url";

export function hit(over: Partial<Hit> = {}): Hit {
  return {
    source: "transcript",
    video_id: "a",
    title: "Talk A",
    channel: "AI Engineer",
    start: 12,
    end: null,
    match_start: 12,
    match_cue_id: 1,
    text: "we cache the keys",
    link: "https://youtu.be/a?t=10",
    cue_ids: [1],
    frame_id: null,
    score: 0.1,
    timestamp: "0:12",
    thumb: null,
    thumb_large: null,
    ...over,
  };
}

export function found(results: Hit[], over: Partial<SearchResponse> = {}): SearchResponse {
  return {
    query: "kv cache",
    content_type: "all",
    results,
    pagination: { limit: 10, offset: 0, has_more: false },
    notes: [],
    data_status: results.length ? null : "ok",
    dropped: 0,
    ...over,
  };
}

/** A response body as the facade sends it (no `dropped`; the schema adds it). */
export function wire(page: SearchResponse) {
  const { dropped, ...body } = page;
  void dropped;
  return Response.json(body);
}

export function streamResponse(text: string, contentType = "text/event-stream; charset=utf-8") {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": contentType } });
}

/** A stream a test feeds by hand. */
export function openStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: new Response(body, {
      status: 200,
      headers: { "content-type": "text/event-stream; charset=utf-8" },
    }),
    send(text: string) {
      controller.enqueue(new TextEncoder().encode(text));
    },
    close() {
      controller.close();
    },
  };
}

export function frame(event: unknown) {
  return `data: ${JSON.stringify(event)}\n\n`;
}

export function answerFrame(answer: string, over: Record<string, unknown> = {}) {
  return frame({ event: "answer", payload: { answer, citations: [], model: null, ...over } });
}

export const ACTIVITY = frame({ event: "activity", id: 1, phase: "start", text: "Searching…" });

/** A fetch that never settles: a request in flight. */
export const never = () => new Promise<Response>(() => {});

export function mountConsole(
  over: Partial<ConsoleProps> & {
    url?: string;
    initialSearch?: SearchOutcome | null;
    /** `live` lets `/api/ask/resume` reach the stubbed fetch; by default the
     *  server has no run for this visitor, so every ask starts from nothing. */
    resume?: "none" | "live";
  } = {},
) {
  const { url, resume = "none", ...props } = over;
  if (resume === "none") {
    const stubbed = globalThis.fetch;
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith("/api/ask/resume")
        ? Promise.resolve(new Response(null, { status: 204 }))
        : stubbed(input, init),
    );
  }
  const snapshot = props.initial ?? { mode: "ask", q: "", type: "all" };
  const at = url ?? (props.path ?? "/demo") + serializeSnapshot(snapshot, props.askEnabled ?? true);
  window.history.replaceState(null, "", at);
  const parsed = new URL(at, "http://x");
  resetNavigation(parsed.search, parsed.pathname);
  const push = vi.spyOn(window.history, "pushState");
  const view = render(
    <Console
      path="/demo"
      askEnabled
      boot="ok"
      initial={{ mode: "ask", q: "", type: "all" }}
      initialSearch={null}
      askExamples={["Why do agents write bad AGENTS.md?"]}
      searchExamples={[{ q: "context window costs money tokens", type: "ocr" }]}
      noMatch="Nothing in the corpus matches this."
      {...props}
    />,
  );
  return { ...view, push };
}

/** Back or Forward: the address moves, then the router's search params follow. */
export async function traverse(url: string) {
  window.history.replaceState(null, "", url);
  await navigateTo(url);
}

export const ASK = { name: /^Ask/ };
export const SEARCH_BOX = "Search this video corpus";
export const QUESTION_BOX = "Your question";
