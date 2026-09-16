// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Hit, Meta, SearchResponse, Video } from "@/lib/api/schemas";

// `lib/search` is `server-only` all the way down, and what this page wants from
// it is three answers. Mocked before the import, so the package index — and the
// `server-only` marker in it — is never loaded.
const reads = vi.hoisted(() => ({
  readMeta: vi.fn(),
  searchCorpus: vi.fn(),
  readCorpus: vi.fn(),
}));
vi.mock("@/lib/search", () => reads);
const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    ...nav,
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/demo",
}));

const META: Meta = {
  name: "vidtheque",
  version: "0.0.6",
  browse: "/dashboard",
  mcp_url: "https://vidtheque.example.com/mcp",
  auth: "none",
  ask_enabled: true,
  ask_model: "deepseek/deepseek-v4-flash-0731",
  videos: 473,
  clamps: { policy: "public", search_max_limit: 20, videos_max_limit: 50 },
  limits: { search_per_min: 30, ask_per_min: 5, ask_per_day: 50 },
  repo: "https://github.com/T0mSIlver/vidtheque",
};

function hit(over: Partial<Hit> = {}): Hit {
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

function found(results: Hit[], over: Partial<SearchResponse> = {}): SearchResponse {
  return {
    query: "kv cache",
    content_type: "all",
    results,
    pagination: { limit: 10, offset: 0, has_more: false },
    notes: [],
    data_status: results.length ? null : "ok",
    dropped: 0,
    ...over,
  };
}

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

type Boot = { kind: "ok"; meta: Meta } | { kind: "rate_limited" } | { kind: "unreachable" };

// The page renders its box before any read lands and streams the rest in, so a
// test has to let the stream finish: `act` awaited around the render is what
// flushes the `<Suspense>` boundaries the reads sit behind. What is asserted
// after it is the settled page.
async function mount(
  params: Record<string, string> = {},
  { meta = { kind: "ok", meta: META } as Boot, videos = [] as Video[] } = {},
) {
  reads.readMeta.mockResolvedValue(meta);
  reads.readCorpus.mockResolvedValue(videos);
  const { Console } = await import("./page");
  await act(async () => {
    render(<Console params={params} />);
  });
}

describe("the demo page", () => {
  afterEach(() => {
    vi.resetModules();
    reads.searchCorpus.mockReset();
    nav.replace.mockClear();
    nav.refresh.mockClear();
  });

  // The headline says "ask it something", so the box under it had better be
  // the one that answers a question (Tom, 2026-08-11; demo-site.md §6.1).
  describe("which mode it opens in", () => {
    it("opens in ask, with the ask examples under it", async () => {
      await mount();
      expect(screen.getByLabelText("Your question")).toBeInTheDocument();
      expect(
        screen.getByRole("button", {
          name: "Why does loop engineering look so much like building RLVR environments?",
        }),
      ).toBeInTheDocument();
    });

    it("takes ?ask=0 as search", async () => {
      await mount({ ask: "0" });
      expect(screen.getByLabelText("Search this video corpus")).toBeInTheDocument();
    });

    // Every search link written before ask was the default looks like this,
    // and so does every one the box writes for a visitor who never touched the
    // switch (§6.2).
    it("takes ?q= with no ask= as search", async () => {
      reads.searchCorpus.mockResolvedValue({ kind: "ok", page: found([hit()]) });
      await mount({ q: "kv cache" });
      expect(screen.getByLabelText("Search this video corpus")).toBeInTheDocument();
    });

    it("loads ?ask=1&q= in ask, holding the question and firing nothing", async () => {
      await mount({ ask: "1", q: "why do agents write bad AGENTS.md?" });
      expect(screen.getByLabelText("Your question")).toHaveValue(
        "why do agents write bad AGENTS.md?",
      );
      expect(reads.searchCorpus).not.toHaveBeenCalled();
    });

    // The one load that swaps the mode on screen, and it is the
    // misconfiguration rather than the demo. It is a *correction* now, not a
    // decision: waiting on `/api/meta` to find out which box to draw costs
    // every other visitor a round trip with nothing to type into, so the
    // markup states the default and the boot call moves the page if it must.
    it("moves a deployment with no key to search, and offers no switch", async () => {
      await mount({ ask: "1" }, { meta: { kind: "ok", meta: { ...META, ask_enabled: false } } });
      expect(nav.replace).toHaveBeenCalledWith("/demo?ask=0");
      expect(screen.queryByRole("button", { name: "ask ✨" })).not.toBeInTheDocument();
    });

    it("carries the typed question into the search it moves to", async () => {
      await mount(
        { ask: "1", q: "why do agents write bad AGENTS.md?" },
        { meta: { kind: "ok", meta: { ...META, ask_enabled: false } } },
      );
      expect(nav.replace).toHaveBeenCalledWith(
        "/demo?ask=0&q=why%20do%20agents%20write%20bad%20AGENTS.md%3F",
      );
    });

    it("leaves every other deployment where it is", async () => {
      await mount({ ask: "1" });
      expect(nav.replace).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "ask ✨" })).toBeInTheDocument();
    });
  });

  // The font preloads, the `autofocus` and the whole argument for this being a
  // search surface rest on the box being *in the markup*. It was behind a
  // `<Suspense>` waiting on `/api/meta`, so a visitor's first paint was an
  // empty 12rem rectangle with nothing to type into.
  describe("the first paint", () => {
    // Reads that never settle: this is what is on screen while they are out.
    async function firstPaint(params: Record<string, string>) {
      const never = () => new Promise(() => {});
      reads.readMeta.mockReturnValue(never());
      reads.readCorpus.mockReturnValue(never());
      reads.searchCorpus.mockReturnValue(never());
      const { Console } = await import("./page");
      render(<Console params={params} />);
    }

    it("draws the box, the chips and the examples before a read lands", async () => {
      await firstPaint({ ask: "0" });
      expect(screen.getByLabelText("Search this video corpus")).toBeInTheDocument();
      expect(screen.getByRole("group", { name: "Search which channel" })).toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: "context window costs money tokens" }),
      ).toBeInTheDocument();
      expect(screen.getByText("ready")).toHaveAttribute("data-s", "ready");
    });

    // …and it says the true thing about itself while it waits: a query in the
    // URL means the machine is already inside the corpus.
    it("says the machine is scanning while the search is out", async () => {
      await firstPaint({ ask: "0", q: "kv cache" });
      expect(screen.getByLabelText("Search this video corpus")).toBeInTheDocument();
      expect(screen.getByText("scanning")).toHaveAttribute("data-s", "working");
    });
  });

  describe("the cold page", () => {
    it("offers the four receipt-checked searches, each pinning the channel it needs", async () => {
      await mount({ ask: "0" });

      expect(
        screen.getByRole("link", { name: "context window costs money tokens" }),
      ).toHaveAttribute("href", "/demo?ask=0&q=context+window+costs+money+tokens&type=ocr");
      // No `data-type` is a reset to `all`: without it, an on-screen example
      // followed by a spoken one runs the second against OCR and reports an
      // empty corpus.
      expect(screen.getByRole("link", { name: "context engineering" })).toHaveAttribute(
        "href",
        "/demo?ask=0&q=context+engineering",
      );
      expect(
        screen.getByRole("link", { name: "architecture diagram with boxes and arrows" }),
      ).toHaveAttribute(
        "href",
        "/demo?ask=0&q=architecture+diagram+with+boxes+and+arrows&type=frame",
      );
      expect(
        screen.getByRole("link", { name: "human annotation calibrate LLM judge" }),
      ).toHaveAttribute(
        "href",
        "/demo?ask=0&q=human+annotation+calibrate+LLM+judge&type=transcript",
      );
      expect(screen.getByText(/Keyword search over every sentence spoken/)).toBeInTheDocument();
    });

    it("lists what is actually in the corpus", async () => {
      await mount({ ask: "0" }, { videos: [VIDEO] });
      const link = screen.getByRole("link", { name: "Let's build GPT" });
      expect(link).toHaveAttribute("href", "https://youtu.be/kCc8FmEb1nY");
      expect(link).toHaveAttribute("target", "_blank");
      expect(screen.getByText("Andrej Karpathy")).toBeInTheDocument();
    });

    it("says nothing about the corpus when the listing did not land", async () => {
      await mount({ ask: "0" }, { videos: [] });
      expect(screen.queryByText("in this corpus")).not.toBeInTheDocument();
    });

    // The 2026-08-28 defect: /api/meta shares the search bucket, so a visitor
    // who spent it and reloaded booted into a page of `undefined`.
    it("says a spent bucket is a spent bucket", async () => {
      await mount({ ask: "0" }, { meta: { kind: "rate_limited" } });
      expect(screen.getByRole("status")).toHaveTextContent(
        "too many requests — this page loads again in a minute",
      );
      expect(screen.getByText("rate limited")).toHaveAttribute("data-s", "refused");
    });

    it("distinguishes an unreachable server from a spent bucket", async () => {
      await mount({ ask: "0" }, { meta: { kind: "unreachable" } });
      expect(screen.getByRole("status")).toHaveTextContent("could not reach the server");
      expect(screen.getByText("no reply")).toHaveAttribute("data-s", "refused");
    });
  });

  describe("what came back", () => {
    it("prints the machine's word for a page of hits", async () => {
      reads.searchCorpus.mockResolvedValue({ kind: "ok", page: found([hit()]) });
      await mount({ ask: "0", q: "kv cache" });
      expect(screen.getByText("ready")).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("1 result");
    });

    // The query is quoted back two centimetres under the box that still holds
    // it, so the state says what is true about the corpus and points at the
    // four things that work (Tom, 2026-08-11).
    it("says the corpus does not have this, and offers the way back", async () => {
      reads.searchCorpus.mockResolvedValue({ kind: "ok", page: found([]) });
      await mount({ ask: "0", q: "flashattention-4" });

      expect(screen.getByText("Nothing in the corpus matches this.")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "one of the examples" })).toHaveAttribute(
        "href",
        "/demo?ask=0",
      );
      expect(screen.getByText("no hits")).toBeInTheDocument();
    });

    // Not advice but the visitor's own filter: leaving someone inside "frames
    // only" is how a demo looks broken.
    it("offers to widen only when a channel is pinned", async () => {
      reads.searchCorpus.mockResolvedValue({ kind: "ok", page: found([]) });
      await mount({ ask: "0", q: "kv cache", type: "frame" });

      expect(screen.getByText(/frames only/)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Search all" })).toHaveAttribute(
        "href",
        "/demo?ask=0&q=kv%20cache",
      );
    });

    // "Nothing matched" is a lie when there is nothing to match.
    it("blames an empty corpus on the corpus and not on the query", async () => {
      reads.searchCorpus.mockResolvedValue({
        kind: "ok",
        page: found([], { data_status: "empty" }),
      });
      await mount({ ask: "0", q: "anything" });

      expect(screen.getByText("Nothing is indexed yet.")).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "one of the examples" })).not.toBeInTheDocument();
    });

    it("counts a 429 down where the results were", async () => {
      reads.searchCorpus.mockResolvedValue({ kind: "rate_limited", retryAfter: 12 });
      await mount({ ask: "0", q: "kv cache" });

      expect(screen.getByText("Too many requests.")).toBeInTheDocument();
      expect(screen.getByText("Try again in 12s.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Try again" })).toBeDisabled();
      expect(screen.getByText("refused")).toBeInTheDocument();
    });

    // An error boundary would replace both of these with one generic line,
    // which is the only account a visitor gets of why.
    it("prints the facade's own sentence and the step it named", async () => {
      reads.searchCorpus.mockResolvedValue({
        kind: "refused",
        message: "search needs either a query or at least one filter.",
        next: "Try list-videos to see what is indexed.",
      });
      await mount({ ask: "0", q: "kv cache" });

      expect(
        screen.getByText("search needs either a query or at least one filter."),
      ).toBeInTheDocument();
      expect(screen.getByText("Try list-videos to see what is indexed.")).toBeInTheDocument();
    });

    it("says the server could not be reached, and prints no status code", async () => {
      reads.searchCorpus.mockResolvedValue({ kind: "unreachable" });
      await mount({ ask: "0", q: "kv cache" });

      expect(screen.getByText("Could not reach the server.")).toBeInTheDocument();
      expect(screen.getByText("no reply")).toBeInTheDocument();
      expect(document.body.textContent).not.toMatch(/\b5\d\d\b/);
    });

    // A leg that could not run is exactly what "all means all" is a promise
    // about, and a page with no hits is where it matters most: the note is the
    // difference between "the corpus does not have this" and "two thirds of
    // the corpus was never searched".
    it("prints the notes on a page that found nothing", async () => {
      reads.searchCorpus.mockResolvedValue({
        kind: "ok",
        page: found([], { notes: ["note: the embedding worker is unreachable; fts only."] }),
      });
      await mount({ ask: "0", q: "kv cache" });

      expect(
        screen.getByText("note: the embedding worker is unreachable; fts only."),
      ).toBeInTheDocument();
      expect(screen.getByText("Nothing in the corpus matches this.")).toBeInTheDocument();
    });

    // A page that dropped every hit it was sent matched something — it just
    // could not read what came back. The note says that; "Nothing in the
    // corpus matches this." would say the opposite over the top of it.
    it("says what it could not read, and does not call the corpus empty for it", async () => {
      reads.searchCorpus.mockResolvedValue({
        kind: "ok",
        page: found([], {
          dropped: 2,
          notes: ["2 result(s) came back in a shape this page cannot read and were left out."],
        }),
      });
      await mount({ ask: "0", q: "kv cache" });

      expect(
        screen.getByText(
          "2 result(s) came back in a shape this page cannot read and were left out.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText("Nothing in the corpus matches this.")).not.toBeInTheDocument();
    });

    it("prints them over an empty corpus too", async () => {
      reads.searchCorpus.mockResolvedValue({
        kind: "ok",
        page: found([], { data_status: "empty", notes: ["note: nothing is queryable yet."] }),
      });
      await mount({ ask: "0", q: "kv cache" });

      expect(screen.getByText("note: nothing is queryable yet.")).toBeInTheDocument();
      expect(screen.getByText("Nothing is indexed yet.")).toBeInTheDocument();
    });
  });

  // Page one is the server's, so a retry is the page asking for itself again.
  // A notice that names a failure and offers nothing to do about it is where a
  // visitor leaves; `app.js` handed a retry to every failure it drew.
  describe("trying again", () => {
    it("offers a retry when the facade refused page one", async () => {
      reads.searchCorpus.mockResolvedValue({
        kind: "refused",
        message: "search needs either a query or at least one filter.",
      });
      await mount({ ask: "0", q: "kv cache" });

      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(nav.refresh).toHaveBeenCalled();
    });

    it("offers one when the server never answered at all", async () => {
      reads.searchCorpus.mockResolvedValue({ kind: "unreachable" });
      await mount({ ask: "0", q: "kv cache" });

      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(nav.refresh).toHaveBeenCalled();
    });

    // The 429 already had one, and it stays gated until the limiter's own
    // countdown is over: a retry fired into a refusal is one more refusal.
    it("keeps the rate limiter's retry behind its countdown", async () => {
      reads.searchCorpus.mockResolvedValue({ kind: "rate_limited", retryAfter: 12 });
      await mount({ ask: "0", q: "kv cache" });

      expect(screen.getByRole("button", { name: "Try again" })).toBeDisabled();
    });
  });
});
