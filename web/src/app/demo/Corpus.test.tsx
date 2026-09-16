// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Video } from "@/lib/api/schemas";
import { Corpus } from "./Corpus";

const VIDEO: Video = {
  video_id: "kCc8FmEb1nY",
  title: "Let's build GPT",
  channel: "Andrej Karpathy",
  published: "2026-08-11",
  duration: "1:56:20",
  coverage: "tof",
  tags: "",
  indexed_at: "2026-08-12",
  index_state: "ready",
  link: "https://youtu.be/kCc8FmEb1nY",
  thumb: null,
};

describe("the corpus listing", () => {
  it("lists what is actually in the corpus", () => {
    render(<Corpus videos={[VIDEO]} />);
    const link = screen.getByRole("link", { name: "Let's build GPT" });
    expect(link).toHaveAttribute("href", "https://youtu.be/kCc8FmEb1nY");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getByText("Andrej Karpathy")).toBeInTheDocument();
  });

  it("says nothing when the listing did not land", () => {
    render(<Corpus videos={[]} />);
    expect(screen.queryByText("in this corpus")).not.toBeInTheDocument();
  });
});
