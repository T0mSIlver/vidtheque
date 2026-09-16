// @vitest-environment jsdom
import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mountDashboard, type Answer, type Route } from "@/test/dashboard";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import { REINDEX_REFUSED, REINDEXED } from "@/test/index-fixtures";
import { DEMO_LIBRARY, OWNER_LIBRARY, OWNER_LIBRARY_CLAMPED } from "@/test/library-fixtures";
import { firstPaint } from "@/test/retry";
import { DEBOUNCE_MS } from "@/components/dashboard/kit/FilterBand";
import { VideosView } from "./VideosView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The round trips: a filter in the band becomes a URL, a link carries the query
// with one thing changed, and the page shows the filters the query ran with.

function mount(
  library: Route,
  {
    search = "",
    session = OWNER_SESSION as unknown,
    post = { body: REINDEXED } as Answer,
  }: { search?: string; session?: unknown; post?: Answer } = {},
) {
  return mountDashboard(<VideosView />, {
    path: "/dashboard/videos",
    search,
    session,
    routes: { "/dashboard/api/library": library, "POST /dashboard/videos/*": post },
  });
}

/** The payload with the filters the query ran with. */
function ran(filters: Record<string, unknown>, rest: Record<string, unknown> = {}) {
  return { ...OWNER_LIBRARY, ...rest, filters: { ...OWNER_LIBRARY.filters, ...filters } };
}

/** Answers by the query the read carried. */
function byQuery(answer: (params: URLSearchParams) => Answer): Route {
  return (request) => answer(new URL(request.url, "http://dashboard.test").searchParams);
}

const EMPTIES =
  "q=&channel=&tags=&published_after=&published_before=&indexed_after=&indexed_before=";

/** Let reads land under a stopped clock. */
async function settle() {
  for (let i = 0; i < 6; i++) await act(() => vi.advanceTimersByTimeAsync(0));
}

/** A band under a stopped clock, with the read landed. */
async function band(route: Route, options?: Parameters<typeof mount>[1]) {
  vi.useFakeTimers();
  const mounted = await mount(route, options);
  await settle();
  return mounted;
}

function typed(label: string, value: string) {
  fireEvent.input(screen.getByLabelText(label), { target: { value } });
}

async function pause(ms = DEBOUNCE_MS) {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}

const form = () => screen.getByLabelText("Channel").closest("form") as HTMLFormElement;

