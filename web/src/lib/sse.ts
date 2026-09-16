// Server-Sent Events as a wire format, parsed by hand. `EventSource` is
// GET-only and the ask is a POST with a body, so the browser side is `fetch`
// plus a reader either way; this is the reader. Pure: chunks in, `data`
// payloads out, no framework and no DOM, so the fixture test drives it with
// the exact bytes the API sent.

// A blank line ends a frame, in whichever newline the sender uses. The buffer
// stays raw and the separator is matched across the whole of it: normalising
// each chunk on its own turned a CRLF that straddled a chunk boundary into
// two lone newlines, and the frame it ended went unnoticed.
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

// A frame is lines; `data:` lines join with "\n", `:` comments and `event:`
// names are ignored (the payload carries `event` itself, so the two cannot
// disagree). A frame with no data line, like the opening `: ok`, is skipped.
function frameData(frame: string): string | null {
  const data: string[] = [];
  for (const line of frame.split(LINE_END)) {
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  return data.length ? data.join("\n") : null;
}

// NDJSON is the other framing over the same POST (demo-site.md §3.5), and it
// came first: `json.dumps` escapes every newline in a payload, so an event is
// always exactly one line and a corpus title with a line break in it cannot
// split a frame. SSE is asked for first because it is the one that survives a
// CDN — Cloudflare buffers every proxied response except `text/event-stream` —
// but a deployment behind a proxy that does not care answers this, and a page
// that could not read it would show a stream that never started.
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
 * Read a streaming Response body as parsed JSON events, one at a time.
 *
 * **A payload that does not parse is not an event, and not an exception.** It
 * is the shape a truncated stream ends in — the connection dropped mid-frame,
 * and what came out of the buffer is half of a `{`. Thrown, it left the reader
 * with a network error and the pane saying "Could not reach the server.", which
 * is a different claim from the true one: the stream stopped without ever
 * saying it was finished.
 *
 * **It is not skipped either: the read ends there.** The vocabulary is three
 * events and a stream that has started is committed to a terminal one
 * (demo-site.md §3.5), so a payload that is not JSON is not one frame lost —
 * it is the stream having stopped making sense, and a frame after it in the
 * same chunk is not evidence of anything. Ending is what puts the caller on
 * the honest end for a stream that stopped: no terminal event, no partial
 * answer shown (`app.js`'s `parseFrame`, which returned `null` for exactly
 * this).
 */
export async function* readJsonEvents(
  body: ReadableStream<Uint8Array>,
  framing: Framing = "sse",
): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = framing === "ndjson" ? ndjsonParser() : sseParser();
  // Set by the frame that did not parse, read by the loop below: a generator
  // can only end itself, and what has to end is this one.
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
    // The decoder may be holding the first bytes of a character split across
    // the last two chunks; flushing it is what completes them.
    yield* parsed(parser.push(decoder.decode()));
    if (broken) return;
    yield* parsed(parser.flush());
  } finally {
    // A consumer that stops early — because the answer arrived, or because
    // the events stopped making sense — leaves the body open otherwise, and
    // the connection with it.
    try {
      await reader.cancel();
    } catch {
      // Already errored or closed: there is nothing left to cancel.
    }
    reader.releaseLock();
  }
}
