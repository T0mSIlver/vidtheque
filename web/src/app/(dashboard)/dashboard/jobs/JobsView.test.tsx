// @vitest-environment jsdom
import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  deferred,
  fullText,
  mountDashboard,
  type Answer,
  type MountOptions,
  type Route,
} from "@/test/dashboard";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import {
  CANCEL_QUEUED,
  CLAMPED_JOBS,
  DEMO_JOBS,
  EMPTY_JOBS,
  FELL_BACK_JOBS,
  FINISHED_JOB,
  NARROWED_EMPTY_JOBS,
  OWNER_JOBS,
  REFUSAL,
  RUNNING_JOB,
  SETTLED_JOBS,
} from "@/test/jobs-fixtures";
import { firstPaint } from "@/test/retry";
import { JobsView } from "./JobsView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

/** One canned answer per read, or a queue of them when the test is about the
 *  tick: the second entry is what the next 2 s reading returns. */
function mount(jobs: Route, options: Omit<MountOptions, "routes"> & { cancel?: Answer } = {}) {
  return mountDashboard(<JobsView />, {
    path: "/dashboard/jobs",
    ...options,
    routes: {
      "/dashboard/api/jobs": jobs,
      "POST /dashboard/jobs/*": options.cancel ?? { body: CANCEL_QUEUED },
    },
  });
}

/** The row a job's id is in; the id is on the meta line under the headline. */
function rowOf(jobId: string) {
  return screen.getByText(jobId, { selector: "code" }).closest("tr") as HTMLElement;
}

function cellOf(row: HTMLElement, label: string) {
  return row.querySelector(`[data-label="${label}"]`) as HTMLElement;
}

