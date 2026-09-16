// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION } from "@/test/dashboard-fixtures";
import { DEMO_HALF, OWNER_HALF } from "@/test/library-fixtures";
import { mountVideo } from "./detail-harness";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The seven stages and their models, and what the projection drops.

describe("the provenance panel", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("shows the seven stages with the model that produced each", async () => {
    await mountVideo({ body: OWNER_HALF }, { videoId: "aaaaaaaaaaa" });

    const table = await screen.findByRole("table", {
      name: /Each pipeline stage, its state and the model/,
    });
    // All seven, `absent` included: a stage that never ran is a different fact
    // from a stage that ran and produced nothing.
    expect(within(table).getAllByRole("row")).toHaveLength(9); // head + 7 + the error row
    expect(within(table).getByText("yt-dlp-2026.07.04")).toBeInTheDocument();
    expect(within(table).getAllByText("absent")).toHaveLength(5);
    // The pipeline's own words about the operator's box, on the owner's page.
    expect(within(table).getByText(/Sign in to confirm you are not a bot/)).toBeInTheDocument();
  });

  it("names the failed stage where the eye already is", async () => {
    await mountVideo({ body: OWNER_HALF }, { videoId: "aaaaaaaaaaa" });

    expect(await screen.findByText(/did not finish/)).toBeInTheDocument();
    // `video-summary`'s refusal is why the panels below are thin, so it is a
    // fact about the video rather than a failure of this page's read.
    expect(screen.getByText(/mid-pipeline; only partial data is queryable/)).toBeInTheDocument();
    expect(screen.getByText("E_INDEXING")).toBeInTheDocument();
  });

  // §2.4: the demo gets the detail whole minus the two fields that are the
  // operator's console. The column those fields filled keeps its place and
  // prints the dash — the table is five columns wide on both projections, so
  // the absence is something a reader can see rather than a layout that
  // silently differs from the one in the screenshot they are comparing against.
  it("drops the model ids and the pipeline's prose in the projection", async () => {
    await mountVideo({ body: DEMO_HALF }, { videoId: "aaaaaaaaaaa", session: DEMO_SESSION });

    const table = await screen.findByRole("table", {
      name: /Each pipeline stage, its state and the model/,
    });
    expect(within(table).getByRole("columnheader", { name: "model" })).toBeInTheDocument();
    // Seven rows, and not one of them names a model.
    expect(within(table).getAllByRole("row")).toHaveLength(8);
    expect(within(table).queryByRole("cell", { name: /whisper|paddle|nvidia/i })).toBeNull();
    expect(screen.queryByText(/Sign in to confirm you are not a bot/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("yt-dlp");
    // …and what a reader can act on survives: the states, the versions and the
    // clocks. Dropping them would leave an empty shell.
    expect(within(table).getByText("failed")).toBeInTheDocument();
    expect(within(table).getAllByText("absent")).toHaveLength(5);
  });
});
