import { describe, expect, it } from "vitest";
import type { SearchOutcome } from "@/lib/api/outcome";
import type { SearchResponse } from "@/lib/api/schemas";
import { holds, initialState, reducer, type ConsoleState } from "./state";

function page(n: number, over: Partial<SearchResponse> = {}): SearchResponse {
  return {
    query: "kv",
    content_type: "all",
    results: Array.from({ length: n }, (_, i) => ({
      source: "transcript",
      video_id: "a",
      title: "Talk A",
      channel: "AI Engineer",
      start: i,
      end: null,
      match_start: i,
      match_cue_id: i,
      text: "kv",
      link: `https://youtu.be/a?t=${i}`,
      cue_ids: [i],
      frame_id: null,
      score: 0.1,
      timestamp: "0:00",
      thumb: null,
      thumb_large: null,
    })),
    pagination: { limit: 10, offset: 0, has_more: true },
    notes: [],
    data_status: null,
    dropped: 0,
    ...over,
  };
}

const ok = (n: number): SearchOutcome => ({ kind: "ok", page: page(n) });

function searched(): ConsoleState {
  return initialState({ mode: "search", q: "kv", type: "all" }, ok(2));
}

describe("the console reducer", () => {
  it("starts from the server's page one for a search deep link", () => {
    const state = searched();
    expect(state.search.outcome).toEqual(ok(2));
    expect(state.draft).toBe("kv");
    expect(holds(state, "kv", "all")).toBe(true);
    expect(holds(state, "kv", "ocr")).toBe(false);
  });

  it("loads a question without a phase", () => {
    const state = initialState({ mode: "ask", q: "why?", type: "all" }, null);
    expect(state.ask).toEqual({ id: 0, question: "why?", phase: { kind: "idle" } });
    expect(state.search.outcome).toBeNull();
  });

  it("keeps the previous results while a search is out, and replaces them on success", () => {
    let state = reducer(searched(), { type: "search", id: 1 });
    expect(state.pending).toBe(true);
    expect(state.search.outcome).toEqual(ok(2));

    state = reducer(state, { type: "searched", id: 1, q: "cache", channel: "ocr", outcome: ok(5) });
    expect(state.pending).toBe(false);
    expect(state.search).toMatchObject({ id: 1, q: "cache", type: "ocr", outcome: ok(5) });
  });

  it("drops a reply that is no longer the newest request", () => {
    let state = reducer(searched(), { type: "search", id: 1 });
    state = reducer(state, { type: "search", id: 2 });
    const stale = reducer(state, {
      type: "searched",
      id: 1,
      q: "old",
      channel: "all",
      outcome: ok(9),
    });
    expect(stale).toBe(state);
  });

  it("appends a later page, and keeps the rows when it fails", () => {
    let state = reducer(searched(), { type: "more", id: 1 });
    expect(state.search.foot).toEqual({ kind: "loading" });
    state = reducer(state, { type: "moreDone", id: 1, outcome: ok(3) });
    expect(state.search.more).toHaveLength(1);
    expect(state.search.foot).toEqual({ kind: "none" });

    state = reducer(state, { type: "more", id: 2 });
    state = reducer(state, {
      type: "moreDone",
      id: 2,
      outcome: { kind: "rate_limited", retryAfter: 7 },
    });
    expect(state.search.more).toHaveLength(1);
    expect(state.search.foot).toEqual({ kind: "rate_limited", seconds: 7 });
  });

  // Switching mode is itself a cancellation, of the request and its half-drawn
  // state (demo-site.md §6.1).
  it("cancels whatever was in flight when the mode changes", () => {
    let state = reducer(searched(), { type: "search", id: 1 });
    state = reducer(state, { type: "mode", mode: "ask" });
    expect(state).toMatchObject({ mode: "ask", pending: false, active: 0 });
    expect(
      reducer(state, { type: "searched", id: 1, q: "x", channel: "all", outcome: ok(1) }),
    ).toBe(state);

    state = reducer(state, { type: "ask", id: 2, question: "kv" });
    state = reducer(state, { type: "mode", mode: "search" });
    expect(state.ask.phase).toEqual({ kind: "idle" });
  });

  it("runs an ask through its phases and settles on the terminal one", () => {
    let state = reducer(searched(), { type: "ask", id: 3, question: "why?" });
    expect(state.ask.phase).toEqual({ kind: "working", lines: [] });
    state = reducer(state, {
      type: "askPhase",
      id: 3,
      phase: { kind: "working", lines: [{ id: 1, text: "Searching…" }] },
    });
    expect(state.active).toBe(3);
    state = reducer(state, {
      type: "askPhase",
      id: 3,
      phase: {
        kind: "answered",
        lines: [],
        answer: { answer: "Yes.", citations: [], model: null },
      },
    });
    expect(state.active).toBe(0);
    expect(state.ask.phase.kind).toBe("answered");
  });

  it("keeps an answer across a round trip to search for the same question", () => {
    let state = initialState({ mode: "ask", q: "why?", type: "all" }, null);
    state = reducer(state, { type: "ask", id: 1, question: "why?" });
    state = reducer(state, {
      type: "askPhase",
      id: 1,
      phase: {
        kind: "answered",
        lines: [],
        answer: { answer: "Yes.", citations: [], model: null },
      },
    });
    state = reducer(reducer(state, { type: "mode", mode: "search" }), {
      type: "mode",
      mode: "ask",
    });
    expect(state.ask.phase.kind).toBe("answered");

    state = reducer(state, { type: "draft", value: "something else" });
    state = reducer(reducer(state, { type: "mode", mode: "search" }), {
      type: "mode",
      mode: "ask",
    });
    expect(state.ask.phase.kind).toBe("idle");
  });

  it("restores a history snapshot into the form", () => {
    let state = reducer(searched(), { type: "draft", value: "half typed" });
    state = reducer(state, { type: "restore", snapshot: { mode: "search", q: "", type: "frame" } });
    expect(state).toMatchObject({ mode: "search", draft: "", type: "frame" });
    expect(state.search.outcome).toBeNull();

    state = reducer(state, { type: "restore", snapshot: { mode: "ask", q: "why?", type: "all" } });
    expect(state).toMatchObject({ mode: "ask", draft: "why?", type: "frame" });
    expect(state.ask).toMatchObject({ question: "why?", phase: { kind: "idle" } });
  });
});