describe("the videos table", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the corpus, its exact count, and one row per video", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });

    await screen.findByRole("table");
    expect(screen.getByRole("heading", { name: "Videos" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("4 shown of 4.");
    expect(screen.getAllByRole("row")).toHaveLength(5);

    const row = screen.getByRole("link", { name: "Let's build GPT: from scratch" });
    expect(row).toHaveAttribute("href", "/dashboard/videos/kCc8FmEb1nY");
    expect(screen.getAllByText("no frame")).toHaveLength(2);
    expect(screen.getByText("1:30:00")).toBeInTheDocument();
    expect(screen.getByText("0:20:00")).toBeInTheDocument();
    expect(screen.getByText("1:00:00")).toBeInTheDocument();
    expect(screen.getByText("1:56:40")).toBeInTheDocument();
  });

  it("holds the page open while the read is out", async () => {
    await mount({ body: OWNER_LIBRARY });
    expect(screen.getByRole("heading", { name: "Videos" })).toBeInTheDocument();
    expect(screen.getByLabelText("Channel")).toBeInTheDocument();
    await screen.findByRole("table");
  });

  // Every bound is Python's: the URL goes on the wire as typed.
  it("sends the URL's own filters, and only the ones the contract takes", async () => {
    const { fetcher } = await mount(
      { body: OWNER_LIBRARY },
      { search: "index_state=all&limit=100000&nonsense=1" },
    );
    await screen.findByRole("table");

    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/library?index_state=all&limit=100000",
      expect.objectContaining({ credentials: "same-origin", cache: "no-store" }),
    );
  });

  it("draws the state as its own word and coverage as three legs", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });

    const table = within(await screen.findByRole("table"));
    expect(table.getByText("indexing")).toBeInTheDocument();
    expect(table.getAllByText("ready")).toHaveLength(3);
    expect(table.getAllByText("transcript: present")).toHaveLength(3);
    expect(table.getAllByText("frame embeddings: missing")).toHaveLength(3);
  });

  it("names the sorted column and links every other head to its own order", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });

    const published = await screen.findByRole("columnheader", { name: "Published" });
    expect(published).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("columnheader", { name: "Title" })).not.toHaveAttribute("aria-sort");
    expect(screen.getByRole("link", { name: "Title" })).toHaveAttribute(
      "href",
      `/dashboard/videos?${EMPTIES}&order=title&limit=50`,
    );
  });

  it("shows the order that ran, and offers no option that is not one", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "" });
    await screen.findByRole("table");

    const order = screen.getByLabelText("Order") as HTMLSelectElement;
    expect(order).toHaveValue("recency");
    expect([...order.options].map((option) => option.value)).toEqual([
      "recency",
      "title",
      "duration",
      "indexed_at",
      "relevance",
    ]);
  });

  it("holds the page size the server accepted, not the one that was asked for", async () => {
    await mount({ body: OWNER_LIBRARY_CLAMPED }, { search: "limit=100000" });
    await screen.findByText(/limit=100000 → 100/);

    expect(screen.getByLabelText("Rows")).toHaveValue(100);
  });

  it("names the filter that ran, never the one in the URL bar", async () => {
    await mount({ body: OWNER_LIBRARY_CLAMPED }, { search: "has=banana" });
    await screen.findByText(/has='banana' is not one of/);

    const head = within(screen.getByRole("heading", { name: "Videos" }).closest("div")!);
    expect(head.queryByText("banana")).toBeNull();
    expect(screen.getByLabelText("Coverage")).toHaveValue("any");
  });

  it("prints the clamp Python applied, in Python's words", async () => {
    await mount({ body: OWNER_LIBRARY_CLAMPED }, { search: "limit=100000&has=banana&order=nope" });

    expect(await screen.findByText(/limit=100000 → 100/)).toBeInTheDocument();
    expect(screen.getByText(/has='banana' is not one of/)).toBeInTheDocument();
    expect(screen.getByText(/order='nope' is not one of/)).toBeInTheDocument();
  });

  it("puts what is narrowing the table on the title's own baseline", async () => {
    await mount(
      { body: ran({ published_after: 1767225600, index_state: "failed" }) },
      { search: "index_state=failed&published_after=2026-01-01" },
    );
    await screen.findByRole("table");

    const head = within(screen.getByRole("heading", { name: "Videos" }).closest("div")!);
    expect(head.getByText("failed")).toBeInTheDocument();
    expect(head.getByText("2026-01-01 – …")).toBeInTheDocument();
  });

  // `_before` is exclusive, so the box holds the echo's day less one.
  it("shows the day a clamped date became, in the box and on the baseline", async () => {
    await mount(
      {
        body: {
          ...ran({ published_before: 1788652800 }),
          notes: ["note: resolved server-side: published_before=2999-01-01 → 2026-09-05."],
        },
      },
      { search: "published_before=2999-01-01" },
    );

    expect(await screen.findByText(/published_before=2999-01-01 → 2026-09-05/)).toBeInTheDocument();
    expect(screen.getByLabelText("Published on or before")).toHaveValue("2026-09-05");
    expect(screen.getByLabelText("Published on or after")).toHaveValue("");
    const head = within(screen.getByRole("heading", { name: "Videos" }).closest("div")!);
    expect(head.getByText("… – 2026-09-05")).toBeInTheDocument();
  });

  it("draws both ends of both ranges without a key collision", async () => {
    const complaints = vi.spyOn(console, "error").mockImplementation(() => {});
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });
    await screen.findByRole("table");
    const said = complaints.mock.calls.map((call) => String(call[0]));
    complaints.mockRestore();

    expect(said.filter((line) => line.includes("same key"))).toEqual([]);
    for (const label of [
      "Published on or after",
      "Published on or before",
      "Indexed on or after",
      "Indexed on or before",
    ]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
  });

  it("leaves the page size to the server's own ceiling", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });

    const rows = await screen.findByLabelText("Rows");
    expect(rows).toHaveAttribute("min", "1");
    expect(rows).not.toHaveAttribute("max");
  });

  describe("the band", () => {
    it("searches the moment a picker changes, without scrolling or losing focus", async () => {
      const { push } = await band({ body: OWNER_LIBRARY }, { search: "" });
      const before = form();
      const coverage = screen.getByLabelText("Coverage");
      coverage.focus();

      fireEvent.change(coverage, { target: { value: "ocr" } });
      await settle();

      expect(push).toHaveBeenCalledWith(
        `/dashboard/videos?q=&channel=&tags=&has=ocr&published_after=&published_before=` +
          `&indexed_after=&indexed_before=&order=recency&limit=50`,
        { scroll: false },
      );
      expect(form()).toBe(before);
      expect(document.activeElement).toBe(coverage);
    });

    // The pause is measured from the last keystroke.
    it("searches a typed field when the typing stops, and not before", async () => {
      const { push } = await band({ body: OWNER_LIBRARY }, { search: "" });

      typed("Title, channel or description", "atten");
      await pause(DEBOUNCE_MS - 1);
      typed("Title, channel or description", "attention");
      await pause(DEBOUNCE_MS - 1);
      expect(push).not.toHaveBeenCalled();

      await pause();
      expect(push).toHaveBeenCalledTimes(1);
      expect(push).toHaveBeenCalledWith(
        `/dashboard/videos?q=attention&channel=&tags=&published_after=&published_before=` +
          `&indexed_after=&indexed_before=&order=recency&limit=50`,
        { scroll: false },
      );
    });

    it("takes Enter as the search, and disarms what was pending", async () => {
      const { push } = await band({ body: OWNER_LIBRARY }, { search: "" });

      const channel = screen.getByLabelText("Channel");
      fireEvent.input(channel, { target: { value: "Karpathy" } });
      fireEvent.submit(form());
      expect(push).toHaveBeenCalledTimes(1);

      await pause(DEBOUNCE_MS * 2);
      expect(push).toHaveBeenCalledTimes(1);
    });

    // Apply stays in the markup for a browser that never ran the script.
    it("hides Apply once it is doing the applying, and keeps Reset", async () => {
      await mount({ body: OWNER_LIBRARY }, { search: "" });
      await screen.findByRole("table");

      const apply = form().querySelector("button[type='submit']") as HTMLButtonElement;
      expect(apply).toHaveTextContent("Apply");
      expect(apply).toHaveAttribute("data-apply");
      expect(form()).toHaveAttribute("data-scripted");
      expect(screen.getByRole("link", { name: "Reset" })).toHaveAttribute(
        "href",
        "/dashboard/videos",
      );
    });

    it("keeps the caret in the field that searched, across the navigation", async () => {
      const { push } = await band(
        byQuery((params) =>
          params.get("channel") ? { body: ran({ channel: "Karpathy" }) } : { body: OWNER_LIBRARY },
        ),
        { search: "" },
      );
      const before = form();
      const channel = screen.getByLabelText("Channel") as HTMLInputElement;
      channel.focus();

      typed("Channel", "Karpathy");
      await pause();
      await settle();

      expect(push).toHaveBeenCalledTimes(1);
      expect(form()).toBe(before);
      expect(document.activeElement).toBe(channel);
      expect(channel).toHaveValue("Karpathy");
    });

    it("re-seeds a control that is not in use from what the server resolved", async () => {
      const { navigate } = await mount(
        byQuery((params) =>
          params.get("limit") === "100000"
            ? { body: OWNER_LIBRARY_CLAMPED }
            : { body: OWNER_LIBRARY },
        ),
      );
      await screen.findByRole("table");
      expect(screen.getByLabelText("Rows")).toHaveValue(50);

      await navigate("/dashboard/videos?limit=100000");
      await screen.findByText(/limit=100000 → 100/);
      expect(screen.getByLabelText("Rows")).toHaveValue(100);
    });

    it("never overwrites newer typing with the reply to an earlier keystroke", async () => {
      vi.useFakeTimers();
      let release: (() => void) | undefined;
      await mount(
        async (request) => {
          const channel = new URL(request.url, "http://dashboard.test").searchParams.get("channel");
          if (!channel) return { body: OWNER_LIBRARY };
          await new Promise<void>((done) => (release = done));
          return { body: ran({ channel }) };
        },
        { search: "" },
      );
      await settle();
      const channel = screen.getByLabelText("Channel") as HTMLInputElement;
      channel.focus();

      typed("Channel", "Karp");
      await pause();
      await settle();
      // The reply to "Karp" is out; the reader keeps typing.
      typed("Channel", "Karpathy");
      await act(async () => release?.());
      await settle();

      expect(channel).toHaveValue("Karpathy");
      expect(document.activeElement).toBe(channel);
    });
  });

  it("pages with the query carried and one number changed", async () => {
    await mount(
      { body: { ...OWNER_LIBRARY, pagination: { limit: 2, offset: 2, has_more: true } } },
      { search: "index_state=all&limit=2&offset=2" },
    );

    const pager = await screen.findByRole("navigation", { name: "Pagination" });
    expect(within(pager).getByRole("link", { name: "← Previous" })).toHaveAttribute(
      "href",
      `/dashboard/videos?${EMPTIES}&order=recency&limit=2&offset=0`,
    );
    expect(within(pager).getByRole("link", { name: "Next 2 →" })).toHaveAttribute(
      "href",
      `/dashboard/videos?${EMPTIES}&order=recency&limit=2&offset=4`,
    );
  });

  it("says which filter emptied the table, in the neutral tone, and offers the way out", async () => {
    await mount(
      {
        body: ran(
          { index_state: "failed" },
          { videos: [], total: 0, pagination: { limit: 50, offset: 0, has_more: false } },
        ),
      },
      { search: "index_state=failed" },
    );

    expect(await screen.findByText("Nothing matches those filters.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Nothing matches those filters." })).toHaveAttribute(
      "data-tone",
      "neutral",
    );
    expect(screen.getByText(/The state filter is on/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Show everything" })).toHaveAttribute(
      "href",
      "/dashboard/videos",
    );
  });

  it("offers the last page to a reader who paged off the end", async () => {
    await mount(
      {
        body: {
          ...OWNER_LIBRARY,
          videos: [],
          pagination: { limit: 2, offset: 900, has_more: false, last_offset: 2 },
        },
      },
      { search: "limit=2&offset=900" },
    );

    expect(await screen.findByText(/past the end of 4 matching video/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the last page" })).toHaveAttribute(
      "href",
      `/dashboard/videos?${EMPTIES}&order=recency&limit=2&offset=2`,
    );
  });

  it("gives the demo the browsable corpus whole", async () => {
    await mount({ body: DEMO_LIBRARY }, { search: "index_state=all", session: DEMO_SESSION });

    await screen.findByRole("table");
    expect(screen.getByRole("status")).toHaveTextContent("4 shown of 4.");
    expect(screen.getByRole("link", { name: "Let's build GPT: from scratch" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  describe("the row action", () => {
    const rowOf = () =>
      screen.getByRole("link", { name: "Let's build GPT: from scratch" }).closest("tr")!;

    it("queues a forced rebuild from the row, names the job, and focuses it", async () => {
      const { posts, fetcher } = await mount(
        { body: OWNER_LIBRARY },
        { search: "index_state=all" },
      );
      await screen.findByRole("table");

      await userEvent.click(within(rowOf()).getByRole("button", { name: "Re-index" }));

      expect(posts()[0].path).toBe("/dashboard/videos/kCc8FmEb1nY/reindex");
      expect(posts()[0].headers.get("accept")).toBe("application/json");
      const post = fetcher.mock.calls.find((call) => call[1]?.method === "POST");
      expect(post?.[1]?.credentials).toBe("same-origin");

      const job = await screen.findByRole("link", { name: "job_02e028870c97" });
      expect(job).toHaveAttribute("href", "/dashboard/jobs/job_02e028870c97");
      expect(rowOf().contains(document.activeElement)).toBe(true);
    });

    it("sends the reader to the panel that has room for two text fields", async () => {
      await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });
      await screen.findByRole("table");

      expect(within(rowOf()).getByRole("link", { name: "Tag" })).toHaveAttribute(
        "href",
        "/dashboard/videos/kCc8FmEb1nY#manage",
      );
    });

    it("prints the tool's refusal in the row it was refused for", async () => {
      await mount(
        { body: OWNER_LIBRARY },
        { search: "index_state=all", post: { status: 409, body: REINDEX_REFUSED } },
      );
      await screen.findByRole("table");

      await userEvent.click(within(rowOf()).getByRole("button", { name: "Re-index" }));

      expect(await within(rowOf()).findByText("E_INDEXING")).toBeInTheDocument();
      expect(
        within(rowOf()).getByText("kCc8FmEb1nY is already being indexed."),
      ).toBeInTheDocument();
    });

    it("disables the action where the database refuses writes", async () => {
      await mount(
        { body: OWNER_LIBRARY },
        { search: "index_state=all", session: { ...OWNER_SESSION, writes_allowed: false } },
      );
      await screen.findByRole("table");

      await vi.waitFor(() =>
        expect(screen.getAllByRole("button", { name: "Re-index" })[0]).toBeDisabled(),
      );
    });

    it("draws no action column at all in the projection", async () => {
      await mount({ body: DEMO_LIBRARY }, { search: "index_state=all", session: DEMO_SESSION });
      await screen.findByRole("table");

      expect(screen.queryByRole("columnheader", { name: "Actions" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Re-index" })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Tag" })).not.toBeInTheDocument();
    });
  });

  describe("when the read does not land", () => {
    it("keeps the band under a refused filter", async () => {
      await mount(
        {
          status: 400,
          body: {
            error: "E_ORDER_SCOPE",
            message: "order=relevance needs a query to be relevant to.",
            next: "pass q=…, or use order=recency.",
          },
        },
        { search: "order=relevance" },
      );

      const refusal = await screen.findByRole("region", {
        name: "order=relevance needs a query to be relevant to.",
      });
      expect(refusal).toHaveAttribute("data-tone", "bad");
      expect(screen.getByText("E_ORDER_SCOPE")).toBeInTheDocument();
      expect(screen.getByLabelText("Title, channel or description")).toBeInTheDocument();
      expect(screen.getByLabelText("Order")).toBeInTheDocument();
    });

    it("seeds the band from the filters a refusal echoed", async () => {
      await mount(
        {
          status: 400,
          body: {
            error: "E_ORDER_SCOPE",
            message: "order=relevance needs a query to be relevant to.",
            next: "pass q=…, or use order=recency.",
            filters: {
              ...OWNER_LIBRARY.filters,
              channel: "Andrej Karpathy",
              index_state: "failed",
              published_after: 1767225600,
            },
          },
        },
        {
          search: "order=relevance&channel=Karpathy&index_state=failed&published_after=2999-01-01",
        },
      );

      await screen.findByText("order=relevance needs a query to be relevant to.");
      expect(screen.getByLabelText("Channel")).toHaveValue("Andrej Karpathy");
      expect(screen.getByLabelText("Published on or after")).toHaveValue("2026-01-01");
      const head = within(screen.getByRole("heading", { name: "Videos" }).closest("div")!);
      expect(head.getByText("2026-01-01 – …")).toBeInTheDocument();
    });

    // A 500 takes the same page a 400 does: the query typed survives.
    it("keeps the band under a refusal that is not the filter's fault", async () => {
      await mount(
        {
          status: 500,
          body: { error: "E_INTERNAL", message: "The index could not be read.", next: null },
        },
        { search: "channel=Karpathy" },
      );

      expect(await screen.findByText("The index could not be read.")).toBeInTheDocument();
      expect(screen.getByText("E_INTERNAL")).toBeInTheDocument();
      expect(screen.getByLabelText("Channel")).toHaveValue("Karpathy");
    });

    it("prints the instance's own refusal when it is signed out, with no band", async () => {
      await mount({
        status: 401,
        body: {
          error: "E_AUTH_REQUIRED",
          message: "This dashboard needs the owner's password, token or session.",
          next: "Sign in at /dashboard/login.",
        },
      });

      expect(
        await screen.findByRole("heading", {
          name: "This dashboard needs the owner's password, token or session.",
        }),
      ).toBeInTheDocument();
      expect(screen.queryByLabelText("Channel")).not.toBeInTheDocument();
    });

    it("counts down a 429", async () => {
      await firstPaint(() =>
        mount({
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
          headers: { "retry-after": "9" },
        }),
      );

      expect(screen.getByRole("button", { name: "retry in 9s" })).toBeDisabled();
    });

    it("says so when the instance answers in a shape it cannot read", async () => {
      await mount({ body: { ...OWNER_LIBRARY, pagination: null } });

      expect(await screen.findByText(/shape this page cannot read/)).toBeInTheDocument();
    });
  });
});