describe("the jobs table", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows every job, its progress and what it cost so far", async () => {
    await mount({ body: OWNER_JOBS });

    expect(await screen.findByText(fullText(/^3 shown\./))).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Jobs" })).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(4);

    const running = rowOf("job_running001");
    expect(within(running).getByText("running")).toBeInTheDocument();
    expect(within(running).getByText("10%")).toBeInTheDocument();
    expect(within(running).getByText("0/2 done")).toBeInTheDocument();
    expect(within(running).getByText("20m 00s")).toBeInTheDocument();
    // The tally is composed here; `basis` is policy text and is read.
    const hint = within(running).getByRole("tooltip");
    expect(hint).toHaveTextContent("0 done · 0 failed · 0 skipped · 0 cancelled · 2 still to run");
    expect(hint).toHaveTextContent("of 2 item(s).");

    const finished = rowOf("job_finished01");
    expect(within(finished).getByText("1/2 done · 1 failed")).toBeInTheDocument();
    expect(within(finished).getByText("1 degraded")).toBeInTheDocument();
  });

  it("prints the countdown a deferred job is held by, and the code that set it", async () => {
    await mount({ body: OWNER_JOBS });
    await screen.findByText(fullText(/^3 shown\./));
    const row = rowOf("job_deferred01");

    expect(within(row).getByText(/held/)).toHaveTextContent("held 4m 00s more");
    expect(within(row).getByText("E_RATE_LIMIT")).toBeInTheDocument();
    expect(within(row).getByText("queued")).toBeInTheDocument();
  });

  it("names an unfetched job with the sentence the payload wrote for it, muted", async () => {
    await mount({ body: OWNER_JOBS });
    await screen.findByText(fullText(/^3 shown\./));

    const headline = within(rowOf("job_deferred01")).getByText("1 item(s), none fetched yet");
    expect(headline).toHaveAttribute("data-tone", "muted");
    const titled = within(rowOf("job_running001")).getByText("Let's build GPT: from scratch");
    expect(titled).not.toHaveAttribute("data-tone");
  });

  it("ticks a live job's wall clock and leaves a finished one alone", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await mount({ body: OWNER_JOBS });
    await screen.findByText(fullText(/^3 shown\./));

    expect(within(rowOf("job_running001")).getByText("20m 00s")).toBeInTheDocument();
    expect(within(rowOf("job_finished01")).getByText("26m 40s")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(3000));

    expect(within(rowOf("job_running001")).getByText("20m 03s")).toBeInTheDocument();
    expect(within(rowOf("job_finished01")).getByText("26m 40s")).toBeInTheDocument();
  });

  it("re-reads on the payload's own cadence and shows what changed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const moved = {
      ...OWNER_JOBS,
      jobs: OWNER_JOBS.jobs.map((job) =>
        job.job_id === "job_running001" ? { ...job, progress: 55, n_done: 1 } : job,
      ),
    };
    const { calls } = await mount([{ body: OWNER_JOBS }, { body: moved }]);
    await screen.findByText(fullText(/^3 shown\./));

    expect(within(rowOf("job_running001")).getByText("10%")).toBeInTheDocument();
    expect(calls("/dashboard/api/jobs")).toHaveLength(1);

    await act(() => vi.advanceTimersByTimeAsync(2000));

    expect(within(rowOf("job_running001")).getByText("55%")).toBeInTheDocument();
    expect(within(rowOf("job_running001")).getByText("1/2 done")).toBeInTheDocument();
  });

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
    await screen.findByText(fullText(/^3 shown\./));
    expect(screen.getAllByRole("row")).toHaveLength(4);

    await act(() => vi.advanceTimersByTimeAsync(2000));

    // The three rows are patched; the fourth is not invented.
    expect(within(rowOf("job_running001")).getByText("55%")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(4);
    expect(screen.getByText(/a job was queued since this page loaded/)).toBeInTheDocument();
  });

  it("says a job arrived on a listing that loaded with none", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await mount([
      { body: { ...EMPTY_JOBS, live: true } },
      { body: { ...OWNER_JOBS, jobs: [RUNNING_JOB] } },
    ]);
    expect(await screen.findByRole("heading", { name: "No jobs to show." })).toBeInTheDocument();
    expect(screen.queryByText(/queued since this page loaded/)).not.toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(2000));

    expect(screen.getByText(/a job was queued since this page loaded/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "No jobs to show." })).toBeInTheDocument();
  });

  // The stale-baseline bug: a new listing is never patched onto the rows of the
  // previous one, and never claims a job arrived.
  it("shows only the new listing's rows when the filter changes inside one mount", async () => {
    const failed = deferred<Answer>();
    const { navigate } = await mount((request) =>
      request.url.includes("state=failed") ? failed.promise : { body: OWNER_JOBS },
    );
    await screen.findByText(fullText(/^3 shown\./));

    await navigate("/dashboard/jobs?state=failed");
    expect(screen.queryByText("job_running001", { selector: "code" })).not.toBeInTheDocument();
    expect(screen.queryByText(/queued since this page loaded/)).not.toBeInTheDocument();

    await act(async () =>
      failed.resolve({
        body: {
          ...OWNER_JOBS,
          live: false,
          jobs: [FINISHED_JOB],
          filters: { ...OWNER_JOBS.filters, state: "failed" },
        },
      }),
    );
    expect(await screen.findByText(fullText(/^1 shown\./))).toBeInTheDocument();
    expect(rowOf("job_finished01")).toBeInTheDocument();
    expect(screen.queryByText("job_deferred01", { selector: "code" })).not.toBeInTheDocument();
    expect(screen.queryByText(/queued since this page loaded/)).not.toBeInTheDocument();
  });

  it("stops the second-by-second clocks when the poll stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await mount([
      { body: OWNER_JOBS },
      { status: 500, body: { error: "E_INTERNAL", message: "the instance fell over." } },
    ]);
    await screen.findByText(fullText(/^3 shown\./));
    expect(within(rowOf("job_running001")).getByText("20m 00s")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText(/the live view stopped/)).toBeInTheDocument();
    const wall = cellOf(rowOf("job_running001"), "Wall clock").textContent;
    const held = within(rowOf("job_deferred01")).getByText(/held/).textContent;

    await act(() => vi.advanceTimersByTimeAsync(5000));

    expect(cellOf(rowOf("job_running001"), "Wall clock")).toHaveTextContent(wall ?? "");
    expect(within(rowOf("job_deferred01")).getByText(/held/)).toHaveTextContent(held ?? "");
  });

  it("stops reading once nothing is live, and says the page is a snapshot", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls } = await mount({ body: SETTLED_JOBS });
    await screen.findByText(/nothing is running/);

    await act(() => vi.advanceTimersByTimeAsync(10_000));

    expect(calls("/dashboard/api/jobs")).toHaveLength(1);
    expect(screen.getByText(/nothing is running, so this is a snapshot/)).toBeInTheDocument();
  });

  it("backs off a 429 by its Retry-After and comes back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls } = await mount([
      { body: OWNER_JOBS },
      {
        status: 429,
        body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests for now." },
        headers: { "retry-after": "5" },
      },
      { body: OWNER_JOBS },
    ]);
    await screen.findByText(fullText(/^3 shown\./));

    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(calls("/dashboard/api/jobs")).toHaveLength(2);
    // The last good rows stay, with the limiter's sentence beside the count.
    expect(screen.getByText(/Too many dashboard requests/)).toBeInTheDocument();
    expect(rowOf("job_running001")).toBeInTheDocument();

    // Not before the delay it named, and not much after.
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(calls("/dashboard/api/jobs")).toHaveLength(2);
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(calls("/dashboard/api/jobs")).toHaveLength(3);
  });

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

  it("stops on a 401 and says what it was told", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls } = await mount({
      status: 401,
      body: {
        error: "E_AUTH_REQUIRED",
        message: "The dashboard needs the owner's token or session.",
        next: "Sign in at /dashboard/login.",
      },
    });

    expect(await screen.findByText(/needs the owner's token or session/)).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(calls("/dashboard/api/jobs")).toHaveLength(1);
  });

  it("says which of the two empties it is looking at", async () => {
    const { unmount } = await mount({ body: EMPTY_JOBS });
    expect(await screen.findByRole("heading", { name: "No jobs to show." })).toBeInTheDocument();
    expect(screen.getByText(/Nothing has ever been queued/)).toBeInTheDocument();
    unmount();

    await mount({ body: NARROWED_EMPTY_JOBS }, { search: "state=failed" });
    expect(await screen.findByText(/The filter is on/)).toHaveTextContent(
      "The filter is on failed.",
    );
    expect(screen.getByRole("link", { name: "Show every job" })).toHaveAttribute(
      "href",
      "/dashboard/jobs?state=all",
    );
  });

  it("draws the empty state in the neutral tone", async () => {
    await mount({ body: EMPTY_JOBS });
    await screen.findByRole("heading", { name: "No jobs to show." });
    expect(screen.getByRole("region", { name: "No jobs to show." })).toHaveAttribute(
      "data-tone",
      "neutral",
    );
  });

  it("does not blame the state filter for an empty a kind filter made", async () => {
    await mount(
      { body: { ...EMPTY_JOBS, filters: { ...EMPTY_JOBS.filters, kind: "delete" } } },
      { search: "kind=delete" },
    );
    await screen.findByRole("heading", { name: "No jobs to show." });
    expect(screen.getByText(/Nothing has ever been queued/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Show every job" })).not.toBeInTheDocument();
  });

  it("narrows from the echo and prints the fallback the server took", async () => {
    await mount({ body: FELL_BACK_JOBS }, { search: "state=nonsense&degraded=maybe" });

    expect(
      await screen.findByText(/state='nonsense' is not one of all, active, failed, done/),
    ).toBeInTheDocument();
    expect(screen.getByText(/degraded='maybe' is not one of 1/)).toBeInTheDocument();
    expect(screen.queryByText("nonsense")).not.toBeInTheDocument();
    expect(screen.queryByText("only")).not.toBeInTheDocument();
    expect(screen.queryByText(/filters are narrowing it/)).not.toBeInTheDocument();
  });

  it("prints the clamp that moved both bounds, and pages on the number that answered", async () => {
    await mount({ body: CLAMPED_JOBS }, { search: "limit=100000&offset=99999999" });
    await screen.findByRole("heading", { name: "No jobs to show." });

    expect(screen.getByText(/clamped server-side: limit=100000 → 100/)).toBeInTheDocument();
    expect(screen.getByLabelText("Rows")).toHaveValue(100);
  });

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
    await screen.findByText(fullText(/^3 shown\./));

    const head = screen.getByRole("heading", { name: "Jobs" }).closest("div") as HTMLElement;
    expect(head).toHaveTextContent("state failed");
    expect(head).toHaveTextContent("kind index");
    expect(head).toHaveTextContent("error code E_SOURCE");
    expect(head).toHaveTextContent("degraded only");
    expect(head).toHaveTextContent("order priority");
  });

  it("prints state and order even when neither narrows anything", async () => {
    await mount({ body: OWNER_JOBS });
    await screen.findByText(fullText(/^3 shown\./));

    const head = screen.getByRole("heading", { name: "Jobs" }).closest("div") as HTMLElement;
    expect(head).toHaveTextContent("state all");
    expect(head).toHaveTextContent("order newest");
    expect(head).not.toHaveTextContent("kind all");
  });

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
    await screen.findByText(/state='nonsense'/);

    expect(screen.getByLabelText("State")).toHaveValue("all");
    expect(screen.getByLabelText("Error code")).toHaveValue("E_SOURCE");
    expect(screen.getByLabelText("Rows")).toHaveValue(50);
  });

  it("sends the URL's own filters, and only the ones the view takes", async () => {
    const { calls } = await mount(
      { body: { ...OWNER_JOBS, pagination: { limit: 100, offset: 0, has_more: true } } },
      { search: "state=failed&limit=100000&degraded=1&nonsense=1" },
    );
    await screen.findByText(fullText(/^3 shown/));

    expect(calls("/dashboard/api/jobs")[0].url).toBe(
      "/dashboard/api/jobs?state=failed&degraded=1&limit=100000",
    );
    expect(screen.getByLabelText("Rows")).toHaveValue(100);
  });

  it("submits the band as a navigation, dropping what says nothing, and keeps focus", async () => {
    const { push } = await mount({ body: OWNER_JOBS });
    await screen.findByText(fullText(/^3 shown\./));

    await userEvent.selectOptions(screen.getByLabelText("State"), "failed");
    await userEvent.type(screen.getByLabelText("Error code"), "E_SOURCE");
    const apply = screen.getByRole("button", { name: "Apply" });
    await userEvent.click(apply);

    expect(push).toHaveBeenCalledWith("/dashboard/jobs?state=failed&error_code=E_SOURCE&limit=25", {
      scroll: false,
    });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Apply" }));
  });

  it("pages with the pagination it was given", async () => {
    await mount(
      { body: { ...OWNER_JOBS, pagination: { limit: 25, offset: 25, has_more: true } } },
      { search: "offset=25" },
    );
    await screen.findByText(fullText(/^3 shown/));

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
    it("cancels a live job and shows the state it is now in", async () => {
      const { posts } = await mount({ body: OWNER_JOBS });
      await screen.findByText(fullText(/^3 shown\./));
      const row = rowOf("job_deferred01");

      await userEvent.click(within(row).getByRole("button", { name: "Cancel" }));

      expect(await within(row).findByText("cancelled")).toBeInTheDocument();
      expect(posts()).toHaveLength(1);
      expect(posts()[0].path).toBe("/dashboard/jobs/job_deferred01/cancel");
      // Not repeatable, so the button goes; focus lands on what replaced it.
      expect(within(row).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
      expect(row.contains(document.activeElement)).toBe(true);
    });

    it("prints the refusal in the API's own words", async () => {
      await mount({ body: OWNER_JOBS }, { cancel: { status: 400, body: REFUSAL } });
      await screen.findByText(fullText(/^3 shown\./));
      const row = rowOf("job_running001");

      await userEvent.click(within(row).getByRole("button", { name: "Cancel" }));

      expect(await within(row).findByText("E_BAD_PARAM")).toBeInTheDocument();
      expect(within(row).getByText(/already failed/)).toBeInTheDocument();
      expect(
        within(row).getByText("only queued or running jobs can be cancelled."),
      ).toBeInTheDocument();
    });

    it("is not drawn at all where the deployment registers no write side", async () => {
      await mount({ body: DEMO_JOBS }, { session: DEMO_SESSION });
      await screen.findByText(fullText(/^3 shown\./));

      expect(screen.queryByRole("columnheader", { name: "action" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
      expect(screen.getAllByRole("row")).toHaveLength(4);
    });

    it("is absent for a signed-out reader of an instance that has one", async () => {
      await mount({ body: OWNER_JOBS }, { session: { ...OWNER_SESSION, write_side: false } });
      await screen.findByText(fullText(/^3 shown\./));
      expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    });
  });

  it("renders the projection without the operator's own box in it", async () => {
    await mount({ body: DEMO_JOBS }, { session: DEMO_SESSION });
    await screen.findByText(fullText(/^3 shown\./));

    expect(screen.queryByText(/cookiefile/)).not.toBeInTheDocument();
    expect(within(rowOf("job_deferred01")).getByText("E_RATE_LIMIT")).toBeInTheDocument();
    expect(within(rowOf("job_finished01")).getByText("26m 40s")).toBeInTheDocument();
    expect(
      screen.getByText("Source URLs and error text are not published on this instance."),
    ).toBeInTheDocument();
  });

  it("does not footnote a redaction an owner's instance is not making", async () => {
    await mount({ body: OWNER_JOBS });
    await screen.findByText(fullText(/^3 shown\./));
    expect(screen.queryByText(/are not published on this instance/)).not.toBeInTheDocument();
  });

  it("falls back to the session on an instance whose payload has no flag", async () => {
    await mount(
      { body: { ...DEMO_JOBS, redacted: undefined } },
      { session: { ...DEMO_SESSION, readonly: true } },
    );
    await screen.findByText(fullText(/^3 shown\./));
    expect(
      screen.getByText("Source URLs and error text are not published on this instance."),
    ).toBeInTheDocument();
  });
});
