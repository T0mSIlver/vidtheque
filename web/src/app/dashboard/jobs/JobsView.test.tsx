// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import { firstPaint } from "@/test/retry";
import {
  CANCEL_QUEUED,
  CLAMPED_JOBS,
  DEMO_JOBS,
  EMPTY_JOBS,
  FELL_BACK_JOBS,
  NARROWED_EMPTY_JOBS,
  OWNER_JOBS,
  REFUSAL,
  RUNNING_JOB,
  SETTLED_JOBS,
} from "@/test/jobs-fixtures";

// The jobs table's job is to answer one question in one glance at 03:00: what
// is this box doing, and what is it waiting on. So the assertions are the two
// facts no other page carries — the countdown on a deferred job and the wall
// clock on a live one — the states the reader can land in with nothing to
// show, and the write column, which is present, absent or refused for three
// different reasons.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

/** One canned answer per read, or a queue of them when the test is about the
 *  tick: the second entry is what the next 2 s reading returns. */
async function mount(
  jobs: Route | Route[],
  { search = "", session = OWNER_SESSION }: { search?: string; session?: unknown } = {},
) {
  const answers = Array.isArray(jobs) ? [...jobs] : [jobs];
  const posts: { path: string; init: RequestInit }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") posts.push({ path: url, init });
    const route: Route = url.startsWith("/dashboard/api/jobs")
      ? answers.length > 1
        ? answers.shift()!
        : answers[0]
      : url === "/dashboard/api/session"
        ? { body: session }
        : init?.method === "POST"
          ? { body: CANCEL_QUEUED }
          : { status: 404, body: {} };
    const text = typeof route.body === "string" ? route.body : JSON.stringify(route.body ?? {});
    return new Response(text, {
      status: route.status ?? 200,
      headers: { "content-type": "application/json", ...route.headers },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  const { mockNavigation } = await import("@/test/next");
  const nav = mockNavigation(search, "/dashboard/jobs");
  const { Chrome } = await import("../Chrome");
  const { JobsView } = await import("./JobsView");
  render(
    <Chrome>
      <JobsView />
    </Chrome>,
  );
  return { ...nav, fetcher, posts };
}

/** The row a job's id is in, whatever column the assertion is about. The id is
 *  on the meta line under the headline, which is where the machine strings go. */
function rowOf(jobId: string) {
  return screen.getByText(jobId, { selector: "code" }).closest("tr") as HTMLElement;
}

describe("the jobs table", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
    vi.useRealTimers();
  });

  it("shows every job, its progress and what it cost so far", async () => {
    await mount({ body: OWNER_JOBS });

    expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("3 shown.");
    expect(screen.getAllByRole("row")).toHaveLength(4); // three jobs and the head

    // The three durations are formatted here from the payload's seconds. The
    // payload sends no rendered strings at all now, bar `basis`.
    const running = rowOf("job_running001");
    expect(within(running).getByText("running")).toBeInTheDocument();
    expect(within(running).getByText("10%")).toBeInTheDocument();
    expect(within(running).getByText("0/2 done")).toBeInTheDocument();
    expect(within(running).getByText("20m 00s")).toBeInTheDocument();
    // The tally is composed from the five counts; the sentence under it is the
    // one string the card carries, because it is policy and not a formatting.
    const hint = within(running).getByRole("tooltip");
    expect(hint).toHaveTextContent("0 done · 0 failed · 0 skipped · 0 cancelled · 2 still to run");
    expect(hint).toHaveTextContent("of 2 item(s).");

    const finished = rowOf("job_finished01");
    expect(within(finished).getByText("1/2 done · 1 failed")).toBeInTheDocument();
    expect(within(finished).getByText("1 degraded")).toBeInTheDocument();
  });

  // The line the incident was about: `defer_job` wrote `not_before`, nothing
  // read it back, and "deferred for another 240s" was true and invisible on
  // every surface vidtheque had.
  it("prints the countdown a deferred job is held by, and the code that set it", async () => {
    await mount({ body: OWNER_JOBS });
    await screen.findByRole("status");
    const row = rowOf("job_deferred01");

    expect(within(row).getByText(/held/)).toHaveTextContent("held 4m 00s more");
    expect(within(row).getByText("E_RATE_LIMIT")).toBeInTheDocument();
    expect(within(row).getByText("queued")).toBeInTheDocument();
  });

  // A job whose items have not been fetched has no title to print, and the row
  // says so with the count it does have — in the muted tone, because a count
  // set at the weight of a name reads as one.
  it("names an unfetched job with the sentence the payload wrote for it", async () => {
    await mount({ body: OWNER_JOBS });
    await screen.findByRole("status");

    const row = rowOf("job_deferred01");
    const headline = within(row).getByText("1 item(s), none fetched yet");
    expect(headline).toBeInTheDocument();
    expect(headline.className).toMatch(/muted/);
    // A job that did resolve prints the title, unmuted.
    const running = within(rowOf("job_running001")).getByText("Let's build GPT: from scratch");
    expect(running.className).not.toMatch(/muted/);
  });

  // A finished job's clock is a measurement; a running one's is still being
  // taken. Both come off the payload, and only one of them moves.
  it("ticks a live job's wall clock and leaves a finished one alone", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await mount({ body: OWNER_JOBS });
    await screen.findByRole("status");

    expect(within(rowOf("job_running001")).getByText("20m 00s")).toBeInTheDocument();
    expect(within(rowOf("job_finished01")).getByText("26m 40s")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);

    expect(within(rowOf("job_running001")).getByText("20m 03s")).toBeInTheDocument();
    expect(within(rowOf("job_finished01")).getByText("26m 40s")).toBeInTheDocument();
  });

  // The whole point of the page being live. The tick reads the same endpoint
  // and the row it patches is the row that changed.
  it("re-reads on the payload's own cadence and shows what changed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const moved = {
      ...OWNER_JOBS,
      jobs: OWNER_JOBS.jobs.map((job) =>
        job.job_id === "job_running001" ? { ...job, progress: 55, n_done: 1 } : job,
      ),
    };
    const { fetcher } = await mount([{ body: OWNER_JOBS }, { body: moved }]);
    await screen.findByRole("status");

    expect(within(rowOf("job_running001")).getByText("10%")).toBeInTheDocument();
    const reads = fetcher.mock.calls.filter((call) =>
      String(call[0]).startsWith("/dashboard/api/jobs"),
    );
    expect(reads).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(2000);

    expect(within(rowOf("job_running001")).getByText("55%")).toBeInTheDocument();
    expect(within(rowOf("job_running001")).getByText("1/2 done")).toBeInTheDocument();
  });

  // `jobs.js` patched the rows it could find; a job queued *since* the page
  // rendered has no row to patch, and a table that grows one under the reader
  // has a count line that has stopped being true. The note is what says so.
  it("patches the rows it has and says a job was queued since it loaded", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const arrived = {
      ...OWNER_JOBS,
      jobs: [
        { ...RUNNING_JOB, job_id: "job_brandnew01", progress: 0 },
        ...OWNER_JOBS.jobs.map((job) =>
          job.job_id === "job_running001" ? { ...job, progress: 55 } : job,
        ),
      ],
    };
    await mount([{ body: OWNER_JOBS }, { body: arrived }]);
    await screen.findByRole("status");
    expect(screen.getAllByRole("row")).toHaveLength(4);

    await vi.advanceTimersByTimeAsync(2000);

    // The three rows that were here are patched; the fourth is not invented.
    expect(within(rowOf("job_running001")).getByText("55%")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(4);
    expect(screen.getByRole("status")).toHaveTextContent("a job was queued since this page loaded");
  });

  // The baseline is the first reading, and on an idle box the first reading is
  // an empty one — which is the listing most likely to grow a job under the
  // reader, and the only one with no count line to hang the note on. So the
  // note is owed to the empty state too, or a page that loaded with nothing
  // never says anything arrived.
  it("says a job arrived on a listing that loaded with none", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await mount([
      { body: { ...EMPTY_JOBS, live: true } },
      { body: { ...OWNER_JOBS, jobs: [RUNNING_JOB] } },
    ]);
    expect(await screen.findByRole("heading", { name: "No jobs to show." })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(2000);

    expect(screen.getByRole("status")).toHaveTextContent("a job was queued since this page loaded");
    // Still the empty state: the row has no baseline to be patched into, and
    // inventing one would be the count line's lie in another place.
    expect(screen.getByRole("heading", { name: "No jobs to show." })).toBeInTheDocument();
  });

  // The per-second arithmetic is a reading of the last payload, so it stops
  // with the reading: a clock still counting against a payload nothing is
  // refreshing is a page inventing a measurement.
  it("stops the second-by-second clocks when the poll stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await mount([
      { body: OWNER_JOBS },
      { status: 500, body: { error: "E_INTERNAL", message: "the instance fell over." } },
    ]);
    await screen.findByRole("status");
    expect(within(rowOf("job_running001")).getByText("20m 00s")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(2000); // the refused tick, which stops it
    expect(screen.getByRole("status")).toHaveTextContent("the live view stopped");
    const wall = rowOf("job_running001").querySelector("td:last-child")?.textContent;
    const held = within(rowOf("job_deferred01")).getByText(/held/).textContent;

    await vi.advanceTimersByTimeAsync(5000);

    expect(rowOf("job_running001").querySelector("td:last-child")?.textContent).toBe(wall);
    expect(within(rowOf("job_deferred01")).getByText(/held/)).toHaveTextContent(held ?? "");
  });

  // `live` is the stop condition: when nothing is `queued|running` there is
  // nothing to poll for, and a tab that keeps asking is a load generator
  // against the process that also holds the only SQLite writer.
  it("stops reading once nothing is live, and says the page is a snapshot", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { fetcher } = await mount({ body: SETTLED_JOBS });
    await screen.findByRole("status");

    await vi.advanceTimersByTimeAsync(10_000);

    const reads = fetcher.mock.calls.filter((call) =>
      String(call[0]).startsWith("/dashboard/api/jobs"),
    );
    expect(reads).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("nothing is running");
  });

  // The limiter's own delay, obeyed. `jobs.js` stops on any refusal, which is
  // right for a script whose page is a complete server-rendered snapshot; this
  // page *is* the payload, so the one refusal that names a delay is waited out.
  it("backs off a 429 by its Retry-After and comes back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { fetcher } = await mount([
      { body: OWNER_JOBS },
      {
        status: 429,
        body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests for now." },
        headers: { "retry-after": "5" },
      },
      { body: OWNER_JOBS },
    ]);
    await screen.findByRole("status");
    const reads = () =>
      fetcher.mock.calls.filter((call) => String(call[0]).startsWith("/dashboard/api/jobs")).length;

    await vi.advanceTimersByTimeAsync(2000); // the refused tick
    expect(reads()).toBe(2);
    // The rows the last good reading produced are still on the page, with the
    // limiter's own sentence beside the count rather than in place of them.
    expect(screen.getByRole("status")).toHaveTextContent("the live view stopped");
    expect(screen.getByRole("status")).toHaveTextContent("Too many dashboard requests");
    expect(rowOf("job_running001")).toBeInTheDocument();

    // Not before the delay it named, and not much after.
    await vi.advanceTimersByTimeAsync(3000);
    expect(reads()).toBe(2);
    await vi.advanceTimersByTimeAsync(2000);
    expect(reads()).toBe(3);
  });

  // The other 429: the first read, with no earlier payload to keep on the page.
  // There is nothing to hold, so the page is the refusal — and the countdown on
  // it has to say the delay the limiter named. Under a stopped clock, because
  // `retryAfter ?? 60` is a fallback that counts down just as convincingly.
  it("counts down the limiter's own delay when the first read is refused", async () => {
    await firstPaint(() =>
      mount({
        status: 429,
        body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests for now." },
        headers: { "retry-after": "5" },
      }),
    );

    expect(screen.getByText("Too many dashboard requests for now.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "retry in 5s" })).toBeDisabled();
  });

  // The refusal is the signal, and the page renders its signed-out state
  // meanwhile. It does not keep polling a gate that just closed.
  it("stops on a 401 and says what it was told", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { fetcher } = await mount({
      status: 401,
      body: {
        error: "E_AUTH_REQUIRED",
        message: "The dashboard needs the owner's token or session.",
        next: "Sign in at /dashboard/login.",
      },
    });

    expect(await screen.findByText(/needs the owner's token or session/)).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(10_000);
    const reads = fetcher.mock.calls.filter((call) =>
      String(call[0]).startsWith("/dashboard/api/jobs"),
    );
    expect(reads).toHaveLength(1);
  });

  // `state` is the filter that empties this table, so it is the one the empty
  // state names — and the way out is the link that empties *it*, not the one
  // that quietly discards everything else the reader typed.
  it("says which of the two empties it is looking at", async () => {
    await mount({ body: EMPTY_JOBS });
    expect(await screen.findByRole("heading", { name: "No jobs to show." })).toBeInTheDocument();
    expect(screen.getByText(/Nothing has ever been queued/)).toBeInTheDocument();

    vi.resetModules();
    vi.unstubAllGlobals();
    await mount({ body: NARROWED_EMPTY_JOBS }, { search: "state=failed" });
    expect(await screen.findByRole("heading", { name: "No jobs to show." })).toBeInTheDocument();
    expect(screen.getByText(/The filter is on/)).toHaveTextContent("The filter is on failed.");
    expect(screen.getByRole("link", { name: "Show every job" })).toHaveAttribute(
      "href",
      "/dashboard/jobs?state=all",
    );
  });

  // A listing narrowed by something other than `state` is not the screen that
  // sentence describes: it would send the reader looking for a filter they
  // would then have to find.
  it("does not blame the state filter for an empty a kind filter made", async () => {
    await mount(
      { body: { ...EMPTY_JOBS, filters: { ...EMPTY_JOBS.filters, kind: "delete" } } },
      { search: "kind=delete" },
    );
    await screen.findByRole("heading", { name: "No jobs to show." });
    expect(screen.getByText(/Nothing has ever been queued/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Show every job" })).not.toBeInTheDocument();
  });

  // The `all` invariant, arriving on a payload with no form to echo into: the
  // strip names the filters that *ran*, and the sentence saying the asked-for
  // ones did not is Python's, rendered whole.
  it("narrows from the echo and prints the fallback the server took", async () => {
    await mount({ body: FELL_BACK_JOBS }, { search: "state=nonsense&degraded=maybe" });
    await screen.findByRole("status");

    expect(
      screen.getByText(/state='nonsense' is not one of all, active, failed, done/),
    ).toBeInTheDocument();
    expect(screen.getByText(/degraded='maybe' is not one of 1/)).toBeInTheDocument();
    // Neither value narrowed anything, so neither is on the strip: the words
    // in the URL were not the words the listing ran with.
    expect(screen.queryByText("nonsense")).not.toBeInTheDocument();
    expect(screen.queryByText("only")).not.toBeInTheDocument();
    // …and the empty state does not blame a filter that never ran.
    expect(screen.queryByText(/filters are narrowing it/)).not.toBeInTheDocument();
  });

  it("prints the clamp that moved both bounds, and pages on the number that answered", async () => {
    await mount({ body: CLAMPED_JOBS }, { search: "limit=100000&offset=99999999" });
    await screen.findByRole("heading", { name: "No jobs to show." });

    expect(screen.getByText(/clamped server-side: limit=100000 → 100/)).toBeInTheDocument();
    // The box echoes the page size that answered, not the one that was asked
    // for: a form still showing 100000 over a page of 100 is a form claiming a
    // bound that never ran.
    expect(screen.getByLabelText("Rows")).toHaveValue(100);
  });

  // The strip, drawn from the values the listing ran with.
  it("names what the listing ran with, filters and order alike", async () => {
    await mount({
      body: {
        ...OWNER_JOBS,
        filters: {
          state: "failed",
          kind: "index",
          error_code: "E_SOURCE",
          degraded: true,
          order: "priority",
        },
      },
    });
    await screen.findByRole("status");

    const head = screen.getByRole("heading", { name: "Jobs" }).closest("div") as HTMLElement;
    expect(head).toHaveTextContent("state failed");
    expect(head).toHaveTextContent("kind index");
    expect(head).toHaveTextContent("error code E_SOURCE");
    expect(head).toHaveTextContent("degraded only");
    expect(head).toHaveTextContent("order priority");
  });

  // The table is read *in an order*, and a listing narrowing nothing is still a
  // listing in an order: both facts are on `jobs.html`'s strip whatever they
  // say, because an order nobody prints is an order nobody can tell has moved.
  it("prints state and order even when neither narrows anything", async () => {
    await mount({ body: OWNER_JOBS });
    await screen.findByRole("status");

    const head = screen.getByRole("heading", { name: "Jobs" }).closest("div") as HTMLElement;
    expect(head).toHaveTextContent("state all");
    expect(head).toHaveTextContent("order newest");
    expect(head).not.toHaveTextContent("kind all");
  });

  // Every control is seeded from the answer rather than the question. A
  // `state=nonsense` that fell back to `all` server-side leaves a picker on
  // `all`: a band echoing the URL is a band vouching for a filter that never
  // ran.
  it("seeds the band from the filters the listing resolved", async () => {
    await mount(
      {
        body: {
          ...FELL_BACK_JOBS,
          filters: { ...FELL_BACK_JOBS.filters, error_code: "E_SOURCE" },
          pagination: { limit: 50, offset: 0, has_more: false },
        },
      },
      { search: "state=nonsense&error_code=E_SOURCE_and_a_very_long_tail&limit=50000" },
    );
    await screen.findByRole("status");

    expect(screen.getByLabelText("State")).toHaveValue("all");
    expect(screen.getByLabelText("Error code")).toHaveValue("E_SOURCE");
    expect(screen.getByLabelText("Rows")).toHaveValue(50);
  });

  // Every bound is Python's: what the reader typed goes on the wire as typed,
  // and the number the server accepted comes back in the Rows box.
  it("sends the URL's own filters, and only the ones the view takes", async () => {
    const { fetcher } = await mount(
      { body: { ...OWNER_JOBS, pagination: { limit: 100, offset: 0, has_more: true } } },
      { search: "state=failed&limit=100000&degraded=1&nonsense=1" },
    );
    await screen.findByRole("status");

    const read = fetcher.mock.calls.find((call) =>
      String(call[0]).startsWith("/dashboard/api/jobs"),
    );
    expect(String(read?.[0])).toBe("/dashboard/api/jobs?state=failed&degraded=1&limit=100000");
    expect(screen.getByLabelText("Rows")).toHaveValue(100);
  });

  it("submits the band as a navigation, dropping what says nothing", async () => {
    const { push } = await mount({ body: OWNER_JOBS });
    await screen.findByRole("status");

    await userEvent.selectOptions(screen.getByLabelText("State"), "failed");
    await userEvent.type(screen.getByLabelText("Error code"), "E_SOURCE");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    // `kind=all` and `order=newest` are the values the API would have used
    // anyway, and a link carrying them says nothing twice. `limit` is not one
    // of those: it is the page size the server accepted, echoed back out of the
    // box it was echoed into.
    expect(push).toHaveBeenCalledWith("/dashboard/jobs?state=failed&error_code=E_SOURCE&limit=25");
  });

  it("pages with the pagination it was given", async () => {
    await mount(
      { body: { ...OWNER_JOBS, pagination: { limit: 25, offset: 25, has_more: true } } },
      { search: "offset=25" },
    );
    await screen.findByRole("status");

    // All six parameters plus the offset, from the payload rather than the URL:
    // page four of a listing is only page four of *that* listing, so a pager
    // link that left the predicates to a default would point at another table.
    expect(screen.getByRole("link", { name: /Newer/ })).toHaveAttribute(
      "href",
      "/dashboard/jobs?state=all&kind=all&error_code=&degraded=0&order=newest&limit=25&offset=0",
    );
    expect(screen.getByRole("link", { name: /Older 25/ })).toHaveAttribute(
      "href",
      "/dashboard/jobs?state=all&kind=all&error_code=&degraded=0&order=newest&limit=25&offset=50",
    );
  });

  describe("the write column", () => {
    // A queued job settles now. That is the answer the poll could not have
    // given, which is why the route answers inline at all.
    it("cancels a live job and shows the state it is now in", async () => {
      const { posts } = await mount({ body: OWNER_JOBS });
      await screen.findByRole("status");
      const row = rowOf("job_deferred01");

      await userEvent.click(within(row).getByRole("button", { name: "Cancel" }));

      expect(await within(row).findByText("cancelled")).toBeInTheDocument();
      expect(posts).toHaveLength(1);
      expect(posts[0].path).toBe("/dashboard/jobs/job_deferred01/cancel");
      expect((posts[0].init.headers as Record<string, string>).accept).toBe("application/json");
      // The action is not repeatable, so the button does not come back.
      expect(within(row).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    });

    it("prints the refusal in the API's own words", async () => {
      vi.stubGlobal("fetch", undefined);
      vi.resetModules();
      const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST") {
          return new Response(JSON.stringify(REFUSAL), {
            status: 400,
            headers: { "content-type": "application/json" },
          });
        }
        const body = url === "/dashboard/api/session" ? OWNER_SESSION : OWNER_JOBS;
        return new Response(JSON.stringify(body), {
          headers: { "content-type": "application/json" },
        });
      });
      vi.stubGlobal("fetch", fetcher);
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard/jobs");
      const { Chrome } = await import("../Chrome");
      const { JobsView } = await import("./JobsView");
      render(
        <Chrome>
          <JobsView />
        </Chrome>,
      );

      await screen.findByRole("status");
      const row = rowOf("job_running001");
      await userEvent.click(within(row).getByRole("button", { name: "Cancel" }));

      expect(await within(row).findByText("E_BAD_PARAM")).toBeInTheDocument();
      expect(within(row).getByText(/already failed/)).toBeInTheDocument();
      // The third sentence, which says what to do instead. It is Python's, and
      // the Jinja refusal printed it under a heading of its own.
      expect(
        within(row).getByText("only queued or running jobs can be cancelled."),
      ).toBeInTheDocument();
    });

    // A route that exists and refuses is a route somebody probes, and a button
    // that 404s is worse UI than a button that is not there. So in the demo
    // projection the column is **absent**, not disabled.
    it("is not drawn at all where the deployment registers no write side", async () => {
      await mount({ body: DEMO_JOBS }, { session: DEMO_SESSION });
      await screen.findByRole("status");

      expect(screen.queryByRole("columnheader", { name: "action" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
      // The rows are all still there: §2.4 keeps the jobs view in the demo
      // because what indexing costs in time is what it is there to show.
      expect(screen.getAllByRole("row")).toHaveLength(4);
    });

    it("is absent for a signed-out reader of an instance that has one", async () => {
      await mount({ body: OWNER_JOBS }, { session: { ...OWNER_SESSION, write_side: false } });
      await screen.findByRole("status");
      expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    });
  });

  // The projection drops the prose and keeps the codes, the counts and every
  // clock — the demo's whole purpose here is showing what indexing costs.
  it("renders the projection without the operator's own box in it", async () => {
    await mount({ body: DEMO_JOBS }, { session: DEMO_SESSION });
    await screen.findByRole("status");

    expect(screen.queryByText(/cookiefile/)).not.toBeInTheDocument();
    expect(within(rowOf("job_deferred01")).getByText("E_RATE_LIMIT")).toBeInTheDocument();
    expect(within(rowOf("job_finished01")).getByText("26m 40s")).toBeInTheDocument();
    // The one line a reader of the demo cannot get anywhere else: what this
    // deployment does not publish. The listing carries no flag of its own, so
    // the fact comes off the session — the same deployment, answering.
    expect(
      screen.getByText("Source URLs and error text are not published on this instance."),
    ).toBeInTheDocument();
  });

  it("does not footnote a redaction an owner's instance is not making", async () => {
    await mount({ body: OWNER_JOBS });
    await screen.findByRole("status");
    expect(screen.queryByText(/are not published on this instance/)).not.toBeInTheDocument();
  });

  // The flag is the listing's own. An instance that predates it says the same
  // thing through the session, which is the same deployment answering.
  it("falls back to the session on an instance whose payload has no flag", async () => {
    await mount(
      { body: { ...DEMO_JOBS, redacted: undefined } },
      { session: { ...DEMO_SESSION, readonly: true } },
    );
    await screen.findByRole("status");
    expect(
      screen.getByText("Source URLs and error text are not published on this instance."),
    ).toBeInTheDocument();
  });
});
