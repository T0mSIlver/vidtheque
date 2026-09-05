import { describe, expect, it } from "vitest";
import { evidenceOf, highlight, insideLink, legsOf, receiptOf } from "./parts";

// The four derivations, against the cases `views._search_receipt`,
// `views._search_inside`, `views._search_evidence` and `views._highlighted`
// were written for. Two surfaces disagreeing about which second a hit happened
// at, or about whether a link is a receipt, is worse than either being wrong.

describe("the receipt", () => {
  it("admits an HTTPS youtu.be link with a numeric second", () => {
    expect(receiptOf("https://youtu.be/kCc8FmEb1nY?t=705")).toEqual({
      href: "https://youtu.be/kCc8FmEb1nY?t=705",
      label: "youtu.be/kCc8FmEb1nY?t=705",
    });
    // `t=0` is a second, and `0` is falsy in every language this page is
    // written in — which is exactly the bug the rule is spelled out to avoid.
    expect(receiptOf("https://youtu.be/kCc8FmEb1nY?t=0")?.label).toBe("youtu.be/kCc8FmEb1nY?t=0");
  });

  // Everything else is not printed at all rather than printed unchecked: the
  // page never invents a link for a source that has none.
  it("refuses anything that is not that", () => {
    for (const link of [
      "http://youtu.be/kCc8FmEb1nY?t=705", // not HTTPS
      "https://youtube.com/watch?v=kCc8FmEb1nY&t=705", // not the receipt host
      "https://youtu.be/?t=705", // no video id
      "https://youtu.be/kCc8FmEb1nY", // no second
      "https://youtu.be/kCc8FmEb1nY?t=12.5", // not a whole second
      "https://youtu.be/kCc8FmEb1nY?t=", // an empty one
      "javascript:alert(1)",
      "not a url at all",
    ]) {
      expect(receiptOf(link), link).toBeNull();
    }
  });
});

describe("the link into the index", () => {
  // `ord` is dense per video and the strip pages by ordinal, so the page that
  // holds a frame is arithmetic. 24 to a page: frame 0 is on the first, frame
  // 25 is on the second, and 48 opens the third.
  it("lands a frame hit on its own strip page, with the frame marked", () => {
    expect(insideLink({ video_id: "vid", frame_id: "vid-00000" })).toBe(
      "/dashboard/videos/vid?frame_offset=0&select=0#frame-0",
    );
    expect(insideLink({ video_id: "vid", frame_id: "vid-00025" })).toBe(
      "/dashboard/videos/vid?frame_offset=24&select=25#frame-25",
    );
    expect(insideLink({ video_id: "vid", frame_id: "vid-00048" })).toBe(
      "/dashboard/videos/vid?frame_offset=48&select=48#frame-48",
    );
  });

  // A transcript hit names its cues by id and the transcript panel pages by
  // offset; there is no honest arithmetic between them, so the link is plain.
  it("links a hit with no keyframe to the video and nothing more", () => {
    expect(insideLink({ video_id: "vid", frame_id: null })).toBe("/dashboard/videos/vid");
    // A frame id that is not this video's, or whose tail is not an ordinal, is
    // a shape this page does not understand — and it says so by not pretending
    // to know which page holds it.
    expect(insideLink({ video_id: "vid", frame_id: "other-00003" })).toBe("/dashboard/videos/vid");
    expect(insideLink({ video_id: "vid", frame_id: "vid-abc" })).toBe("/dashboard/videos/vid");
  });

  it("encodes the id, because a reader can have typed it", () => {
    expect(insideLink({ video_id: "a/b?c", frame_id: null })).toBe("/dashboard/videos/a%2Fb%3Fc");
    expect(insideLink({ video_id: "", frame_id: null })).toBeNull();
  });
});

describe("the evidence badges", () => {
  it("says the three sources in the demo's own words, and keeps the key", () => {
    expect(evidenceOf("transcript")).toEqual({
      key: "transcript",
      pills: [{ label: "spoken", kind: "spoken" }],
      kind: "spoken",
    });
    expect(evidenceOf("ocr").kind).toBe("screen");
    expect(evidenceOf("frame").kind).toBe("frame");
  });

  it("draws both words for a fused source, and sets the snippet as neither", () => {
    const fused = evidenceOf("transcript+ocr");
    expect(fused.pills.map((pill) => pill.label)).toEqual(["spoken", "on-screen"]);
    expect(fused.kind).toBe("mixed");
  });

  // A fourth leg one day must arrive as an unfamiliar word, never as a hit with
  // no provenance on it at all.
  it("gives an unknown source a badge carrying its own name", () => {
    expect(evidenceOf("caption_track")).toEqual({
      key: "caption_track",
      pills: [{ label: "caption_track", kind: "other" }],
      kind: "other",
    });
    expect(evidenceOf("").pills).toEqual([]);
  });
});

