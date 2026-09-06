import { describe, expect, it } from "vitest";
import type { Hit } from "@/lib/api";
import { badges, channelWord, groupByVideo, highlight, presentationOf } from "./group";

function hit(over: Partial<Hit>): Hit {
  return {
    source: "transcript",
    video_id: "a",
    title: "A",
    channel: "c",
    start: 0,
    end: null,
    match_start: null,
    match_cue_id: null,
    text: "",
    link: "https://youtu.be/a",
    cue_ids: [],
    frame_id: null,
    score: 0,
    timestamp: "0:00",
    thumb: null,
    thumb_large: null,
    ...over,
  };
}

describe("groupByVideo", () => {
  it("keeps the order of first appearance and never re-ranks", () => {
    const groups = groupByVideo([
      hit({ video_id: "b", title: "B" }),
      hit({ video_id: "a" }),
      hit({ video_id: "b", start: 9 }),
    ]);
    expect(groups.map((g) => g.video_id)).toEqual(["b", "a"]);
    expect(groups[0].hits).toHaveLength(2);
  });

  it("takes the first frame any hit in the group has", () => {
    const groups = groupByVideo([
      hit({ thumb: null }),
      hit({ thumb: "https://x/1.jpg", start: 5 }),
      hit({ thumb: "https://x/2.jpg", start: 6 }),
    ]);
    expect(groups[0].thumb).toBe("https://x/1.jpg");
  });
});

describe("badges", () => {
  it("names each leg, and both when two agreed", () => {
    expect(badges("transcript")).toEqual(["spoken"]);
    expect(badges("ocr+frame")).toEqual(["on-screen", "frame"]);
    expect(badges("transcript+ocr")).toEqual(["spoken", "on-screen"]);
  });
});

describe("channelWord", () => {
  it("names the channel a frameless moment came from", () => {
    expect(channelWord("transcript")).toBe("spoken");
    expect(channelWord("ocr")).toBe("on-screen");
    expect(channelWord("frame")).toBe("frame");
  });

  it("falls back to `video` for a leg it has never heard of", () => {
    expect(channelWord("audio")).toBe("video");
    expect(channelWord("")).toBe("video");
  });
});

describe("presentationOf", () => {
  it("gives each provenance its own presentation, and the fusion neither", () => {
    expect(presentationOf("transcript")).toBe("spoken");
    expect(presentationOf("ocr")).toBe("screen");
    expect(presentationOf("frame")).toBe("frame");
    expect(presentationOf("transcript+ocr")).toBe("mixed");
  });
});

// `app.js`'s `highlight`, run against the cases the original's own comments
// name: the marks are the visitor's words in the corpus's sentence, and a term
// under three characters is a join and not a word.
describe("highlight", () => {
  it("marks each term where it occurs, in reading order", () => {
    expect(highlight("we cache the keys and the values", "kv cache")).toEqual([
      { text: "we ", hit: false },
      { text: "cache", hit: true },
      { text: " the keys and the values", hit: false },
    ]);
  });

  it("marks every occurrence, whatever its case", () => {
    const runs = highlight("Cache and cache and CACHE", "cache");
    expect(runs.filter((run) => run.hit).map((run) => run.text)).toEqual([
      "Cache",
      "cache",
      "CACHE",
    ]);
  });

  it("takes the earliest occurrence at each position, so marks never overlap", () => {
    const runs = highlight("the context engineering context", "context engineering");
    expect(runs).toEqual([
      { text: "the ", hit: false },
      { text: "context", hit: true },
      { text: " ", hit: false },
      { text: "engineering", hit: true },
      { text: " ", hit: false },
      { text: "context", hit: true },
    ]);
  });

  it("ignores terms of two characters or fewer", () => {
    expect(highlight("a kv cache of a", "kv")).toEqual([{ text: "a kv cache of a", hit: false }]);
    expect(highlight("a cache", "?! -")).toEqual([{ text: "a cache", hit: false }]);
  });

  it("splits the query on punctuation rather than matching it", () => {
    const runs = highlight("owl:FunctionalProperty on a slide", "owl:FunctionalProperty");
    expect(runs.filter((run) => run.hit).map((run) => run.text)).toEqual([
      "owl",
      "FunctionalProperty",
    ]);
  });

  it("never marks a snippet that has no term in it", () => {
    expect(highlight("nothing of the sort", "paged attention")).toEqual([
      { text: "nothing of the sort", hit: false },
    ]);
  });

  it("returns nothing at all for a hit with no text", () => {
    expect(highlight(null, "cache")).toEqual([]);
    expect(highlight("", "cache")).toEqual([]);
  });

  // OCR text is whatever was on somebody's screen. It comes back as runs of
  // text, and a caller that renders them as text nodes cannot be made to run it.
  it("keeps adversarial screen text as text", () => {
    expect(highlight("<script>alert(1)</script>", "script")).toEqual([
      { text: "<", hit: false },
      { text: "script", hit: true },
      { text: ">alert(1)</", hit: false },
      { text: "script", hit: true },
      { text: ">", hit: false },
    ]);
  });
});
