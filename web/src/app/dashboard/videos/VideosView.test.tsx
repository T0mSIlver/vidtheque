// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import { DEMO_LIBRARY, OWNER_LIBRARY, OWNER_LIBRARY_CLAMPED } from "@/test/library-fixtures";

// The table's job is to say what set it is showing and to be honest about how
// it was narrowed: the filters are the URL, the count is exact, the clamps are
// Python's and arrive as sentences, and every column head that sorts says which
// way. So the assertions are the round trips — a filter typed into the band
// becomes a URL, a pager link carries the query with one number changed — and
// the states a reader can land in with nothing to show.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

// Routed by prefix rather than by the exact URL: the query string this page
// sends is ordered by the contract's own parameter list, not by whatever order
// the reader's URL happened to hold them in, and a test that spelled the
// expected order into every stub key would be asserting it eight times by
// accident. One test asserts it deliberately, off the fetch spy.
async function mount(
  library: Route,
  { search = "", session = OWNER_SESSION }: { search?: string; session?: unknown } = {},
) {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const route: Route = url.startsWith("/dashboard/api/library")
      ? library
      : url === "/dashboard/api/session"
        ? { body: session }
        : { status: 404, body: {} };
    const text = typeof route.body === "string" ? route.body : JSON.stringify(route.body ?? {});
    return new Response(text, {
      status: route.status ?? 200,
      headers: { "content-type": "application/json", ...route.headers },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  const { mockNavigation } = await import("@/test/next");
  const nav = mockNavigation(search, "/dashboard/videos");
  const { Chrome } = await import("../Chrome");
  const { VideosView } = await import("./VideosView");
  render(
    <Chrome>
      <VideosView />
    </Chrome>,
  );
  return { ...nav, fetcher };
}

// The table with a date bound echoed back on it. The dates on the wire are the
// epochs the query ran on, and the page reads its own pickers out of them —
// so a test about a date filter has to put one on the payload, not just in the
// URL, which is the whole point of the echo.
function dated(filters: Record<string, number>) {
  return { ...OWNER_LIBRARY, filters: { ...OWNER_LIBRARY.filters, ...filters } };
}

describe("the videos table", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("shows the corpus, its exact count, and one row per video", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });

    expect(await screen.findByRole("heading", { name: "Videos" })).toBeInTheDocument();
    // Exact, not the tool's `~` probe: a tilde over a table with a Next button
    // is the one thing on the line a reader cannot act on.
    expect(screen.getByRole("status")).toHaveTextContent("4 shown of 4.");
    expect(screen.getAllByRole("row")).toHaveLength(5); // four videos and the head

    const row = screen.getByRole("link", { name: "Let's build GPT: from scratch" });
    expect(row).toHaveAttribute("href", "/dashboard/videos/kCc8FmEb1nY");
    // A row with no keyframe yet says so rather than leaving a hole where a
    // still would be.
    expect(screen.getAllByText("no frame")).toHaveLength(2);
  });

  // Every bound is Python's: what the reader typed goes on the wire exactly as
  // typed, and the clamp comes back as a sentence. A value "helpfully"
  // corrected here would be a clamp nobody is ever told about.
  it("sends the URL's own filters, and only the ones the contract takes", async () => {
    const { fetcher } = await mount(
      { body: OWNER_LIBRARY },
      { search: "index_state=all&limit=100000&nonsense=1" },
    );
    await screen.findByRole("status");

    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/library?index_state=all&limit=100000",
      expect.objectContaining({ credentials: "same-origin", cache: "no-store" }),
    );
  });

  it("draws the state as its own word and coverage as three legs", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });

    // Scoped to the table: the same five words are the state picker's options,
    // and a bare query would be counting the band as well as the rows.
    const table = within(await screen.findByRole("table"));
    expect(table.getByText("indexing")).toBeInTheDocument();
    expect(table.getAllByText("ready")).toHaveLength(3);
    // The `t/o/f` letters, with the word behind each for anyone not reading
    // letters. The half-indexed video has none of the three.
    expect(table.getAllByText("transcript: present")).toHaveLength(3);
    expect(table.getAllByText("frame embeddings: missing")).toHaveLength(3);
  });

  it("names the sorted column and links every other head to its own order", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });

    // `order` is echoed by the payload — never inferred from the URL, which
    // may not carry one at all.
    const published = await screen.findByRole("columnheader", { name: "Published" });
    expect(published).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("columnheader", { name: "Title" })).not.toHaveAttribute("aria-sort");
    // A new order is a new set in a new arrangement, so the offset goes.
    expect(screen.getByRole("link", { name: "Title" })).toHaveAttribute(
      "href",
      "/dashboard/videos?index_state=all&order=title",
    );
  });

  // The Jinja page echoed an accepted `limit` back into the field the reader
  // typed it into. A JSON caller has no form, so the sentence rides on `notes`.
  it("prints the clamp Python applied, in Python's words", async () => {
    await mount({ body: OWNER_LIBRARY_CLAMPED }, { search: "limit=100000&has=banana&order=nope" });

    expect(await screen.findByText(/limit=100000 → 100/)).toBeInTheDocument();
    expect(screen.getByText(/has='banana' is not one of/)).toBeInTheDocument();
    expect(screen.getByText(/order='nope' is not one of/)).toBeInTheDocument();
  });

  it("puts what is narrowing the table on the title's own baseline", async () => {
    await mount(
      { body: dated({ published_after: 1767225600 }) },
      { search: "index_state=failed&published_after=2026-01-01" },
    );

    const head = within((await screen.findByRole("heading", { name: "Videos" })).closest("div")!);
    expect(head.getByText("failed")).toBeInTheDocument();
    // An open end is `…`, not an invented boundary.
    expect(head.getByText("2026-01-01 – …")).toBeInTheDocument();
  });

  // The server clamps every date bound and snaps it to a UTC day before it
  // filters, and says so. The page must show the filter that ran: a picker
  // still holding `2999-01-01` beside a note naming a different day is the
  // page vouching for a query nobody made.
  it("shows the day a clamped date became, in the box and on the baseline", async () => {
    await mount(
      {
        body: {
          ...dated({ published_before: 1788652800 }), // 2026-09-06, exclusive
          notes: [
            "note: resolved server-side: published_before=2999-01-01 → 2026-09-05. " +
              "Each bound is filtered as a whole UTC day, inside a floor of " +
              "1970-01-01 and a ceiling a year from now; the day named here is " +
              "the one that ran.",
          ],
        },
      },
      { search: "published_before=2999-01-01" },
    );

    expect(await screen.findByText(/published_before=2999-01-01 → 2026-09-05/)).toBeInTheDocument();
    // `_before` is exclusive, so the day the reader asked for is the echo's
    // own day less one — and that is what the picker holds.
    expect(screen.getByLabelText("Published on or before")).toHaveValue("2026-09-05");
    expect(screen.getByLabelText("Published on or after")).toHaveValue("");
    const head = within(screen.getByRole("heading", { name: "Videos" }).closest("div")!);
    expect(head.getByText("… – 2026-09-05")).toBeInTheDocument();
  });

  // The rows box has no ceiling of its own: that number is
  // `OWNER_CLAMPS.videos_max_limit`, which this page has no copy of and which
  // a deployment may have moved. The clamp and its note are the control.
  it("leaves the page size to the server's own ceiling", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });

    const rows = await screen.findByLabelText("Rows");
    expect(rows).toHaveAttribute("min", "1");
    expect(rows).not.toHaveAttribute("max");
  });

  it("turns the band into a URL on submit, and drops what is empty", async () => {
    const nav = await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });
    await screen.findByRole("status");

    await userEvent.type(screen.getByLabelText("Title, channel or description"), "attention");
    await userEvent.selectOptions(screen.getByLabelText("Coverage"), "ocr");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    // No `&channel=&tags=&published_after=`, and no `index_state=all` either —
    // an empty control is not a filter and neither is a picker resting on the
    // value the API would have used anyway. This URL is one somebody sends.
    expect(nav.push).toHaveBeenCalledWith("/dashboard/videos?q=attention&has=ocr");
  });

  it("pages with the query carried and one number changed", async () => {
    await mount(
      { body: { ...OWNER_LIBRARY, pagination: { limit: 2, offset: 2, has_more: true } } },
      { search: "index_state=all&limit=2&offset=2" },
    );

    const pager = await screen.findByRole("navigation", { name: "Pagination" });
    expect(within(pager).getByRole("link", { name: "← Previous" })).toHaveAttribute(
      "href",
      "/dashboard/videos?index_state=all&limit=2&offset=0",
    );
    expect(within(pager).getByRole("link", { name: "Next 2 →" })).toHaveAttribute(
      "href",
      "/dashboard/videos?index_state=all&limit=2&offset=4",
    );
  });

  it("says which filter emptied the table, and offers the way out", async () => {
    await mount(
      {
        body: {
          ...OWNER_LIBRARY,
          videos: [],
          total: 0,
          pagination: { limit: 50, offset: 0, has_more: false },
        },
      },
      { search: "index_state=failed" },
    );

    expect(await screen.findByText("Nothing matches those filters.")).toBeInTheDocument();
    expect(screen.getByText(/The state filter is on/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Show everything" })).toHaveAttribute(
      "href",
      "/dashboard/videos",
    );
  });

  // `last_offset` arrives exactly when the offset asked for ran past the end,
  // and it is where the last page starts.
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
      "/dashboard/videos?limit=2&offset=2",
    );
  });

  it("gives the demo the browsable corpus whole", async () => {
    await mount({ body: DEMO_LIBRARY }, { search: "index_state=all", session: DEMO_SESSION });

    expect(await screen.findByRole("status")).toHaveTextContent("4 shown of 4.");
    expect(screen.getByRole("link", { name: "Let's build GPT: from scratch" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  describe("when the read does not land", () => {
    // `order=relevance` without a `q` is the tool's own refusal, and it is a
    // filter the reader can fix — so the band stays on the page with the other
    // seven controls in it.
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

      expect(
        await screen.findByText("order=relevance needs a query to be relevant to."),
      ).toBeInTheDocument();
      expect(screen.getByText("E_ORDER_SCOPE")).toBeInTheDocument();
      expect(screen.getByLabelText("Title, channel or description")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
    });

    it("prints the instance's own refusal when it is signed out", async () => {
      await mount({
        status: 401,
        body: {
          error: "E_AUTH_REQUIRED",
          message: "This dashboard needs the owner's password, token or session.",
          next: "Sign in at /dashboard/login.",
        },
      });

      expect(
        await screen.findByText("This dashboard needs the owner's password, token or session."),
      ).toBeInTheDocument();
      // No band: there is no filter to fix, and a control bar over a refusal is
      // an invitation to a page this browser cannot read.
      expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
    });

    it("counts down a 429", async () => {
      await mount({
        status: 429,
        body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
        headers: { "retry-after": "9" },
      });

      expect(await screen.findByRole("button", { name: "retry in 9s" })).toBeDisabled();
    });

    it("says so when the instance answers in a shape it cannot read", async () => {
      await mount({ body: { ...OWNER_LIBRARY, pagination: null } });

      expect(await screen.findByText(/shape this page cannot read/)).toBeInTheDocument();
    });
  });
});
