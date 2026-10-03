// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mountDashboard, type Answer } from "@/test/dashboard/harness";
import { DEMO_CORPUS, DEMO_SESSION, OWNER_CORPUS, OWNER_SESSION } from "@/test/dashboard/fixtures";
import { firstPaint } from "@/test/dashboard/retry";
import { CorpusView } from "./CorpusView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// What is in the corpus: the counts, the state words with their filters, the
// lists, and what the projection drops.

function mount(corpus: Answer, session: unknown = OWNER_SESSION) {
  return mountDashboard(<CorpusView />, {
    path: "/dashboard/corpus",
    session,
    routes: { "/dashboard/api/corpus": corpus },
  });
}

describe("the corpus page", () => {
  it("carries the corpus band, stamped once", async () => {
    await mount({ body: OWNER_CORPUS });

    expect(await screen.findByText("transcript cues")).toBeInTheDocument();
    expect(screen.getByText("videos").closest("div")).toHaveTextContent("4");
    expect(screen.getByText("runtime").closest("div")).toHaveTextContent("4.8h");
    expect(screen.getByText("transcript cues").closest("div")).toHaveTextContent(
      "in 3 embedding chunks",
    );
    expect(screen.getByText("on-screen lines").closest("div")).toHaveTextContent("5");
    expect(screen.getByText("videos").closest("div")).toHaveTextContent(
      "published 2023-01-17–2025-02-19",
    );
    // One reading, stamped once, to the second: it is the instant of a
    // reading and not a date in the corpus.
    const stamp = screen.getByText("2026-09-05T16:34:40Z");
    expect(stamp.tagName).toBe("TIME");
    expect(stamp).toHaveAttribute("datetime", "2026-09-05T16:34:40Z");
  });

  // An empty corpus has no oldest video and no newest one, which is exactly
  // the corpus an operator is staring at while they wonder why. `published
  // —–—` is a line whose whole content is the absence of one, so there is no
  // line: the count above it already says none.
  it("leaves the published span out on a corpus that has none", async () => {
    await mount({
      body: {
        ...OWNER_CORPUS,
        corpus: { ...OWNER_CORPUS.corpus, published: { oldest: null, newest: null } },
      },
    });

    expect(await screen.findByText("transcript cues")).toBeInTheDocument();
    expect(screen.getByText("videos").closest("div")).not.toHaveTextContent("published");
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  // The five state words existed only as a filter on the videos table until
  // this page; each figure is the link to its own filter.
  it("counts the videos by state, and every count is its own filter", async () => {
    await mount({ body: OWNER_CORPUS });

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
    await mount({ body: OWNER_CORPUS });

    expect(await screen.findByText("Jobs by state")).toBeInTheDocument();
    const queued = screen.getByText("queued").closest("div");
    expect(queued?.querySelector("a")).toHaveAttribute("href", "/dashboard/jobs?state=active");
    const cancelled = screen.getByText("cancelled").closest("div");
    expect(cancelled?.querySelector("a")).toBeNull();
    expect(cancelled).toHaveTextContent("0");
    expect(screen.getByText("of the queued jobs are waiting on a backoff")).toBeInTheDocument();
    expect(screen.getByText(/job\(s\) failed in the last 24 hours/)).toBeInTheDocument();
  });

  it("lists the channels and tags, and what it costs on disk", async () => {
    await mount({ body: OWNER_CORPUS });

    expect(await screen.findByRole("link", { name: "GPU MODE" })).toHaveAttribute(
      "href",
      "/dashboard/videos?channel=GPU%20MODE&index_state=all",
    );
    expect(screen.getByRole("link", { name: /topic:attention/ })).toBeInTheDocument();
    expect(screen.getByText("keyframe JPEGs").closest("div")).toHaveTextContent("4.3 kB");
    expect(screen.getByText("index file").closest("div")).toHaveTextContent("4.7 MB");
    // Health's, and only Health's (§24).
    expect(screen.queryByText("Pipeline readiness")).not.toBeInTheDocument();
    expect(screen.queryByText("What is missing")).not.toBeInTheDocument();
    expect(screen.queryByText(/has_more|The largest/)).not.toBeInTheDocument();
  });

  it("says a capped channel list is the largest ones", async () => {
    await mount({
      body: { ...OWNER_CORPUS, channels: { ...OWNER_CORPUS.channels, has_more: true } },
    });

    expect(await screen.findByText(/The largest 3/)).toBeInTheDocument();
  });

  it("keeps the corpus and drops the box in the projection", async () => {
    await mount({ body: DEMO_CORPUS }, DEMO_SESSION);

    expect(await screen.findByText("transcript cues")).toBeInTheDocument();
    expect(screen.getByText("videos").closest("div")).toHaveTextContent("4");
    // §2.4 drops the operator's box, not the corpus: the span is a fact about
    // what is in it, so a visitor gets it.
    expect(screen.getByText("videos").closest("div")).toHaveTextContent(
      "published 2023-01-17–2025-02-19",
    );
    expect(screen.getByText("Channels")).toBeInTheDocument();

    expect(screen.queryByText("Storage")).not.toBeInTheDocument();
    expect(screen.queryByText("keyframe JPEGs")).not.toBeInTheDocument();
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

      // The refusal replaces the page: the message is the title, the code is
      // the state beside it, and the page's own head is not over the top of
      // it claiming a reading that did not happen.
      expect(
        await screen.findByRole("heading", {
          name: "This dashboard needs the owner's password, token or session.",
        }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Corpus" })).not.toBeInTheDocument();
      expect(screen.getByText("E_AUTH_REQUIRED")).toBeInTheDocument();
      // Python writes the `next:` line as a fragment that used to trail a
      // colon; standing on its own under a heading it takes the capital.
      expect(screen.getByText("Sign in at /dashboard/login.")).toBeInTheDocument();
    });

    // Under a stopped clock: the label has to be the delay the limiter named,
    // and a page that halved it would count down just as convincingly.
    it("counts down a 429", async () => {
      await firstPaint(() =>
        mount({
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
          headers: { "retry-after": "12" },
        }),
      );

      expect(screen.getByRole("button", { name: "retry in 12s" })).toBeDisabled();
    });

    // A payload that does not match the contract fails at the boundary rather
    // than three components deep as `undefined`.
    it("says so when the instance answers in a shape it cannot read", async () => {
      await mount({ body: { ...OWNER_CORPUS, videos_by_state: null } });

      expect(
        await screen.findByRole("heading", { name: /shape this page cannot read/ }),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    });
  });
});
