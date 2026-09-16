// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_SESSION, OWNER_SESSION } from "@/test/dashboard/fixtures";
import { REINDEX_REFUSED, TAG_REFUSED, TAGGED } from "@/test/dashboard/index-fixtures";
import { DEMO_VIDEO, OWNER_HALF, OWNER_VIDEO } from "@/test/dashboard/library-fixtures";
import { firstPaint } from "@/test/dashboard/retry";
import { mountVideo } from "./detail-harness";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The page around its panels: the header, what was stored, chapters and runs,
// a read that does not land, and the write side.

describe("the video detail", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("carries the header, both state words and the source", async () => {
    await mountVideo({ body: OWNER_VIDEO });

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
    await waitFor(() => expect(document.title).toBe("Let's build GPT: from scratch — vidtheque"));
  });

  it("counts what was stored, and where the cues came from", async () => {
    await mountVideo({ body: OWNER_VIDEO });

    expect(await screen.findByText("What was stored")).toBeInTheDocument();
    // The breakdown is the bare integer, not a second grouped figure.
    expect(screen.getByText("cues").closest("div")).toHaveTextContent("whisperx 6");
    expect(screen.getByText("keyframes").closest("div")).toHaveTextContent("kept of 3 captured");
    expect(screen.getByText("frames with text").closest("div")).toHaveTextContent("2 lines read");
    expect(screen.getByText("keyframe bytes").closest("div")).toHaveTextContent(
      "no word timings stored",
    );
  });

  it("says a transcript is absent rather than showing an empty box", async () => {
    await mountVideo({ body: OWNER_HALF }, { videoId: "aaaaaaaaaaa" });

    expect(await screen.findByText("No transcript cues on this page.")).toBeInTheDocument();
    expect(
      screen.getByText("No keyframes were captured, so this video has no shots."),
    ).toBeInTheDocument();
    expect(screen.getByText("No indexing job is linked to this video.")).toBeInTheDocument();
  });

  // A chapter start is a boundary, not a quoted moment: no `DEEPLINK_LEAD` (§3.6).
  it("links a chapter at its own start, not two seconds before it", async () => {
    await mountVideo({
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
    await mountVideo({ body: OWNER_VIDEO });

    expect(await screen.findByText("Recent indexing runs")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "job_running001" })).toHaveAttribute(
      "href",
      "/dashboard/jobs/job_running001",
    );
    expect(screen.getByText("Latest 10 at most; no total is computed.")).toBeInTheDocument();
  });

  it("gives the demo every panel of a finished video", async () => {
    await mountVideo({ body: DEMO_VIDEO }, { session: DEMO_SESSION });

    expect(await screen.findByText("What was stored")).toBeInTheDocument();
    expect(screen.getByText("nvidia-smi 18304MiB")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/null|NaN|undefined/);
  });

  describe("when the read does not land", () => {
    // An id that is not in the corpus is not a failure to read the instance:
    // the read succeeded and the answer is "there is no such video".
    it("answers an unknown video with the refusal and a way back", async () => {
      await mountVideo(
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
      await waitFor(() => expect(document.title).toBe("Unknown video — vidtheque"));
    });

    it("prints the instance's own refusal when it is signed out", async () => {
      await mountVideo({
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
        mountVideo({
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
          headers: { "retry-after": "7" },
        }),
      );

      expect(screen.getByRole("button", { name: "retry in 7s" })).toBeDisabled();
    });

    it("says so when the instance answers in a shape it cannot read", async () => {
      await mountVideo({ body: { ...OWNER_VIDEO, stages: null } });

      expect(await screen.findByText(/shape this page cannot read/)).toBeInTheDocument();
    });
  });

  // The write side, last on the page and only where it is registered. Two
  // actions and no third: `jobs.kind='delete'` is in the schema with no
  // pipeline behind it, so a delete button here would queue a job that fails.
  describe("the manage panel", () => {
    it("queues a forced rebuild and names the job it made", async () => {
      const { posts } = await mountVideo({ body: OWNER_VIDEO });
      await screen.findByRole("heading", { name: "Manage this video" });

      await userEvent.click(screen.getByRole("button", { name: "Re-index this video" }));

      expect(posts()[0].path).toBe("/dashboard/videos/kCc8FmEb1nY/reindex");
      // No fields: the video is named by the path.
      expect(posts()[0].fields.toString()).toBe("");
      expect(posts()[0].headers.get("accept")).toBe("application/json");

      // The control does not come back: a second POST would queue a second
      // rebuild of the same video.
      expect(await screen.findByRole("link", { name: "job_02e028870c97" })).toHaveAttribute(
        "href",
        "/dashboard/jobs/job_02e028870c97",
      );
      expect(screen.queryByRole("button", { name: "Re-index this video" })).not.toBeInTheDocument();
    });

    it("prints the tool's refusal rather than claiming it queued something", async () => {
      await mountVideo({ body: OWNER_VIDEO }, { post: { status: 409, body: REINDEX_REFUSED } });
      await screen.findByRole("heading", { name: "Manage this video" });

      await userEvent.click(screen.getByRole("button", { name: "Re-index this video" }));

      expect(await screen.findByText("E_INDEXING")).toBeInTheDocument();
      expect(screen.getByText("kCc8FmEb1nY is already being indexed.")).toBeInTheDocument();
    });

    // The row's tags *after* the write, read back — never a diff applied on
    // this side, because `tag_video` reports what it changed across a batch and
    // this panel is showing the row.
    it("replaces the tag list with the tags the row carries after the write", async () => {
      const { posts } = await mountVideo({ body: OWNER_VIDEO }, { post: { body: TAGGED } });
      await screen.findByRole("heading", { name: "Manage this video" });

      await userEvent.type(screen.getByLabelText("Add"), "topic:json, series:writes");
      await userEvent.click(screen.getByRole("button", { name: "Apply" }));

      expect(posts()[0].path).toBe("/dashboard/videos/kCc8FmEb1nY/tags");
      const body = posts()[0].fields;
      expect(body.get("add")).toBe("topic:json, series:writes");
      expect(body.get("remove")).toBe("");

      // Both places this page prints tags follow the row: the chips under the
      // title as well as the list under the form.
      expect(await screen.findAllByText("series:writes")).toHaveLength(2);
      expect(screen.getAllByText("topic:json")).toHaveLength(2);
    });

    it("prints tag_video's own refusal, in its own words", async () => {
      await mountVideo({ body: OWNER_VIDEO }, { post: { status: 400, body: TAG_REFUSED } });
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
      await mountVideo(
        { body: OWNER_VIDEO },
        { session: { ...OWNER_SESSION, writes_allowed: false } },
      );
      await screen.findByRole("heading", { name: "Manage this video" });

      expect(screen.getByRole("button", { name: "Re-index this video" })).toBeDisabled();
      expect(screen.getByText(/Indexing is refused on this instance/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled();
    });

    it("is not on the page at all in the projection", async () => {
      await mountVideo({ body: DEMO_VIDEO }, { session: DEMO_SESSION });
      await screen.findByRole("heading", { name: "Let's build GPT: from scratch" });

      expect(screen.queryByText("Manage this video")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("link", { name: "Queue more from this channel" }),
      ).not.toBeInTheDocument();
    });

    // A `GET` prefill and not a write: the index form remains the place the
    // operator reviews it, and the POST remains the only state change.
    it("links the channel into the index form, seeded", async () => {
      await mountVideo({ body: OWNER_VIDEO });

      expect(
        await screen.findByRole("link", { name: "Queue more from this channel" }),
      ).toHaveAttribute(
        "href",
        "/dashboard/index?urls=https%3A%2F%2Fyoutu.be%2FkCc8FmEb1nY&expand=channel_recent",
      );
    });
  });
});
