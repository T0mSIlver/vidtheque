// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mountDashboard, type Answer } from "@/test/dashboard/harness";
import { DEMO_HEALTH, DEMO_SESSION, OWNER_HEALTH, OWNER_SESSION } from "@/test/dashboard/fixtures";
import { firstPaint } from "@/test/dashboard/retry";
import { HealthView } from "./HealthView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// Two payloads, not one: every state that differs is asserted for the owner's
// instance and for the projection, which drops the box.

function mount(
  health: Answer,
  session: unknown = OWNER_SESSION,
  path = "/dashboard",
  costs?: Answer,
) {
  return mountDashboard(<HealthView />, {
    path,
    session,
    routes: {
      "/dashboard/api/health": health,
      ...(costs ? { "/dashboard/api/costs": costs } : {}),
    },
  });
}

const costWindow = (calls: number, cost: number | null, unpriced = 0) => ({
  since: 1_790_000_000,
  calls,
  unpriced_calls: unpriced,
  cost_micro_usd: cost,
  verdicts: calls,
  per_verdict_micro_usd: calls && cost !== null ? Math.round(cost / calls) : null,
});

const COSTS = {
  currency: "USD",
  pricing: "list",
  windows: {
    today: costWindow(4, 16_400),
    month: costWindow(40, 1_640_000, 2),
    "7d": costWindow(20, 82_000),
    "30d": costWindow(40, 1_640_000, 2),
  },
  by_purpose: {
    window: "30d",
    items: [
      {
        purpose: "verdict",
        calls: 38,
        unpriced_calls: 2,
        cost_micro_usd: 1_600_000,
        prompt_tokens: 410_000,
        completion_tokens: 52_000,
      },
      {
        purpose: "nightly_update",
        calls: 2,
        unpriced_calls: 0,
        cost_micro_usd: 40_000,
        prompt_tokens: 9_000,
        completion_tokens: 2_000,
      },
    ],
  },
  top: {
    window: "30d",
    items: [
      {
        at: 1_790_000_000,
        purpose: "verdict",
        video_id: "kCc8FmEb1nY",
        title: "Deep dive",
        model: "zai-glm-5-3",
        prompt_tokens: 11_800,
        cached_tokens: 0,
        completion_tokens: 2_100,
        latency_ms: 41_000,
        outcome: "invalid_output",
        cost_micro_usd: 25_760,
      },
    ],
  },
};

