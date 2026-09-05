// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import {
  BAD_DURATION,
  CHECKED_OUTCOME,
  DELETED_OUTCOME,
  FOLLOW_DETAIL,
  IN_FLIGHT_DETAIL,
  PAUSED_OUTCOME,
  QUEUED_OUTCOME,
  QUIET_DETAIL,
  RESUMED_OUTCOME,
  RULES_OUTCOME,
  UNKNOWN_FOLLOW,
} from "@/test/following-fixtures";

// A follow's own page is the one place on this instance that says what a
// standing rule *did not* do. So the assertions are the third band and its
// receipts — the reason printed verbatim, the near-miss line that exists only
// when it is true, and the button that overrules a decision — plus the five
// writes, each of which has to show its outcome inline because this page does
// not poll.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

async function mount({
  detail = { body: FOLLOW_DETAIL },
  post = { body: PAUSED_OUTCOME },
  search = "",
  session = OWNER_SESSION,
  slug = "andrej-karpathy",
}: {
  detail?: Route | Route[];
  post?: Route;
  search?: string;
  session?: unknown;
  slug?: string;
} = {}) {
  const reads = Array.isArray(detail) ? [...detail] : [detail];
  const posts: { path: string; init: RequestInit }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") posts.push({ path: url, init });
    let route: Route;
    if (init?.method === "POST") route = post;
    else if (url === "/dashboard/api/session") route = { body: session };
    else if (url.startsWith("/dashboard/api/following"))
      route = reads.length > 1 ? reads.shift()! : reads[0];
    else route = { status: 404, body: {} };
    const text = typeof route.body === "string" ? route.body : JSON.stringify(route.body ?? {});
    return new Response(text, {
      status: route.status ?? 200,
      headers: { "content-type": "application/json", ...route.headers },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  const { mockNavigation } = await import("@/test/next");
  const nav = mockNavigation(search, `/dashboard/following/${slug}`);
  const { Chrome } = await import("../../Chrome");
  const { FollowDetailView } = await import("./FollowDetailView");
  render(
    <Chrome>
      <FollowDetailView slug={slug} />
    </Chrome>,
  );
  return { ...nav, fetcher, posts };
}

/** The ledger row a candidate's title is in. */
function rowOf(title: string | RegExp) {
  return screen.getByText(title).closest("tr") as HTMLElement;
}

function noNulls() {
  expect(document.body.textContent).not.toMatch(/\bnull\b|NaN|undefined/);
}

describe("one follow's page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("opens with the rule as facts, the clocks and the last error", async () => {
    await mount();

    expect(await screen.findByRole("heading", { name: "Andrej Karpathy" })).toBeInTheDocument();
    // The same formatter the table uses, one size up: the sentence
    // `follows.rules.describe` renders does not travel on this payload.
    expect(screen.getByText("0:08:00 floor")).toBeInTheDocument();
    expect(screen.getByText("every 6h 00m")).toBeInTheDocument();

    const rule = screen.getByRole("region", { name: "The rule" });
    expect(within(rule).getByText("2026-09-05 14:34")).toBeInTheDocument();
    expect(within(rule).getByText("https://www.youtube.com/@karpathy")).toBeInTheDocument();
    // The error is printed whether or not the state is `failing`.
    expect(within(rule).getByText("E_RATE_LIMIT")).toBeInTheDocument();
    expect(within(rule).getByText("the source rate-limited this box")).toBeInTheDocument();
    noNulls();
  });

  it("links each check and each job it queued to the war story already written", async () => {
    await mount();
    await screen.findByRole("heading", { name: "Andrej Karpathy" });

    expect(screen.getByRole("link", { name: "job_followchk1" })).toHaveAttribute(
      "href",
      "/dashboard/jobs/job_followchk1",
    );
    expect(screen.getByRole("link", { name: "job_followidx1" })).toHaveAttribute(
      "href",
      "/dashboard/jobs/job_followidx1",
    );
    // The two caps ride on the payload, so the page says "ten" without
    // hard-coding ten.
    expect(screen.getByText(/The 10 most recent/)).toBeInTheDocument();
    // How long the check took, from the two stamps it kept.
    expect(screen.getByText("20s")).toBeInTheDocument();
  });

  // A check already queued or running is named, so `Check now` cannot look like
  // it did nothing.
  it("names a check that is already on the queue", async () => {
    await mount({ detail: { body: IN_FLIGHT_DETAIL } });
    await screen.findByRole("heading", { name: "Andrej Karpathy" });

    expect(screen.getByText(/A check is already on the queue/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "job_followchk2" })).toBeInTheDocument();
  });

  describe("what it passed over", () => {
    it("prints every decision but queued, with the reason verbatim", async () => {
      await mount();
      await screen.findByRole("heading", { name: "What it passed over" });

      const near = rowOf("Near the floor");
      // The receipt, with the number that made the call inside it. Not
      // re-derived here: "7:48, shorter than your 8:00 floor" is evidence and
      // "too short" is an opinion.
      expect(within(near).getByText("7:48, shorter than your 8:00 floor")).toBeInTheDocument();
      expect(within(near).getByText("skipped_duration")).toBeInTheDocument();
      expect(within(near).getByText("7m 48s")).toBeInTheDocument();
      expect(within(near).getByText("2025-02-19")).toBeInTheDocument();

      // A probe cost a request, and the row says so.
      expect(within(rowOf("Also near the floor")).getByText(/judged from a probe/)).toBeVisible();

      // A provisional decision says so rather than looking terminal.
      expect(
        within(rowOf("Held by the budget")).getByText("re-decided on the next check"),
      ).toBeInTheDocument();
      expect(within(rowOf("Held by the budget")).getByText("held_budget")).toBeInTheDocument();
      noNulls();
    });

    // The one derived line, and the reason it is a line at all: it is read out
    // of the rows below it and printed only when it is true.
    it("prints the near-miss finding with the payload's own threshold", async () => {
      await mount();
      expect(
        await screen.findByText(
          "2 of the last 6 passed over were within 60 seconds of your floor.",
        ),
      ).toBeInTheDocument();
    });

    // `null` means print nothing. A "0 of the last 25" line is a fact about
    // nothing dressed as a finding, and this band has to stay believable.
    it("prints nothing at all when the near miss is null", async () => {
      await mount({ detail: { body: QUIET_DETAIL }, slug: "paused-channel" });
      await screen.findByRole("heading", { name: "What it passed over" });

      expect(screen.queryByText(/passed over were within/)).not.toBeInTheDocument();
      expect(screen.queryByText(/0 of the last/)).not.toBeInTheDocument();
      expect(screen.getByText(/Nothing has been passed over/)).toBeInTheDocument();
      noNulls();
    });

    it("does not interpolate a candidate's title or its reason", async () => {
      await mount();
      await screen.findByRole("heading", { name: "What it passed over" });

      const hostile = rowOf(/Held for you/);
      expect(hostile.innerHTML).not.toContain("<script");
      expect(hostile.innerHTML).not.toContain("<img src=x");
      expect(document.querySelector("script[src]")).toBeNull();
    });

    it("pages the ledger back to the band it pages", async () => {
      await mount({
        detail: {
          body: { ...FOLLOW_DETAIL, pagination: { limit: 25, offset: 25, has_more: true } },
        },
        search: "offset=25",
      });
      await screen.findByRole("heading", { name: "What it passed over" });

      expect(screen.getByRole("link", { name: /Newer/ })).toHaveAttribute(
        "href",
        "/dashboard/following/andrej-karpathy?offset=0#passed",
      );
      expect(screen.getByRole("link", { name: /Older 25/ })).toHaveAttribute(
        "href",
        "/dashboard/following/andrej-karpathy?offset=50#passed",
      );
    });

    it("renders the clamp the server moved", async () => {
      await mount({
        detail: { body: { ...FOLLOW_DETAIL, notes: ["limit=100000 → 100"] } },
        search: "limit=100000",
      });
      expect(await screen.findByText("limit=100000 → 100")).toBeInTheDocument();
    });
  });

  describe("the five writes", () => {
    it("pauses, and shows the state the follow is now in", async () => {
      const { posts } = await mount();
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Pause" }));

      expect(posts[0].path).toBe("/dashboard/following/andrej-karpathy/state");
      expect(String(posts[0].init.body)).toBe("action=pause");
      expect((posts[0].init.headers as Record<string, string>).accept).toBe("application/json");
      // The row is re-read after the write, so the head follows the outcome.
      const head = await screen.findAllByText("paused");
      expect(head.length).toBeGreaterThan(0);
    });

    // `failing` resumes for the same reason `paused` does: it is a follow the
    // scheduler will not enqueue, and resume is what re-arms the clock.
    it("offers Try again on a failing follow, through the same route", async () => {
      const { posts } = await mount({
        detail: {
          body: { ...FOLLOW_DETAIL, follow: { ...FOLLOW_DETAIL.follow, state: "failing" } },
        },
        post: { body: RESUMED_OUTCOME },
      });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(String(posts[0].init.body)).toBe("action=resume");
    });

    // It does not run a check; it makes the clock due. `next_check_at: 0` is
    // the row saying so.
    it("makes the clock due, and says it is due", async () => {
      const { posts } = await mount({ post: { body: CHECKED_OUTCOME } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Check now" }));

      expect(posts[0].path).toBe("/dashboard/following/andrej-karpathy/check");
      expect(await screen.findByText("due now")).toBeInTheDocument();
    });

    // A paused follow has no clock to make due, and a control that exists to be
    // refused is worse UI than no control.
    it("offers no Check now on a follow that is not active", async () => {
      await mount({ detail: { body: QUIET_DETAIL }, slug: "paused-channel" });
      await screen.findByRole("heading", { name: "Paused Channel" });

      expect(screen.queryByRole("button", { name: "Check now" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    });

    it("saves the rule through the validator both callers share", async () => {
      const { posts } = await mount({ post: { body: RULES_OUTCOME } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      // The form is prefilled from the row, so what is posted is the rule the
      // store kept with one field changed.
      const floor = screen.getByLabelText("Longer than");
      expect(floor).toHaveValue("0:08:00");
      await userEvent.clear(floor);
      await userEvent.type(floor, "9:00");
      await userEvent.click(screen.getByRole("button", { name: "Save the rule" }));

      expect(posts[0].path).toBe("/dashboard/following/andrej-karpathy/rules");
      const body = new URLSearchParams(String(posts[0].init.body));
      expect(body.get("min_duration")).toBe("9:00");
      expect(body.get("tab_videos")).toBe("1");
      expect(body.get("tags")).toBe("topic:llm");
      expect(await screen.findByText(/the one the store kept/)).toBeInTheDocument();
    });

    it("prints the validator's refusal rather than guessing at the field", async () => {
      await mount({ post: { status: 400, body: BAD_DURATION } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.type(screen.getByLabelText("Longer than"), "banana");
      await userEvent.click(screen.getByRole("button", { name: "Save the rule" }));

      expect(await screen.findByText("E_BAD_TIME_FORMAT")).toBeInTheDocument();
      expect(screen.getByText(/Could not parse min_duration/)).toBeInTheDocument();
    });

    // The one irreversible control here, so it asks twice and states what
    // survives before it writes rather than after.
    it("asks before it unfollows, then lands on the list", async () => {
      const { posts, push } = await mount({ post: { body: DELETED_OUTCOME } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));
      expect(posts).toHaveLength(0);
      expect(screen.getByText(/Stop the checks and delete the ledger\?/)).toBeInTheDocument();

      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));
      expect(posts[0].path).toBe("/dashboard/following/andrej-karpathy/delete");
      expect(await screen.findByText(/stayed in the corpus/)).toBeInTheDocument();
      expect(push).toHaveBeenCalledWith("/dashboard/following");
    });

    it("keeps the follow when the confirmation is declined", async () => {
      const { posts } = await mount();
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));
      await userEvent.click(screen.getByRole("button", { name: "Keep it" }));

      expect(posts).toHaveLength(0);
      expect(screen.getByRole("button", { name: "Unfollow" })).toBeInTheDocument();
    });

    // The whole argument for the third band: a rule that turned something away
    // is only honest if the person who wrote it can overrule it in one click.
    it("overrules one row, and names the job it queued", async () => {
      const { posts } = await mount({ post: { body: QUEUED_OUTCOME } });
      await screen.findByRole("heading", { name: "What it passed over" });

      const row = rowOf("Near the floor");
      await userEvent.click(within(row).getByRole("button", { name: "Index anyway" }));

      expect(posts[0].path).toBe("/dashboard/following/andrej-karpathy/queue");
      expect(String(posts[0].init.body)).toBe("url=https%3A%2F%2Fyoutu.be%2Fnearmiss001");
      expect(await within(row).findByRole("link", { name: "job_02e028870c97" })).toHaveAttribute(
        "href",
        "/dashboard/jobs/job_02e028870c97",
      );
    });
  });

  describe("what is not there", () => {
    // An unknown slug is the store's answer, not a failed read: it gets the
    // refusal's own words and a way back, never a retry button that would
    // produce the same answer again.
    it("says an unknown slug is unknown, in Python's words", async () => {
      await mount({ detail: { status: 404, body: UNKNOWN_FOLLOW }, slug: "nope" });

      expect(await screen.findByRole("heading", { name: /is not a follow/ })).toBeInTheDocument();
      expect(screen.getByText("E_UNKNOWN_FOLLOW")).toBeInTheDocument();
      expect(screen.getByText(/lists every channel/)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Back to Following" })).toHaveAttribute(
        "href",
        "/dashboard/following",
      );
      expect(screen.queryByRole("button", { name: "try again" })).not.toBeInTheDocument();
    });

    // A `404` with no envelope is the other 404: the route is not registered,
    // because this deployment has no write side.
    it("is absent where the deployment registers no write side", async () => {
      await mount({ session: DEMO_SESSION });

      expect(
        await screen.findByRole("heading", { name: "This deployment does not follow channels." }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    });

    it("says what it was told when the session went away", async () => {
      await mount({
        detail: {
          status: 401,
          body: {
            error: "E_AUTH_REQUIRED",
            message: "The dashboard needs the owner's token or session.",
            next: "Sign in at /dashboard/login.",
          },
        },
      });

      expect(await screen.findByText(/not open to this browser/)).toBeInTheDocument();
    });

    it("waits out the limiter's own delay", async () => {
      await mount({
        detail: {
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests for now." },
          headers: { "retry-after": "9" },
        },
      });

      expect(await screen.findByText("Too many dashboard requests for now.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "retry in 9s" })).toBeDisabled();
    });
  });
});
