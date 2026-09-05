// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import {
  CANCEL_QUEUED,
  CLAMPED_JOBS,
  DEMO_JOBS,
  EMPTY_JOBS,
  FELL_BACK_JOBS,
  NARROWED_EMPTY_JOBS,
  OWNER_JOBS,
  REFUSAL,
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

    // The three durations are formatted here from the payload's seconds, never
    // read out of its `text` block.
    const running = rowOf("job_running001");
    expect(within(running).getByText("running")).toBeInTheDocument();
    expect(within(running).getByText("10%")).toBeInTheDocument();
    expect(within(running).getByText("0/2 done")).toBeInTheDocument();
    expect(within(running).getByText("20m 00s")).toBeInTheDocument();

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

    expect(await screen.findByText(/not open to this browser/)).toBeInTheDocument();
    expect(screen.getByText(/needs the owner's token or session/)).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(10_000);
    const reads = fetcher.mock.calls.filter((call) =>
      String(call[0]).startsWith("/dashboard/api/jobs"),
    );
    expect(reads).toHaveLength(1);
  });

  it("says which of the two empties it is looking at", async () => {
    await mount({ body: EMPTY_JOBS });
    expect(await screen.findByRole("heading", { name: "No jobs to show." })).toBeInTheDocument();
    expect(screen.getByText(/Nothing has ever been queued/)).toBeInTheDocument();

    vi.resetModules();
    vi.unstubAllGlobals();
    await mount({ body: NARROWED_EMPTY_JOBS }, { search: "state=failed" });
    expect(await screen.findByRole("heading", { name: "No jobs to show." })).toBeInTheDocument();
    expect(screen.getByText(/filters are narrowing it/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Show every job" })).toHaveAttribute(
      "href",
      "/dashboard/jobs",
    );
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
    expect(screen.getByLabelText("Rows")).toHaveAttribute("placeholder", "100");
  });

  // The narrowing strip, drawn from the values the listing ran with.
  it("names every filter that took rows out, and not the order", async () => {
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
    // An order takes no rows out, so it is not a narrowing.
    expect(head).not.toHaveTextContent("priority");
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
    expect(screen.getByLabelText("Rows")).toHaveAttribute("placeholder", "100");
  });

  it("submits the band as a navigation, dropping what says nothing", async () => {
    const { push } = await mount({ body: OWNER_JOBS });
    await screen.findByRole("status");

    await userEvent.selectOptions(screen.getByLabelText("State"), "failed");
    await userEvent.type(screen.getByLabelText("Error code"), "E_SOURCE");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    // `kind=all` and `order=newest` are the values the API would have used
    // anyway, and a link carrying them says nothing twice.
    expect(push).toHaveBeenCalledWith("/dashboard/jobs?state=failed&error_code=E_SOURCE");
  });

  it("pages with the pagination it was given", async () => {
    await mount(
      { body: { ...OWNER_JOBS, pagination: { limit: 25, offset: 25, has_more: true } } },
      { search: "offset=25" },
    );
    await screen.findByRole("status");

    expect(screen.getByRole("link", { name: /Newer/ })).toHaveAttribute(
      "href",
      "/dashboard/jobs?offset=0",
    );
    expect(screen.getByRole("link", { name: /Older 25/ })).toHaveAttribute(
      "href",
      "/dashboard/jobs?offset=50",
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
  });
});
