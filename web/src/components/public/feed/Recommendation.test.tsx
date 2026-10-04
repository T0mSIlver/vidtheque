// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { FeedItem } from "@/lib/api/schemas";
import { Recommendation } from "./Recommendation";

// A video that is not on YouTube has no deeplink; the rest of the feed still parses.
it("shows a verdict on a non-YouTube video without links", () => {
  const item = FeedItem.parse({
    video_id: "vimeo:123",
    title: "A talk elsewhere",
    channel: "Elsewhere",
    duration_s: 600,
    published_at: null,
    url: null,
    thumb: null,
    score: 2,
    reason: "Why.",
    summary: "What.",
    matches: [],
    moments: [{ offset_s: 30, url: null, why: "this part", excerpt: "a quote", speaker: null }],
  });
  render(<Recommendation item={item} />);
  expect(screen.getByRole("heading", { name: "A talk elsewhere" })).toBeInTheDocument();
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText("0:30")).toBeInTheDocument();
});