describe("the marked words", () => {
  it("marks the query's own words, and leaves the rest as text", () => {
    expect(highlight("we cache the keys", "cache")).toEqual([
      { text: "we ", hit: false },
      { text: "cache", hit: true },
      { text: " the keys", hit: false },
    ]);
  });

  it("matches whatever the case, and marks every occurrence", () => {
    const runs = highlight("Cache and cache and CACHE", "cache");
    expect(runs.filter((run) => run.hit).map((run) => run.text)).toEqual([
      "Cache",
      "cache",
      "CACHE",
    ]);
  });

  // Longest first, so a short term cannot shadow the long one containing it.
  it("prefers the longer term where two overlap", () => {
    const runs = highlight("the kv cache", "cache kv ca");
    expect(runs.filter((run) => run.hit).map((run) => run.text)).toEqual(["kv", "cache"]);
  });

  // A word of one character is punctuation as far as a snippet is concerned,
  // and a query of nothing but those marks nothing.
  it("ignores one-character terms and marks nothing when there are none", () => {
    expect(highlight("a cache of a", "a")).toEqual([{ text: "a cache of a", hit: false }]);
    expect(highlight("a cache", "?! -")).toEqual([{ text: "a cache", hit: false }]);
  });

  // A term never carries a regex metacharacter, because the split that makes
  // one keeps only word characters, an apostrophe and a hyphen — a query of
  // `(b)` is the single letter `b`, which is too short to mark. The escaping in
  // the pattern is belt to that braces.
  it("takes a query's punctuation out of the terms rather than into the pattern", () => {
    expect(highlight("a (b) c", "(b)")).toEqual([{ text: "a (b) c", hit: false }]);
    expect(highlight("re-entrant it's", "re-entrant it's")).toEqual([
      { text: "re-entrant", hit: true },
      { text: " ", hit: false },
      { text: "it's", hit: true },
    ]);
  });

  // A slide that says `<script>` is a normal slide: this returns text runs, and
  // the component decides what a marked run looks like.
  it("marks a word inside markup without ever building any", () => {
    expect(highlight("<script>alert(1)</script>", "script")).toEqual([
      { text: "<", hit: false },
      { text: "script", hit: true },
      { text: ">alert(1)</", hit: false },
      { text: "script", hit: true },
      { text: ">", hit: false },
    ]);
  });

  it("takes at most eight terms and marks at most forty times", () => {
    const query = "aa bb cc dd ee ff gg hh ii jj";
    const text = "aa bb cc dd ee ff gg hh ii jj";
    // The ninth and tenth terms are not in the pattern at all, so their words
    // stay unmarked in the text.
    expect(highlight(text, query).filter((run) => run.hit)).toHaveLength(8);

    const many = highlight("x ".repeat(60).replaceAll("x", "cache"), "cache");
    expect(many.filter((run) => run.hit)).toHaveLength(40);
    // What was not marked is still there: the cap stops the marking, not the
    // text.
    expect(many.map((run) => run.text).join("")).toHaveLength("cache ".length * 60);
  });

  // A frame hit that matched on imagery has no text at all, and the page prints
  // its own sentence rather than styling a stand-in as a quotation.
  it("has nothing to mark in a hit with no text", () => {
    expect(highlight(null, "cache")).toEqual([]);
    expect(highlight("", "cache")).toEqual([]);
  });
});

describe("the leg counts", () => {
  it("reads in the table's order, with the sub-legs under what they explain", () => {
    const legs = legsOf({
      frame_knn: 12,
      transcript_fts: 4,
      transcript: 3,
      ocr: 2,
    });
    expect(legs.map((leg) => leg.key)).toEqual([
      "transcript",
      "transcript_fts",
      "ocr",
      "frame_knn",
    ]);
    expect(legs.map((leg) => leg.sub)).toEqual([false, true, false, true]);
    expect(legs[1].label).toBe("Transcript — keyword match (FTS)");
    expect(legs[1].unit).toBe("cues");
  });

  // A zero is a reading, not an absence: `fts 0` is how you learn the corpus
  // does not contain your phrasing.
  it("draws a leg that counted nothing", () => {
    expect(legsOf({ frame: 0 })).toEqual([
      { key: "frame", label: "Frames — visual match", unit: "", count: 0, sub: false },
    ]);
  });

  it("gives a key it does not know its own name, rather than dropping it", () => {
    const legs = legsOf({ transcript: 1, caption_knn: 9 });
    expect(legs[1]).toEqual({
      key: "caption_knn",
      label: "caption_knn",
      unit: "",
      count: 9,
      sub: true,
    });
  });

  it("draws nothing at all for a payload with no leg counts", () => {
    expect(legsOf(undefined)).toEqual([]);
    expect(legsOf({})).toEqual([]);
  });
});
