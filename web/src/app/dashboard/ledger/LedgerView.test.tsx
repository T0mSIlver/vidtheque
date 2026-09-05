// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_LEDGER, DEMO_SESSION, OWNER_LEDGER, OWNER_SESSION } from "@/test/dashboard-fixtures";

// Nothing on this page is new information; what it must not do is disagree with
// the page the numbers came from. So the assertions are the counts, the state
// words with their filters behind them, and the two byte totals the projection
// does not take the read for at all.

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

async function mount(ledger: Route, session: unknown = OWNER_SESSION) {
  stub({
    "/dashboard/api/ledger": ledger,
    "/dashboard/api/session": { body: session },
  });
  const { mockNavigation } = await import("@/test/next");
  mockNavigation("", "/dashboard/ledger");
  const { Chrome } = await import("../Chrome");
  const { LedgerView } = await import("./LedgerView");
  render(
    <Chrome>
      <LedgerView />
    </Chrome>,
  );
}

describe("the ledger", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("carries the corpus band, stamped once", async () => {
    await mount({ body: OWNER_LEDGER });

    expect(await screen.findByRole("heading", { name: "The ledger" })).toBeInTheDocument();
    expect(screen.getByText("videos").closest("div")).toHaveTextContent("4");
    expect(screen.getByText("runtime").closest("div")).toHaveTextContent("4.8h");
    expect(screen.getByText("transcript cues").closest("div")).toHaveTextContent(
      "in 3 embedding chunks",
    );
    expect(screen.getByText("on-screen lines").closest("div")).toHaveTextContent("5");
    // The span under the video count, printed the way the overview prints it:
    // one fact, one spelling, whichever of the two pages you are reading.
    expect(screen.getByText("videos").closest("div")).toHaveTextContent(
      "published 2023-01-17–2025-02-19",
    );
    // One reading, taken inside one request: the head's `counted` stamp and
    // the readiness panel's health check are the same second, by construction.
    expect(screen.getAllByText("2026-09-05 16:34")).toHaveLength(2);
  });

  // An empty corpus has no oldest video and no newest one, which is exactly
  // the corpus an operator is staring at while they wonder why. `published
  // —–—` is a line whose whole content is the absence of one, so there is no
  // line: the count above it already says none.
  it("leaves the published span out on a corpus that has none", async () => {
    await mount({
      body: {
        ...OWNER_LEDGER,
        corpus: { ...OWNER_LEDGER.corpus, published: { oldest: null, newest: null } },
      },
    });

    expect(await screen.findByRole("heading", { name: "The ledger" })).toBeInTheDocument();
    expect(screen.getByText("videos").closest("div")).not.toHaveTextContent("published");
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  // The five state words existed only as a filter on the videos table until
  // this page; each figure is the link to its own filter.
  it("counts the videos by state, and every count is its own filter", async () => {
    await mount({ body: OWNER_LEDGER });

    const states = (await screen.findByText("Videos by state")).closest("section");
    const figure = (label: string) =>
      [...states!.querySelectorAll("div")].find(
        (div) => div.querySelector("dt")?.textContent === label,
      );
    expect(figure("ready")).toHaveTextContent("3");
    expect(figure("ready")?.querySelector("a")).toHaveAttribute(
      "href",
      "/dashboard/videos?index_state=ready",
    );
    expect(figure("stale")).toHaveTextContent("0");
    // A zero is not a door and does not wear the accent, but it is still a
    // link: the filter is one click from the number that says it is empty.
    expect(figure("stale")?.querySelector("a")).toHaveAttribute(
      "href",
      "/dashboard/videos?index_state=stale",
    );
  });

  // The jobs view filters on `all|active|failed|done` and this page does not
  // invent a sixth vocabulary: queued and running both link to `active`, and
  // cancelled — which has no filter of its own — is a figure and not a link.
  it("counts the jobs by state, without inventing a filter for cancelled", async () => {
    await mount({ body: OWNER_LEDGER });

    expect(await screen.findByText("Jobs by state")).toBeInTheDocument();
    const queued = screen.getByText("queued").closest("div");
    expect(queued?.querySelector("a")).toHaveAttribute("href", "/dashboard/jobs?state=active");
    const cancelled = screen.getByText("cancelled").closest("div");
    expect(cancelled?.querySelector("a")).toBeNull();
    expect(cancelled).toHaveTextContent("0");
    expect(screen.getByText("of the queued jobs are waiting on a backoff")).toBeInTheDocument();
    expect(screen.getByText(/job\(s\) failed in the last 24 hours/)).toBeInTheDocument();
  });

  it("reports what is missing, what it is filed under, and what it costs", async () => {
    await mount({ body: OWNER_LEDGER });

    expect(await screen.findByText("What is missing")).toBeInTheDocument();
    expect(screen.getByText("no on-screen text").closest("div")).toHaveTextContent(
      "have a transcript, no OCR",
    );
    expect(screen.getByText("channels").closest("div")).toHaveTextContent("4");
    expect(screen.getByText("keyframe JPEGs").closest("div")).toHaveTextContent("4.3 kB");
    expect(screen.getByText("index file").closest("div")).toHaveTextContent("4.7 MB");
    // The pipeline observation, and the deployment's own state beside it.
    expect(screen.getByText("unavailable")).toBeInTheDocument();
    expect(screen.getByText("allowed")).toBeInTheDocument();
  });

  it("keeps the corpus and drops the box in the projection", async () => {
    await mount({ body: DEMO_LEDGER }, DEMO_SESSION);

    expect(await screen.findByRole("heading", { name: "The ledger" })).toBeInTheDocument();
    expect(screen.getByText("videos").closest("div")).toHaveTextContent("4");
    // §2.4 drops the operator's box, not the corpus: the span is a fact about
    // what is in it, so a visitor gets it.
    expect(screen.getByText("videos").closest("div")).toHaveTextContent(
      "published 2023-01-17–2025-02-19",
    );
    expect(screen.getByText("What it is filed under")).toBeInTheDocument();

    expect(screen.queryByText("keyframe JPEGs")).not.toBeInTheDocument();
    expect(screen.queryByText("index file")).not.toBeInTheDocument();
    expect(screen.queryByText("unavailable")).not.toBeInTheDocument();
    expect(screen.queryByText("allowed")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  describe("when the read does not land", () => {
    it("prints the instance's own refusal", async () => {
      await mount({
        status: 401,
        body: {
          error: "E_AUTH_REQUIRED",
          message: "This dashboard needs the owner's password, token or session.",
          next: "Sign in at /dashboard/login.",
        },
      });

      expect(await screen.findByRole("heading", { name: "The ledger" })).toBeInTheDocument();
      expect(
        screen.getByText("This dashboard needs the owner's password, token or session."),
      ).toBeInTheDocument();
      expect(screen.getByText("Sign in at /dashboard/login.")).toBeInTheDocument();
    });

    it("counts down a 429", async () => {
      await mount({
        status: 429,
        body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
        headers: { "retry-after": "12" },
      });

      expect(await screen.findByRole("button", { name: "retry in 12s" })).toBeDisabled();
    });

    // A payload that does not match the contract fails at the boundary rather
    // than three components deep as `undefined`.
    it("says so when the instance answers in a shape it cannot read", async () => {
      await mount({ body: { ...OWNER_LEDGER, videos_by_state: null } });

      expect(await screen.findByText(/shape this page cannot read/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "try again" })).toBeEnabled();
    });
  });
});
