// Server-Sent Events as a wire format, parsed by hand. `EventSource` is
// GET-only and the ask is a POST with a body, so the browser side is `fetch`
// plus a reader either way; this is the reader. Pure: chunks in, `data`
// payloads out, no framework and no DOM, so the fixture test drives it with
// the exact bytes the API sent.

/** Feed chunks in any split; get each complete frame's `data` back. */
export function sseParser() {
  let buffer = "";
  return {
    push(chunk: string): string[] {
      buffer += chunk.replace(/\r\n/g, "\n");
      const out: string[] = [];
      let cut: number;
      while ((cut = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const data = frameData(frame);
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
  for (const line of frame.split("\n")) {
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
    for (const data of parser.flush()) yield JSON.parse(data);
  } finally {
    reader.releaseLock();
  }
}
