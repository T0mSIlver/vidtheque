import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { AskEvent } from "@/lib/api/schemas";
import { framingOf, ndjsonParser, readJsonEvents, sseParser } from "./sse";

// The bytes a real `POST /api/ask` sent, recorded 2026-09-01 against the
// sandbox corpus: an opening comment, five activity pairs, one answer.
const FIXTURE = readFileSync(new URL("./__fixtures__/ask.sse", import.meta.url), "utf8");

function chunked(text: string, size: number): string[] {
  const out: string[] = [];
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size));
  return out;
}

describe("sseParser", () => {
  it("yields one data payload per frame, whatever the chunking", () => {
    for (const size of [1, 7, 64, 1000, FIXTURE.length]) {
      const parser = sseParser();
      const frames = chunked(FIXTURE, size).flatMap((c) => parser.push(c));
      frames.push(...parser.flush());
      expect(frames, `chunk size ${size}`).toHaveLength(11);
    }
  });

  it("skips comments and joins multi-line data", () => {
    const parser = sseParser();
    const frames = parser.push(": ok\n\ndata: a\ndata: b\n\nevent: x\ndata: {}\n\n");
    expect(frames).toEqual(["a\nb", "{}"]);
  });

  it("tolerates CRLF framing", () => {
    const parser = sseParser();
    expect(parser.push("data: 1\r\n\r\ndata: 2\r\n\r\n")).toEqual(["1", "2"]);
  });

  // The bug this pins: each chunk used to be CRLF-normalised on its own, so a
  // "\r\n" the network split in half became two lone newlines and the blank
  // line between frames stopped being blank. Splitting the recorded stream at
  // every single position is the cheapest way to say "any split".
  it("frames a CRLF stream at every possible chunk boundary", () => {
    const wire = FIXTURE.replace(/\n/g, "\r\n");
    for (let cut = 0; cut <= wire.length; cut++) {
      const parser = sseParser();
      const frames = [...parser.push(wire.slice(0, cut)), ...parser.push(wire.slice(cut))];
      frames.push(...parser.flush());
      expect(frames, `split at ${cut}`).toHaveLength(11);
    }
  });

  it("frames a CRLF stream one character at a time", () => {
    const wire = ': ok\r\n\r\ndata: {"a":1}\r\n\r\ndata: two\r\n\r\n';
    const parser = sseParser();
    const frames = [...wire].flatMap((c) => parser.push(c));
    frames.push(...parser.flush());
    expect(frames).toEqual(['{"a":1}', "two"]);
  });

  it("keeps a trailing CR buffered until it knows what follows", () => {
    const parser = sseParser();
    expect(parser.push("data: 1\r\n\r")).toEqual([]);
    expect(parser.push("\ndata: 2\r\n\r\n")).toEqual(["1", "2"]);
  });
});

