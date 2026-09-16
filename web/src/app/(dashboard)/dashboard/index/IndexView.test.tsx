// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mountDashboard, type Answer } from "@/test/dashboard/harness";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard/fixtures";
import {
  NO_URLS,
  NOTHING_ACCEPTED,
  ONE_JOB,
  SPLIT_RECEIPT,
  TOO_MANY_URLS,
} from "@/test/dashboard/index-fixtures";
import { countingDownFrom } from "@/test/dashboard/retry";
import { IndexView } from "./IndexView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The form reads only the session and writes once: what it draws before and
// without a write side, the fields it posts, and the receipt (a `409` too).

function mount({
  post = { body: ONE_JOB },
  search = "",
  session = OWNER_SESSION as unknown,
}: { post?: Answer; search?: string; session?: unknown } = {}) {
  return mountDashboard(<IndexView />, {
    path: "/dashboard/index",
    search,
    session,
    routes: { "POST /dashboard/index": post },
  });
}

/** The body the one POST carried. */
function sent(posts: () => { fields: URLSearchParams }[]) {
  return posts()[0].fields;
}

/** Fill the paste box and submit. */
async function queue(urls: string) {
  await userEvent.type(screen.getByLabelText("URLs"), urls);
  await userEvent.click(screen.getByRole("button", { name: "Queue the job" }));
}