describe("the health page", () => {
  describe("on the owner's instance", () => {
    it("holds the head and the page's space while the read is out", async () => {
      await mount({ body: OWNER_HEALTH });

      expect(screen.getByRole("heading", { name: "Health" })).toBeInTheDocument();
      expect(screen.getByText("reading…")).toBeInTheDocument();
      expect(await screen.findByText("The queue")).toBeInTheDocument();
      expect(screen.queryByText("reading…")).not.toBeInTheDocument();
    });

    it("prints corpus-summary's own state word and the last index clock", async () => {
      await mount({ body: OWNER_HEALTH });

      expect(await screen.findByText("indexing")).toBeInTheDocument();
      expect(screen.getByText("2025-06-15 15:06")).toBeInTheDocument();
      // The health check reads in UTC minutes like every other clock
      // (§24.6); the attribute keeps the instant.
      expect(screen.getByText("2026-09-05 16:34")).toHaveAttribute(
        "datetime",
        "2026-09-05T16:34:40Z",
      );
    });

    it("shows the queue and the gaps as sentences with numbers in them", async () => {
      await mount({ body: OWNER_HEALTH });

      expect(await screen.findByText(/job\(s\) queued or running/)).toBeInTheDocument();
      expect(screen.getByText("1 of them waiting on a backoff")).toBeInTheDocument();
      expect(screen.getByText(/job\(s\) failed in the last 24 hours/)).toBeInTheDocument();
      expect(
        screen.getByText(/video\(s\) have a transcript but no on-screen text/),
      ).toBeInTheDocument();
      expect(screen.getByText(/waiting to embed their transcript/)).toBeInTheDocument();
      // What is in the corpus is Corpus's page (§24): no band, no lists.
      expect(screen.queryByText("transcript cues")).not.toBeInTheDocument();
      expect(screen.queryByText("Recently indexed")).not.toBeInTheDocument();
      expect(screen.queryByText("Storage")).not.toBeInTheDocument();
    });

    // A failed video is counted on Corpus; Health says there is one and links
    // there, so the number has one home.
    it("points at Corpus for failed videos instead of counting them", async () => {
      await mount({ body: { ...OWNER_HEALTH, gaps: { ...OWNER_HEALTH.gaps, has_failed: true } } });

      expect(await screen.findByText("counts the videos marked failed")).toBeInTheDocument();
      expect(
        screen.getAllByRole("link", { name: "Corpus" }).map((link) => link.getAttribute("href")),
      ).toContain("/dashboard/corpus#states");
    });

    it("drops the gaps panel when nothing is missing", async () => {
      await mount({
        body: {
          ...OWNER_HEALTH,
          gaps: { transcript_no_ocr: 0, indexing: 0, has_failed: false },
        },
      });

      expect(await screen.findByText("The queue")).toBeInTheDocument();
      expect(screen.queryByText("What is missing")).not.toBeInTheDocument();
    });

    it("shows the box: the models it was built with and the worker", async () => {
      await mount({ body: OWNER_HEALTH });

      expect(await screen.findByText("large-v3")).toBeInTheDocument();
      expect(screen.getByText("2048")).toBeInTheDocument();
      // The worker's state is a word in its tone, and its sentence is the
      // footnote rather than the reading.
      expect(screen.getByText("unavailable")).toBeInTheDocument();
      expect(screen.getByText("The worker did not answer its status check.")).toBeInTheDocument();
      // The fifth state is the deployment's, and it comes from this payload.
      expect(screen.getByText("allowed")).toBeInTheDocument();
    });

    // The flag is on this payload because the two things drawn from it are on
    // this page (§19). A session saying otherwise does not get a vote: it is
    // read for other reasons and lands whenever it lands, and a banner that
    // appears a moment after the page is a banner the reader watches arrive.
    it("draws the write state and the drift banner from the payload, not the session", async () => {
      await mount({ body: { ...OWNER_HEALTH, writes_allowed: false } }, OWNER_SESSION);

      expect(
        await screen.findByRole("heading", { name: "The corpus and the worker disagree" }),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Indexing is refused, so no video can mix embedding spaces/),
      ).toBeInTheDocument();
      expect(screen.getByText("refused")).toBeInTheDocument();
      expect(screen.queryByText("allowed")).not.toBeInTheDocument();
      // The one band on this page that opts into the failure tone, as
      // `overview.html`'s `notice notice-bad` did.
      const band = screen.getByRole("region", { name: "The corpus and the worker disagree" });
      expect(band).toHaveAttribute("data-tone", "bad");
    });
  });

  describe("the model's cost", () => {
    it("prints each window, the cost per verdict and the most expensive calls", async () => {
      await mount({ body: OWNER_HEALTH }, OWNER_SESSION, "/dashboard", { body: COSTS });

      expect(await screen.findByRole("heading", { name: "Model cost" })).toBeInTheDocument();
      expect(screen.getByText("$0.0164")).toBeInTheDocument();
      // Two calls had no price: said, never summed as free.
      expect(screen.getAllByText("2 with no known cost")).toHaveLength(2);
      expect(screen.getByText("$0.0410")).toBeInTheDocument();
      expect(screen.getByText("nightly_update")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Deep dive" })).toHaveAttribute(
        "href",
        "/dashboard/videos/kCc8FmEb1nY",
      );
      expect(screen.getByText("invalid_output")).toBeInTheDocument();
    });

    it("is no panel where nothing was called or the route is absent", async () => {
      const idle = { ...COSTS, windows: { ...COSTS.windows, "30d": costWindow(0, null) } };
      await mount({ body: OWNER_HEALTH }, OWNER_SESSION, "/dashboard", { body: idle });
      expect(await screen.findByText("The queue")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Model cost" })).not.toBeInTheDocument();
    });
  });

  describe("in the public projection", () => {
    it("keeps the queue and drops the box, with no nulls on screen", async () => {
      await mount({ body: DEMO_HEALTH }, DEMO_SESSION);

      expect(await screen.findByText(/job\(s\) queued or running/)).toBeInTheDocument();

      // The box is absent rather than blank: no model tables, no worker
      // state, no indexing state.
      expect(screen.queryByText("large-v3")).not.toBeInTheDocument();
      expect(screen.queryByText("unavailable")).not.toBeInTheDocument();
      expect(screen.queryByText("allowed")).not.toBeInTheDocument();
      expect(screen.queryByText("refused")).not.toBeInTheDocument();

      // And nothing rendered the absence itself.
      expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
    });

    // §2.4: the *reason* is written for whoever set the env; the *effect* is
    // what changes a visitor's reading of the results, so the projection keeps
    // the effect and loses the sentence.
    it("tells a visitor search is answering from full-text, and not why", async () => {
      await mount(
        {
          body: {
            ...DEMO_HEALTH,
            readiness: {
              ...DEMO_HEALTH.readiness,
              vectors: { enabled: false, reason: null },
            },
          },
        },
        DEMO_SESSION,
      );

      expect(
        await screen.findByRole("heading", { name: "Vector search is off on this instance" }),
      ).toBeInTheDocument();
      expect(screen.getByText(/Search still answers from full-text/)).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "The corpus and the worker disagree" }),
      ).not.toBeInTheDocument();
    });
  });

  describe("when the read does not land", () => {
    it("says the instance refused it, and offers the way in", async () => {
      await mount({
        status: 401,
        body: {
          error: "E_AUTH_REQUIRED",
          message: "This dashboard needs the owner's password, token or session.",
          next: "Sign in at /dashboard/login, or send Authorization: Bearer $VIDTHEQUE_TOKEN.",
        },
      });

      // `error.html`'s shape: the message is the `<h1>`, the code is a state
      // beside it in its tone, and the page it refused is gone rather than
      // standing over the refusal with a title claiming a reading.
      expect(
        await screen.findByRole("heading", {
          name: "This dashboard needs the owner's password, token or session.",
        }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Health" })).not.toBeInTheDocument();
      expect(screen.getByText("E_AUTH_REQUIRED")).toBeInTheDocument();
      // The message and its next: line are the API's own — policy text stays
      // Python's. Only the first letter is this side's call.
      expect(screen.getByText(/send Authorization: Bearer/)).toBeInTheDocument();
      expect(document.body.textContent).not.toContain("undefined");
    });

    // `templates/error.html`'s panel, and the two links it is made of: the
    // rail already carries every destination, so what a refusal owes the
    // operator is the list the thing they asked for should have been in.
    it("offers somewhere to go, and the way in when there is one", async () => {
      await mount({
        status: 401,
        body: {
          error: "E_AUTH_REQUIRED",
          message: "The dashboard needs the owner's token or session.",
          next: "sign in at /dashboard/login.",
        },
      });

      expect(
        await screen.findByRole("heading", { name: "Where to go from here" }),
      ).toBeInTheDocument();
      expect(screen.getByText("Sign in at /dashboard/login.")).toBeInTheDocument();
      // The 401's own way back is the page that can resolve it, and it goes
      // first — `sign_in_page` set it as the refusal's `back`.
      expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute(
        "href",
        "/dashboard/login",
      );
      expect(screen.getByRole("link", { name: "Everything that is indexed" })).toHaveAttribute(
        "href",
        "/dashboard/videos",
      );
      // The rail's and the refusal's: both go to the landing route.
      for (const link of screen.getAllByRole("link", { name: "Health" })) {
        expect(link).toHaveAttribute("href", "/dashboard");
      }
      // Not the jobs list: that link is the jobs section's, and this is
      // Health.
      expect(
        screen.queryByRole("link", { name: "Every job this index has run" }),
      ).not.toBeInTheDocument();
      // A refusal is a document with a name, the way `_error_page` named one.
      expect(document.title).toBe("The dashboard needs the owner's token or session. — vidtheque");
    });

    // The same panel from a page in the jobs section: the standing link is the
    // list the thing they asked for should have been in.
    it("points a jobs refusal at the jobs list", async () => {
      await mount(
        {
          status: 404,
          body: { error: "E_UNKNOWN_JOB", message: 'No job "job_missing01".', next: null },
        },
        OWNER_SESSION,
        "/dashboard/jobs/job_missing01",
      );

      expect(
        await screen.findByRole("link", { name: "Every job this index has run" }),
      ).toHaveAttribute("href", "/dashboard/jobs");
      expect(
        screen.queryByRole("link", { name: "Everything that is indexed" }),
      ).not.toBeInTheDocument();
    });

    // Under a stopped clock, so the label is the limiter's own delay and not
    // whichever second the box got here: a page halving `Retry-After` counts
    // down just as convincingly.
    it("counts down a 429 rather than inventing a wait", async () => {
      await firstPaint(() =>
        mount({
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
          headers: { "retry-after": "24" },
        }),
      );

      expect(screen.getByText("Too many dashboard requests.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "retry in 24s" })).toBeDisabled();
    });
  });
});
