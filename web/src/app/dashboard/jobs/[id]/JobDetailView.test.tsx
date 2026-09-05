// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import {
  CANCEL_QUEUED,
  DEFERRED_JOB_DETAIL,
  DEMO_JOB_DETAIL,
  OWNER_JOB_DETAIL,
  RETRY_RECEIPT,
  RUNNING_JOB_DETAIL,
} from "@/test/jobs-fixtures";
import { firstPaint } from "@/test/retry";

// The job's own page is the war story: what it cost, which item it broke on,
// and — the reason the page exists — what it is waiting for. So the assertions
// are the deferral notice and its countdown, the three clocks and what
// separates them, the items table's three shapes of row, and the two controls,
// each under exactly the condition `job.html` renders it under.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

async function mount(
  detail: Route | Route[],
  {
    session = OWNER_SESSION,
    write,
    jobId = "job_finished01",
  }: { session?: unknown; write?: Route; jobId?: string } = {},
) {
  const answers = Array.isArray(detail) ? [...detail] : [detail];
  const posts: { path: string; init: RequestInit }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") {
      posts.push({ path: url, init });
      const answer = write ?? { body: CANCEL_QUEUED };
      return new Response(JSON.stringify(answer.body ?? {}), {
        status: answer.status ?? 200,
        headers: { "content-type": "application/json" },
      });
    }
    const route: Route = url.startsWith("/dashboard/api/jobs/")
      ? answers.length > 1
        ? answers.shift()!
        : answers[0]
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
  const nav = mockNavigation("", `/dashboard/jobs/${jobId}`);
  const { Chrome } = await import("../../Chrome");
  const { JobDetailView } = await import("./JobDetailView");
  render(
    <Chrome>
      <JobDetailView jobId={jobId} />
    </Chrome>,
  );
  return { ...nav, fetcher, posts };
}

/** The countdown pill, whose number sits in a span of its own so the two
 *  words around it never wrap through it. */
function held() {
  return screen.getByText(/held/).closest("span") as HTMLElement;
}

