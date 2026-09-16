// @vitest-environment jsdom
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { navigateTo } from "@/test/next";
import { found, hit, mountConsole, never, SEARCH_BOX, traverse, wire } from "@/test/public/console";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

const SEARCH = { mode: "search", q: "kv cache", type: "all" } as const;

function searchUrl(fetchSpy: ReturnType<typeof vi.fn>, call = 0) {
  return new URL((fetchSpy.mock.calls[call] as unknown as [string])[0], "http://x");
}

describe("the console in search mode", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the server's page one without a request", () => {
    const fetchSpy = vi.fn(never);
    vi.stubGlobal("fetch", fetchSpy);
    mountConsole({
      url: "/demo?q=kv+cache",
      initial: SEARCH,
      initialSearch: { kind: "ok", page: found([hit()]) },
    });
    expect(screen.getByRole("status")).toHaveTextContent("1 result");
    expect(screen.getByText("ready")).toHaveAttribute("data-s", "ready");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("fetches on Enter, writes the URL through history, and never on a keystroke", async () => {
    const fetchSpy = vi.fn(async () => wire(found([hit(), hit({ start: 20 })])));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    const { push } = mountConsole({ initial: { mode: "search", q: "", type: "all" } });

    await user.click(screen.getByRole("button", { name: "on-screen text" }));
    await user.type(screen.getByLabelText(SEARCH_BOX), "kv cache");
    expect(fetchSpy).not.toHaveBeenCalled();
    await user.keyboard("{Enter}");

    const url = searchUrl(fetchSpy);
    expect(url.pathname).toBe("/api/search");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      q: "kv cache",
      content_type: "ocr",
      limit: "10",
      offset: "0",
    });
    expect(push).toHaveBeenCalledWith(null, "", "/demo?q=kv+cache&type=ocr");
    expect(await screen.findByText("2 results")).toBeInTheDocument();
    // Focus stays in the box on a fine pointer.
    expect(screen.getByLabelText(SEARCH_BOX)).toHaveFocus();
  });

  it("keeps the previous results on screen until the new ones land", async () => {
    let land!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => (land = resolve))),
    );
    const user = userEvent.setup();
    mountConsole({
      url: "/demo?q=kv+cache",
      initial: SEARCH,
      initialSearch: { kind: "ok", page: found([hit({ title: "Old talk" })]) },
    });

    const box = screen.getByLabelText(SEARCH_BOX);
    await user.clear(box);
    await user.type(box, "paged{Enter}");

    expect(screen.getByText("Old talk")).toBeInTheDocument();
    expect(screen.getByText("Old talk").closest("[aria-busy]")).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.getByText("scanning")).toHaveAttribute("data-s", "working");

    await act(async () => land(await wire(found([hit({ title: "New talk" })]))));
    expect(await screen.findByText("New talk")).toBeInTheDocument();
    expect(screen.queryByText("Old talk")).not.toBeInTheDocument();
  });

  it("keeps the previous results when the new search is refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { error: "E_BAD", message: "search needs a query.", next: "Type something." },
          { status: 400 },
        ),
      ),
    );
    const user = userEvent.setup();
    mountConsole({
      url: "/demo?q=kv+cache",
      initial: SEARCH,
      initialSearch: { kind: "ok", page: found([hit()]) },
    });
    await user.type(screen.getByLabelText(SEARCH_BOX), " more{Enter}");
    expect(await screen.findByText("search needs a query.")).toBeInTheDocument();
    expect(screen.getByText("Type something.")).toBeInTheDocument();
    expect(screen.getByText("refused")).toHaveAttribute("data-s", "refused");
  });

  it("re-runs the search on screen when a channel is pinned", async () => {
    const fetchSpy = vi.fn(async () => wire(found([hit()])));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    const { push } = mountConsole({
      url: "/demo?q=kv+cache",
      initial: SEARCH,
      initialSearch: { kind: "ok", page: found([hit()]) },
    });
    await user.click(screen.getByRole("button", { name: "frames" }));
    expect(searchUrl(fetchSpy).searchParams.get("content_type")).toBe("frame");
    expect(push).toHaveBeenCalledWith(null, "", "/demo?q=kv+cache&type=frame");
  });

  it("counts a 429 down and retries its own request, not the page", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ error: "E_RATE_LIMIT", message: "no", retry_after_s: 2 }, { status: 429 }),
      )
      .mockResolvedValueOnce(wire(found([hit()])));
    vi.stubGlobal("fetch", fetchSpy);
    mountConsole({ initial: { mode: "search", q: "", type: "all" } });

    fireEvent.change(screen.getByLabelText(SEARCH_BOX), { target: { value: "kv cache" } });
    fireEvent.submit(screen.getByRole("search"));
    await act(async () => {});
    expect(screen.getByText("Try again in 2s.")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Try again" });
    expect(retry).toBeDisabled();

    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByText("Try again in 1s.")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1000));
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    await act(async () => {});
    expect(screen.getByText("1 result")).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(searchUrl(fetchSpy, 1).searchParams.get("q")).toBe("kv cache");
  });

  describe("More results", () => {
    const first = {
      kind: "ok",
      page: found([hit()], {
        pagination: { limit: 10, offset: 0, has_more: true, approx_total: 38 },
      }),
    } as const;

    it("appends page two into the card its video already has", async () => {
      const fetchSpy = vi.fn(async () =>
        wire(
          found([hit({ start: 90, link: "https://youtu.be/a?t=88" })], {
            pagination: { limit: 10, offset: 10, has_more: false },
          }),
        ),
      );
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      const { container, push } = mountConsole({
        url: "/demo?q=kv+cache",
        initial: SEARCH,
        initialSearch: first,
      });
      expect(screen.getByRole("status")).toHaveTextContent("1 result of ~38");

      await user.click(screen.getByRole("button", { name: "More results" }));
      expect(await screen.findByText("2 moments")).toBeInTheDocument();
      expect(container.querySelectorAll("article")).toHaveLength(1);
      expect(searchUrl(fetchSpy).searchParams.get("offset")).toBe("10");
      expect(push).not.toHaveBeenCalled();
    });

    it("pages by the server's cursor when unreadable rows were dropped", async () => {
      const row = (start: number) => hit({ start, link: `https://youtu.be/a?t=${start}` });
      const pageTwo = {
        query: "kv cache",
        content_type: "all",
        // Ten rows sent, one unreadable: the schema keeps nine.
        results: [...Array.from({ length: 9 }, (_, i) => row(100 + i)), { source: "transcript" }],
        pagination: { limit: 10, offset: 10, has_more: true },
        notes: [],
        data_status: null,
      };
      const fetchSpy = vi
        .fn()
        .mockResolvedValueOnce(Response.json(pageTwo))
        .mockResolvedValueOnce(wire(found([row(300)])));
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      mountConsole({ url: "/demo?q=kv+cache", initial: SEARCH, initialSearch: first });

      await user.click(screen.getByRole("button", { name: "More results" }));
      expect(await screen.findByText(/1 result\(s\) came back in a shape/)).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "More results" }));
      await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
      expect(searchUrl(fetchSpy, 0).searchParams.get("offset")).toBe("10");
      expect(searchUrl(fetchSpy, 1).searchParams.get("offset")).toBe("20");
    });

    it("keeps the rows and the count when page two is refused", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          Response.json(
            { error: "E_RATE_LIMIT", message: "no", retry_after_s: 12 },
            { status: 429 },
          ),
        ),
      );
      const user = userEvent.setup();
      const { container } = mountConsole({
        url: "/demo?q=kv+cache",
        initial: SEARCH,
        initialSearch: first,
      });
      await user.click(screen.getByRole("button", { name: "More results" }));
      expect(await screen.findByText("Try again in 12s.")).toBeInTheDocument();
      expect(container.querySelectorAll("article")).toHaveLength(1);
      expect(screen.getByRole("status")).toHaveTextContent("1 result of ~38");
    });

    it("is inert while page one of a newer search is out, which then lands", async () => {
      let land!: (response: Response) => void;
      const fetchSpy = vi.fn(() => new Promise<Response>((resolve) => (land = resolve)));
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      const { push } = mountConsole({
        url: "/demo?q=kv+cache",
        initial: SEARCH,
        initialSearch: first,
      });

      const box = screen.getByLabelText(SEARCH_BOX);
      await user.clear(box);
      await user.type(box, "paged{Enter}");
      expect(push).toHaveBeenCalledWith(null, "", "/demo?q=paged");
      expect(fetchSpy).toHaveBeenCalledTimes(1);

      const more = screen.getByRole("button", { name: "More results" });
      expect(more).toBeDisabled();
      fireEvent.click(more);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const { signal } = (
        fetchSpy.mock.calls[0] as unknown as [string, { signal: AbortSignal }]
      )[1];
      expect(signal.aborted).toBe(false);

      await act(async () => land(await wire(found([hit({ title: "Paged talk" })]))));
      expect(await screen.findByText("Paged talk")).toBeInTheDocument();
    });

    it("keeps an edition scope on page two", async () => {
      const fetchSpy = vi.fn(async () => wire(found([])));
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      mountConsole({
        url: "/paris?q=kv+cache",
        path: "/paris",
        tags: "series:aie-paris-2026",
        initial: SEARCH,
        initialSearch: first,
      });
      await user.click(screen.getByRole("button", { name: "More results" }));
      await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
      expect(searchUrl(fetchSpy).searchParams.get("tags")).toBe("series:aie-paris-2026");
    });
  });

  describe("what came back empty", () => {
    it("offers to widen a pinned channel, and the examples, in place", async () => {
      const fetchSpy = vi.fn(async () => wire(found([hit()])));
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      const { push } = mountConsole({
        url: "/demo?q=kv+cache&type=frame",
        initial: { ...SEARCH, type: "frame" },
        initialSearch: { kind: "ok", page: found([]) },
      });
      expect(screen.getByText("Nothing in the corpus matches this.")).toBeInTheDocument();
      expect(screen.getByText("no hits")).toBeInTheDocument();
      const widen = screen.getByRole("link", { name: "Search all" });
      expect(widen).toHaveAttribute("href", "/demo?q=kv+cache");

      await user.click(screen.getByRole("link", { name: "one of the examples" }));
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(push).toHaveBeenCalledWith(null, "", "/demo?q=");
      expect(screen.getByLabelText(SEARCH_BOX)).toHaveValue("");
      expect(screen.getByLabelText(SEARCH_BOX)).toHaveFocus();
      expect(
        screen.getByRole("link", { name: "context window costs money tokens" }),
      ).toHaveAttribute("href", "/demo?q=context+window+costs+money+tokens&type=ocr");
    });

    it("prints the notes, and does not call a page of unreadable hits empty", () => {
      mountConsole({
        url: "/demo?q=kv+cache",
        initial: SEARCH,
        initialSearch: {
          kind: "ok",
          page: found([], { dropped: 2, notes: ["2 result(s) came back unreadable."] }),
        },
      });
      expect(screen.getByText("2 result(s) came back unreadable.")).toBeInTheDocument();
      expect(screen.queryByText("Nothing in the corpus matches this.")).not.toBeInTheDocument();
    });

    it("blames an empty corpus on the corpus", () => {
      mountConsole({
        url: "/demo?q=kv+cache",
        initial: SEARCH,
        initialSearch: { kind: "ok", page: found([], { data_status: "empty" }) },
      });
      expect(screen.getByText("Nothing is indexed yet.")).toBeInTheDocument();
    });

    it("says the server could not be reached, with no status code", () => {
      mountConsole({
        url: "/demo?q=kv+cache",
        initial: SEARCH,
        initialSearch: { kind: "unreachable" },
      });
      expect(screen.getByText("Could not reach the server.")).toBeInTheDocument();
      expect(screen.getByText("no reply")).toBeInTheDocument();
      expect(document.body.textContent).not.toMatch(/\b5\d\d\b/);
    });
  });

  describe("the cold page", () => {
    it("runs an example in place, pinning the channel it names", async () => {
      const fetchSpy = vi.fn(async () => wire(found([hit()])));
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      mountConsole({ initial: { mode: "search", q: "", type: "all" } });
      await user.click(screen.getByRole("link", { name: "context window costs money tokens" }));
      expect(searchUrl(fetchSpy).searchParams.get("content_type")).toBe("ocr");
      expect(screen.getByLabelText(SEARCH_BOX)).toHaveValue("context window costs money tokens");
      expect(screen.getByRole("button", { name: "on-screen text" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

    it("pins a channel into the URL with no query to run", async () => {
      const fetchSpy = vi.fn(never);
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      const { push } = mountConsole({ initial: { mode: "search", q: "", type: "all" } });

      await user.click(screen.getByRole("button", { name: "frames" }));
      expect(push).toHaveBeenCalledWith(null, "", "/demo?q=&type=frame");
      expect(screen.getByRole("button", { name: "frames" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("says a spent bucket is a spent bucket", () => {
      mountConsole({ boot: "rate_limited", askEnabled: false, initial: { ...SEARCH, q: "" } });
      expect(screen.getByRole("status")).toHaveTextContent("too many requests");
      expect(screen.getByText("rate limited")).toHaveAttribute("data-s", "refused");
      expect(screen.queryByRole("button", { name: "ask ✨" })).not.toBeInTheDocument();
    });
  });

  it("restores a history entry, fetching only what it does not hold", async () => {
    const fetchSpy = vi.fn(async () => wire(found([hit({ title: "Frames talk" })])));
    vi.stubGlobal("fetch", fetchSpy);
    mountConsole({
      url: "/demo?q=kv+cache",
      initial: SEARCH,
      initialSearch: { kind: "ok", page: found([hit()]) },
    });

    await traverse("/demo?q=paged&type=frame");
    expect(screen.getByLabelText(SEARCH_BOX)).toHaveValue("paged");
    expect(await screen.findByText("Frames talk")).toBeInTheDocument();
    expect(searchUrl(fetchSpy).searchParams.get("content_type")).toBe("frame");

    await traverse("/demo?ask=why%3F");
    expect(screen.getByLabelText("Your question")).toHaveValue("why?");
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("ignores search params the address has already moved past", async () => {
    const fetchSpy = vi.fn(async () => wire(found([hit()])));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    const { push } = mountConsole({ initial: { mode: "search", q: "", type: "all" } });
    await user.type(screen.getByLabelText(SEARCH_BOX), "kv cache{Enter}");
    expect(push).toHaveBeenCalledWith(null, "", "/demo?q=kv+cache");
    expect(await screen.findByText("1 result")).toBeInTheDocument();

    // A router render one entry behind the address restores nothing.
    await navigateTo("/demo?q=older");
    expect(screen.getByLabelText(SEARCH_BOX)).toHaveValue("kv cache");
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  // A page the router restored from its cache can be older than its URL.
  it("reads the URL on mount when it names a different snapshot than the server's", async () => {
    const fetchSpy = vi.fn(async () => wire(found([hit({ title: "Newer talk" })])));
    vi.stubGlobal("fetch", fetchSpy);
    mountConsole({
      url: "/demo?q=paged",
      initial: SEARCH,
      initialSearch: { kind: "ok", page: found([hit()]) },
    });
    expect(await screen.findByText("Newer talk")).toBeInTheDocument();
    expect(screen.getByLabelText(SEARCH_BOX)).toHaveValue("paged");
  });
});
