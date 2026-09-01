import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { AskEvent } from "@/lib/api/schemas";
import { readJsonEvents, sseParser } from "./sse";

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
});
