// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import { REINDEX_REFUSED, REINDEXED } from "@/test/index-fixtures";
import { DEMO_LIBRARY, OWNER_LIBRARY, OWNER_LIBRARY_CLAMPED } from "@/test/library-fixtures";
import { firstPaint, settled } from "@/test/retry";
import { DEBOUNCE_MS, FOCUS_KEY } from "../search/band";

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
  {
    search = "",
    session = OWNER_SESSION as unknown,
    post = { body: REINDEXED } as Route,
  }: { search?: string; session?: unknown; post?: Route } = {},
) {
  const posts: { path: string; init: RequestInit }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") posts.push({ path: url, init });
    const route: Route =
      init?.method === "POST"
        ? post
        : url.startsWith("/dashboard/api/library")
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
  return { ...nav, fetcher, posts };
}

// The table as the server answered it. Every control, every fact on the
// narrowing strip and every link reads the payload's `filters` echo — the
// filters the query *ran* with, after the clamps, the choice fallbacks and the
// UTC-day snap — so a test about a filter has to put it on the payload and not
// only in the URL. That is the whole point of the echo: `?has=bogus` ran as
// `any`, and a page that printed `bogus` would be vouching for a query nobody
// made.
function ran(filters: Record<string, unknown>, rest: Record<string, unknown> = {}) {
  return { ...OWNER_LIBRARY, ...rest, filters: { ...OWNER_LIBRARY.filters, ...filters } };
}

// The nine keys `carried()` put on every link, as they come out on a table with
// nothing set: the two pickers resting on the API's own default come off, and
// the other seven ride along empty, because a key that disappears when its box
// is empty makes two URLs for one query.
const EMPTIES =
  "q=&channel=&tags=&published_after=&published_before=&indexed_after=&indexed_before=";

// The band's own listeners are the form's, delegated and native — the same
// three events `dashboard.js` bound — so the events are dispatched rather than
// typed. `userEvent` drives its own clock, and this one has to be stopped: what
// is being asserted is that nothing goes on the wire *until* 450ms after the
// last keystroke.

/** A band under a stopped clock, with the read landed. */
async function band(route: Route, options?: Parameters<typeof mount>[1]) {
  vi.useFakeTimers();
  const nav = await mount(route, options);
  await settled();
  return nav;
}

/** A keystroke in a text field: the event a half-typed box actually raises. */
function typed(label: string, value: string) {
  fireEvent.input(screen.getByLabelText(label), { target: { value } });
}

