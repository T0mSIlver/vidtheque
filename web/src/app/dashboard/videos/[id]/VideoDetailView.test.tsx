// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import {
  DEMO_HALF,
  DEMO_VIDEO,
  OWNER_CUES,
  OWNER_HALF,
  OWNER_VIDEO,
} from "@/test/library-fixtures";

// The page the dashboard exists for: what the pipeline did to one video, what
// it produced, and what it read off the screen. So the assertions are the
// receipts — the seven stages with the model that produced each, the shot band
// drawn from seconds this page turned into percentages, the OCR boxes at the
// coordinates the store holds — and the two fields the projection drops.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

async function mount(
  detail: Route,
  {
    cues = { body: OWNER_CUES } as Route,
    search = "",
    session = OWNER_SESSION as unknown,
    videoId = "kCc8FmEb1nY",
  } = {},
) {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const route: Route = url.includes("/cues")
      ? cues
      : url.startsWith("/dashboard/api/library/")
        ? detail
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
  const nav = mockNavigation(search, `/dashboard/videos/${videoId}`);
  const { Chrome } = await import("../../Chrome");
  const { VideoDetailView } = await import("./VideoDetailView");
  render(
    <Chrome>
      <VideoDetailView videoId={videoId} />
    </Chrome>,
  );
  return { ...nav, fetcher };
}