describe("the index form", () => {
  it("draws the Jinja form's fields, and what a submission is split into", async () => {
    await mount();

    expect(await screen.findByRole("heading", { name: "Add to the index" })).toBeInTheDocument();
    expect(screen.getByText("A video, a playlist or a channel.")).toBeInTheDocument();
    // The two numbers the Jinja page prints, which are `URLS_PER_JOB` and
    // `MAX_FORM_URLS` — printed on a control, never enforced here. Waited on
    // rather than read once: a `getBy` asserts whatever paint happened to be
    // current when the line above it resolved, and `waitFor` asserts the state
    // the page settles in.
    await waitFor(() => {
      expect(screen.getByText("queued in jobs of")).toBeInTheDocument();
      expect(screen.getAllByText("10").length).toBeGreaterThan(0);
      expect(screen.getByText("200")).toBeInTheDocument();
    });

    expect(screen.getByLabelText("URLs")).toBeEnabled();
    expect(screen.getByLabelText("Expand")).toHaveValue("playlist");
    expect(screen.getByLabelText("Max items")).toHaveValue(25);
    expect(screen.getByLabelText("Priority")).toHaveValue("normal");
    expect(screen.getByLabelText("Tags")).toHaveValue("");
    // All three channels ticked, and force off — `_index_form_values`.
    expect(screen.getByLabelText(/Transcript/)).toBeChecked();
    expect(screen.getByLabelText(/On-screen text/)).toBeChecked();
    expect(screen.getByLabelText(/Frame embeddings/)).toBeChecked();
    expect(screen.getByLabelText(/Force re-index/)).not.toBeChecked();
  });

  // The three things a write on this surface carries, and all three together
  // (frontend-migration.md §9).
  it("posts the fields the Jinja form posts, to the route it posts to", async () => {
    const { posts } = await mount();
    await screen.findByLabelText("URLs");

    await userEvent.type(screen.getByLabelText("Tags"), "topic:attention");
    await userEvent.click(screen.getByLabelText(/Force re-index/));
    await queue("kCc8FmEb1nY");

    expect(posts()[0].path).toBe("/dashboard/index");
    expect(posts()[0].headers.get("accept")).toBe("application/json");
    expect(posts()[0].headers.get("content-type")).toBe("application/x-www-form-urlencoded");

    const body = sent(posts);
    expect(body.get("urls")).toBe("kCc8FmEb1nY");
    expect(body.get("expand")).toBe("playlist");
    expect(body.get("max_items")).toBe("25");
    expect(body.get("priority")).toBe("normal");
    expect(body.get("tags")).toBe("topic:attention");
    expect(body.get("force_reindex")).toBe("1");
    // Three ticked boxes, sent as three fields: collapsing them to the tool's
    // word `all` is `_submitted`'s reading of the form, not this side's.
    expect(body.get("channel_transcript")).toBe("1");
    expect(body.get("channel_ocr")).toBe("1");
    expect(body.get("channel_frames")).toBe("1");
  });

  it("leaves an unticked box out of the submission, as a browser would", async () => {
    const { posts } = await mount();
    await screen.findByLabelText("URLs");

    await userEvent.click(screen.getByLabelText(/Frame embeddings/));
    await queue("kCc8FmEb1nY");

    const body = sent(posts);
    expect(body.get("channel_frames")).toBeNull();
    expect(body.get("channel_ocr")).toBe("1");
  });

  describe("the receipt", () => {
    it("names the job it queued and the queue to watch it in", async () => {
      await mount();
      await screen.findByLabelText("URLs");

      await queue("https://youtu.be/solo0000002");

      const receipt = await screen.findByRole("status");
      expect(receipt).toHaveTextContent("1 URL(s) in one job.");
      expect(within(receipt).getByRole("listitem")).toHaveTextContent(/1 video\(s\) queued$/);
      expect(within(receipt).getByRole("link", { name: "job_02e028870c97" })).toHaveAttribute(
        "href",
        "/dashboard/jobs/job_02e028870c97",
      );
      expect(within(receipt).getByRole("link", { name: "Watch the queue" })).toHaveAttribute(
        "href",
        "/dashboard/jobs?state=active",
      );
      // Above the form and not instead of it: the next thing an operator does
      // after queueing a batch is queue another one.
      expect(screen.getByLabelText("URLs")).toBeInTheDocument();
    });

    it("says what the split was, what was left alone, and what was refused", async () => {
      await mount({ post: { body: SPLIT_RECEIPT } });
      await screen.findByLabelText("URLs");

      await queue("vid00000000");

      const receipt = within(await screen.findByRole("status"));
      // The split the operator cannot see is a job count they cannot explain.
      expect(screen.getByRole("status")).toHaveTextContent(
        "23 URL(s) split into 3 jobs of at most 10.",
      );
      expect(receipt.getByRole("link", { name: "job_aaaaaaaaaaaa" })).toBeInTheDocument();
      expect(receipt.getByRole("link", { name: "job_bbbbbbbbbbbb" })).toBeInTheDocument();

      // Already in the corpus is a fact about the corpus, not a failure, and
      // each id is a door to the video it names.
      expect(receipt.getByText(/already indexed and left alone/)).toBeInTheDocument();
      expect(receipt.getByRole("link", { name: "kCc8FmEb1nY" })).toHaveAttribute(
        "href",
        "/dashboard/videos/kCc8FmEb1nY",
      );

      // The refusal in the API's own words, with the batch it was refused for:
      // which URLs went with which refusal is what makes a partial failure
      // actionable.
      expect(receipt.getByText("E_BAD_PARAM")).toBeInTheDocument();
      expect(receipt.getByText("Tags must be namespace:value, lowercase.")).toBeInTheDocument();
      expect(receipt.getByText("vid00000022")).toBeInTheDocument();
      expect(receipt.getByText(/fix the tag and submit that batch again/)).toBeInTheDocument();
    });

    // `409` means nothing was accepted, and the body is still the receipt — so
    // it is read rather than thrown, exactly as the retry's is.
    it("reads a 409 as a receipt rather than as a refusal", async () => {
      await mount({ post: { status: 409, body: NOTHING_ACCEPTED } });
      await screen.findByLabelText("URLs");

      await queue("vid00000042");

      const receipt = within(await screen.findByRole("status"));
      expect(receipt.getByText("E_BAD_PARAM")).toBeInTheDocument();
      expect(receipt.getByText("vid00000042")).toBeInTheDocument();
      // Nothing was queued, so there is nowhere to go and watch.
      expect(receipt.queryByRole("link", { name: "Watch the queue" })).not.toBeInTheDocument();
    });

    // `POST → 303 → GET` left the operator looking at a document they could
    // reload. A receipt held in component state is gone the moment anything
    // reloads the page — which here is a reader pressing Ctrl-R to see whether
    // the queue moved, and finding no evidence they ever submitted.
    it("survives a reload, where the 303 used to put it", async () => {
      const first = await mount();
      await screen.findByLabelText("URLs");
      await queue("https://youtu.be/solo0000002");
      await screen.findByRole("status");

      // The reload: this tree goes, and a new one mounts against the same tab.
      first.unmount();
      await mount();
      await screen.findByLabelText("URLs");
      const receipt = await screen.findByRole("status");
      expect(receipt).toHaveTextContent("1 URL(s) in one job.");
      expect(within(receipt).getByRole("link", { name: "job_02e028870c97" })).toBeInTheDocument();
    });

    // …and it is the receipt of the batch on screen, never the one before it:
    // a refusal read over an old receipt is a page saying two things about one
    // click.
    it("drops the kept receipt when the next submission is refused", async () => {
      const first = await mount();
      await screen.findByLabelText("URLs");
      await queue("https://youtu.be/solo0000002");
      await screen.findByRole("status");

      first.unmount();
      await mount({ post: { status: 400, body: NO_URLS } });
      await screen.findByRole("heading", { name: "What that submission did" });
      await userEvent.click(screen.getByRole("button", { name: "Queue the job" }));

      expect(await screen.findByText(NO_URLS.message)).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "What that submission did" }),
      ).not.toBeInTheDocument();
    });

    // `_submitted` clamps `max_items` to the tool's own 1..200 and falls both
    // vocabularies back to their defaults, and the Jinja page re-rendered the
    // form from it — so what a reader was left looking at was the number the
    // batch actually used. A clamp nobody is shown is a clamp that looks like
    // a bug in the thing that clamped.
    //
    // The controls' own `min`/`max` stop a browser posting 9000 at all, so
    // what this is about is the resolution the *server* made: the clamp
    // against a value that arrived some other way, and the two vocabularies
    // falling back rather than being refused.
    it("echoes what the server ran on back into the three controls", async () => {
      await mount({
        post: {
          body: { ...ONE_JOB, accepted: { expand: "playlist", max_items: 200, priority: "high" } },
        },
      });
      await screen.findByLabelText("URLs");

      await userEvent.clear(screen.getByLabelText("Max items"));
      await userEvent.type(screen.getByLabelText("Max items"), "5");
      await queue("https://youtu.be/solo0000002");
      await screen.findByRole("status");

      expect(screen.getByLabelText("Max items")).toHaveValue(200);
      expect(screen.getByLabelText("Expand")).toHaveValue("playlist");
      expect(screen.getByLabelText("Priority")).toHaveValue("high");
      // What was typed is still there: the Jinja re-render kept it too, and a
      // form that reseeds three pickers by remounting throws it away.
      expect(screen.getByLabelText("URLs")).toHaveValue("https://youtu.be/solo0000002");
    });

    // An instance that predates `accepted` sends none, and the controls keep
    // what was typed rather than snapping back to a default nobody chose.
    it("leaves the controls alone when the outcome carries no echo", async () => {
      await mount();
      await screen.findByLabelText("URLs");

      await userEvent.selectOptions(screen.getByLabelText("Priority"), "high");
      await queue("https://youtu.be/solo0000002");
      await screen.findByRole("status");

      expect(screen.getByLabelText("Priority")).toHaveValue("high");
    });
  });

  describe("the refusals", () => {
    it("prints the form's own bounds in the instance's words", async () => {
      await mount({ post: { status: 413, body: TOO_MANY_URLS } });
      await screen.findByLabelText("URLs");

      await queue("vid00000000");

      expect(
        await screen.findByText("201 URLs is past this form's cap of 200."),
      ).toBeInTheDocument();
      expect(screen.getByText("E_TOO_LARGE")).toBeInTheDocument();
      expect(
        screen.getByText("submit it in parts, or point one job at the playlist."),
      ).toBeInTheDocument();
      // The form stays, with what was typed still in it.
      expect(screen.getByLabelText("URLs")).toHaveValue("vid00000000");
    });

    // The refusal is raised after `_submitted` resolved the three, so it says
    // what the server would have run on (§21). A form told "that list is too
    // long" while still showing the 9000 it typed is a form reporting two
    // different problems, only one of which is the one it was refused for.
    it("echoes the resolved values back into the controls on a refusal too", async () => {
      await mount({ post: { status: 413, body: TOO_MANY_URLS } });
      await screen.findByLabelText("URLs");

      await userEvent.clear(screen.getByLabelText("Max items"));
      await userEvent.type(screen.getByLabelText("Max items"), "5");
      await queue("vid00000000");

      expect(await screen.findByText("E_TOO_LARGE")).toBeInTheDocument();
      expect(screen.getByLabelText("Max items")).toHaveValue(200);
      expect(screen.getByLabelText("Expand")).toHaveValue("playlist");
      expect(screen.getByLabelText("Priority")).toHaveValue("normal");
      expect(screen.getByLabelText("URLs")).toHaveValue("vid00000000");
    });

    it("keeps the paste box when the submission had no URL in it", async () => {
      await mount({ post: { status: 400, body: NO_URLS } });
      await screen.findByLabelText("URLs");

      await queue(" ");

      expect(
        await screen.findByText("Paste at least one video, playlist or channel URL."),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Queue the job" })).toBeEnabled();
    });

    // The refusal lands under the actions and takes the focus the button had:
    // nothing moves, and the keyboard is not dropped to the document.
    it("keeps the actions in place and focus on the refusal", async () => {
      await mount({ post: { status: 400, body: NO_URLS } });
      await screen.findByLabelText("URLs");
      const button = screen.getByRole("button", { name: "Queue the job" });

      await userEvent.click(button);
      const message = await screen.findByText(NO_URLS.message);

      expect(button).toBeInTheDocument();
      expect(document.activeElement).not.toBe(document.body);
      const region = button.closest("[data-write]") as HTMLElement;
      expect(region.contains(document.activeElement)).toBe(true);
      expect(region.contains(message)).toBe(true);
    });

    it("prints the instance's own refusal when the session went away", async () => {
      await mount({
        post: {
          status: 401,
          body: {
            error: "E_AUTH_REQUIRED",
            message: "This dashboard needs the owner's password, token or session.",
            next: "Sign in at /dashboard/login.",
          },
        },
      });
      await screen.findByLabelText("URLs");

      await queue("kCc8FmEb1nY");

      expect(
        await screen.findByText("This dashboard needs the owner's password, token or session."),
      ).toBeInTheDocument();
      expect(screen.getByText("E_AUTH_REQUIRED")).toBeInTheDocument();
    });

    it("prints the limiter's refusal without inventing a delay of its own", async () => {
      await mount({
        post: {
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard writes.", next: null },
          headers: { "retry-after": "9" },
        },
      });
      await screen.findByLabelText("URLs");

      await queue("kCc8FmEb1nY");

      expect(await screen.findByText("Too many dashboard writes.")).toBeInTheDocument();
      expect(screen.getByText("E_RATE_LIMIT")).toBeInTheDocument();
      // A write is not a read: the countdown belongs to a page that can re-run
      // its own read, and this one re-runs when the operator submits again.
      expect(screen.queryByRole("button", { name: countingDownFrom(9) })).not.toBeInTheDocument();
    });
  });

  describe("what the deployment allows", () => {
    // `GET /dashboard/index` is registered with the write routes, so on a
    // deployment that registers none it is not a disabled form — it is not
    // there at all.
    it("is not a page at all where there is no write side", async () => {
      await mount({ session: DEMO_SESSION });

      expect(
        await screen.findByText("This deployment does not index anything."),
      ).toBeInTheDocument();
      expect(screen.queryByLabelText("URLs")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Queue the job" })).not.toBeInTheDocument();
      // Not an error state: nothing here failed — so the band wears the
      // neutral tone rather than the one a refusal wears.
      expect(screen.queryByText(/could not read/)).not.toBeInTheDocument();
      expect(
        screen.getByRole("region", { name: "This deployment does not index anything." }),
      ).toHaveAttribute("data-tone", "neutral");
    });

    // §5.5: refuse honestly. Disabled with the reason above it, rather than
    // accepting a submission that comes back `E_FEATURE_DISABLED` after the
    // operator has typed sixty URLs into it.
    it("disables every control when the database refuses writes", async () => {
      await mount({ session: { ...OWNER_SESSION, writes_allowed: false } });

      expect(await screen.findByText("Indexing is disabled on this instance.")).toBeInTheDocument();
      // The head's own pill, not the rail's foot line, which says the same
      // thing about the deployment one level up.
      expect(within(screen.getByRole("banner")).getByText("indexing refused")).toBeInTheDocument();
      expect(screen.getAllByText("indexing refused")).toHaveLength(2);
      expect(screen.getByLabelText("URLs")).toBeDisabled();
      expect(screen.getByLabelText("Expand")).toBeDisabled();
      expect(screen.getByLabelText(/Force re-index/)).toBeDisabled();
      expect(screen.getByRole("button", { name: "Queue the job" })).toBeDisabled();
    });

    // `_assert_dimensions` turns `writes_allowed` off and writes the sentence
    // in the same breath, and the Jinja page printed that sentence under the
    // disabled controls. A form refused with no reason is a form an operator
    // retypes.
    it("prints the mismatch in the instance's own words", async () => {
      await mount({
        session: {
          ...OWNER_SESSION,
          writes_allowed: false,
          writes_refused_reason:
            "text_embed dim 2048 in config, 1024 in the vector table (12 rows).",
        },
      });

      expect(
        await screen.findByText(/text_embed dim 2048 in config, 1024 in the vector table/),
      ).toBeInTheDocument();
    });

    // …and never a sentence about nothing: the field is `null` in the
    // projection and on an instance that predates it, and the notice reads as
    // a complete line without it.
    it("says nothing more where the instance sent no reason", async () => {
      await mount({ session: { ...OWNER_SESSION, writes_allowed: false } });

      expect(
        await screen.findByText("The corpus config and the vector tables disagree."),
      ).toBeInTheDocument();
      expect(document.body.textContent).not.toMatch(/\bnull\b|undefined/);
    });

    // Drawn before the session lands, this page would tell the reader indexing
    // is refused on the strength of not yet having asked.
    it("says nothing about the deployment before it has been told", async () => {
      await mountDashboard(<IndexView />, {
        path: "/dashboard/index",
        routes: { "/dashboard/api/session": () => new Promise<Answer>(() => {}) },
      });

      // The head is there, holding its line; the body waits.
      expect(screen.getByRole("heading", { name: "Add to the index" })).toBeInTheDocument();
      expect(screen.getByText("reading…")).toBeInTheDocument();
      expect(screen.queryByText("Indexing is disabled on this instance.")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("URLs")).not.toBeInTheDocument();
    });

    // The session is the chassis's read, and retrying it re-asks it in place.
    it("re-asks a session that could not be read, and draws the form once it answers", async () => {
      const { calls } = await mountDashboard(<IndexView />, {
        path: "/dashboard/index",
        routes: {
          "/dashboard/api/session": [
            { status: 500, body: { error: "E_INTERNAL", message: "The instance fell over." } },
            { body: OWNER_SESSION },
          ],
        },
      });

      await userEvent.click(await screen.findByRole("button", { name: "Try again" }));

      expect(await screen.findByLabelText("URLs")).toBeEnabled();
      expect(calls("/dashboard/api/session")).toHaveLength(2);
    });
  });

  // The seeding link a video's detail page carries: `urls` and `expand`. A
  // prefill is a draft — nothing here normalises a URL or decides anything, and
  // the POST stays the only thing that interprets it.
  describe("the prefill", () => {
    it("seeds the controls from the link that queued more from a channel", async () => {
      await mount({
        search: "urls=https%3A%2F%2Fyoutu.be%2FkCc8FmEb1nY&expand=channel_recent",
      });

      expect(await screen.findByLabelText("URLs")).toHaveValue("https://youtu.be/kCc8FmEb1nY");
      expect(screen.getByLabelText("Expand")).toHaveValue("channel_recent");
    });

    it("takes tags too, and leaves an expansion the tool does not know", async () => {
      await mount({ search: "tags=topic%3Aattention&expand=everything" });

      expect(await screen.findByLabelText("Tags")).toHaveValue("topic:attention");
      expect(screen.getByLabelText("Expand")).toHaveValue("playlist");
    });

    // The two character bounds are this page's own: the Jinja handler that held
    // them is deleted, and the mcp test that pinned them went with it. These
    // are the numbers it enforced, so a link that worked against Python still
    // lands the same draft here.
    it("cuts a `urls` parameter at 16,384 characters", async () => {
      const search = new URLSearchParams({ urls: "u".repeat(16_384 + 500) }).toString();

      await mount({ search });

      expect(await screen.findByLabelText("URLs")).toHaveValue("u".repeat(16_384));
    });

    it("cuts a `tags` parameter at 800", async () => {
      const search = new URLSearchParams({ tags: "t".repeat(800 + 50) }).toString();

      await mount({ search });

      expect(await screen.findByLabelText("Tags")).toHaveValue("t".repeat(800));
    });

    it("posts the seeded draft as typed", async () => {
      const { posts } = await mount({ search: "urls=kCc8FmEb1nY&expand=channel_recent" });
      await screen.findByLabelText("URLs");

      await userEvent.click(screen.getByRole("button", { name: "Queue the job" }));

      const body = sent(posts);
      expect(body.get("urls")).toBe("kCc8FmEb1nY");
      expect(body.get("expand")).toBe("channel_recent");
    });
  });
});
