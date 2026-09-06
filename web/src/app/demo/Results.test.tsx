// @vitest-environment jsdom
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Hit, Pagination } from "@/lib/api/schemas";
import { Results } from "./Results";

// The refused foot is `RetryIn`, which reaches for the router when its retry is
// the page's own reload. Here the retry is this component's, but the hook is
// read either way.
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/demo",
}));

function hit(over: Partial<Hit>): Hit {
  return {
    source: "transcript",
    video_id: "a",
    title: "Talk A",
    channel: "AI Engineer",
    start: 12,
    end: null,
    match_start: 12,
    match_cue_id: 1,
    text: "we cache the keys",
    link: "https://youtu.be/a?t=10",
    cue_ids: [1],
    frame_id: null,
    score: 0.1,
    timestamp: "0:12",
    thumb: null,
    thumb_large: null,
    ...over,
  };
}

function pagination(over: Partial<Pagination> = {}): Pagination {
  return { limit: 10, offset: 0, has_more: true, approx_total: 38, ...over };
}

function page(hits: Hit[], over: Partial<Pagination> = {}) {
  return {
    query: "kv cache",
    content_type: "all",
    results: hits,
    pagination: pagination(over),
    notes: [],
    data_status: null,
  };
}

function mount(hits: Hit[], over: Partial<Pagination> = {}, notes: string[] = []) {
  return render(
    <Results q="kv cache" type="all" hits={hits} pagination={pagination(over)} notes={notes} />,
  );
}

describe("the results and their one control", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("counts what is on screen, and names the estimate only while there is more", () => {
    const { rerender } = mount([hit({}), hit({ start: 20, timestamp: "0:20" })]);
    expect(screen.getByRole("status")).toHaveTextContent("2 results of ~38");

    rerender(
      <Results
        q="kv cache"
        type="all"
        hits={[hit({})]}
        pagination={pagination({ has_more: false })}
        notes={[]}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("1 result");
    expect(screen.getByRole("status")).not.toHaveTextContent("~38");
  });

  // `all` means all: a leg that could not run says so rather than narrowing
  // the answer in silence.
  it("prints the facade's notes under the count", () => {
    mount([hit({})], {}, ["The embedding worker is unreachable — the vector leg was skipped."]);
    expect(
      screen.getByText("The embedding worker is unreachable — the vector leg was skipped."),
    ).toBeInTheDocument();
  });

  it("offers no More results when the server said there are none", () => {
    mount([hit({})], { has_more: false });
    expect(screen.queryByRole("button", { name: "More results" })).not.toBeInTheDocument();
  });

  describe("More results", () => {
    it("appends page two into the card its video already has", async () => {
      const fetchSpy = vi.fn(async () =>
        Response.json(
          page([hit({ start: 90, timestamp: "1:30", link: "https://youtu.be/a?t=88" })], {
            offset: 10,
            has_more: false,
          }),
        ),
      );
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      const { container } = mount([hit({})]);

      await user.click(screen.getByRole("button", { name: "More results" }));

      await waitFor(() => expect(screen.getByText("2 moments")).toBeInTheDocument());
      // One card, not two: a page two that repeats a title reads as the list
      // restarting (demo-site.md §6.5).
      expect(container.querySelectorAll("article")).toHaveLength(1);
      expect(screen.getByRole("status")).toHaveTextContent("2 results");
      const [url] = fetchSpy.mock.calls[0] as unknown as [string];
      expect(url).toBe("/api/search?q=kv+cache&content_type=all&limit=10&offset=1");
    });

    it("opens a card for a talk page two brought in", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          Response.json(
            page([hit({ video_id: "b", title: "Talk B", link: "https://youtu.be/b?t=4" })], {
              offset: 10,
              has_more: false,
            }),
          ),
        ),
      );
      const user = userEvent.setup();
      const { container } = mount([hit({})]);

      await user.click(screen.getByRole("button", { name: "More results" }));
      await waitFor(() => expect(container.querySelectorAll("article")).toHaveLength(2));
    });

    // `append` is the whole difference between a hiccup and a wipe: losing ten
    // rows to a rate-limit hiccup on page two reads as the corpus breaking.
    it("keeps the rows and the count when page two is refused", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          Response.json(
            { error: "E_RATE_LIMIT", message: "Too many requests", retry_after_s: 12 },
            { status: 429 },
          ),
        ),
      );
      const user = userEvent.setup();
      const { container } = mount([hit({}), hit({ start: 20, timestamp: "0:20" })]);

      await user.click(screen.getByRole("button", { name: "More results" }));

      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument(),
      );
      expect(screen.getByText("Try again in 12s.")).toBeInTheDocument();
      expect(container.querySelectorAll("article")).toHaveLength(1);
      // Not "loading more…" frozen forever: the count of what is still on
      // screen is the truth.
      expect(screen.getByRole("status")).toHaveTextContent("2 results of ~38");
    });

    it("prints the facade's own sentence and next step on any other refusal", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          Response.json(
            {
              error: "E_BAD_FILTER",
              message: "content_type is not one of these.",
              next: "Try all.",
            },
            { status: 400 },
          ),
        ),
      );
      const user = userEvent.setup();
      mount([hit({})]);

      await user.click(screen.getByRole("button", { name: "More results" }));
      await waitFor(() =>
        expect(screen.getByText("content_type is not one of these.")).toBeInTheDocument(),
      );
      expect(screen.getByText("Try all.")).toBeInTheDocument();
    });

    it("says the server could not be reached when the fetch never lands", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          throw new TypeError("network");
        }),
      );
      const user = userEvent.setup();
      mount([hit({})]);

      await user.click(screen.getByRole("button", { name: "More results" }));
      await waitFor(() =>
        expect(screen.getByText("Could not reach the server.")).toBeInTheDocument(),
      );
    });

    // The next attempt clears the foot before it starts, so a retry can never
    // layer fresh rows under a stale error box.
    it("clears the foot before it asks again", async () => {
      const fetchSpy = vi
        .fn()
        .mockResolvedValueOnce(
          Response.json(
            { error: "E_RATE_LIMIT", message: "no", retry_after_s: 1 },
            { status: 429 },
          ),
        )
        .mockResolvedValueOnce(
          Response.json(page([hit({ video_id: "b", title: "Talk B" })], { has_more: false })),
        );
      vi.stubGlobal("fetch", fetchSpy);
      vi.useFakeTimers();
      try {
        const { container } = mount([hit({})]);
        act(() => {
          screen.getByRole("button", { name: "More results" }).click();
        });
        await act(async () => {});
        act(() => vi.advanceTimersByTime(1000));

        act(() => {
          screen.getByRole("button", { name: "Try again" }).click();
        });
        await act(async () => {});

        expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
        expect(container.querySelectorAll("article")).toHaveLength(2);
      } finally {
        vi.useRealTimers();
      }
    });

    it("refuses a body it cannot read rather than rendering half of it", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => Response.json({ results: "not a list" })),
      );
      const user = userEvent.setup();
      mount([hit({})]);

      await user.click(screen.getByRole("button", { name: "More results" }));
      await waitFor(() =>
        expect(
          screen.getByText("The corpus answered in a shape this page cannot read."),
        ).toBeInTheDocument(),
      );
    });
  });
});