describe("one job's page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
    vi.useRealTimers();
  });

  it("shows the three clocks, and says what each of them measures", async () => {
    await mount({ body: OWNER_JOB_DETAIL });

    expect(await screen.findByRole("heading", { name: /Job job_finished01/ })).toBeInTheDocument();
    // `started_at` is the *first* claim, so created -> finished is the honest
    // wall clock and first claim -> finished is time on the runner. A deferred
    // job spends the difference waiting, which is why both are printed.
    const cost = screen.getByRole("region", { name: "What it cost" });
    expect(within(cost).getByText("26m 40s")).toBeInTheDocument();
    expect(within(cost).getByText("25m 00s")).toBeInTheDocument();
    expect(within(cost).getByText("1m 40s")).toBeInTheDocument();
    expect(within(cost).getByText("created → finished")).toBeInTheDocument();
    // The five buckets, and they add up to the number above them.
    expect(
      within(cost).getByText("1 done · 1 failed · 0 skipped · 0 cancelled · 0 still to run"),
    ).toBeInTheDocument();
  });

  it("draws each item as what it actually is", async () => {
    await mount({ body: OWNER_JOB_DETAIL });
    const items = await screen.findByRole("region", { name: "Items" });

    // Resolved to a video: a link to its own page.
    expect(within(items).getByRole("link", { name: "Visualizing transformers" })).toHaveAttribute(
      "href",
      "/dashboard/videos/eMlx5fFNoYc",
    );
    expect(within(items).getByText("1/3")).toBeInTheDocument();
    expect(within(items).getByText("15m 00s")).toBeInTheDocument();

    // Never resolved: the URL it was submitted as, and the error underneath.
    expect(within(items).getByText("https://youtu.be/failedvideo")).toBeInTheDocument();
    expect(within(items).getByText("never resolved to a video")).toBeInTheDocument();
    expect(within(items).getByText("E_SOURCE")).toBeInTheDocument();
    expect(within(items).getByText(/Sign in to confirm you are not a bot/)).toBeInTheDocument();
  });

  // The page's whole reason for existing: "waiting, and coming back" was the
  // honest state of the system and nothing rendered it.
  it("says a deferred job is waiting rather than stuck, and for how much longer", async () => {
    await mount({ body: DEFERRED_JOB_DETAIL }, { jobId: "job_deferred01" });

    const notice = await screen.findByRole("region", { name: "Waiting, not stuck" });
    expect(within(notice).getByText("4m 00s")).toBeInTheDocument();
    expect(within(notice).getByText("E_RATE_LIMIT")).toBeInTheDocument();
    // And on the title's own baseline, where the countdown belongs on a job
    // whose only interesting fact is the wait.
    expect(held()).toHaveTextContent("held 4m 00s more");
  });

  // The one record a non-rate-limit deferral has anywhere in the system.
  it("prints the event log, newest first", async () => {
    await mount({ body: DEFERRED_JOB_DETAIL }, { jobId: "job_deferred01" });
    const log = await screen.findByRole("region", { name: "Event log" });

    expect(within(log).getByText("warn")).toBeInTheDocument();
    expect(within(log).getByText(/retrying in 300s after E_RATE_LIMIT/)).toBeInTheDocument();
    expect(within(log).getByText(/Newest first, 1 shown/)).toBeInTheDocument();
  });

  it("keeps the tick running while the job is live, and drops it when it is not", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const advanced = {
      ...RUNNING_JOB_DETAIL,
      job: { ...RUNNING_JOB_DETAIL.job, progress: 61 },
    };
    const { fetcher } = await mount([{ body: RUNNING_JOB_DETAIL }, { body: advanced }], {
      jobId: "job_running001",
    });
    await screen.findByRole("region", { name: "Items" });
    expect(screen.getByText("10%")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByText("61%")).toBeInTheDocument();

    // The finished job is the contrast: one reading and no more. The live page
    // is unmounted first, or its own tick would be counted against this one.
    cleanup();
    vi.resetModules();
    vi.unstubAllGlobals();
    const settled = await mount({ body: OWNER_JOB_DETAIL });
    await screen.findByRole("region", { name: "Items" });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(
      settled.fetcher.mock.calls.filter((call) =>
        String(call[0]).startsWith("/dashboard/api/jobs/"),
      ),
    ).toHaveLength(1);
    expect(screen.getByText(/this is the final record/)).toBeInTheDocument();
    void fetcher;
  });

  // The other 429: refused on the first read, with no war story to keep on the
  // page. The countdown is then the whole page, and it has to say the delay the
  // limiter named — under a stopped clock, because `retryAfter ?? 60` is a
  // fallback that counts down just as convincingly.
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

  it("waits out a 429 by the delay it named", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { fetcher } = await mount(
      [
        { body: RUNNING_JOB_DETAIL },
        {
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests for now." },
          headers: { "retry-after": "5" },
        },
        { body: RUNNING_JOB_DETAIL },
      ],
      { jobId: "job_running001" },
    );
    await screen.findByRole("region", { name: "Items" });
    const reads = () =>
      fetcher.mock.calls.filter((call) => String(call[0]).startsWith("/dashboard/api/jobs/"))
        .length;

    await vi.advanceTimersByTimeAsync(2000);
    expect(reads()).toBe(2);
    // The war story is still on the page; only the liveness stopped.
    expect(screen.getByText(/the live view stopped/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Items" })).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);
    expect(reads()).toBe(2);
    await vi.advanceTimersByTimeAsync(2000);
    expect(reads()).toBe(3);
  });

  it("sends a refused reader to its signed-out state and stops asking", async () => {
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
    await vi.advanceTimersByTimeAsync(10_000);
    expect(
      fetcher.mock.calls.filter((call) => String(call[0]).startsWith("/dashboard/api/jobs/")),
    ).toHaveLength(1);
  });

  // An id that is not a job here is not a failed read: the read succeeded and
  // the answer is "there is no such job". It gets the refusal's own words and a
  // way back, not a retry button that would produce the same answer.
  it("answers an unknown id with the designed not-found rather than a retry", async () => {
    await mount(
      {
        status: 404,
        body: {
          error: "E_UNKNOWN_JOB",
          message: "no such job.",
          next: "the jobs table lists every job this index has run.",
        },
      },
      { jobId: "job_nope" },
    );

    expect(await screen.findByRole("heading", { name: "Unknown job" })).toBeInTheDocument();
    expect(screen.getByText("E_UNKNOWN_JOB")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the jobs table" })).toHaveAttribute(
      "href",
      "/dashboard/jobs",
    );
    expect(screen.queryByRole("button", { name: "try again" })).not.toBeInTheDocument();
  });

  describe("the two controls", () => {
    // Cancel on a live job, Retry on a finished one with something to repair —
    // `job.html`'s own conditions, and each excludes the other.
    it("offers cancel on a live job and nothing else", async () => {
      await mount({ body: RUNNING_JOB_DETAIL }, { jobId: "job_running001" });
      expect(await screen.findByRole("button", { name: "Cancel this job" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
    });

    it("offers retry on a finished job with failed or degraded items, counting both", async () => {
      await mount({ body: OWNER_JOB_DETAIL });
      // One item failed and one `done` item has a failed stage underneath it.
      expect(
        await screen.findByRole("button", { name: "Retry 2 failed or degraded item(s)" }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
    });

    it("offers neither on a finished job with nothing to repair", async () => {
      const clean = {
        ...OWNER_JOB_DETAIL,
        job: { ...OWNER_JOB_DETAIL.job, state: "done", n_failed: 0, degraded: 0 },
      };
      await mount({ body: clean });
      await screen.findByRole("region", { name: "Items" });
      expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
    });

    // A `delete` or a `follow_check` has nothing `index_video` could repair.
    it("offers no retry on a job kind indexing cannot repair", async () => {
      const other = {
        ...OWNER_JOB_DETAIL,
        job: { ...OWNER_JOB_DETAIL.job, kind: "delete" },
      };
      await mount({ body: other });
      await screen.findByRole("region", { name: "Items" });
      expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
    });

    it("shows the retry receipt, with a link to each job it queued", async () => {
      const { posts } = await mount({ body: OWNER_JOB_DETAIL }, { write: { body: RETRY_RECEIPT } });
      await userEvent.click(
        await screen.findByRole("button", { name: "Retry 2 failed or degraded item(s)" }),
      );

      expect(posts[0].path).toBe("/dashboard/jobs/job_finished01/retry");
      expect((posts[0].init.headers as Record<string, string>)["content-type"]).toBe(
        "application/x-www-form-urlencoded",
      );
      expect(await screen.findByText(/2 item\(s\) selected/)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /job_4cee026cb790/ })).toHaveAttribute(
        "href",
        "/dashboard/jobs/job_4cee026cb790",
      );
      // What the new work inherited, typed: the tool's own words, not a
      // sentence this page wrote.
      expect(screen.getByText(/preserved:/)).toHaveTextContent("channels all");
      expect(screen.getByText(/preserved:/)).toHaveTextContent("priority normal");
    });

    it("prints a refused retry in the API's own words, with its next step", async () => {
      await mount(
        { body: OWNER_JOB_DETAIL },
        {
          write: {
            status: 400,
            body: {
              error: "E_TOO_LARGE",
              message: "More than 10 items need repair.",
              next: "retry the affected videos in smaller batches from the index form.",
            },
          },
        },
      );
      await userEvent.click(
        await screen.findByRole("button", { name: "Retry 2 failed or degraded item(s)" }),
      );

      expect(await screen.findByText("E_TOO_LARGE")).toBeInTheDocument();
      expect(screen.getByText(/More than 10 items need repair/)).toBeInTheDocument();
      expect(screen.getByText(/smaller batches from the index form/)).toBeInTheDocument();
    });

    // Running work does not settle. Which of the two just happened is the thing
    // the 2 s tick cannot say, and the reason this route answers inline.
    it("cancels a running job and says the request was recorded", async () => {
      await mount(
        { body: RUNNING_JOB_DETAIL },
        {
          jobId: "job_running001",
          write: { body: { job_id: "job_running001", state: "running", cancel_requested: true } },
        },
      );
      await userEvent.click(await screen.findByRole("button", { name: "Cancel this job" }));

      expect(await screen.findByText("cancel requested")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Cancel this job" })).not.toBeInTheDocument();
    });

    // The database's own flag is a different question from whether the routes
    // are registered: this one disables, and only the control that feeds
    // `index_video`. Cancel writes the job row and stays live.
    it("disables retry where the database refuses writes", async () => {
      await mount(
        { body: OWNER_JOB_DETAIL },
        { session: { ...OWNER_SESSION, writes_allowed: false } },
      );
      expect(await screen.findByRole("button", { name: /Retry/ })).toBeDisabled();
    });

    it("draws no control at all in the projection", async () => {
      await mount({ body: DEMO_JOB_DETAIL }, { session: DEMO_SESSION, jobId: "job_deferred01" });
      await screen.findByRole("region", { name: "Items" });
      expect(screen.queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
    });
  });

  // The projection keeps the clocks, the codes and the counts, and drops
  // exactly two things: the submitted URLs and the error text.
  it("renders the projection with the codes and the clocks and none of the prose", async () => {
    await mount({ body: DEMO_JOB_DETAIL }, { session: DEMO_SESSION, jobId: "job_deferred01" });
    await screen.findByRole("region", { name: "Items" });

    expect(screen.queryByText(/cookiefile/)).not.toBeInTheDocument();
    expect(screen.queryByText(/youtu\.be/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/never resolved to a video, and not published here/),
    ).toBeInTheDocument();
    expect(screen.getByText("message not published on this instance")).toBeInTheDocument();
    // Twice: the code beside the state, and again in the deferral notice that
    // says what set the backoff.
    expect(screen.getAllByText("E_RATE_LIMIT")).toHaveLength(2);
    expect(held()).toHaveTextContent("held 4m 00s more");
  });

  // Six of the Jinja page's panels are assembled in `views._job_detail` and are
  // not on this payload. Each renders the day it arrives, and shows nothing
  // meanwhile — a heading over an empty panel is a page pretending to have an
  // answer it was never given.
  it("draws the degraded list and the stage table only when the payload carries them", async () => {
    await mount({ body: OWNER_JOB_DETAIL });
    await screen.findByRole("region", { name: "Items" });
    expect(screen.queryByRole("region", { name: /Stage by stage/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Finished with something missing" }),
    ).not.toBeInTheDocument();

    cleanup();
    vi.resetModules();
    vi.unstubAllGlobals();
    await mount({
      body: {
        ...OWNER_JOB_DETAIL,
        degraded: [{ seq: 0, video_id: "eMlx5fFNoYc", stage: "ocr", error: "worker returned 503" }],
        focus: OWNER_JOB_DETAIL.items[0],
        stages: [
          {
            stage: "fetch",
            state: "done",
            started_at: 1788618180,
            finished_at: 1788618240,
            took_s: 60,
          },
          {
            stage: "ocr",
            state: "failed",
            started_at: 1788618880,
            finished_at: 1788618980,
            took_s: 100,
          },
        ],
        error_counts: { E_SOURCE: 1 },
      },
    });

    const stages = await screen.findByRole("region", { name: /Stage by stage/ });
    expect(within(stages).getByText("fetch")).toBeInTheDocument();
    expect(within(stages).getByText("failed")).toBeInTheDocument();
    const degraded = screen.getByRole("region", { name: "Finished with something missing" });
    expect(within(degraded).getByRole("link", { name: "eMlx5fFNoYc" })).toHaveAttribute(
      "href",
      "/dashboard/videos/eMlx5fFNoYc",
    );
    expect(screen.getByText(/item error codes/)).toBeInTheDocument();
  });
});
