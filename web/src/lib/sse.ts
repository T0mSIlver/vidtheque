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

/** Read a streaming Response body as parsed JSON events, one at a time. */
export async function* readJsonEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = sseParser();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const data of parser.push(decoder.decode(value, { stream: true }))) {
        yield JSON.parse(data);
      }
    }
    // The decoder may be holding the first bytes of a character split across
    // the last two chunks; flushing it is what completes them.
    for (const data of parser.push(decoder.decode())) yield JSON.parse(data);
    for (const data of parser.flush()) yield JSON.parse(data);
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
