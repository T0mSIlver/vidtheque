// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deferred, type Answer } from "@/test/dashboard";
import { OWNER_CUES, OWNER_VIDEO } from "@/test/library-fixtures";
import { cuePage, mountVideo, stripPage } from "./detail-harness";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The transcript, a batch at a time from the endpoint the payload names.

describe("the transcript", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  // The transcript is a pointer, not a copy: the detail payload carries the
  // totals and the endpoint's name, and the cues arrive a page at a time.
  it("reads the transcript from the endpoint the payload names", async () => {
    const { fetcher } = await mountVideo({ body: OWNER_VIDEO });

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
    await mountVideo({ body: OWNER_VIDEO });
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
    const { fetcher } = await mountVideo({ body: OWNER_VIDEO });
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

  // Appending in place does not stop the position being addressable.
  it("seeds the transcript from ?cue_offset= and ?cues=", async () => {
    const { fetcher } = await mountVideo(
      { body: OWNER_VIDEO },
      { search: "cue_offset=100&cues=25", cues: (url) => ({ body: cuePage(url) }) },
    );

    expect(await screen.findByText("cue 100")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith(
      "/dashboard/api/videos/kCc8FmEb1nY/cues?offset=100&limit=25",
      expect.objectContaining({ credentials: "same-origin" }),
    );
    expect(screen.getByRole("link", { name: "Next 25 cues →" })).toBeInTheDocument();
    // Real addresses at the server's offsets, so a page can go to a tab.
    expect(screen.getByRole("link", { name: "Next 25 cues →" })).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?cues=25&cue_offset=125#transcript",
    );
    expect(screen.getByRole("link", { name: "← Earlier" })).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?cues=25&cue_offset=75#transcript",
    );
  });

  // The empty page, not the empty transcript: an offset past the end. The
  // transcript is still there, so the page that says so carries the way back.
  it("says the page is empty when the offset lands past the end, and offers Earlier", async () => {
    await mountVideo(
      { body: OWNER_VIDEO },
      {
        search: "cue_offset=9000",
        cues: (url) =>
          new URL(url, "http://localhost").searchParams.get("offset") === "9000"
            ? { body: { cues: [], offset: 9000, limit: 50, has_more: false } }
            : { body: cuePage(url) },
      },
    );

    expect(await screen.findByText("No transcript cues on this page.")).toBeInTheDocument();
    // A step back from the end of the transcript, not from an offset it never
    // had: 9000 minus a page would still be past the last cue.
    const earlier = screen.getByRole("link", { name: "← Earlier" });
    expect(earlier).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?cue_offset=0#transcript",
    );

    await userEvent.click(earlier);
    expect(await screen.findByText("cue 0")).toBeInTheDocument();
  });

  // A hand-typed page size above the endpoint's own ceiling is held at it,
  // using the number the payload carries rather than one written here.
  it("holds ?cues= under the endpoint's max_limit", async () => {
    const { fetcher } = await mountVideo(
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
    const { fetcher } = await mountVideo(
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
    const { fetcher } = await mountVideo(
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
    expect(window.location.href).toContain("cue_offset=75#transcript");
  });

  it("reaches the first cue and then stops offering Earlier", async () => {
    await mountVideo(
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

  // `cue.t` is the whole second the endpoint sends for the deeplink.
  it("makes each cue timecode the deeplink at that second", async () => {
    await mountVideo({ body: OWNER_VIDEO });
    const row = (
      await screen.findByText("otherwise you would recompute attention over the entire prefix")
    ).closest("li");

    expect(within(row!).getByRole("link", { name: "0:03" })).toHaveAttribute(
      "href",
      "https://youtu.be/kCc8FmEb1nY?t=3",
    );
    expect(within(row!).getByRole("link", { name: "0:03" })).toHaveAttribute("target", "_blank");
  });
  it("keeps the page when only the transcript's next batch fails", async () => {
    await mountVideo(
      { body: OWNER_VIDEO },
      { cues: { status: 429, body: { error: "E_RATE_LIMIT", message: "Slow down." } } },
    );

    expect(await screen.findByText("What was stored")).toBeInTheDocument();
    expect(await screen.findByText("Slow down.")).toBeInTheDocument();
    // The panels the detail payload answered for are untouched.
    expect(screen.getByText("Provenance")).toBeInTheDocument();
  });

  // The box holds the rows the totals predict before they land, and the pager
  // is drawn only where the totals say there is a next page.
  it("reserves the first batch's rows and predicts its pager", async () => {
    const held = deferred<Answer>();
    await mountVideo({ body: OWNER_VIDEO }, { cues: () => held.promise });

    const panel = (await screen.findByRole("heading", { name: "Transcript" })).closest("section")!;
    const box = panel.querySelector<HTMLElement>("[tabindex='0']")!;
    // Six cues in all, fifty a page: six rows, and no next page.
    expect(box.style.getPropertyValue("--cue-rows")).toBe("6");
    expect(within(panel).queryByRole("navigation")).toBeNull();

    held.resolve({ body: OWNER_CUES });
    await within(panel).findByText("we cache the keys and the values at every new token");
    expect(box.style.getPropertyValue("--cue-rows")).toBe("6");
  });

  // Paging the strip re-reads the video, not the transcript: the cues already
  // appended stay where the reader left them.
  it("keeps the appended cues across a strip page", async () => {
    const { calls, navigate } = await mountVideo(
      (url) => ({ body: stripPage(url.includes("frame_offset=1") ? 1 : 0) }),
      {
        search: "frames=1&cues=25",
        cues: (url) => ({ body: cuePage(url) }),
      },
    );
    await screen.findByText("cue 0");
    await userEvent.click(screen.getByRole("link", { name: "Next 25 cues →" }));
    await screen.findByText("cue 25");

    await navigate("/dashboard/videos/kCc8FmEb1nY?frames=1&cues=25&frame_offset=1");

    await screen.findByRole("button", { name: "Keyframe 1 at 7:10" });
    expect(screen.getByText("cue 25")).toBeInTheDocument();
    expect(calls("/dashboard/api/videos/")).toHaveLength(2);
  });
});