/** Let a debounce that is armed run out. */
async function pause(ms = DEBOUNCE_MS) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
  });
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
    // A new order is a new set in a new arrangement, so the offset goes — and
    // `carried()`'s nine keys ride along, so the range a reader set survives the
    // sort head as well as the pager.
    expect(screen.getByRole("link", { name: "Title" })).toHaveAttribute(
      "href",
      `/dashboard/videos?${EMPTIES}&order=title&limit=50`,
    );
  });

  // The five orders the tool takes and no sixth. An "unset" option would be the
  // one entry on this picker that does not name an order, on a page whose whole
  // job is to say which query ran.
  it("shows the order that ran, and offers no option that is not one", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "" });
    await screen.findByRole("status");

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

  // The Jinja form echoed the *accepted* limit back into the field the reader
  // typed it into: a hundred thousand asked for and a hundred granted is a
  // number the box has to say out loud, beside the note that names both.
  it("holds the page size the server accepted, not the one that was asked for", async () => {
    await mount({ body: OWNER_LIBRARY_CLAMPED }, { search: "limit=100000" });
    await screen.findByText(/limit=100000 → 100/);

    expect(screen.getByLabelText("Rows")).toHaveValue(100);
  });

  // `?has=bogus` ran as `has=any`. A strip that re-printed the URL would be the
  // page vouching for a filter that never applied, next to a `note:` saying
  // which one did.
  it("names the filter that ran, never the one in the URL bar", async () => {
    await mount({ body: OWNER_LIBRARY_CLAMPED }, { search: "has=banana" });
    await screen.findByText(/has='banana' is not one of/);

    const head = within(screen.getByRole("heading", { name: "Videos" }).closest("div")!);
    expect(head.queryByText("banana")).toBeNull();
    expect(screen.getByLabelText("Coverage")).toHaveValue("any");
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
      { body: ran({ published_after: 1767225600, index_state: "failed" }) },
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
          ...ran({ published_before: 1788652800 }), // 2026-09-06, exclusive
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

  // The band a reader lands on has no date on it, which is the case the four
  // re-keyed boxes have to survive: two ends of one range are siblings, and a
  // key that is only the value is one key for both of them when both are
  // empty. React answered that with "Encountered two children with the same
  // key, ``" on every render of the page against the real corpus, and its own
  // remedy for a duplicate is to drop a child.
  it("keys the two ends of a range apart when neither is set", async () => {
    const complaints = vi.spyOn(console, "error").mockImplementation(() => {});
    await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });
    await screen.findByRole("status");
    const said = complaints.mock.calls.map((call) => String(call[0]));
    complaints.mockRestore();

    expect(said.filter((line) => line.includes("same key"))).toEqual([]);
    // And all four boxes are still on the band: `getByLabelText` is the count,
    // because it refuses both a missing box and a second one.
    for (const label of [
      "Published on or after",
      "Published on or before",
      "Indexed on or after",
      "Indexed on or before",
    ]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
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

  // A picker has no half-made state: the moment it changes, the reader has said
  // what they want, and the change *is* the search.
  it("searches the moment a picker changes", async () => {
    const { push } = await band({ body: OWNER_LIBRARY }, { search: "" });

    fireEvent.change(screen.getByLabelText("Coverage"), { target: { value: "ocr" } });

    // Every key `carried()` carried, minus the picker still resting on the
    // API's own default. This URL is one somebody sends.
    expect(push).toHaveBeenCalledWith(
      `/dashboard/videos?q=&channel=&tags=&has=ocr&published_after=&published_before=` +
        `&indexed_after=&indexed_before=&order=recency&limit=50`,
    );
  });

  // A text field is half-typed for most of its life, so it waits for a pause —
  // and a page that searched per keystroke would put a request on the wire for
  // every letter of a channel name.
  it("searches a typed field when the typing stops, and not before", async () => {
    const { push } = await band({ body: OWNER_LIBRARY }, { search: "" });

    typed("Title, channel or description", "atten");
    await pause(DEBOUNCE_MS - 1);
    typed("Title, channel or description", "attention");
    await pause(DEBOUNCE_MS - 1);
    // Two keystrokes, no request: the pause is measured from the last one.
    expect(push).not.toHaveBeenCalled();

    await pause();
    expect(push).toHaveBeenCalledTimes(1);
    expect(push).toHaveBeenCalledWith(
      `/dashboard/videos?q=attention&channel=&tags=&published_after=&published_before=` +
        `&indexed_after=&indexed_before=&order=recency&limit=50`,
    );
  });

  // Enter still submits, and must not leave a debounce armed behind it: the
  // second search would be the same search, one page-load late.
  it("takes Enter as the search, and disarms what was pending", async () => {
    const { push } = await band({ body: OWNER_LIBRARY }, { search: "" });

    const channel = screen.getByLabelText("Channel");
    fireEvent.input(channel, { target: { value: "Karpathy" } });
    fireEvent.submit(channel.closest("form") as HTMLFormElement);
    expect(push).toHaveBeenCalledTimes(1);

    await pause(DEBOUNCE_MS * 2);
    expect(push).toHaveBeenCalledTimes(1);
  });

  // `Apply` is real, and is what a browser that never ran the band's script
  // submits with. It comes off the page only once the script has taken over.
  it("takes Apply off the band once it is doing the applying", async () => {
    await mount({ body: OWNER_LIBRARY }, { search: "" });
    await screen.findByRole("status");

    const apply = document.querySelector(
      "form:has(#f-q) button[type='submit']",
    ) as HTMLButtonElement;
    expect(apply).toHaveTextContent("Apply");
    expect(apply.hidden).toBe(true);
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
    // Reset is a link and not a submit, so it keeps its job: it is the one
    // control here that still has one when every change is already a search.
    expect(screen.getByRole("link", { name: "Reset" })).toHaveAttribute(
      "href",
      "/dashboard/videos",
    );
  });

  // The one thing a reloading search box owes its reader is the caret back.
  // `sessionStorage` and not the URL: which control had focus is not a fact
  // about the result set and has no business in a link somebody sends.
  it("remembers which control searched, and puts the caret back in it", async () => {
    const { push } = await band({ body: OWNER_LIBRARY }, { search: "" });

    typed("Channel", "Karpathy");
    await pause();
    expect(push).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(FOCUS_KEY)).toBe("f-channel");

    // The navigation that push stands for. The band comes up on a new form
    // node, and the caret comes back with it, at the end of what was typed.
    cleanup();
    vi.useRealTimers();
    vi.resetModules();
    await mount({ body: ran({ channel: "Karpathy" }) }, { search: "channel=Karpathy" });
    await screen.findByRole("status");

    const landed = screen.getByLabelText("Channel") as HTMLInputElement;
    expect(landed).toHaveValue("Karpathy");
    expect(landed).toHaveFocus();
    expect(landed.selectionStart).toBe("Karpathy".length);
    // Released once the answer has landed, so the next page opened in this tab
    // does not inherit somebody else's caret.
    expect(sessionStorage.getItem(FOCUS_KEY)).toBeNull();
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

  it("says which filter emptied the table, and offers the way out", async () => {
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
      `/dashboard/videos?${EMPTIES}&order=recency&limit=2&offset=2`,
    );
  });

  it("gives the demo the browsable corpus whole", async () => {
    await mount({ body: DEMO_LIBRARY }, { search: "index_state=all", session: DEMO_SESSION });

    expect(await screen.findByRole("status")).toHaveTextContent("4 shown of 4.");
    expect(screen.getByRole("link", { name: "Let's build GPT: from scratch" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  // One control and one link per row, present exactly where the write routes
  // are registered. Not a disabled column in the projection: a control that
  // cannot work is worse UI than no column (§2.4).
  describe("the row action", () => {
    it("queues a forced rebuild from the row, and names the job", async () => {
      const { posts } = await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });
      await screen.findByRole("table");

      const row = screen.getByRole("link", { name: "Let's build GPT: from scratch" }).closest("tr");
      await userEvent.click(within(row!).getByRole("button", { name: "Re-index" }));

      expect(posts[0].path).toBe("/dashboard/videos/kCc8FmEb1nY/reindex");
      expect(posts[0].init.method).toBe("POST");
      expect((posts[0].init.headers as Record<string, string>).accept).toBe("application/json");
      expect(posts[0].init.credentials).toBe("same-origin");

      expect(await screen.findByRole("link", { name: "job_02e028870c97" })).toHaveAttribute(
        "href",
        "/dashboard/jobs/job_02e028870c97",
      );
    });

    it("sends the reader to the panel that has room for two text fields", async () => {
      await mount({ body: OWNER_LIBRARY }, { search: "index_state=all" });
      await screen.findByRole("table");

      const row = screen.getByRole("link", { name: "Let's build GPT: from scratch" }).closest("tr");
      expect(within(row!).getByRole("link", { name: "Tag" })).toHaveAttribute(
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

      const row = screen.getByRole("link", { name: "Let's build GPT: from scratch" }).closest("tr");
      await userEvent.click(within(row!).getByRole("button", { name: "Re-index" }));

      expect(await within(row!).findByText("E_INDEXING")).toBeInTheDocument();
      expect(within(row!).getByText("kCc8FmEb1nY is already being indexed.")).toBeInTheDocument();
    });

    it("disables the action where the database refuses writes", async () => {
      await mount(
        { body: OWNER_LIBRARY },
        { search: "index_state=all", session: { ...OWNER_SESSION, writes_allowed: false } },
      );
      await screen.findByRole("table");

      expect(screen.getAllByRole("button", { name: "Re-index" })[0]).toBeDisabled();
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
      expect(screen.getByLabelText("Order")).toBeInTheDocument();
    });

    // The Jinja view rendered the band with *every* refusal it owned, whatever
    // the status: a 500 takes the same page a 400 does, and a reader who lost
    // the whole band to one has lost the query they typed as well.
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

    // Under a stopped clock: the label has to be the delay the limiter named,
    // and a page that halved it would count down just as convincingly.
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
