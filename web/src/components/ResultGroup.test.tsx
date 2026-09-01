// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Hit } from "@/lib/api";
import { ResultGroup } from "./ResultGroup";

function hit(over: Partial<Hit>): Hit {
  return {
    source: "transcript",
    video_id: "kCc8FmEb1nY",
    title: "Let's build GPT",
    channel: "Andrej Karpathy",
    start: 12,
    end: 14.8,
    match_start: 12,
    match_cue_id: 1,
    text: "we cache the keys and the values",
    link: "https://youtu.be/kCc8FmEb1nY?t=10",
    cue_ids: [1],
    frame_id: null,
    score: 0.1,
    timestamp: "0:12",
    thumb: null,
    thumb_large: null,
    ...over,
  };
}

describe("ResultGroup", () => {
  it("prints the receipt for every moment and the talk's id once", () => {
    render(
      <ResultGroup
        group={{
          video_id: "kCc8FmEb1nY",
          title: "Let's build GPT",
          channel: "Andrej Karpathy",
          thumb: null,
          hits: [hit({}), hit({ start: 430, timestamp: "7:10", source: "ocr", text: "kv cache size", link: "https://youtu.be/kCc8FmEb1nY?t=428" })],
        }}
      />,
    );
    expect(screen.getByText("youtu.be/kCc8FmEb1nY?t=10")).toBeInTheDocument();
    expect(screen.getByText("youtu.be/kCc8FmEb1nY?t=428")).toBeInTheDocument();
    expect(screen.getByText("2 moments")).toBeInTheDocument();
  });

  it("quotes speech, badges on-screen text, and marks a visual match as muted", () => {
    render(
      <ResultGroup
        group={{
          video_id: "v",
          title: "t",
          channel: "c",
          thumb: null,
          hits: [
            hit({ source: "transcript", text: "spoken words" }),
            hit({ source: "ocr", text: "slide text", start: 20 }),
            hit({ source: "frame", text: "visible words", start: 30 }),
          ],
        }}
      />,
    );
    expect(screen.getByText("“spoken words”")).toBeInTheDocument();
    expect(screen.getByText("on-screen")).toBeInTheDocument();
    expect(screen.getByText("visible words").className).toMatch(/muted/);
  });
});
