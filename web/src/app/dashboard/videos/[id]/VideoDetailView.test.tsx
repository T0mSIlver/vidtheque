// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import { REINDEX_REFUSED, REINDEXED, TAG_REFUSED, TAGGED } from "@/test/index-fixtures";
import { firstPaint } from "@/test/retry";

// The page the dashboard exists for: what the pipeline did to one video, what
// it produced, and what it read off the screen. So the assertions are the
// receipts — the seven stages with the model that produced each, the shot band
// drawn from seconds this page turned into percentages, the OCR boxes at the
// coordinates the store holds — and the two fields the projection drops.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

async function mount(
  detail: Route,
  {
    // A function where the answer depends on which page was asked for: the
    // transcript is the one panel here that reads more than once.
    cues = { body: OWNER_CUES } as Route | ((url: string) => Route),
    post = { body: REINDEXED } as Route,
    search = "",
    session = OWNER_SESSION as unknown,
    videoId = "kCc8FmEb1nY",
  } = {},
) {
  const posts: { path: string; init: RequestInit }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") posts.push({ path: url, init });
    const route: Route =
      init?.method === "POST"
        ? post
        : url.includes("/cues")
          ? typeof cues === "function"
            ? cues(url)
            : cues
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
  return { ...nav, fetcher, posts };
}

/** `OWNER_VIDEO` with a second OCR line on frame 1: the pairing is by index,
 *  and one line cannot tell a working pairing from a lit-everything one. */
const TWO_LINES = {
  ...OWNER_VIDEO,
  frames: {
    ...OWNER_VIDEO.frames,
    frames: OWNER_VIDEO.frames.frames.map((frame) =>
      frame.ord === 1
        ? {
            ...frame,
            lines: [
              ...frame.lines,
              { line_no: 1, text: "loss 3.14", conf: 0.71, box: [0.1, 0.6, 0.4, 0.68] },
            ],
          }
        : frame,
    ),
  },
};

/**
 * A page of cues the endpoint would answer with, for whatever `offset` and
 * `limit` were asked for — the one panel here that reads more than once, and
 * the only way to tell an appended batch from a prepended one is for the two
 * to hold different rows. Each cue's text is its own ordinal, and `limit` is
 * echoed back as the endpoint echoes the number it ran.
 */
function cuePage(url: string): unknown {
  const asked = new URL(url, "http://localhost").searchParams;
  const offset = Number(asked.get("offset") ?? 0);
  const limit = Number(asked.get("limit") ?? 50);
  const rows = Array.from({ length: Math.min(limit, 25) }, (_, index) => ({
    ...OWNER_CUES.cues[1],
    start_s: offset + index,
    end_s: offset + index + 1,
    t: offset + index,
    text: `cue ${offset + index}`,
  }));
  return { cues: rows, offset, limit, has_more: true };
}

/** The shot band, with the geometry jsdom does not compute: 1000px wide, a
 *  hundred pixels down the viewport. Every percentage the preview clamps
 *  against is read off this. */
