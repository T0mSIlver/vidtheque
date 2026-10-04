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

function mount(corpus: Answer, session: unknown = OWNER_SESSION, ledger?: Answer) {
  return mountDashboard(<CorpusView />, {
    path: "/dashboard/corpus",
    session,
    routes: {
      "/dashboard/api/corpus": corpus,
      ...(ledger ? { "/dashboard/api/valued-time": ledger } : {}),
    },
  });
}

const week = (
  start: number,
  current: boolean,
  kept: number,
  offered: number,
  down: number,
  watched: number,
) => ({
  start,
  current,
  hits: { kept, offered, rate: offered ? kept / offered : null, capped: false },
  regret: { down, watched, rate: watched ? down / watched : null, capped: false },
  misses: { count: 2, pending: 1, shared: 4, capped: false },
});

const LEDGER = {
  regret_target: 0.1,
  // Mondays 00:00 at UTC+2, the box's clock.
  weeks: [week(1_791_756_000, true, 3, 7, 1, 6), week(1_791_151_200, false, 0, 0, 0, 0)],
};

describe("the corpus page", () => {
  it("says the corpus's size in one line under the title", async () => {
    await mount({ body: OWNER_CORPUS });

    const line = (await screen.findByRole("link", { name: "4 videos" })).closest("p")!;
    expect(line).toHaveTextContent("4 videos, 4.8 hours, published 2023-01-17–2025-02-19");
    expect(screen.getByRole("link", { name: "4 videos" })).toHaveAttribute(
      "href",
      "/dashboard/videos?index_state=all",
    );
    // No band of big figures, and no "counted" stamp.
    expect(screen.queryByText("runtime")).not.toBeInTheDocument();
    expect(screen.queryByText("2026-09-05 16:34")).not.toBeInTheDocument();
  });

  it("leaves the published span out on a corpus that has none", async () => {
    await mount({
      body: {
        ...OWNER_CORPUS,
        corpus: { ...OWNER_CORPUS.corpus, published: { oldest: null, newest: null } },
      },
    });

    const line = (await screen.findByRole("link", { name: "4 videos" })).closest("p")!;
    expect(line).not.toHaveTextContent("published");
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  // Statistics on demand: the counts are behind a toggle, and a state no
  // video is in is not printed.
  it("keeps every other count behind a toggle, zero states left out", async () => {
    await mount({ body: OWNER_CORPUS });

    expect(await screen.findByRole("button", { name: "Show every count" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByRole("link", { name: "3 ready" })).toHaveAttribute(
      "href",
      "/dashboard/videos?index_state=ready",
    );
    expect(screen.getByRole("link", { name: "1 indexing" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /stale/ })).not.toBeInTheDocument();
    expect(screen.getByText("Transcript cues").closest("div")).toHaveTextContent("10 in 3 chunks");
    expect(screen.getByText("Keyframe images").closest("div")).toHaveTextContent("4.3 kB");
    expect(screen.getByText("Index file").closest("div")).toHaveTextContent("4.7 MB");
  });

  it("lists the channels and tags as filters on the videos table", async () => {
    await mount({ body: OWNER_CORPUS });

    expect(await screen.findByRole("link", { name: "GPU MODE" })).toHaveAttribute(
      "href",
      "/dashboard/videos?channel=GPU%20MODE&index_state=all",
    );
    expect(screen.getByRole("link", { name: /topic:attention/ })).toBeInTheDocument();
    expect(screen.queryByText(/The largest/)).not.toBeInTheDocument();
  });

  it("says a capped channel list is the largest ones", async () => {
    await mount({
      body: { ...OWNER_CORPUS, channels: { ...OWNER_CORPUS.channels, has_more: true } },
    });

    expect(await screen.findByText("The largest 3.")).toBeInTheDocument();
  });

  it("says this week against YouTube in a line, the weeks before behind a toggle", async () => {
    await mount({ body: OWNER_CORPUS }, OWNER_SESSION, { body: LEDGER });

    expect(await screen.findByText(/hit rate, 3 of 7 kept/)).toBeInTheDocument();
    expect(screen.getByText("17%")).toHaveClass(/warn/);
    expect(screen.getByText(/regret, 1 of 6 watched/)).toBeInTheDocument();
    expect(screen.getByText(/misses, 1 not judged/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show the week before" })).toBeInTheDocument();
    // An empty week is a dash, never 0%; dated by the box's Monday.
    expect(screen.getByText("from 2026-10-05").closest("tr")).toHaveTextContent("—0 / 0—0 / 0");
  });

  it("has no ledger where the instance has no feed", async () => {
    await mount({ body: OWNER_CORPUS }, OWNER_SESSION, {
      status: 404,
      body: { error: "E_NOT_FOUND" },
    });

    expect(await screen.findByRole("heading", { name: "Channels" })).toBeInTheDocument();
    expect(screen.queryByText("Against YouTube")).not.toBeInTheDocument();
  });

  it("keeps the corpus and drops the box in the projection", async () => {
    await mount({ body: DEMO_CORPUS }, DEMO_SESSION);

    const line = (await screen.findByRole("link", { name: "4 videos" })).closest("p")!;
    // §2.4 drops the operator's box, not the corpus.
    expect(line).toHaveTextContent("published 2023-01-17–2025-02-19");
    expect(screen.getByText("Channels")).toBeInTheDocument();
    expect(screen.queryByText("Keyframe images")).not.toBeInTheDocument();
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
