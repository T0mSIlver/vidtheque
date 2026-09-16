import { describe, expect, it } from "vitest";
import { parseSnapshot, serializeSnapshot, type Snapshot } from "./url";

const params = (query: string) => new URLSearchParams(query);

describe("the console URL", () => {
  it("opens a bare path in ask where ask exists, in search where it does not", () => {
    expect(parseSnapshot(params(""), true)).toEqual({ mode: "ask", q: "", type: "all" });
    expect(parseSnapshot(params(""), false)).toEqual({ mode: "search", q: "", type: "all" });
  });

  it("reads a search and its pinned channel", () => {
    expect(parseSnapshot(params("q=kv+cache&type=ocr"), true)).toEqual({
      mode: "search",
      q: "kv cache",
      type: "ocr",
    });
    // An unknown channel is all, never a narrowed search.
    expect(parseSnapshot(params("q=kv&type=audio"), true).type).toBe("all");
  });

  it("reads a loaded question", () => {
    expect(parseSnapshot({ ask: "why?" }, true)).toEqual({ mode: "ask", q: "why?", type: "all" });
  });

  // A question on a deployment with no key is searched instead of dropped.
  it("turns a question into a search where there is no ask", () => {
    expect(parseSnapshot(params("ask=paged+attention"), false)).toEqual({
      mode: "search",
      q: "paged attention",
      type: "all",
    });
  });

  it("takes the first value of a repeated parameter", () => {
    expect(parseSnapshot({ q: ["one", "two"] }, true).q).toBe("one");
  });

  it("writes the default as nothing, and search explicitly where ask is the default", () => {
    expect(serializeSnapshot({ mode: "ask", q: "", type: "all" }, true)).toBe("");
    expect(serializeSnapshot({ mode: "search", q: "", type: "all" }, false)).toBe("");
    expect(serializeSnapshot({ mode: "search", q: "", type: "all" }, true)).toBe("?q=");
    expect(serializeSnapshot({ mode: "search", q: "kv cache", type: "all" }, true)).toBe(
      "?q=kv+cache",
    );
    expect(serializeSnapshot({ mode: "ask", q: "why?", type: "ocr" }, true)).toBe("?ask=why%3F");
  });

  it.each<[Snapshot, boolean]>([
    [{ mode: "ask", q: "", type: "all" }, true],
    [{ mode: "ask", q: "Is the harness or the model more important?", type: "all" }, true],
    [{ mode: "search", q: "", type: "all" }, true],
    [{ mode: "search", q: "", type: "frame" }, true],
    [{ mode: "search", q: "owl:FunctionalProperty", type: "ocr" }, true],
    [{ mode: "search", q: "", type: "all" }, false],
    [{ mode: "search", q: "a & b = c", type: "transcript" }, false],
  ])("round-trips %j (ask enabled: %s)", (snapshot, askEnabled) => {
    const query = serializeSnapshot(snapshot, askEnabled);
    expect(parseSnapshot(params(query.replace(/^\?/, "")), askEnabled)).toEqual(snapshot);
  });
});
