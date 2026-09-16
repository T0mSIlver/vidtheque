import { describe, expect, it } from "vitest";
import type { Citation, EditionTalk, Hit } from "@/lib/api";
import { labelCitation, labelHit, speakerLine } from "./edition";

// The talk table is the page's only label source (aie-paris-2026.md §4.3): a
// hit inside a mapped span is relabelled with the talk it fell in, and a hit
// anywhere else keeps whatever the corpus called it.
function talk(over: Partial<EditionTalk> = {}): EditionTalk {
  return {
    session_id: "s1",
    day: "2026-09-24",
    scheduled_start: "10:00",
    scheduled_end: "10:30",
    title: "Serving models without the tail latency",
    speakers: [
      { name: "Alice Martin", company: "Mistral" },
      { name: "Bob Nguyen", company: "" },
    ],
    category: "Inference",
    alignment_state: "aligned",
    video_id: "abcdefghijk",
    source_kind: "stream",
    start_s: 600,
    end_s: 1800,
    source: "https://youtu.be/abcdefghijk?t=600",
    ...over,
  };
}

function hit(over: Partial<Hit>): Hit {
  return {
    source: "transcript",
    video_id: "abcdefghijk",
    title: "AI Engineer Paris 2026 — day two",
    channel: "AI Engineer",
    start: 700,
    end: null,
    match_start: null,
    match_cue_id: null,
    text: "",
    link: "https://youtu.be/abcdefghijk?t=700",
    cue_ids: [],
    frame_id: null,
    score: 0,
    timestamp: "11:40",
    thumb: null,
    thumb_large: null,
    ...over,
  };
}

const citation: Citation = {
  n: 1,
  video_id: "abcdefghijk",
  title: "AI Engineer Paris 2026 — day two",
  channel: "AI Engineer",
  t: 700,
  timestamp: "11:40",
  link: "https://youtu.be/abcdefghijk?t=700",
  source: "transcript",
  text: "the cache makes it linear in the number of new tokens",
  thumb: null,
  thumb_large: null,
};

describe("speakerLine", () => {
  it("drops the comma for a speaker with no company", () => {
    expect(speakerLine(talk())).toBe("Alice Martin, Mistral · Bob Nguyen");
  });
});

describe("labelHit", () => {
  it("names the talk a moment fell inside, not the day stream", () => {
    const labelled = labelHit(hit({}), [talk()]);
    expect(labelled.title).toBe("Serving models without the tail latency");
    expect(labelled.channel).toBe("Alice Martin, Mistral · Bob Nguyen");
  });

  it("leaves a moment outside every mapped span alone", () => {
    // The span is half open: the second a talk ends belongs to the next one.
    for (const start of [599, 1800]) {
      expect(labelHit(hit({ start }), [talk()]).title).toBe("AI Engineer Paris 2026 — day two");
    }
  });

  it("never labels from a row that has no offsets yet", () => {
    const pending = talk({
      alignment_state: "not_yet_indexed",
      video_id: null,
      start_s: null,
      end_s: null,
      source: null,
      source_kind: null,
    });
    expect(labelHit(hit({}), [pending]).title).toBe("AI Engineer Paris 2026 — day two");
  });

  it("leaves a hit from another video alone", () => {
    expect(labelHit(hit({ video_id: "zzzzzzzzzzz" }), [talk()]).title).toBe(
      "AI Engineer Paris 2026 — day two",
    );
  });
});

describe("labelCitation", () => {
  it("labels an answer's source the same way a result row is labelled", () => {
    const labelled = labelCitation(citation, [talk()]);
    expect(labelled.title).toBe("Serving models without the tail latency");
    expect(labelled.channel).toBe("Alice Martin, Mistral · Bob Nguyen");
    // The receipt is the server's and is never rewritten by a label.
    expect(labelled.link).toBe(citation.link);
  });

  it("leaves a citation outside the mapped spans alone", () => {
    expect(labelCitation({ ...citation, t: 12 }, [talk()]).title).toBe(citation.title);
  });
});
