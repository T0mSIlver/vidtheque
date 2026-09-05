// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEMO_OVERVIEW,
  DEMO_SESSION,
  OWNER_OVERVIEW,
  OWNER_SESSION,
} from "@/test/dashboard-fixtures";

// The page renders against two payloads, not one. `docs/ROADMAP.md` names the
// failure this guards: a page that renders a field the projection drops. So
// every state below is asserted twice where the two differ — the operator's
// instance sees the box it is on, the demo sees the corpus and nothing else.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

function stub(routes: Record<string, Route>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const route = routes[String(input)] ?? { status: 404, body: {} };
      const text = typeof route.body === "string" ? route.body : JSON.stringify(route.body ?? {});
      return new Response(text, {
        status: route.status ?? 200,
        headers: { "content-type": "application/json", ...route.headers },
      });
    }),
  );
}

async function mount(overview: Route, session: unknown = OWNER_SESSION) {
  stub({
    "/dashboard/api/overview": overview,
    "/dashboard/api/session": { body: session },
  });
  const { Chrome } = await import("./Chrome");
  const { OverviewView } = await import("./OverviewView");
  render(
    <Chrome>
      <OverviewView />
    </Chrome>,
  );
}

describe("the corpus overview", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  describe("on the owner's instance", () => {
    it("counts the corpus, in the band", async () => {
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount({ body: OWNER_OVERVIEW });

      expect(await screen.findByRole("heading", { name: "Corpus overview" })).toBeInTheDocument();
      // Five figures, each with its label; the hours are rounded here and not
      // on the wire.
      expect(screen.getByText("videos").closest("div")).toHaveTextContent("4");
      expect(screen.getByText("runtime").closest("div")).toHaveTextContent("4.8h");
      expect(screen.getByText("transcript cues").closest("div")).toHaveTextContent("10");
      expect(screen.getByText("keyframes").closest("div")).toHaveTextContent("3");
      expect(screen.getByText("on-screen lines").closest("div")).toHaveTextContent("5");
      // The note is several text nodes, because "not ready" is a link.
      expect(screen.getByText("videos").closest("div")).toHaveTextContent("3 ready · 1 not ready");
      expect(screen.getByText("2023-01-17")).toBeInTheDocument();
    });

    // An empty corpus has no oldest video and no newest one. `published —–—`
    // is a line whose whole content is the absence of one, so there is no
    // line — the same absent state the ledger renders, because it is the same
    // note printed from the same place.
    it("leaves the published span out on a corpus that has none", async () => {
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount({
        body: {
          ...OWNER_OVERVIEW,
          corpus: { ...OWNER_OVERVIEW.corpus, published: { oldest: null, newest: null } },
        },
      });

      expect(await screen.findByRole("heading", { name: "Corpus overview" })).toBeInTheDocument();
      expect(screen.getByText("videos").closest("div")).not.toHaveTextContent("published");
      expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
    });

    it("prints corpus-summary's own state word and the last index clock", async () => {
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount({ body: OWNER_OVERVIEW });

      expect(await screen.findByText("indexing")).toBeInTheDocument();
      // The head's clock, and the two arrivals' — one formatter, one reading.
      expect(screen.getAllByText("2025-06-15 15:06").length).toBeGreaterThan(0);
    });

    it("shows the queue, the gaps and the arrivals as sentences with numbers in them", async () => {
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount({ body: OWNER_OVERVIEW });

      expect(await screen.findByText(/job\(s\) queued or running/)).toBeInTheDocument();
      expect(screen.getByText("1 of them waiting on a backoff")).toBeInTheDocument();
      expect(screen.getByText(/job\(s\) failed in the last 24 hours/)).toBeInTheDocument();
      expect(
        screen.getByText(/video\(s\) have a transcript but no on-screen text/),
      ).toBeInTheDocument();

      expect(screen.getByText("Let's build GPT: from scratch")).toBeInTheDocument();
      // 7000 s, formatted here, in the span the whole surface uses.
      expect(screen.getByText("1h 56m")).toBeInTheDocument();
      // A video with no keyframe keeps the row's height and says so.
      expect(screen.getByText("no frame")).toBeInTheDocument();
    });

    // A video id is a key in the store, not a piece of URL: one carrying a `?`
    // or a `#` unencoded would open the detail page for a different video, or
    // for none — with the rest of the id read as a query string.
    it("encodes the id of the video each arrival links to", async () => {
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount({
        body: {
          ...OWNER_OVERVIEW,
          recent: [{ ...OWNER_OVERVIEW.recent[0], video_id: "a?b#c", title: "An odd id" }],
        },
      });

      expect(await screen.findByRole("link", { name: "An odd id" })).toHaveAttribute(
        "href",
        "/dashboard/videos/a%3Fb%23c",
      );
    });

    it("shows the box: the models it was built with, the worker, and the bytes", async () => {
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount({ body: OWNER_OVERVIEW });

      expect(await screen.findByText("large-v3")).toBeInTheDocument();
      expect(screen.getByText("2048")).toBeInTheDocument();
      // The worker's state is a word in its tone, and its sentence is the
      // footnote rather than the reading.
      expect(screen.getByText("unavailable")).toBeInTheDocument();
      expect(screen.getByText("The worker did not answer its status check.")).toBeInTheDocument();
      expect(screen.getByText("Storage")).toBeInTheDocument();
      expect(screen.getByText("4.3 kB")).toBeInTheDocument();
      expect(screen.getByText("4.7 MB")).toBeInTheDocument();
      // The fifth state is the deployment's, and it comes from the session.
      expect(screen.getByText("allowed")).toBeInTheDocument();
    });
  });

  describe("in the public projection", () => {
    it("keeps the corpus and drops the box, with no nulls on screen", async () => {
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount({ body: DEMO_OVERVIEW }, DEMO_SESSION);

      // The corpus is all still there.
      expect(await screen.findByRole("heading", { name: "Corpus overview" })).toBeInTheDocument();
      expect(screen.getByText("videos").closest("div")).toHaveTextContent("3 ready");
      expect(screen.getByText("Let's build GPT: from scratch")).toBeInTheDocument();
      expect(screen.getByText("topic:attention")).toBeInTheDocument();

      // The box is absent rather than blank: no storage panel, no model
      // tables, no worker state, no indexing state.
      expect(screen.queryByText("Storage")).not.toBeInTheDocument();
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
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount(
        {
          body: {
            ...DEMO_OVERVIEW,
            readiness: {
              ...DEMO_OVERVIEW.readiness,
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
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount({
        status: 401,
        body: {
          error: "E_AUTH_REQUIRED",
          message: "This dashboard needs the owner's password, token or session.",
          next: "Sign in at /dashboard/login, or send Authorization: Bearer $VIDTHEQUE_TOKEN.",
        },
      });

      expect(
        await screen.findByText("This dashboard needs the owner's password, token or session."),
      ).toBeInTheDocument();
      // The message and its next: line are the API's own — policy text stays
      // Python's.
      expect(screen.getByText(/send Authorization: Bearer/)).toBeInTheDocument();
      expect(document.body.textContent).not.toContain("undefined");
    });

    it("counts down a 429 rather than inventing a wait", async () => {
      const { mockNavigation } = await import("@/test/next");
      mockNavigation("", "/dashboard");
      await mount({
        status: 429,
        body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
        headers: { "retry-after": "24" },
      });

      expect(await screen.findByText("Too many dashboard requests.")).toBeInTheDocument();
      const retry = screen.getByRole("button", { name: "retry in 24s" });
      expect(retry).toBeDisabled();
    });
  });
});
