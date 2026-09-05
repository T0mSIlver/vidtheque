// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
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
import { countingDownFrom } from "@/test/retry";

// The page that answers the question the corpus exists for. Its job is to say
// what matched, what kind of thing it is evidence of, and *which second*, and
// then to hand over two links that answer two different questions: the receipt
// out to YouTube, and the moment inside this deployment's own index.
//
// So the assertions are the three traps §14.2 names — the timecode is
// `match_start` and not `timestamp`, a frame hit with no text says so in the
// page's own words, and the notes arrive with their `note:` marker already
// gone — plus the URL round trips, the states a reader lands in with nothing to
// show, and the refusals.

type Route = { status?: number; body?: unknown; headers?: Record<string, string> };

async function mount(
  search_: Route,
  { search = "", session = OWNER_SESSION }: { search?: string; session?: unknown } = {},
) {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const route: Route = url.startsWith("/dashboard/api/search")
      ? search_
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
  const nav = mockNavigation(search, "/dashboard/search");
  const { Chrome } = await import("../Chrome");
  const { SearchView } = await import("./SearchView");
  render(
    <Chrome>
      <SearchView />
    </Chrome>,
  );
  return { ...nav, fetcher };
}

/** The row one moment is on, found by the timecode it prints. */
function momentAt(timecode: string) {
  return screen.getByText(timecode).closest("li") as HTMLElement;
}

describe("the owner's search page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("groups the ranking by talk and says which slice it is showing", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });

    expect(await screen.findByRole("heading", { name: "Results" })).toBeInTheDocument();
    // Five hits over two videos: the server ranked and paginated, and the page
    // grouped what it was handed.
    expect(screen.getAllByRole("link", { name: "Making LLMs go brrr" })).toHaveLength(1);
    expect(screen.getByText("3 moment(s)")).toBeInTheDocument();
    expect(screen.getByText("2 moment(s)")).toBeInTheDocument();

    const head = screen
      .getByRole("heading", { name: "Search the corpus" })
      .closest("div") as HTMLElement;
    expect(head).toHaveTextContent("1–5");
    expect(head).toHaveTextContent("of 5");
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

  // ------------------------------------------------------------- the frame

  // The payload sends the demo's 320 and 960 against `PUBLIC_URL`; this page
  // builds its own at the dashboard's widths and on its own origin, which is
  // what a dashboard read through a tunnel needs (§14.2).
  it("builds its frames at this surface's widths, on this origin", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const shot = within(momentAt("0:05")).getByRole("img");
    expect(shot).toHaveAttribute("src", "/frames/kCc8FmEb1nY-00000.jpg?w=192&q=70");
    expect(shot).toHaveAttribute("width", "128");
    expect(shot.closest("a")).toHaveAttribute("href", "/frames/kCc8FmEb1nY-00000.jpg?w=1280&q=70");
  });

  it("keeps the column a column where a moment has no frame", async () => {
    await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    const row = momentAt("3:20");
    expect(within(row).queryByRole("img")).toBeNull();
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

  // ---------------------------------------------------------- the empties

  it("tells the two empties apart", async () => {
    await mount({ body: NO_MATCH_SEARCH }, { search: "q=zzzznothingmatchesthis" });
    expect(await screen.findByText("No moments matched.")).toBeInTheDocument();

    vi.resetModules();
    vi.unstubAllGlobals();
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
    const { fetcher } = await mount({ body: OWNER_SEARCH });

    expect(await screen.findByText("No search has run.")).toBeInTheDocument();
    expect(
      fetcher.mock.calls.filter((call) => String(call[0]).startsWith("/dashboard/api/search")),
    ).toHaveLength(0);
  });

  // ------------------------------------------------------- the URL round trip

  it("submits the band as a navigation, and sends what the reader typed", async () => {
    const { push, fetcher } = await mount({ body: OWNER_SEARCH }, { search: "q=cache" });
    await screen.findByRole("heading", { name: "Results" });

    // Every bound is Python's and caller-keyed, so the query goes as typed.
    const read = fetcher.mock.calls.find((call) =>
      String(call[0]).startsWith("/dashboard/api/search"),
    );
    expect(String(read?.[0])).toBe("/dashboard/api/search?q=cache");

    await userEvent.clear(screen.getByLabelText("Query"));
    await userEvent.type(screen.getByLabelText("Query"), "paged attention");
    await userEvent.selectOptions(screen.getByLabelText("Searched content"), "ocr");
    await userEvent.type(screen.getByLabelText("Video channel"), "GPU MODE");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    // `content_type=all` is the value the handler would have used anyway, and a
    // link carrying it says nothing twice. The parameter names are the
    // handler's, so a link bookmarked off the Jinja page still opens this one.
    expect(push).toHaveBeenCalledWith(
      "/dashboard/search?q=paged+attention&content_type=ocr&channel=GPU+MODE",
    );
  });

  it("carries a bound the URL holds but the band has no control for", async () => {
    const { push } = await mount({ body: OWNER_SEARCH }, { search: "q=cache&limit=50" });
    await screen.findByRole("heading", { name: "Results" });

    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(push).toHaveBeenCalledWith("/dashboard/search?q=cache&limit=50");
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
    expect(screen.getByText("Let's build GPT: from scratch")).toBeInTheDocument();
    expect(momentAt("0:05")).toHaveTextContent("kv cache size = 2 * n_layers * n_heads");
    expect(within(momentAt("0:05")).getByRole("img")).toHaveAttribute(
      "src",
      "/frames/kCc8FmEb1nY-00000.jpg?w=192&q=70",
    );
  });

  // ---------------------------------------------------------- the refusals

  it("keeps the band on the page when the query itself is refused", async () => {
    await mount({ status: 400, body: EMPTY_QUERY_REFUSAL }, { search: "q=" });

    expect(await screen.findByText(EMPTY_QUERY_REFUSAL.message)).toBeInTheDocument();
    expect(screen.getByText("E_EMPTY_QUERY")).toBeInTheDocument();
    expect(screen.getByText(EMPTY_QUERY_REFUSAL.next)).toBeInTheDocument();
    // The band is what the reader fixes it with, so it stays.
    expect(screen.getByLabelText("Query")).toBeInTheDocument();
  });

  it("says the same about a content channel it does not know", async () => {
    await mount({ status: 400, body: BAD_PARAM_REFUSAL }, { search: "q=cache&content_type=x" });

    expect(await screen.findByText(BAD_PARAM_REFUSAL.message)).toBeInTheDocument();
    expect(screen.getByLabelText("Searched content")).toBeInTheDocument();
  });

  it("renders the signed-out state, in the API's own words", async () => {
    await mount({ status: 401, body: AUTH_REFUSAL }, { search: "q=cache" });

    expect(
      await screen.findByText("This dashboard is not open to this browser."),
    ).toBeInTheDocument();
    expect(screen.getByText(AUTH_REFUSAL.message)).toBeInTheDocument();
  });

  it("counts down the limiter's own delay, and retries the read", async () => {
    await mount(
      { status: 429, body: RATE_REFUSAL, headers: { "retry-after": "24" } },
      { search: "q=cache" },
    );

    const retry = await screen.findByRole("button", { name: countingDownFrom(24) });
    expect(retry).toBeDisabled();
    expect(screen.getByText(RATE_REFUSAL.message)).toBeInTheDocument();
  });
});
