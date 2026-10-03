// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeedShell } from "@/components/feed/FeedShell";
import { mountDashboard, type Route } from "@/test/dashboard/harness";
import { NO_MATCH_SEARCH, OWNER_SEARCH } from "@/test/dashboard/search-fixtures";
import { SearchView } from "./SearchView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The tool's search on the feed (dashboard.md §25.9): every channel, no filter,
// one `mcp_search` per query submitted, and a `watch` when a moment is opened.

const RECORDED = { recorded: true, signal_id: 1, kind: "mcp_search", video_id: null };
const DATED = {
  ...OWNER_SEARCH,
  results: OWNER_SEARCH.results.map((hit) => ({ ...hit, published_at: 1674000000 })),
};

function mount(search = "", answer: Route = { body: DATED }) {
  return mountDashboard(
    <FeedShell>
      <SearchView />
    </FeedShell>,
    {
      bare: true,
      path: "/feed/search",
      search,
      routes: {
        "/dashboard/api/search": answer,
        "POST /dashboard/api/signals": { body: RECORDED },
      },
    },
  );
}

const asked = (view: Awaited<ReturnType<typeof mount>>) =>
  view.calls("/dashboard/api/search").map((request) => new URL(request.url, "http://x").search);
const signals = (view: Awaited<ReturnType<typeof mount>>) =>
  view.calls("/dashboard/api/signals", "POST").map((request) => request.json);

describe("feed SearchView", () => {
  it("runs nothing until a query is submitted, then sends one mcp_search", async () => {
    const view = await mount();
    expect(screen.getByRole("link", { name: "Search" })).toHaveAttribute("aria-current", "page");
    await userEvent.type(screen.getByRole("searchbox"), "  kv cache {Enter}");
    await waitFor(() => expect(signals(view)).toEqual([{ kind: "mcp_search", text: "kv cache" }]));
    expect(view.push).toHaveBeenCalledWith("/feed/search?q=kv+cache");
    await screen.findAllByRole("link", { name: /Let's build GPT/ });
    expect(asked(view)).toEqual(["?q=kv+cache&offset=0"]);
  });

  it("shows the video, channel, date and the moment, and links each", async () => {
    const view = await mount("?q=cache");
    const videos = await screen.findAllByRole("link", { name: /Let's build GPT/ });
    expect(videos[0]).toHaveAttribute("href", "/feed/kCc8FmEb1nY");
    expect(within(videos[0]).getByText("Andrej Karpathy · 2023-01-18")).toBeInTheDocument();
    const moment = screen.getByRole("link", { name: /kv cache size/ });
    expect(moment).toHaveAttribute("href", "https://youtu.be/kCc8FmEb1nY?t=3");
    expect(within(moment).getByText("0:05")).toBeInTheDocument();
    expect(within(moment).getByText("on-screen")).toBeInTheDocument();
    await userEvent.click(moment);
    // A reload of `?q=` is no new query: the only signal is the watch.
    await waitFor(() =>
      expect(signals(view)).toEqual([{ kind: "watch", video_id: "kCc8FmEb1nY", offset_s: 5 }]),
    );
  });

  it("pages on has_more from the offset the server answered", async () => {
    const first = {
      ...DATED,
      results: DATED.results.slice(0, 2),
      pagination: { limit: 2, offset: 0, has_more: true },
    };
    const second = {
      ...DATED,
      results: DATED.results.slice(2),
      pagination: { limit: 2, offset: 2, has_more: false },
    };
    const view = await mount("?q=cache", [{ body: first }, { body: second }]);
    await userEvent.click(await screen.findByRole("button", { name: "More" }));
    await waitFor(() => expect(asked(view)).toEqual(["?q=cache&offset=0", "?q=cache&offset=2"]));
    const results = screen.getByRole("list", { name: "Results" });
    await waitFor(() => expect(within(results).getAllByRole("listitem")).toHaveLength(5));
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
    expect(signals(view)).toEqual([]);
  });

  it("says nothing is indexed apart from nothing matched, and prints the notes", async () => {
    await mount("?q=zzzz", { body: NO_MATCH_SEARCH });
    expect(await screen.findByText("Nothing is indexed yet.")).toBeInTheDocument();
    expect(
      screen.getByText(/semantic \(nearest-neighbour\) legs were not queried/),
    ).toBeInTheDocument();
  });
});
