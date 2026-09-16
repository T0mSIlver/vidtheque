// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mountDashboard, type Answer } from "@/test/dashboard";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard-fixtures";
import {
  AUTH_REFUSAL,
  BAD_PARAM_REFUSAL,
  DEMO_SEARCH,
  EMPTY_CORPUS_SEARCH,
  EMPTY_QUERY_REFUSAL,
  NO_MATCH_SEARCH,
  OWNER_SEARCH,
  PAGED_SEARCH,
  PROBED_SEARCH,
  RATE_REFUSAL,
  TRAP_SEARCH,
} from "@/test/search-fixtures";
import { firstPaint } from "@/test/retry";
import { SearchView } from "./SearchView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// What matched, what kind of evidence it is, and which second; the receipt out
// and the moment in; §14.2's three traps; the URL round trip; the empties and
// the refusals.

function mount(
  search_: Answer,
  { search = "", session = OWNER_SESSION }: { search?: string; session?: unknown } = {},
) {
  return mountDashboard(<SearchView />, {
    path: "/dashboard/search",
    search,
    session,
    routes: { "/dashboard/api/search": search_ },
  });
}

/** The row one moment is on, found by the timecode it prints. */
function momentAt(timecode: string) {
  return screen.getByText(timecode).closest("li") as HTMLElement;
}

/** The control that opens a moment's frame. */
function frameOf(timecode: string) {
  return within(momentAt(timecode)).getByRole("button", {
    name: `Enlarge the frame at ${timecode}`,
  });
}

