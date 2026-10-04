// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FeedOutcome } from "@/lib/api/search";
import type { FeedItem } from "@/lib/api";

const feed = vi.hoisted(() => ({ outcome: null as FeedOutcome | null }));
vi.mock("@/lib/api/search", () => ({ readFeed: async () => feed.outcome }));
vi.mock("@/components/public/facts", () => ({ RailFacts: () => null }));

import LandingPage from "./page";

function item(n: number): FeedItem {
  return {
    video_id: `vid${n}`,
    title: `Talk ${n}`,
    channel: "AI Engineer",
    duration_s: 1200,
    published_at: 1_786_464_006,
    url: `https://youtu.be/vid${n}?t=0`,
    thumb: null,
    score: 3,
    reason: `Reason ${n}.`,
    summary: `Summary ${n}.`,
    matches: [{ text: "Coding agent evals", direction: "up" }],
    moments: [
      {
        offset_s: 61,
        url: `https://youtu.be/vid${n}?t=59`,
        why: "the eval loop",
        excerpt: "we grade every run",
        speaker: null,
      },
    ],
  };
}

const OK: FeedOutcome = {
  kind: "ok",
  feed: {
    profile: { name: "A builder shipping coding agents", entries: [] },
    items: [item(1), item(2), item(3)],
    has_more: true,
    next_offset: 3,
  },
};

describe("the landing at /", () => {
  it("leads with the positioning sentence and its two ways in", async () => {
    feed.outcome = OK;
    render(await LandingPage());
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "vidtheque watches the channels you follow and tells you which videos, and which minutes, will teach you something.",
    );
    expect(screen.getByRole("link", { name: /See the sample feed/ })).toHaveAttribute(
      "href",
      "/demo",
    );
    expect(screen.getByRole("link", { name: "Run your own" })).toHaveAttribute("href", "/docs");
  });

  it("shows three sample recommendations, each quoting a moment that lands on its second", async () => {
    feed.outcome = OK;
    const { container } = render(await LandingPage());
    const picks = container.querySelectorAll("#picks article");
    expect(picks).toHaveLength(3);
    const first = within(picks[0] as HTMLElement);
    expect(first.getByRole("link", { name: "1:01" })).toHaveAttribute(
      "href",
      "https://youtu.be/vid1?t=59",
    );
    expect(first.getByText("“we grade every run”")).toBeInTheDocument();
  });

  it("leaves the picks out rather than showing an empty beat", async () => {
    feed.outcome = { kind: "unreachable" };
    const { container } = render(await LandingPage());
    expect(container.querySelector("#picks")).toBeNull();
    expect(screen.getByRole("link", { name: "Run your own" })).toBeInTheDocument();
  });

  // A public commitment (research/positioning-2026-08-10.md §9.1).
  it("keeps the footer's promise: the videos are theirs, and no needs no appeal", async () => {
    feed.outcome = OK;
    render(await LandingPage());
    expect(
      screen.getByText(/^The videos belong to the people who made them\./),
    ).toBeInTheDocument();
    expect(screen.getByText(/there is no appeal to make/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Removal on request" })).toHaveAttribute(
      "href",
      "https://github.com/T0mSIlver/vidtheque/blob/main/docs/takedown.md",
    );
  });
});