async function bandOf(): Promise<HTMLElement> {
  const band = await screen.findByRole("list", { name: "Shots across the runtime" });
  band.getBoundingClientRect = () =>
    ({ left: 0, top: 100, right: 1000, bottom: 148, width: 1000, height: 48 }) as DOMRect;
  return band;
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
    const facts = screen.getByText("Andrej Karpathy").closest("p");
    // The runtime as `h:mm:ss`, because every other clock on this page — the
    // band's ticks, a cue's timecode, a chapter's start — is an offset into it
    // and is spelled that way.
    expect(facts).toHaveTextContent("1:56:40");
    expect(screen.getByRole("link", { name: "Open on YouTube" })).toHaveAttribute(
      "href",
      "https://youtu.be/kCc8FmEb1nY",
    );
    // The document is named after data the server never saw, in the effect
    // that runs once the read lands — so it is waited for like every other
    // consequence of that read, rather than read out of the render that
    // happened to be on screen when the assertion ran.
    await waitFor(() => expect(document.title).toBe("Let's build GPT: from scratch — vidtheque"));
  });

  it("counts what was stored, and where the cues came from", async () => {
    await mount({ body: OWNER_VIDEO });

    expect(await screen.findByText("What was stored")).toBeInTheDocument();
    // The breakdown is the bare integer Jinja printed, not a second grouped
    // figure beside the one above it.
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
    // …and the native tooltip is gone once the preview is bound: two renderings
    // of one sentence, the platform's arriving a second later, under a box that
    // already said it with the frame attached (`dashboard.js:429-435`).
    expect(band.querySelector("[data-shot='0']")).not.toHaveAttribute("title");
    // The scale is the video's runtime quartered, not the band's fallback span.
    const ticks = band.parentElement?.querySelectorAll("p[aria-hidden='true'] span");
    expect(Array.from(ticks ?? []).map((tick) => tick.textContent)).toEqual([
      "0:00",
      "29:10",
      "58:20",
      "1:27:30",
      "1:56:40",
    ]);
  });

  // Every link off this page carries all four bounds, as Jinja's `nav_link`
  // did: paging the strip must not throw away where the reader had got to in
  // the transcript, and a page reached by a `?cue_offset=` link stays that link.
  it("carries the transcript's bounds across a strip navigation", async () => {
    await mount(
      { body: { ...OWNER_VIDEO, frames: { ...OWNER_VIDEO.frames, limit: 2, has_more: true } } },
      { search: "frames=2&cues=25&cue_offset=100", cues: (url) => ({ body: cuePage(url) }) },
    );

    const pager = await screen.findByRole("navigation", { name: "Keyframe pages" });
    expect(within(pager).getByRole("link", { name: "Next 2 frames →" })).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?frames=2&cues=25&cue_offset=100&frame_offset=2#frames",
    );
    const band = await screen.findByRole("list", { name: "Shots across the runtime" });
    expect(within(band).getAllByRole("link")[0].getAttribute("href")).toContain(
      "cues=25&cue_offset=100",
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
    // The card is the way into the enlarged frame, not a link out to a JPEG:
    // the still it shows is the 512px one, and the 1280px one is the dialog's.
    const card = screen.getByRole("button", { name: "Keyframe 0 at 0:05" });
    expect(within(card).getByRole("img")).toHaveAttribute(
      "src",
      "/frames/kCc8FmEb1nY-00000.jpg?w=512&q=70",
    );
  });

  // Point at a line and its box lights. Only that direction at this size: a
  // detection box on a 512px still is a few millimetres of screen, and a
  // pointer aimed at one would be stealing the click that opens the frame.
  it("lights a card's box from its line", async () => {
    await mount({ body: OWNER_VIDEO });
    await screen.findByText("Frames, and what the machine read");

    const line = screen.getByText("nvidia-smi 18304MiB").closest("li");
    const card = line?.closest("li[id]");
    const box = card?.querySelector("[aria-hidden='true'][style*='left']");
    expect(box).toBeTruthy();
    const before = box?.getAttribute("class");

    await userEvent.hover(line as HTMLElement);
    expect(box?.getAttribute("class")).not.toBe(before);
    expect(line?.getAttribute("class")).toContain("isLit");

    await userEvent.unhover(line as HTMLElement);
    expect(box?.getAttribute("class")).toBe(before);
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

  // The endpoint answers in numbers and every rendering is this page's: the
  // timecode from `start_s`, the confidence from `avg_logprob`, the chunk label
  // composed from the chunk's own five fields.
  it("composes the timecode, the confidence and the chunk label from the numbers", async () => {
    await mount({ body: OWNER_VIDEO });
    expect(
      await screen.findByText(/chunk 0 · 0:00–7:03 · 54 words · 297 chars/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("0:00").length).toBeGreaterThan(0);
    expect(screen.getByText("-0.42")).toBeInTheDocument();
    // A cue whose log-probability is `null` prints no confidence at all, rather
    // than a word standing in for one.
    expect(screen.getAllByTitle("avg_logprob")).toHaveLength(1);
  });

  it("appends the next batch rather than reloading the page", async () => {
    const { fetcher } = await mount({ body: OWNER_VIDEO });
    await screen.findByText("we cache the keys and the values at every new token");

    await userEvent.click(screen.getByRole("link", { name: /Next 50 cues/ }));

    // The offset is the server's own: `page.offset + page.cues.length`, not
    // this page's arithmetic over a limit it asked for.
    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/videos/kCc8FmEb1nY/cues?offset=3&limit=50",
      expect.anything(),
    );
    // Nothing above the first row, so nothing to go back to: the Earlier
    // control belongs to a panel that was deep-linked into.
    expect(screen.queryByRole("link", { name: "← Earlier" })).toBeNull();
  });

  // The transcript's place is in the URL again. Appending in place is how a
  // batch arrives; it was never a reason for the position to stop being
  // addressable, and Jinja's two parameters are the ones a reader already has.
  it("seeds the transcript from ?cue_offset= and ?cues=", async () => {
    const { fetcher } = await mount(
      { body: OWNER_VIDEO },
      { search: "cue_offset=100&cues=25", cues: (url) => ({ body: cuePage(url) }) },
    );

    expect(await screen.findByText("cue 100")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/videos/kCc8FmEb1nY/cues?offset=100&limit=25",
      expect.objectContaining({ credentials: "same-origin" }),
    );
    expect(screen.getByRole("link", { name: "Next 25 cues →" })).toBeInTheDocument();
    // Real addresses, at the server's own offsets and carrying the strip's
    // page, exactly as `video.html`'s two links did: a reader can open the next
    // page of a transcript in a tab, or copy where they got to.
    expect(screen.getByRole("link", { name: "Next 25 cues →" })).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?cues=25&cue_offset=125#transcript",
    );
    expect(screen.getByRole("link", { name: "← Earlier" })).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?cues=25&cue_offset=75#transcript",
    );
  });

  // The empty *page*, which is what `video.html` keyed on: a `?cue_offset=`
  // past the end of a transcript that does exist met an empty scrollbox with a
  // pager under it, rather than the panel that says so.
  it("says the page is empty when the offset lands past the end", async () => {
    await mount(
      { body: OWNER_VIDEO },
      {
        search: "cue_offset=9000",
        cues: { body: { cues: [], offset: 9000, limit: 50, has_more: false } },
      },
    );

    expect(await screen.findByText("No transcript cues on this page.")).toBeInTheDocument();
  });

  // A hand-typed page size above the endpoint's own ceiling is held at it,
  // using the number the payload carries rather than one written here.
  it("holds ?cues= under the endpoint's max_limit", async () => {
    const { fetcher } = await mount(
      { body: OWNER_VIDEO },
      { search: "cues=5000", cues: (url) => ({ body: cuePage(url) }) },
    );

    await screen.findByText("cue 0");
    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/videos/kCc8FmEb1nY/cues?offset=0&limit=200",
      expect.anything(),
    );
  });

  // `?cues=0` asks for a page of no cues: the pager reads "Next 0 cues", and
  // Earlier steps back by nothing, so neither control moves. A size is at
  // least one cue — `?cue_offset=0`, which is the top of the transcript, keeps
  // its zero.
  it("floors ?cues= at a cue, and leaves ?cue_offset= its zero", async () => {
    const { fetcher } = await mount(
      { body: OWNER_VIDEO },
      { search: "cues=0&cue_offset=0", cues: (url) => ({ body: cuePage(url) }) },
    );

    await screen.findByText("cue 0");
    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/videos/kCc8FmEb1nY/cues?offset=0&limit=1",
      expect.anything(),
    );
    expect(screen.getByRole("link", { name: "Next 1 cues →" })).toBeInTheDocument();
  });

  it("pages back from a seeded offset and writes where it landed", async () => {
    const replaceState = vi.spyOn(window.history, "replaceState");
    const { fetcher } = await mount(
      { body: OWNER_VIDEO },
      { search: "cue_offset=100&cues=25", cues: (url) => ({ body: cuePage(url) }) },
    );
    await screen.findByText("cue 100");

    await userEvent.click(screen.getByRole("link", { name: "← Earlier" }));

    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/videos/kCc8FmEb1nY/cues?offset=75&limit=25",
      expect.anything(),
    );
    // Prepended, not appended: the earlier batch goes above the rows the reader
    // was already on.
    await screen.findByText("cue 75");
    const rows = screen.getAllByText(/^cue \d+$/).map((row) => row.textContent);
    expect(rows[0]).toBe("cue 75");
    expect(rows[rows.length - 1]).toBe("cue 124");
    // …and the address bar names where this view now starts, under the panel's
    // own fragment, so the link is one somebody can send.
    expect(replaceState).toHaveBeenCalledWith(
      null,
      "",
      expect.stringContaining("cue_offset=75#transcript"),
    );
    replaceState.mockRestore();
  });

  it("reaches the first cue and then stops offering Earlier", async () => {
    await mount(
      { body: OWNER_VIDEO },
      { search: "cue_offset=10&cues=25", cues: (url) => ({ body: cuePage(url) }) },
    );
    await screen.findByText("cue 10");

    await userEvent.click(screen.getByRole("link", { name: "← Earlier" }));

    // The backwards page starts at 0 and runs past the rows already on screen,
    // so only the ones that are actually earlier are kept — cue 12 is printed
    // once, not twice.
    await screen.findByText("cue 0");
    expect(screen.getAllByText("cue 12")).toHaveLength(1);
    // Nothing above the first row now, so the control goes.
    await waitFor(() => expect(screen.queryByRole("link", { name: "← Earlier" })).toBeNull());
  });

  // `cue.t` is the whole second the endpoint sends for exactly this, and the
  // timecode has been the link to it since Jinja.
  it("makes each cue timecode the deeplink at that second", async () => {
    await mount({ body: OWNER_VIDEO });
    const row = (
      await screen.findByText("otherwise you would recompute attention over the entire prefix")
    ).closest("li");

    expect(within(row!).getByRole("link", { name: "0:03" })).toHaveAttribute(
      "href",
      "https://youtu.be/kCc8FmEb1nY?t=3",
    );
    expect(within(row!).getByRole("link", { name: "0:03" })).toHaveAttribute("target", "_blank");
  });

  it("says a transcript is absent rather than showing an empty box", async () => {
    await mount({ body: OWNER_HALF }, { videoId: "aaaaaaaaaaa" });

    expect(await screen.findByText("No transcript cues on this page.")).toBeInTheDocument();
    expect(
      screen.getByText("No keyframes were captured, so this video has no shots."),
    ).toBeInTheDocument();
    expect(screen.getByText("No indexing job is linked to this video.")).toBeInTheDocument();
  });

  // §3.6's `DEEPLINK_LEAD` exists so a *quoted moment* is not missed by a
  // second. A chapter start is not a moment, it is a boundary — two seconds
  // before it is the previous chapter — so this page builds the href from the
  // chapter's own start, as Jinja did, and ignores the payload's led `link`.
  it("links a chapter at its own start, not two seconds before it", async () => {
    await mount({
      body: {
        ...OWNER_VIDEO,
        chapters: [
          { start_s: 305.0, title: "attention", link: "https://youtu.be/kCc8FmEb1nY?t=303" },
        ],
      },
    });

    const chapters = (await screen.findByText("Chapters")).closest("section");
    expect(within(chapters!).getByRole("link", { name: "5:05" })).toHaveAttribute(
      "href",
      "https://youtu.be/kCc8FmEb1nY?t=305",
    );
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
  // operator's console. The column those fields filled keeps its place and
  // prints the dash — the table is five columns wide on both projections, so
  // the absence is something a reader can see rather than a layout that
  // silently differs from the one in the screenshot they are comparing against.
  it("drops the model ids and the pipeline's prose in the projection", async () => {
    await mount({ body: DEMO_HALF }, { videoId: "aaaaaaaaaaa", session: DEMO_SESSION });

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

  it("gives the demo every panel of a finished video", async () => {
    await mount({ body: DEMO_VIDEO }, { session: DEMO_SESSION });

    expect(await screen.findByText("What was stored")).toBeInTheDocument();
    expect(screen.getByText("nvidia-smi 18304MiB")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  // The enlarged frame — the half of the interaction a 512px card cannot
  // carry. At this size a detection box is a target a pointer can find, which
  // is why the linkage runs both ways here and one way on the card.
  describe("the enlarged frame", () => {
    it("opens the frame in the page rather than navigating to a JPEG", async () => {
      await mount({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));

      const shot = screen.getByRole("dialog");
      expect(within(shot).getByRole("img")).toHaveAttribute(
        "src",
        "/frames/kCc8FmEb1nY-00001.jpg?w=1280&q=70",
      );
      // The caption is `video.html`'s `data-caption`, all four facts of it:
      // the frame's own id, the second it was cut at, its pixel size and its
      // byte weight. §5.3 puts the last two here rather than on the card,
      // because they are facts about the file you are now looking at.
      expect(
        within(shot).getByText("kCc8FmEb1nY-00001 · 7:10 · 1280×720 · 70 B"),
      ).toBeInTheDocument();
      expect(within(shot).getByText(/shot 1 · sharpness 10.0 · done · 2 line/)).toBeInTheDocument();
      // Both lines, and a box for each at the coordinates the store holds.
      expect(within(shot).getByText("loss 3.14")).toBeInTheDocument();
      expect(shot.querySelectorAll("[aria-hidden='true'][style*='left']")).toHaveLength(2);
      // The file itself stays reachable, one click further in.
      expect(within(shot).getByRole("link", { name: "Open the file" })).toHaveAttribute(
        "href",
        "/frames/kCc8FmEb1nY-00001.jpg?w=1280&q=70",
      );
      expect(within(shot).getByRole("link", { name: "Open at this second" })).toHaveAttribute(
        "href",
        "https://youtu.be/kCc8FmEb1nY?t=430",
      );
      expect(shot.textContent).not.toMatch(/null|NaN|undefined/);
    });

    // The frame going into evidence is written in the one place it belongs:
    // `?select=`, the same address a shot bar would have produced.
    it("marks the opened frame in the URL", async () => {
      const replaceState = vi.spyOn(window.history, "replaceState");
      await mount({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));

      expect(replaceState).toHaveBeenCalledWith(
        null,
        "",
        expect.stringContaining("select=1#frame-1"),
      );
      replaceState.mockRestore();
    });

    it("lights a line from its box and a box from its line", async () => {
      await mount({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");
      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));

      const shot = screen.getByRole("dialog");
      const boxes = shot.querySelectorAll("[aria-hidden='true'][style*='left']");
      const second = within(shot).getByText("loss 3.14").closest("li");

      // Point at the second box: its line lights, and only its line.
      await userEvent.hover(boxes[1] as HTMLElement);
      expect(second?.getAttribute("class")).toContain("isLit");
      expect(boxes[1].getAttribute("class")).toContain("isLit");
      expect(boxes[0].getAttribute("class")).not.toContain("isLit");

      await userEvent.unhover(boxes[1] as HTMLElement);
      expect(second?.getAttribute("class")).not.toContain("isLit");

      // And the other way: point at the line, the box lights.
      await userEvent.hover(second as HTMLElement);
      expect(boxes[1].getAttribute("class")).toContain("isLit");
      expect(boxes[0].getAttribute("class")).not.toContain("isLit");
    });

    it("closes on Escape and hands the focus back to the card", async () => {
      await mount({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");
      const card = screen.getByRole("button", { name: "Keyframe 1 at 7:10" });

      await userEvent.click(card);
      const shot = screen.getByRole("dialog");
      expect(document.activeElement).toBe(within(shot).getByRole("button", { name: "Close" }));

      await userEvent.keyboard("{Escape}");

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(document.activeElement).toBe(card);
    });

    it("closes on the Close control", async () => {
      await mount({ body: TWO_LINES });
      await screen.findByText("Frames, and what the machine read");

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 1 at 7:10" }));
      await userEvent.click(screen.getByRole("button", { name: "Close" }));

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    // A frame with nothing on it is the same dialog without the list, and its
    // pill says which kind of nothing it is.
    it("says what a deduplicated frame is instead of listing lines", async () => {
      await mount({ body: OWNER_VIDEO });
      await screen.findByText("Frames, and what the machine read");

      await userEvent.click(screen.getByRole("button", { name: "Keyframe 7 at 11:40" }));

      const shot = screen.getByRole("dialog");
      expect(within(shot).getByText(/shot 7 · duplicate of #0 · skipped/)).toBeInTheDocument();
      expect(shot.textContent).not.toMatch(/null|NaN|undefined/);
    });
  });

  // Point anywhere along the shot band and the shot under the pointer shows
  // its own first keyframe, the way a video player previews a seek — except
  // that the unit here is a shot, because that is the unit the band is made of
  // and the only one the index has a frame for.
  describe("the scrub preview", () => {
    it("previews the shot the pointer is inside", async () => {
      await mount({ body: OWNER_VIDEO });
      const band = await bandOf();

      fireEvent.pointerMove(band, { clientX: 62, pointerType: "mouse" });

      // shot 1 runs 430–435s of a 7000s runtime: 6.143% to 6.214% of the band,
      // and 62px of 1000 is 6.2%.
      expect(screen.getByText("7:10–7:15")).toBeInTheDocument();
      expect(screen.getByText("shot 1 · 1/1 kept")).toBeInTheDocument();
      // The 192px still, after the pause that keeps a sweep from being one
      // request per bar.
      await waitFor(() =>
        expect(
          document.querySelector('img[src="/frames/kCc8FmEb1nY-00001.jpg?w=192&q=70"]'),
        ).toBeInTheDocument(),
      );
    });

    // `min-width: 3px` means a rendered bar can be wider than its share of the
    // runtime, so a pointer in the gap between two bars is previewing the
    // nearest one rather than nothing.
    it("previews the nearest shot when the pointer is in a gap", async () => {
      await mount({ body: OWNER_VIDEO });
      const band = await bandOf();

      fireEvent.pointerMove(band, { clientX: 500, pointerType: "mouse" });

      expect(screen.getByText("shot 7 · 0/1 kept")).toBeInTheDocument();
      expect(screen.getByText("11:40–12:25")).toBeInTheDocument();

      fireEvent.pointerLeave(band);
      expect(screen.queryByText("shot 7 · 0/1 kept")).not.toBeInTheDocument();
    });

    // A tap is a navigation, not a hover: on a touch screen the bar's own link
    // is the whole interaction and a preview would only be in front of it.
    it("stays out of the way of a tap", async () => {
      await mount({ body: OWNER_VIDEO });
      const band = await bandOf();

      fireEvent.pointerMove(band, { clientX: 62, pointerType: "touch" });

      expect(screen.queryByText("shot 1 · 1/1 kept")).not.toBeInTheDocument();
    });

    // The keyboard path through this page, and the reason `selectFrame` exists:
    // the card the bar points at is already on screen, so following the link
    // would reload the whole page to move a mark and drop the reader at the top
    // of it. The click is intercepted instead — the mark goes in the URL, the
    // strip scrolls to that moment, and focus lands on the frame's own button,
    // which is the control the next Enter should open (`dashboard.js:261-296`).
    it("selects a frame in place rather than navigating to it", async () => {
      const replaceState = vi.spyOn(window.history, "replaceState");
      const scroll = vi.fn();
      Element.prototype.scrollIntoView = scroll;
      await mount({ body: OWNER_VIDEO });
      const band = await bandOf();
      const bar = within(band).getAllByRole("link")[1];

      const click = new MouseEvent("click", { bubbles: true, cancelable: true });
      bar.dispatchEvent(click);

      expect(click.defaultPrevented).toBe(true);
      expect(replaceState).toHaveBeenCalledWith(
        null,
        "",
        expect.stringContaining("select=1#frame-1"),
      );
      expect(scroll).toHaveBeenCalledWith({ block: "center", behavior: "smooth" });
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Keyframe 1 at 7:10" }),
      );
      replaceState.mockRestore();
    });

    // …and a bar pointing at a page of the strip this one is not showing has
    // nothing to select, so the link it always was does the navigating.
    it("lets the link navigate when the frame is not on this page", async () => {
      const thin = {
        ...OWNER_VIDEO,
        frames: { ...OWNER_VIDEO.frames, frames: [] },
      };
      await mount({ body: thin });
      const band = await bandOf();

      const click = new MouseEvent("click", { bubbles: true, cancelable: true });
      within(band).getAllByRole("link")[1].dispatchEvent(click);

      expect(click.defaultPrevented).toBe(false);
    });

    // Focus is the keyboard's pointer, and the arrows step between the bars'
    // own links rather than inventing a selection model of their own.
    it("previews what the keyboard is on, and steps with the arrows", async () => {
      await mount({ body: OWNER_VIDEO });
      const band = await bandOf();
      const bars = within(band).getAllByRole("link");

      bars[0].focus();
      await waitFor(() => expect(screen.getByText("shot 0 · 1/1 kept")).toBeInTheDocument());

      fireEvent.keyDown(bars[0], { key: "ArrowRight" });
      expect(document.activeElement).toBe(bars[1]);

      fireEvent.keyDown(bars[1], { key: "End" });
      expect(document.activeElement).toBe(bars[2]);

      fireEvent.keyDown(bars[2], { key: "Escape" });
      expect(screen.queryByText(/^shot \d+ · \d+\/\d+ kept$/)).not.toBeInTheDocument();
    });

    // The band and the strip are two views of one thing, and the link between
    // them is otherwise invisible.
    it("lights a shot's frames from its bar", async () => {
      await mount({ body: OWNER_VIDEO });
      const band = await bandOf();
      const bars = within(band).getAllByRole("listitem");
      const card = screen.getByRole("button", { name: "Keyframe 1 at 7:10" }).closest("li");

      fireEvent.pointerEnter(bars[1]);
      expect(card?.getAttribute("class")).toContain("isLinked");
      expect(bars[1].getAttribute("class")).toContain("isLinked");

      fireEvent.pointerLeave(bars[1]);
      expect(card?.getAttribute("class")).not.toContain("isLinked");
    });
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
      // The name `views.video_detail` gave this document. The shell is served
      // before the read that refuses has gone out, so the page renames itself
      // when the answer comes back.
      await waitFor(() => expect(document.title).toBe("Unknown video — vidtheque"));
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

    // Under a stopped clock: the label has to be the delay the limiter named,
    // and a page that halved it would count down just as convincingly.
    it("counts down a 429", async () => {
      await firstPaint(() =>
        mount({
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
          headers: { "retry-after": "7" },
        }),
      );

      expect(screen.getByRole("button", { name: "retry in 7s" })).toBeDisabled();
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

  // The write side, last on the page and only where it is registered. Two
  // actions and no third: `jobs.kind='delete'` is in the schema with no
  // pipeline behind it, so a delete button here would queue a job that fails.
  describe("the manage panel", () => {
    it("queues a forced rebuild and names the job it made", async () => {
      const { posts } = await mount({ body: OWNER_VIDEO });
      await screen.findByRole("heading", { name: "Manage this video" });

      await userEvent.click(screen.getByRole("button", { name: "Re-index this video" }));

      expect(posts[0].path).toBe("/dashboard/videos/kCc8FmEb1nY/reindex");
      expect(posts[0].init.method).toBe("POST");
      // No fields: the Jinja form posts none either, and the video is named by
      // the path.
      expect(String(posts[0].init.body)).toBe("");
      expect((posts[0].init.headers as Record<string, string>).accept).toBe("application/json");

      // The control does not come back: a second POST would queue a second
      // rebuild of the same video.
      expect(await screen.findByRole("link", { name: "job_02e028870c97" })).toHaveAttribute(
        "href",
        "/dashboard/jobs/job_02e028870c97",
      );
      expect(screen.queryByRole("button", { name: "Re-index this video" })).not.toBeInTheDocument();
    });

    it("prints the tool's refusal rather than claiming it queued something", async () => {
      await mount({ body: OWNER_VIDEO }, { post: { status: 409, body: REINDEX_REFUSED } });
      await screen.findByRole("heading", { name: "Manage this video" });

      await userEvent.click(screen.getByRole("button", { name: "Re-index this video" }));

      expect(await screen.findByText("E_INDEXING")).toBeInTheDocument();
      expect(screen.getByText("kCc8FmEb1nY is already being indexed.")).toBeInTheDocument();
    });

    // The row's tags *after* the write, read back — never a diff applied on
    // this side, because `tag_video` reports what it changed across a batch and
    // this panel is showing the row.
    it("replaces the tag list with the tags the row carries after the write", async () => {
      const { posts } = await mount({ body: OWNER_VIDEO }, { post: { body: TAGGED } });
      await screen.findByRole("heading", { name: "Manage this video" });

      await userEvent.type(screen.getByLabelText("Add"), "topic:json, series:writes");
      await userEvent.click(screen.getByRole("button", { name: "Apply" }));

      expect(posts[0].path).toBe("/dashboard/videos/kCc8FmEb1nY/tags");
      const body = new URLSearchParams(String(posts[0].init.body));
      expect(body.get("add")).toBe("topic:json, series:writes");
      expect(body.get("remove")).toBe("");

      // Both places this page prints tags follow the row: the chips under the
      // title as well as the list under the form.
      expect(await screen.findAllByText("series:writes")).toHaveLength(2);
      expect(screen.getAllByText("topic:json")).toHaveLength(2);
    });

    it("prints tag_video's own refusal, in its own words", async () => {
      await mount({ body: OWNER_VIDEO }, { post: { status: 400, body: TAG_REFUSED } });
      await screen.findByRole("heading", { name: "Manage this video" });

      await userEvent.type(screen.getByLabelText("Add"), "NotATag");
      await userEvent.click(screen.getByRole("button", { name: "Apply" }));

      expect(
        await screen.findByText("Tags must be namespace:value, lowercase."),
      ).toBeInTheDocument();
      expect(screen.getByText("E_BAD_PARAM")).toBeInTheDocument();
    });

    // §5.5: the database's own flag disables the one control that feeds
    // `index_video`. Tagging writes the row, not the index, and stays live.
    it("disables only the rebuild when the database refuses writes", async () => {
      await mount({ body: OWNER_VIDEO }, { session: { ...OWNER_SESSION, writes_allowed: false } });
      await screen.findByRole("heading", { name: "Manage this video" });

      expect(screen.getByRole("button", { name: "Re-index this video" })).toBeDisabled();
      expect(screen.getByText(/Indexing is refused on this instance/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled();
    });

    it("is not on the page at all in the projection", async () => {
      await mount({ body: DEMO_VIDEO }, { session: DEMO_SESSION });
      await screen.findByRole("heading", { name: "Let's build GPT: from scratch" });

      expect(screen.queryByText("Manage this video")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("link", { name: "Queue more from this channel" }),
      ).not.toBeInTheDocument();
    });

    // A `GET` prefill and not a write: the index form remains the place the
    // operator reviews it, and the POST remains the only state change.
    it("links the channel into the index form, seeded", async () => {
      await mount({ body: OWNER_VIDEO });

      expect(
        await screen.findByRole("link", { name: "Queue more from this channel" }),
      ).toHaveAttribute(
        "href",
        "/dashboard/index?urls=https%3A%2F%2Fyoutu.be%2FkCc8FmEb1nY&expand=channel_recent",
      );
    });
  });
});