describe("the owner's search page", () => {
  it("lists the ranking a moment a row, and says which slice it is showing", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });

    expect(await screen.findByRole("heading", { name: "Results" })).toBeInTheDocument();
    // Five hits, five rows, in the order the server ranked them — no head over
    // a talk, so a single-hit video's title is printed once and the second
    // moment in the ranking is the second row on the page.
    expect(screen.queryByText("3 moment(s)")).toBeNull();
    expect(momentAt("0:05").closest("ol")?.children).toHaveLength(5);
    // Three of the five moments are in that talk, and each row's title is its
    // own link into the index — never a heading's, printed once above them.
    expect(screen.getAllByRole("link", { name: "Making LLMs go brrr" })).toHaveLength(3);

    const head = screen
      .getByRole("heading", { name: "Search the corpus" })
      .closest("div") as HTMLElement;
    expect(head).toHaveTextContent("1–5");
    expect(head).toHaveTextContent("of 5");
  });

  // `<ol start="{{ offset + 1 }}">`, as `search.html` drew it: the list is the
  // ranking and page two picks it up where page one left off.
  it("numbers a page of hits from where the ranking starts", async () => {
    await mount({ body: PAGED_SEARCH }, { search: "q=cache&limit=1&offset=1" });
    await screen.findByRole("heading", { name: "Results" });

    expect(momentAt("3:20").closest("ol")).toHaveAttribute("start", "2");
  });

  it("starts at one on the first page", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    expect(momentAt("0:05").closest("ol")).toHaveAttribute("start", "1");
  });

  // The `~` is `has_more` over exact totals reaching the one line on the page
  // where a number could be mistaken for a count.
  it("marks the total as an estimate when the pool ran out before the count did", async () => {
    await mount({ body: PROBED_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const head = screen
      .getByRole("heading", { name: "Search the corpus" })
      .closest("div") as HTMLElement;
    expect(head).toHaveTextContent("of ~200");
  });

  it("prints every leg with its unit and its raw key, and no total under them", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    const legs = await screen.findByLabelText("Search legs");

    expect(within(legs).getByText("transcript_fts")).toBeInTheDocument();
    expect(within(legs).getByText("Transcript — keyword match (FTS)")).toBeInTheDocument();
    expect(within(legs).getByText("cues")).toBeInTheDocument();
    // A zero is a reading: `fts 0` is how you learn the corpus does not contain
    // your phrasing, so the leg that counted nothing is drawn too.
    expect(within(legs).getByText("frame_knn")).toBeInTheDocument();
    expect(within(legs).getAllByRole("definition")).toHaveLength(8);
  });

  // ------------------------------------------------------------ the traps

  // `timestamp` is `clock(start)`, the segment's own opening. For a fused
  // transcript hit that is a different second from the cue that matched, and
  // the second the page prints is the one that matched.
  it("prints the timecode of the cue that matched, not the segment's opening", async () => {
    await mount({ body: TRAP_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    expect(screen.getByText("1:12:03")).toBeInTheDocument();
    expect(screen.queryByText("1:11:40")).not.toBeInTheDocument();
  });

  it("says a frame matched on the picture in its own words, never as a quotation", async () => {
    await mount({ body: TRAP_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const row = momentAt("10:12");
    expect(within(row).getByText("visual match, no text hit")).toBeInTheDocument();
    expect(within(row).getByText("frame")).toBeInTheDocument();
  });

  // `humanize.notes` strips the marker and leaves the sentence: the marker is
  // machinery for a model and the sentence is the operator's.
  it("renders the query layer's notes as they arrive, marker already gone", async () => {
    await mount({ body: NO_MATCH_SEARCH }, { search: "q=zzzznothingmatchesthis" });
    const notes = await screen.findByLabelText("Search notes");

    expect(notes).toHaveTextContent(/No word of this query occurs anywhere in the corpus/);
    expect(notes).not.toHaveTextContent("note:");
  });

  // ------------------------------------------------------- the two links

  it("sends a frame hit to its own strip page, and a spoken one to the video", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    // `kCc8FmEb1nY-00000` is ordinal 0, so it is on the first page of a strip
    // that holds 24, marked by `select` and by the fragment.
    expect(within(momentAt("0:05")).getByRole("link", { name: "0:05" })).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?frame_offset=0&select=0#frame-0",
    );
    expect(within(momentAt("3:20")).getByRole("link", { name: "3:20" })).toHaveAttribute(
      "href",
      "/dashboard/videos/zduSFxRajkE",
    );
  });

  it("prints the receipt the tool sent, and none where the link is not one", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const receipt = within(momentAt("0:05")).getByRole("link", { name: /youtu\.be/ });
    expect(receipt).toHaveAttribute("href", "https://youtu.be/kCc8FmEb1nY?t=3");
    expect(receipt).toHaveTextContent("youtu.be/kCc8FmEb1nY?t=3");
    expect(receipt).toHaveAttribute("rel", "noopener noreferrer");
  });

  // The odd hit's link is `http`, which §14's admission rule refuses — so the
  // row carries no receipt rather than an unchecked one.
  it("prints no receipt where the tool's link is not an exact-second proof", async () => {
    await mount({ body: TRAP_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const row = momentAt("0:10");
    expect(within(row).queryByRole("link", { name: /youtu\.be/ })).toBeNull();
    // …and its unknown source still arrives as a word, never as a hit with no
    // provenance on it at all — on the badge, and in the box the frame it does
    // not have would have taken.
    expect(within(row).getAllByText("caption_track")).toHaveLength(2);
  });

  // The receipt at the end of the row goes out to YouTube, which is the
  // product's argument; the title goes to what the index actually *stored*
  // about that second, which is what this surface is for. A hit that names a
  // keyframe lands **on the frame**, and the group's head keeps the plain video
  // page — two questions, two destinations.
  it("sends a moment's title to the frame, and the group's to the video", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const moment = within(momentAt("0:05")).getByRole("link", {
      name: "Let's build GPT: from scratch",
    });
    expect(moment).toHaveAttribute(
      "href",
      "/dashboard/videos/kCc8FmEb1nY?frame_offset=0&select=0#frame-0",
    );
    // A transcript hit names its cues by id while the transcript panel pages by
    // offset, so it links to the video plainly rather than inventing a position.
    expect(
      within(momentAt("3:20")).getByRole("link", { name: "Making LLMs go brrr" }),
    ).toHaveAttribute("href", "/dashboard/videos/zduSFxRajkE");
  });

  // The group's head prints the channel once. A moment prints its own only when
  // it is not the one already on the head — two hits filed under one `video_id`
  // that disagree about their channel is a corpus fact, and a row that borrowed
  // a heading's answer could not report it.
  it("prints every moment's own channel", async () => {
    const odd = { ...OWNER_SEARCH.results[1], channel: "GPU MODE reruns", match_start: 250.0 };
    await mount(
      {
        body: {
          ...OWNER_SEARCH,
          results: [...OWNER_SEARCH.results, odd],
          pagination: { ...OWNER_SEARCH.pagination, approx_total: 6 },
        },
      },
      { search: "q=cache" },
    );
    await screen.findByRole("heading", { name: "Results" });

    expect(within(momentAt("4:10")).getByText("GPU MODE reruns")).toBeInTheDocument();
    expect(within(momentAt("3:20")).getByText("GPU MODE")).toBeInTheDocument();
  });

  // ------------------------------------------------------------- the frame

  // The payload sends the demo's 320 and 960 against `PUBLIC_URL`; this page
  // builds its own at the dashboard's widths and on its own origin, which is
  // what a dashboard read through a tunnel needs (§14.2).
  it("builds its frames at this surface's widths, on this origin", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const control = frameOf("0:05");
    const shot = control.querySelector("img") as HTMLImageElement;
    expect(shot).toHaveAttribute("src", "/frames/kCc8FmEb1nY-00000.jpg?w=192&q=70");
    expect(shot).toHaveAttribute("width", "128");
    // The picture is the control's whole content, so it is the control that
    // carries the label and the `alt` is empty: a screen reader that read both
    // would announce the frame twice.
    expect(shot).toHaveAttribute("alt", "");
  });

  // For an OCR or a frame hit the picture *is* the evidence, so it opens where
  // the reader is rather than in a tab that has lost the ranking.
  it("opens the frame in the overlay, with its three facts on the caption", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    await userEvent.click(frameOf("0:05"));

    const shot = screen.getByRole("dialog");
    expect(shot).toHaveAttribute("open");
    expect(
      within(shot).getByText("kCc8FmEb1nY-00000 · 0:05 · Let's build GPT: from scratch"),
    ).toBeInTheDocument();
    expect(within(shot).getByRole("img")).toHaveAttribute(
      "src",
      "/frames/kCc8FmEb1nY-00000.jpg?w=1280&q=70",
    );
    // The other half of the row's evidence, in the overlay's footer: the same
    // second, out on YouTube.
    expect(within(shot).getByRole("link", { name: "Open at this second" })).toHaveAttribute(
      "href",
      "https://youtu.be/kCc8FmEb1nY?t=3",
    );
  });

  // A 1280px JPEG per frame opened adds up over a browsing session, and nothing
  // needs it once the dialog is shut.
  it("releases the enlarged frame when the overlay closes", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    await userEvent.click(frameOf("0:05"));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }),
    );

    const shot = screen.getByRole("dialog", { hidden: true });
    expect(shot).not.toHaveAttribute("open");
    expect(shot.querySelector("img")).toBeNull();
  });

  it("keeps the column a column where a moment has no frame", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const row = momentAt("3:20");
    expect(within(row).queryByRole("button")).toBeNull();
    // The channel it came from, in the box the frame would have taken.
    expect(within(row).getAllByText("spoken")).not.toHaveLength(0);
  });

  // ------------------------------------------------------- the marked words

  it("marks the reader's own words in the snippet", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const marks = [...document.body.querySelectorAll("mark")].map((mark) => mark.textContent);
    expect(marks).toContain("cache");
    // Never a claim about *why* a hit ranked: the semantic legs match no words
    // at all, so a snippet with nothing marked is ordinary.
    expect(marks.every((text) => text?.toLowerCase() === "cache")).toBe(true);
  });

  // ------------------------------------------------------------- the band

  // A search box you have to click into before you can change the query makes
  // you re-aim after every result.
  it("puts the caret in the query box", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });

    expect(screen.getByLabelText("Query")).toHaveFocus();
  });

  // `search.run` takes `video_id`, `search_payload` reads it off the request's
  // own query string, and a whitelist that quietly dropped it was a filter that
  // silently did not apply — with no `note:` to say so.
  it("passes a video_id filter through to the handler that takes it", async () => {
    const { calls } = await mount(
      { body: OWNER_SEARCH },
      { search: "q=cache&video_id=kCc8FmEb1nY" },
    );
    await screen.findByRole("heading", { name: "Results" });

    expect(calls("/dashboard/api/search")[0].url).toBe(
      "/dashboard/api/search?q=cache&video_id=kCc8FmEb1nY",
    );
  });

  // ---------------------------------------------------------- the empties

  it("tells the two empties apart", async () => {
    const { unmount } = await mount(
      { body: NO_MATCH_SEARCH },
      { search: "q=zzzznothingmatchesthis" },
    );
    expect(await screen.findByText("No moments matched.")).toBeInTheDocument();

    unmount();
    await mount({ body: EMPTY_CORPUS_SEARCH }, { search: "q=zzzznothingmatchesthis" });
    expect(await screen.findByText("Nothing is indexed in this corpus yet.")).toBeInTheDocument();
  });

  it("offers all three channels back when a narrowed search found nothing", async () => {
    await mount(
      { body: { ...NO_MATCH_SEARCH, content_type: "ocr" } },
      { search: "q=slide&content_type=ocr&offset=20" },
    );
    await screen.findByText("No moments matched.");

    expect(screen.getByRole("link", { name: "Search all three channels" })).toHaveAttribute(
      "href",
      "/dashboard/search?q=slide",
    );
  });

  // A page nobody has asked anything of yet is not an error and not an empty
  // result set: it is a page with no question on it.
  it("reads nothing at all until there is a query in the URL", async () => {
    const { calls } = await mount({ body: OWNER_SEARCH });

    expect(await screen.findByText("No search has run.")).toBeInTheDocument();
    expect(calls("/dashboard/api/search")).toHaveLength(0);
  });

  // ------------------------------------------------------- the URL round trip

  it("submits the band as a navigation, and sends what the reader typed", async () => {
    const { push, calls } = await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    // Every bound is Python's and caller-keyed, so the query goes as typed.
    expect(calls("/dashboard/api/search")[0].url).toBe("/dashboard/api/search?q=cache");

    await userEvent.clear(screen.getByLabelText("Query"));
    await userEvent.type(screen.getByLabelText("Query"), "paged attention");
    await userEvent.selectOptions(screen.getByLabelText("Searched content"), "ocr");
    await userEvent.type(screen.getByLabelText("Video channel"), "GPU MODE");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    // `content_type=all` would say nothing twice, so it is left off.
    expect(push).toHaveBeenCalledWith(
      "/dashboard/search?q=paged+attention&content_type=ocr&channel=GPU+MODE",
      { scroll: false },
    );
  });

  // The band is one form for the life of the page: a resubmit keeps the node,
  // the caret stays in the query box, and the page does not scroll.
  it("keeps the band and the caret through a resubmit", async () => {
    const { push } = await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });
    const form = screen.getByRole("search");
    const box = screen.getByLabelText("Query");

    await userEvent.type(box, " size{Enter}");

    expect(push).toHaveBeenCalledWith("/dashboard/search?q=cache+size", { scroll: false });
    await screen.findByRole("heading", { name: "Results" });
    expect(screen.getByRole("search")).toBe(form);
    expect(screen.getByLabelText("Query")).toHaveFocus();
  });

  it("carries a bound the URL holds but the band has no control for", async () => {
    const { push } = await mount({ body: OWNER_SEARCH }, { search: "q=cache&limit=50" });
    await screen.findByRole("heading", { name: "Results" });

    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(push).toHaveBeenCalledWith("/dashboard/search?q=cache&limit=50", { scroll: false });
  });

  it("pages on the limit the server accepted, not the one it was asked for", async () => {
    await mount({ body: PAGED_SEARCH }, { search: "q=cache&limit=1&offset=1" });
    await screen.findByRole("heading", { name: "Results" });

    expect(screen.getByRole("link", { name: /Previous/ })).toHaveAttribute(
      "href",
      "/dashboard/search?q=cache&limit=1&offset=0",
    );
    expect(screen.getByRole("link", { name: /More results/ })).toHaveAttribute(
      "href",
      "/dashboard/search?q=cache&limit=1&offset=2",
    );
  });

  // The clamp is the *caller's*, not the prefix's: an anonymous reader on this
  // same route gets the public page of ten, and the page reads its own page
  // size off the payload rather than knowing one.
  // What `views._search_page_link` paged with. The handler bounds a query of
  // its own accord; a Next link carrying the whole of a pasted log is a link
  // that only works once.
  it("cuts the query to what the box accepts before it pages on it", async () => {
    const long = "cache".repeat(200);
    await mount({ body: PAGED_SEARCH }, { search: `q=${long}&limit=1&offset=1` });
    await screen.findByRole("heading", { name: "Results" });

    const next = screen.getByRole("link", { name: /More results/ });
    expect(next).toHaveAttribute(
      "href",
      `/dashboard/search?q=${long.slice(0, 512)}&limit=1&offset=2`,
    );
  });

  it("takes the page size the projection granted", async () => {
    await mount(
      { body: { ...DEMO_SEARCH, pagination: { ...DEMO_SEARCH.pagination, has_more: true } } },
      { search: "q=cache", session: DEMO_SESSION },
    );
    await screen.findByRole("heading", { name: "Results" });

    expect(screen.getByRole("link", { name: /More results/ })).toHaveAttribute(
      "href",
      "/dashboard/search?q=cache&offset=10",
    );

    // …and nothing on a hit is dropped for it: §2.4 gives the demo the
    // browsable corpus whole, and a search result is corpus. The frames are
    // still this page's own relative URLs, which in `AUTH=none` are simply
    // unsigned against a route that is open.
    expect(screen.getAllByText("Let's build GPT: from scratch")).not.toHaveLength(0);
    expect(momentAt("0:05")).toHaveTextContent("kv cache size = 2 * n_layers * n_heads");
    expect(frameOf("0:05").querySelector("img")).toHaveAttribute(
      "src",
      "/frames/kCc8FmEb1nY-00000.jpg?w=192&q=70",
    );
  });

  // ---------------------------------------------------------- the refusals

  // The one refusal shape on this surface: the message is the heading, the code
  // under it, and the `next:` line as the API wrote it.
  it("keeps the band on the page when the query itself is refused", async () => {
    await mount({ status: 400, body: EMPTY_QUERY_REFUSAL }, { search: "q=" });

    const notice = await screen.findByRole("region", { name: EMPTY_QUERY_REFUSAL.message });
    expect(notice).toHaveAttribute("data-tone", "bad");
    expect(within(notice).getByText("E_EMPTY_QUERY")).toBeInTheDocument();
    expect(within(notice).getByText(EMPTY_QUERY_REFUSAL.next as string)).toBeInTheDocument();
    // The band is what the reader fixes it with, so it stays.
    expect(screen.getByLabelText("Query")).toBeInTheDocument();
  });

  // Every refusal the search leg gives back takes the same notice, whatever its
  // status: the Jinja page had one shape for all of them, and a 500 that turns
  // into "this page could not read the instance" has thrown away the code.
  it("prints a refusal that is not the query's fault in the same shape", async () => {
    await mount(
      {
        status: 500,
        body: { error: "E_INTERNAL", message: "search failed", next: null },
      },
      { search: "q=cache" },
    );

    const notice = await screen.findByRole("region", { name: "search failed" });
    expect(within(notice).getByText("E_INTERNAL")).toBeInTheDocument();
    expect(screen.getByLabelText("Query")).toBeInTheDocument();
  });

  it("says the same about a content channel it does not know", async () => {
    await mount({ status: 400, body: BAD_PARAM_REFUSAL }, { search: "q=cache&content_type=x" });

    expect(await screen.findByText(BAD_PARAM_REFUSAL.message)).toBeInTheDocument();
    expect(screen.getByLabelText("Searched content")).toBeInTheDocument();
  });

  it("renders the signed-out state, in the API's own words", async () => {
    await mount({ status: 401, body: AUTH_REFUSAL }, { search: "q=cache" });

    // The instance's own message, and nothing this side wrote over the top of
    // it: `sign_in_page` rendered `error.html` with the refusal in it.
    expect(await screen.findByText(AUTH_REFUSAL.message)).toBeInTheDocument();
    expect(screen.getByText("E_AUTH_REQUIRED")).toBeInTheDocument();
  });

  // Under a stopped clock: the label has to be the delay the limiter named,
  // and a page that halved it would count down just as convincingly.
  it("counts down the limiter's own delay, and retries the read", async () => {
    await firstPaint(() =>
      mount(
        { status: 429, body: RATE_REFUSAL, headers: { "retry-after": "24" } },
        { search: "q=cache" },
      ),
    );

    expect(screen.getByRole("button", { name: "retry in 24s" })).toBeDisabled();
    expect(screen.getByText(RATE_REFUSAL.message)).toBeInTheDocument();
  });
});
