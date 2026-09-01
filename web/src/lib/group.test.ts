import { describe, expect, it } from "vitest";
import type { Hit } from "@/lib/api";
import { badges, groupByVideo } from "./group";

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