describe("readJsonEvents", () => {
  it("parses the recorded stream into typed events, answer last", async () => {
    const body = new Blob([FIXTURE]).stream();
    const events: AskEvent[] = [];
    for await (const raw of readJsonEvents(body)) events.push(AskEvent.parse(raw));
    expect(events).toHaveLength(11);
    expect(events.slice(0, 10).every((e) => e.event === "activity")).toBe(true);
    const last = events.at(-1);
    expect(last?.event).toBe("answer");
    if (last?.event === "answer") {
      expect(last.payload.citations.length).toBeGreaterThan(0);
      expect(last.payload.answer).toMatch(/KV cache/);
    }
  });

  it("decodes a character the chunk boundary cut in half", async () => {
    const payload = { event: "note", text: "café — déjà vu" };
    const bytes = new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`);
    // Between the two bytes of "é": a decoder without streaming state would
    // put a replacement character here.
    const cut = bytes.indexOf(0xc3) + 1;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, cut));
        c.enqueue(bytes.slice(cut));
        c.close();
      },
    });
    const events: unknown[] = [];
    for await (const raw of readJsonEvents(body)) events.push(raw);
    expect(events).toEqual([payload]);
  });

  // The stream stopped mid-frame: the events before the cut are real and are
  // handed over, and the half-frame is not an event and not an error either.
  // Thrown, it reached the caller as a network failure, and the pane blamed a
  // server that had answered — "Answer interrupted." is the true sentence.
  it("ends a truncated stream rather than throwing out of it", async () => {
    const body = new Blob(['data: {"event":"activity","id":1}\n\ndata: {"event":"ans']).stream();
    const events: unknown[] = [];
    for await (const raw of readJsonEvents(body)) events.push(raw);
    expect(events).toEqual([{ event: "activity", id: 1 }]);
  });

  // A frame that is not JSON mid-stream is not one row lost: the framing has
  // stopped making sense, and what follows it in the same read is not evidence
  // of anything. The read ends where it stopped, which is the end a truncated
  // stream reaches — the caller gets no terminal event and says the answer was
  // interrupted, rather than printing an answer that arrived after a hole.
  it("ends at a frame it cannot parse, in that read and in the one after it", async () => {
    const bytes = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        // The good frame after the bad one is in the same read…
        c.enqueue(
          bytes.encode(
            'data: {"event":"activity","id":1}\n\ndata: {oh no\n\n' +
              'data: {"event":"answer","payload":{"answer":"kv caching"}}\n\n',
          ),
        );
        // …and this one is in the read after it, which the loop used to go on to.
        c.enqueue(bytes.encode('data: {"event":"answer","payload":{"answer":"kv caching"}}\n\n'));
        c.close();
      },
    });
    const events: unknown[] = [];
    for await (const raw of readJsonEvents(body)) events.push(raw);
    expect(events).toEqual([{ event: "activity", id: 1 }]);
  });

  it("cancels the body when the consumer stops early", async () => {
    let cancelled = false;
    // Never closed: only the cancel releases it, which is what a component
    // unmounting mid-answer depends on.
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode("data: 1\n\ndata: 2\n\n"));
      },
      cancel() {
        cancelled = true;
      },
    });
    for await (const raw of readJsonEvents(body)) {
      expect(raw).toBe(1);
      break;
    }
    expect(cancelled).toBe(true);
    expect(body.locked).toBe(false);
  });
});

// The other framing over the same POST (demo-site.md §3.5). It came first and
// is the better parse: `json.dumps` escapes every newline in a payload, so an
// event is always exactly one line and a corpus title with a line break in it
// cannot split a frame.
describe("ndjsonParser", () => {
  it("gives one event per line, in any split", () => {
    const wire = '{"event":"activity","id":1}\n{"event":"answer"}\n';
    for (const size of [1, 5, 1000]) {
      const parser = ndjsonParser();
      const out: string[] = [];
      for (const chunk of chunked(wire, size)) out.push(...parser.push(chunk));
      out.push(...parser.flush());
      expect(out).toEqual(['{"event":"activity","id":1}', '{"event":"answer"}']);
    }
  });

  it("hands back a last line the sender never terminated", () => {
    const parser = ndjsonParser();
    expect(parser.push('{"a":1}')).toEqual([]);
    expect(parser.flush()).toEqual(['{"a":1}']);
  });

  it("skips the blank lines a writer may pad with", () => {
    const parser = ndjsonParser();
    expect(parser.push('\n\n{"a":1}\n\n')).toEqual(['{"a":1}']);
  });

  it("reads a whole NDJSON body through the same reader", async () => {
    const wire = '{"n":1}\n{"n":2}\n';
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(wire));
        c.close();
      },
    });
    const events: unknown[] = [];
    for await (const raw of readJsonEvents(body, "ndjson")) events.push(raw);
    expect(events).toEqual([{ n: 1 }, { n: 2 }]);
  });
});

describe("framingOf", () => {
  it("names the framing the server chose, and nothing when it chose neither", () => {
    expect(framingOf("text/event-stream; charset=utf-8")).toBe("sse");
    expect(framingOf("application/x-ndjson")).toBe("ndjson");
    // The §3 body, byte for byte: an answer in one piece, not a stream.
    expect(framingOf("application/json")).toBeNull();
    expect(framingOf(null)).toBeNull();
  });
});
