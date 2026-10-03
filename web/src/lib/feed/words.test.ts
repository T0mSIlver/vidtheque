import { describe, expect, it } from "vitest";
import { claudeUrl, parsePasted, signed, videoPrompt } from "./words";

describe("parsePasted", () => {
  it("reads a weight at either end and defaults the rest", () => {
    expect(
      parsePasted(
        [
          "- Eval harnesses for coding agents (+0.9)",
          "2. -0.8 Model launch hype",
          "Local inference: 0.6",
          "",
          "   ",
          "• Rust tooling",
          "Claude 3 Opus benchmarks",
        ].join("\n"),
      ),
    ).toEqual([
      { text: "Eval harnesses for coding agents", weight: 0.9 },
      { text: "Model launch hype", weight: -0.8 },
      { text: "Local inference", weight: 0.6 },
      { text: "Rust tooling", weight: 0.5 },
      { text: "Claude 3 Opus benchmarks", weight: 0.5 },
    ]);
  });

  it("keeps a number that is part of the text", () => {
    expect(parsePasted("GPT 5")).toEqual([{ text: "GPT 5", weight: 0.5 }]);
  });
});

it("prints a weight with its sign", () => {
  expect([signed(0.9), signed(-0.8), signed(0)]).toEqual(["+0.9", "−0.8", "0.0"]);
});

it("puts the whole prompt in one query parameter", () => {
  const prompt = videoPrompt({ video_id: "kCc8FmEb1nY", title: "A & B?", channel: "Theo" });
  const url = new URL(claudeUrl(prompt));
  expect(url.origin + url.pathname).toBe("https://claude.ai/new");
  expect(url.searchParams.get("q")).toBe(prompt);
  expect(prompt).toContain("vidtheque");
  expect(prompt).toContain("kCc8FmEb1nY");
});
