// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mountDashboard, type Answer, type Route } from "@/test/dashboard";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import {
  BAD_DURATION,
  CHECKED_OUTCOME,
  DELETED_OUTCOME,
  DELETED_SPENT_OUTCOME,
  CHECKS_OFF_DETAIL,
  FOLLOW_DETAIL,
  GAVE_UP_DETAIL,
  IN_FLIGHT_DETAIL,
  NOT_SCHEDULABLE,
  PAUSED_OUTCOME,
  QUEUED_OUTCOME,
  QUIET_DETAIL,
  RESUMED_OUTCOME,
  RETRYING_DETAIL,
  RULES_OUTCOME,
  UNKNOWN_FOLLOW,
} from "@/test/following-fixtures";
import { firstPaint } from "@/test/retry";
import { FollowDetailView } from "./FollowDetailView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// What a standing rule did not do: the ledger and its receipts, the near-miss
// line that exists only when true, and the five writes, each answered inline.

function mount({
  detail = { body: FOLLOW_DETAIL },
  post = { body: PAUSED_OUTCOME },
  search = "",
  session = OWNER_SESSION,
  slug = "andrej-karpathy",
}: {
  detail?: Route;
  post?: Answer;
  search?: string;
  session?: unknown;
  slug?: string;
} = {}) {
  return mountDashboard(<FollowDetailView slug={slug} />, {
    path: `/dashboard/following/${slug}`,
    search,
    session,
    routes: { "/dashboard/api/following/*": detail, "POST /dashboard/following/*": post },
  });
}

/** The ledger row a candidate's title is in. */
function rowOf(title: string | RegExp) {
  return screen.getByText(title).closest("tr") as HTMLElement;
}

function noNulls() {
  expect(document.body.textContent).not.toMatch(/\bnull\b|NaN|undefined/);
}

