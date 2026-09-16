// The ask stream's two framings over one POST, parsed by hand: `EventSource`
// is GET-only. Pure, so the fixture test drives it with the API's own bytes.

// A blank line ends a frame, in whichever newline the sender uses. Matched on
// the raw buffer, so a CRLF split across chunks still ends its frame.
const FRAME_END = /\r\n\r\n|\n\n|\r\r/;
const LINE_END = /\r\n|\n|\r/;

/** Feed chunks in any split; get each complete frame's `data` back. */
export function sseParser() {
  let buffer = "";
  return {
    push(chunk: string): string[] {
      buffer += chunk;
      const out: string[] = [];
      for (;;) {
        const end = FRAME_END.exec(buffer);
        if (!end) break;
        const data = frameData(buffer.slice(0, end.index));
        buffer = buffer.slice(end.index + end[0].length);
        if (data !== null) out.push(data);
      }
      return out;
    },
    /** Whatever a stream left unterminated when it closed. */
    flush(): string[] {
      const rest = buffer;
      buffer = "";
      const data = rest.trim() ? frameData(rest) : null;
      return data === null ? [] : [data];
    },
  };
}

// `data:` lines join with "\n"; comments and `event:` names are ignored (the
// payload carries `event`). A frame with no data line is skipped.
function frameData(frame: string): string | null {
  const data: string[] = [];
  for (const line of frame.split(LINE_END)) {
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  return data.length ? data.join("\n") : null;
}

// NDJSON: one event per line, since `json.dumps` escapes every newline. SSE is
// preferred because it survives a CDN (demo-site.md §3.5).
export function ndjsonParser() {
  let buffer = "";
  return {
    push(chunk: string): string[] {
      buffer += chunk;
      const out: string[] = [];
      for (;;) {
        const end = LINE_END.exec(buffer);
        if (!end) break;
        const line = buffer.slice(0, end.index).trim();
        buffer = buffer.slice(end.index + end[0].length);
        if (line) out.push(line);
      }
      return out;
    },
    flush(): string[] {
      const rest = buffer.trim();
      buffer = "";
      return rest ? [rest] : [];
    },
  };
}

/** Which framing a body is in, from the `Content-Type` the server chose. */
export type Framing = "sse" | "ndjson";

export function framingOf(contentType: string | null): Framing | null {
  const type = contentType ?? "";
  if (type.includes("text/event-stream")) return "sse";
  if (type.includes("application/x-ndjson")) return "ndjson";
  return null;
}

/**
 * Read a streaming body as parsed JSON events.
 *
 * A payload that does not parse ends the read, neither thrown nor skipped: it
 * is what a truncated stream looks like, and the caller then reports a stream
 * that stopped without its terminal event (demo-site.md §3.5).
 */
export async function* readJsonEvents(
  body: ReadableStream<Uint8Array>,
  framing: Framing = "sse",
): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = framing === "ndjson" ? ndjsonParser() : sseParser();
  let broken = false;
  function* parsed(frames: string[]): Generator<unknown> {
    for (const data of frames) {
      let event: unknown;
      try {
        event = JSON.parse(data);
      } catch {
        broken = true;
        return;
      }
      yield event;
    }
  }
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      yield* parsed(parser.push(decoder.decode(value, { stream: true })));
      if (broken) return;
    }
    // Completes a character split across the last two chunks.
    yield* parsed(parser.push(decoder.decode()));
    if (broken) return;
    yield* parsed(parser.flush());
  } finally {
    // A consumer that stops early would otherwise leave the connection open.
    try {
      await reader.cancel();
    } catch {
      // Already closed.
    }
    reader.releaseLock();
  }
}