describe("the video detail", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("carries the header, both state words and the source", async () => {
    await mount({ body: OWNER_VIDEO });

    expect(
      await screen.findByRole("heading", { name: "Let's build GPT: from scratch" }),
    ).toBeInTheDocument();
    expect(screen.getByText("index_state")).toBeInTheDocument();
    // `data_status` is shown only when it says something `index_state` did not,
    // and on a finished video it says `ok` where the state says `ready`.
    expect(screen.getByText("data_status")).toBeInTheDocument();
    expect(screen.getByText("Andrej Karpathy")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open on YouTube" })).toHaveAttribute(
      "href",
      "https://youtu.be/kCc8FmEb1nY",
    );
    // The document is named after data the server never saw.
    expect(document.title).toBe("Let's build GPT: from scratch — vidtheque");
  });

  it("counts what was stored, and where the cues came from", async () => {
    await mount({ body: OWNER_VIDEO });

    expect(await screen.findByText("What was stored")).toBeInTheDocument();
    expect(screen.getByText("cues").closest("div")).toHaveTextContent("whisperx 6");
    expect(screen.getByText("keyframes").closest("div")).toHaveTextContent("kept of 3 captured");
    expect(screen.getByText("frames with text").closest("div")).toHaveTextContent("2 lines read");
    expect(screen.getByText("keyframe bytes").closest("div")).toHaveTextContent(
      "no word timings stored",
    );
  });

  // The percentages are this page's arithmetic over three numbers that are all
  // on the payload; none of the three is a percentage.
  it("draws one bar per shot, positioned against the runtime", async () => {
    await mount({ body: OWNER_VIDEO });

    const band = await screen.findByRole("list", { name: "Shots across the runtime" });
    const bars = within(band).getAllByRole("listitem");
    expect(bars).toHaveLength(3);
    // 5s into 7000s, five seconds long.
    expect(bars[0]).toHaveStyle({ left: "0.07142857142857142%" });
    expect(
      within(band).getByText("Shot 0, 0:05 to 0:10, 1 of 1 keyframes kept"),
    ).toBeInTheDocument();
    // A bar carries the strip page holding its first keyframe, and the ordinal
    // the fragment carries — a fragment never reaches a server.
    expect(within(band).getAllByRole("link")[2]).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?frame_offset=0&select=7#frame-7",
    );
  });

  it("shows the seven stages with the model that produced each", async () => {
    await mount({ body: OWNER_HALF }, { videoId: "aaaaaaaaaaa" });

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
    await mount({ body: OWNER_HALF }, { videoId: "aaaaaaaaaaa" });

    expect(await screen.findByText(/did not finish/)).toBeInTheDocument();
    // `video-summary`'s refusal is why the panels below are thin, so it is a
    // fact about the video rather than a failure of this page's read.
    expect(screen.getByText(/mid-pipeline; only partial data is queryable/)).toBeInTheDocument();
    expect(screen.getByText("E_INDEXING")).toBeInTheDocument();
  });

  it("draws every OCR box at the coordinates the store holds", async () => {
    const { container } = { container: document.body };
    await mount({ body: OWNER_VIDEO });

    await screen.findByText("Frames, and what the machine read");
    const boxes = container.querySelectorAll("[aria-hidden='true'][style*='left']");
    // Two of the three keyframes carry a line; the third was deduplicated.
    expect(boxes.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("nvidia-smi 18304MiB")).toBeInTheDocument();
    expect(screen.getByText("duplicate of #0")).toBeInTheDocument();
    // The card links the full-width still, never an inline base64 copy of it.
    expect(screen.getAllByRole("link", { name: /Keyframe 0 at 0:05/ })[0]).toHaveAttribute(
      "href",
      "/frames/kCc8FmEb1nY-00000.jpg?w=1280&q=70",
    );
  });

  it("pages the strip through the URL, keeping the reader's page size", async () => {
    await mount(
      { body: { ...OWNER_VIDEO, frames: { ...OWNER_VIDEO.frames, limit: 2, has_more: true } } },
      { search: "frames=2" },
    );

    const pager = await screen.findByRole("navigation", { name: "Keyframe pages" });
    expect(within(pager).getByRole("link", { name: "Next 2 frames →" })).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?frames=2&frame_offset=2#frames",
    );
  });

  // The transcript is a pointer, not a copy: the detail payload carries the
  // totals and the endpoint's name, and the cues arrive a page at a time.
  it("reads the transcript from the endpoint the payload names", async () => {
    const { fetcher } = await mount({ body: OWNER_VIDEO });

    expect(await screen.findByText("we cache the keys and the values at every new token"));
    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/videos/kCc8FmEb1nY/cues?offset=0&limit=50",
      expect.objectContaining({ credentials: "same-origin" }),
    );
    // Totals, not position: the count the counts band already read, plus the
    // words and characters, which is what "how big is this transcript" means.
    const totals = screen.getByText("292").closest("p");
    expect(totals).toHaveTextContent("6 cues");
    expect(totals).toHaveTextContent("54 words");
    expect(totals).toHaveTextContent("292 chars");
  });

  // The endpoint answers with both halves. This renders the typed one — the
  // timecode from `start_s`, the chunk label composed from the chunk's own five
  // fields — and falls back to the strings on an instance that predates them.
  it("renders the typed cue fields, and falls back to the strings without them", async () => {
    await mount({ body: OWNER_VIDEO });
    expect(
      await screen.findByText(/chunk 0 · 0:00–7:03 · 54 words · 297 chars/),
    ).toBeInTheDocument();

    vi.unstubAllGlobals();
    vi.resetModules();

    const TYPED = ["start_s", "end_s", "avg_logprob", "chunk_opens", "chunk_closes"];
    const stringsOnly = {
      ...OWNER_CUES,
      cues: OWNER_CUES.cues.map((cue) =>
        Object.fromEntries(Object.entries(cue).filter(([key]) => !TYPED.includes(key))),
      ),
    };
    await mount({ body: OWNER_VIDEO }, { cues: { body: stringsOnly } });
    expect(
      await screen.findByText(/chunk 0 · 0:00–7:03 · 54 words · 297 chars/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("0:00").length).toBeGreaterThan(0);
  });

  it("appends the next batch rather than reloading the page", async () => {
    const { fetcher } = await mount({ body: OWNER_VIDEO });
    await screen.findByText("we cache the keys and the values at every new token");

    await userEvent.click(screen.getByRole("button", { name: /Next 50 cues/ }));

    // The offset is the server's own: `page.offset + page.cues.length`, not
    // this page's arithmetic over a limit it asked for.
    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/videos/kCc8FmEb1nY/cues?offset=3&limit=50",
      expect.anything(),
    );
  });

  it("says a transcript is absent rather than showing an empty box", async () => {
    await mount({ body: OWNER_HALF }, { videoId: "aaaaaaaaaaa" });

    expect(await screen.findByText("No transcript cues for this video.")).toBeInTheDocument();
    expect(
      screen.getByText("No keyframes were captured, so this video has no shots."),
    ).toBeInTheDocument();
    expect(screen.getByText("No indexing job is linked to this video.")).toBeInTheDocument();
  });

  it("lists the jobs that touched this video", async () => {
    await mount({ body: OWNER_VIDEO });

    expect(await screen.findByText("Recent indexing runs")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "job_running001" })).toHaveAttribute(
      "href",
      "/dashboard/jobs/job_running001",
    );
    expect(screen.getByText("Latest 10 at most; no total is computed.")).toBeInTheDocument();
  });

  // §2.4: the demo gets the detail whole minus the two fields that are the
  // operator's console, and the column those fields filled is simply not drawn.
  it("drops the model ids and the pipeline's prose in the projection", async () => {
    await mount({ body: DEMO_HALF }, { videoId: "aaaaaaaaaaa", session: DEMO_SESSION });

    const table = await screen.findByRole("table", {
      name: /Each pipeline stage, its state and the model/,
    });
    expect(within(table).queryByRole("columnheader", { name: "model" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Sign in to confirm you are not a bot/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("yt-dlp");
    // …and what a reader can act on survives: the states, the versions and the
    // clocks. Dropping them would leave an empty shell.
    expect(within(table).getByText("failed")).toBeInTheDocument();
    expect(within(table).getAllByText("absent")).toHaveLength(5);
  });

  it("gives the demo every panel of a finished video", async () => {
    await mount({ body: DEMO_VIDEO }, { session: DEMO_SESSION });

    expect(await screen.findByText("What was stored")).toBeInTheDocument();
    expect(screen.getByText("nvidia-smi 18304MiB")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  describe("when the read does not land", () => {
    // An id that is not in the corpus is not a failure to read the instance:
    // the read succeeded and the answer is "there is no such video".
    it("answers an unknown video with the refusal and a way back", async () => {
      await mount(
        {
          status: 404,
          body: {
            error: "E_UNKNOWN_VIDEO",
            message: '"nope" is not in the corpus.',
            next: "browse the videos table for what is indexed.",
          },
        },
        { videoId: "nope" },
      );

      expect(await screen.findByText('"nope" is not in the corpus.')).toBeInTheDocument();
      expect(screen.getByText("E_UNKNOWN_VIDEO")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Back to the videos table" })).toHaveAttribute(
        "href",
        "/dashboard/videos",
      );
      // Not the error state: retrying will produce this answer again.
      expect(screen.queryByRole("button", { name: "try again" })).not.toBeInTheDocument();
    });

    it("prints the instance's own refusal when it is signed out", async () => {
      await mount({
        status: 401,
        body: {
          error: "E_AUTH_REQUIRED",
          message: "This dashboard needs the owner's password, token or session.",
          next: "Sign in at /dashboard/login.",
        },
      });

      expect(
        await screen.findByText("This dashboard needs the owner's password, token or session."),
      ).toBeInTheDocument();
    });

    it("counts down a 429", async () => {
      await mount({
        status: 429,
        body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
        headers: { "retry-after": "7" },
      });

      expect(await screen.findByRole("button", { name: "retry in 7s" })).toBeDisabled();
    });

    it("keeps the page when only the transcript's next batch fails", async () => {
      await mount(
        { body: OWNER_VIDEO },
        { cues: { status: 429, body: { error: "E_RATE_LIMIT", message: "Slow down." } } },
      );

      expect(await screen.findByText("What was stored")).toBeInTheDocument();
      expect(await screen.findByText("Slow down.")).toBeInTheDocument();
      // The panels the detail payload answered for are untouched.
      expect(screen.getByText("Provenance")).toBeInTheDocument();
    });

    it("says so when the instance answers in a shape it cannot read", async () => {
      await mount({ body: { ...OWNER_VIDEO, stages: null } });

      expect(await screen.findByText(/shape this page cannot read/)).toBeInTheDocument();
    });
  });
});