describe("one follow's page", () => {
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

  // Tom, 2026-09-05, and the argument is stronger here than on the list: this
  // page is about one follow's clock, so both lines that read as a schedule —
  // the next check and the job on the queue — say what checks off means.
  it("replaces the clock and re-captions the queued check when checks are off", async () => {
    await mount({ detail: { body: CHECKS_OFF_DETAIL } });
    await screen.findByRole("heading", { name: "Andrej Karpathy" });

    const rule = screen.getByRole("region", { name: "The rule" });
    expect(within(rule).getByText("checks off")).toBeInTheDocument();
    // The time it would otherwise have promised is nowhere on the page; the
    // two clocks that stay are readings of things that happened.
    expect(screen.queryByText("2026-09-05 20:34")).not.toBeInTheDocument();
    expect(within(rule).getByText("2026-09-05 14:34")).toBeInTheDocument();

    // The job is real and still linked — it is waiting, not lost.
    expect(
      within(rule).getByText(/nothing will claim it while checks are off/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "job_followchk2" })).toBeInTheDocument();
    noNulls();
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

      // The ledger page size the server accepted rides on both links whether
      // or not the reader typed it, so a link sent on pages the listing it was
      // made from.
      expect(screen.getByRole("link", { name: /Newer/ })).toHaveAttribute(
        "href",
        "/dashboard/following/andrej-karpathy?limit=25&offset=0#passed",
      );
      expect(screen.getByRole("link", { name: /Older 25/ })).toHaveAttribute(
        "href",
        "/dashboard/following/andrej-karpathy?limit=25&offset=50#passed",
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

      expect(posts()[0].path).toBe("/dashboard/following/andrej-karpathy/state");
      expect(posts()[0].fields.toString()).toBe("action=pause");
      expect(posts()[0].headers.get("accept")).toBe("application/json");
      const head = await screen.findAllByText("paused");
      expect(head.length).toBeGreaterThan(0);
      // The answer takes the focus the button had.
      expect(document.activeElement).toHaveAttribute("role", "status");
      expect(document.activeElement).toHaveTextContent("paused");
    });

    // A resume clears both error columns and re-arms the clock; the page re-reads
    // once so the checks, jobs and ledger catch up too.
    it("clears the last failure on a resume, and re-reads the page it changed", async () => {
      const resumed = {
        ...FOLLOW_DETAIL,
        follow: {
          ...FOLLOW_DETAIL.follow,
          state: "active",
          last_error_code: null,
          last_error_message: null,
        },
      };
      const { posts, calls } = await mount({
        detail: [
          { body: { ...FOLLOW_DETAIL, follow: { ...FOLLOW_DETAIL.follow, state: "failing" } } },
          { body: resumed },
        ],
        post: { body: RESUMED_OUTCOME },
      });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });
      expect(screen.getByText("E_RATE_LIMIT")).toBeInTheDocument();

      const reads = () => calls("/dashboard/api/following").length;
      expect(reads()).toBe(1);

      await userEvent.click(screen.getByRole("button", { name: "Try again" }));

      expect(posts()[0].path).toBe("/dashboard/following/andrej-karpathy/state");
      // `active` arrives with the outcome, the cleared pill with the re-read.
      await waitFor(() => {
        expect(screen.queryByText("E_RATE_LIMIT")).not.toBeInTheDocument();
        expect(reads()).toBe(2);
      });
      expect(screen.getAllByText("active").length).toBeGreaterThan(0);
      expect(screen.queryByText("the source rate-limited this box")).not.toBeInTheDocument();
      // The control comes back: Pause follows Resume, and none of these
      // actions is a one-shot.
      expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
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
      expect(posts()[0].fields.toString()).toBe("action=resume");
    });

    // Since 0008 a `failing` follow with retries left is schedulable, so
    // Check now does what it says — the gesture an operator makes when a
    // channel comes back, which used to have to be spelled pause-then-resume.
    it("spells the retry out beside the state, and offers Check now to a retrying follow", async () => {
      const { posts } = await mount({
        detail: { body: RETRYING_DETAIL },
        post: { body: CHECKED_OUTCOME },
      });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      const fact = screen.getByText("retry 2 of 7, once a day");
      expect(fact).toHaveAttribute("data-tone", "warn");
      // The clock it is still coming back on is a clock the minirow prints —
      // a day out, which is the retry cadence.
      expect(screen.getByText("2026-09-06 16:34")).toBeInTheDocument();

      await userEvent.click(screen.getByRole("button", { name: "Check now" }));
      expect(posts()[0].path).toBe("/dashboard/following/andrej-karpathy/check");
      expect(await screen.findByText("due now")).toBeInTheDocument();
      noNulls();
    });

    it("says a follow that gave up in the error tone, disables Check now on it, and prints no next check", async () => {
      const { posts } = await mount({ detail: { body: GAVE_UP_DETAIL } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      const fact = screen.getByText("gave up after 7 tries");
      expect(fact).toHaveAttribute("data-tone", "bad");
      // Nothing will enqueue it, so no clock is promised: the minirow prints
      // the dash, not the timestamp its `next_check_at` still carries.
      const rule = screen.getByRole("region", { name: "The rule" });
      expect(within(rule).getAllByText("—")).toHaveLength(1);
      expect(within(rule).queryByText("2026-09-06 16:34")).not.toBeInTheDocument();

      // The control stays, disabled, with the refusal the write would be
      // refused with as its help — and the control that clears the count is
      // the one standing beside it.
      const check = screen.getByRole("button", { name: "Check now" });
      expect(check).toBeDisabled();
      expect(check).toHaveAttribute(
        "title",
        "Not scheduled: Andrej Karpathy is failing and has stopped retrying after 7 " +
          "consecutive failures. Nothing was queued.",
      );
      expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
      expect(posts()).toHaveLength(0);
      noNulls();
    });

    // The page can be a beat behind the row: a follow that gave up between
    // the read and the click is refused, and the refusal renders inline with
    // Python's own words rather than as a button that looks broken.
    it("renders a check-now refusal that still arrives, in the API's words", async () => {
      await mount({
        detail: { body: RETRYING_DETAIL },
        post: { status: 409, body: NOT_SCHEDULABLE },
      });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Check now" }));

      expect(await screen.findByText("E_NOT_SCHEDULABLE")).toBeInTheDocument();
      expect(screen.getByText(/has stopped retrying after 7/)).toBeInTheDocument();
      expect(screen.getByText(/clears the failure count/)).toBeInTheDocument();
      noNulls();
    });

    // It does not run a check; it makes the clock due. `next_check_at: 0` is
    // the row saying so.
    it("makes the clock due, and says it is due", async () => {
      const { posts } = await mount({ post: { body: CHECKED_OUTCOME } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Check now" }));

      expect(posts()[0].path).toBe("/dashboard/following/andrej-karpathy/check");
      // The control says what the route answered, beside the button that
      // stays: asking twice is a thing an operator does when the first check
      // found nothing.
      expect(await screen.findByText("due now")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Check now" })).toBeInTheDocument();
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

      expect(posts()[0].path).toBe("/dashboard/following/andrej-karpathy/rules");
      const body = new URLSearchParams(posts()[0].fields.toString());
      expect(body.get("min_duration")).toBe("9:00");
      expect(body.get("tab_videos")).toBe("1");
      expect(body.get("tags")).toBe("topic:llm");
      expect(await screen.findByText(/the one the store kept/)).toBeInTheDocument();
    });

    // The store clamps, parses and normalises what it is sent, so what it kept
    // is what the eleven controls have to show — all eleven, not the three the
    // form used to re-key on.
    it("reseeds every control from the row the store kept", async () => {
      const saved = {
        ...FOLLOW_DETAIL,
        follow: {
          ...FOLLOW_DETAIL.follow,
          // Everything a save can move that the old key did not watch.
          tabs: ["videos", "shorts"],
          mode: "review",
          channels: "transcript",
          tags: ["topic:llm", "series:zero"],
          max_duration_s: 5400,
          title_include: ["lecture"],
          title_exclude: ["trailer"],
          backfill: 4,
        },
      };
      await mount({
        detail: [{ body: FOLLOW_DETAIL }, { body: saved }],
        post: { body: { follow: saved.follow } },
      });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });
      expect(screen.getByLabelText("Shorter than")).toHaveValue("");
      expect(screen.getByLabelText("Backfill")).toHaveValue(0);

      await userEvent.click(screen.getByRole("button", { name: "Save the rule" }));
      await screen.findByText(/the one the store kept/);

      expect(screen.getByLabelText("Shorter than")).toHaveValue("1:30:00");
      expect(screen.getByLabelText("Backfill")).toHaveValue(4);
      expect(screen.getByLabelText("Title contains")).toHaveValue("lecture");
      expect(screen.getByLabelText("Title never contains")).toHaveValue("trailer");
      expect(screen.getByLabelText("Tags")).toHaveValue("topic:llm, series:zero");
      expect(screen.getByLabelText("When something matches")).toHaveValue("review");
      expect(screen.getByLabelText("/shorts")).toBeChecked();
      expect(screen.getByLabelText(/On-screen text/)).not.toBeChecked();
    });

    // The way out of a disclosure that has been opened and thought better of.
    // A navigation rather than a close, because it throws away what was typed.
    it("offers the way out of the edit form that the Jinja page offered", async () => {
      await mount();
      await screen.findByRole("heading", { name: "Andrej Karpathy" });
      expect(screen.getByRole("link", { name: "Cancel" })).toHaveAttribute(
        "href",
        "/dashboard/following/andrej-karpathy",
      );
    });

    it("prints the validator's refusal rather than guessing at the field", async () => {
      await mount({ post: { status: 400, body: BAD_DURATION } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.type(screen.getByLabelText("Longer than"), "banana");
      await userEvent.click(screen.getByRole("button", { name: "Save the rule" }));

      expect(await screen.findByText("E_BAD_TIME_FORMAT")).toBeInTheDocument();
      expect(document.activeElement).toBe(
        screen.getByText("E_BAD_TIME_FORMAT").closest('[role="status"]'),
      );
      expect(screen.getByText(/Could not parse min_duration/)).toBeInTheDocument();
    });

    // The one irreversible control here, so it asks twice and states what
    // survives before it writes rather than after.
    it("asks before it unfollows, then lands on the list", async () => {
      const { posts, push } = await mount({ post: { body: DELETED_OUTCOME } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));
      expect(posts()).toHaveLength(0);
      expect(screen.getByText(/Stop the checks and delete the ledger\?/)).toBeInTheDocument();

      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));
      expect(posts()[0].path).toBe("/dashboard/following/andrej-karpathy/delete");
      expect(await screen.findByText(/stayed in the corpus/)).toBeInTheDocument();
      expect(push).toHaveBeenCalledWith("/dashboard/following");
    });

    // Since migration 0007 the day an unfollowed follow spent stays spent, so
    // the budget on the list it lands on will not move — and the receipt owes
    // the operator the line the tool prints before they wonder whether the
    // unfollow worked. Nothing spent, and there is no line at all.
    it("prints the not-a-refund line when the unfollow kept a spent day", async () => {
      await mount({ post: { body: DELETED_SPENT_OUTCOME } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));
      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));

      expect(
        await screen.findByText(
          "Not a refund: the 1.0h this follow accepted in the last 24h stay spent. " +
            "They were downloaded and indexed; deleting the rule does not un-spend the day.",
        ),
      ).toBeInTheDocument();
      noNulls();
    });

    it("prints no not-a-refund line when the follow spent nothing", async () => {
      await mount({ post: { body: DELETED_OUTCOME } });
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));
      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));

      expect(await screen.findByText(/stayed in the corpus/)).toBeInTheDocument();
      expect(screen.queryByText(/Not a refund/)).not.toBeInTheDocument();
    });

    it("keeps the follow when the confirmation is declined", async () => {
      const { posts } = await mount();
      await screen.findByRole("heading", { name: "Andrej Karpathy" });

      await userEvent.click(screen.getByRole("button", { name: "Unfollow" }));
      await userEvent.click(screen.getByRole("button", { name: "Keep it" }));

      expect(posts()).toHaveLength(0);
      expect(screen.getByRole("button", { name: "Unfollow" })).toBeInTheDocument();
    });

    // The whole argument for the third band: a rule that turned something away
    // is only honest if the person who wrote it can overrule it in one click.
    it("overrules one row, and names the job it queued", async () => {
      const { posts } = await mount({ post: { body: QUEUED_OUTCOME } });
      await screen.findByRole("heading", { name: "What it passed over" });

      const row = rowOf("Near the floor");
      await userEvent.click(within(row).getByRole("button", { name: "Index anyway" }));

      expect(posts()[0].path).toBe("/dashboard/following/andrej-karpathy/queue");
      expect(posts()[0].fields.toString()).toBe("url=https%3A%2F%2Fyoutu.be%2Fnearmiss001");
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
    // `views.follow_detail` named the page after the follow before a byte of it
    // was written. This shell is named by the route, so the slug in the URL
    // stands in until the read has a better name — never the section's word
    // over a page about one channel.
    it("names the tab from the URL until the read lands, then from the follow", async () => {
      const { unmount } = await mount({
        detail: () => new Promise<Answer>(() => {}),
        slug: "andrej-karpathy",
      });
      expect(document.title).toBe("andrej-karpathy — vidtheque");

      unmount();
      await mount();
      await screen.findByRole("heading", { name: "Andrej Karpathy" });
      expect(document.title).toBe("Andrej Karpathy — vidtheque");
    });

    // `views.follow_detail`'s own fallback, and the same one the table uses.
    it("falls back to the slug when the follow has no name", async () => {
      await mount({
        detail: { body: { ...FOLLOW_DETAIL, follow: { ...FOLLOW_DETAIL.follow, title: "" } } },
      });
      await screen.findByRole("region", { name: "The rule" });
      expect(
        screen.getByRole("heading", { name: "andrej-karpathy", level: 1 }),
      ).toBeInTheDocument();
    });

    it("says an unknown slug is unknown, in Python's words", async () => {
      await mount({ detail: { status: 404, body: UNKNOWN_FOLLOW }, slug: "nope" });

      expect(await screen.findByRole("heading", { name: /is not a follow/ })).toBeInTheDocument();
      expect(screen.getByText("E_UNKNOWN_FOLLOW")).toBeInTheDocument();
      // Capitalised, standing on its own under a heading rather than trailing
      // a colon.
      expect(screen.getByText(/^The Following page lists every channel/)).toBeInTheDocument();
      // The `back` a write handler supplies, and the two standing links the
      // refusal page always carried. Scoped to the recovery panel, because the
      // rail beside it carries a Following link of its own.
      const recover = screen.getByRole("region", { name: "Where to go from here" });
      expect(within(recover).getByRole("link", { name: "Following" })).toHaveAttribute(
        "href",
        "/dashboard/following",
      );
      expect(
        within(recover).getByRole("link", { name: "Everything that is indexed" }),
      ).toBeInTheDocument();
      expect(within(recover).getByRole("link", { name: "Corpus overview" })).toBeInTheDocument();
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

      expect(await screen.findByText(/needs the owner.s token or session/)).toBeInTheDocument();
    });

    // Under a stopped clock: the label has to be the delay the limiter named,
    // and a page that halved it would count down just as convincingly.
    it("waits out the limiter's own delay", async () => {
      await firstPaint(() =>
        mount({
          detail: {
            status: 429,
            body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests for now." },
            headers: { "retry-after": "9" },
          },
        }),
      );

      expect(screen.getByText("Too many dashboard requests for now.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "retry in 9s" })).toBeDisabled();
    });
  });
});
