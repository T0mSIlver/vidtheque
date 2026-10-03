// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeedShell } from "@/components/feed/FeedShell";
import { mountDashboard, type Route } from "@/test/dashboard/harness";
import { VERDICT } from "@/test/feed/fixtures";
import { VideoView } from "./VideoView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// Every tap here is a signal (companion.md §2.3): which kind, and with which offset.

const RECORDED = { recorded: true, signal_id: 1, kind: "open", video_id: "kCc8FmEb1nY" };

function mount(verdict: Route = { body: VERDICT }) {
  return mountDashboard(
    <FeedShell>
      <VideoView videoId="kCc8FmEb1nY" />
    </FeedShell>,
    {
      bare: true,
      path: "/feed/kCc8FmEb1nY",
      routes: {
        "/dashboard/api/verdicts/kCc8FmEb1nY": verdict,
        "POST /dashboard/api/signals": { body: RECORDED },
        "POST /dashboard/api/feedback": { body: { video_id: "kCc8FmEb1nY", state: "up" } },
      },
    },
  );
}

const signals = (view: Awaited<ReturnType<typeof mount>>) =>
  view.calls("/dashboard/api/signals", "POST").map((request) => request.json);

describe("VideoView", () => {
  it("records one open, and a watch with the moment's offset", async () => {
    const view = await mount();
    const moment = await screen.findByRole("link", { name: /the self-attention block/ });
    expect(moment).toHaveAttribute("href", "https://youtu.be/kCc8FmEb1nY?t=840");
    expect(screen.getByText("14:02")).toBeInTheDocument();
    expect(screen.getByText(/1 moment is left out/)).toBeInTheDocument();
    await waitFor(() => expect(signals(view)).toEqual([{ kind: "open", video_id: "kCc8FmEb1nY" }]));

    await userEvent.click(moment);
    await waitFor(() =>
      expect(signals(view)).toContainEqual({
        kind: "watch",
        video_id: "kCc8FmEb1nY",
        offset_s: 842.5,
      }),
    );
  });

  it("plays from the start with a watch at 0", async () => {
    const view = await mount();
    const play = await screen.findByRole("link", { name: /Play from the start/ });
    expect(play).toHaveAttribute("href", "https://youtu.be/kCc8FmEb1nY");
    await userEvent.click(play);
    await waitFor(() =>
      expect(signals(view)).toContainEqual({ kind: "watch", video_id: "kCc8FmEb1nY", offset_s: 0 }),
    );
  });

  it("asks Claude about this video by id, title and channel", async () => {
    const view = await mount();
    const ask = await screen.findByRole("link", { name: "Ask Claude" });
    const q = new URL(ask.getAttribute("href")!).searchParams.get("q")!;
    for (const part of ["vidtheque", "kCc8FmEb1nY", VERDICT.video.title, "Andrej Karpathy"]) {
      expect(q).toContain(part);
    }
    await userEvent.click(ask);
    await waitFor(() =>
      expect(signals(view)).toContainEqual({ kind: "ask_claude", video_id: "kCc8FmEb1nY" }),
    );
  });

  it("shows the stored thumb or mute, and a second tap takes it back", async () => {
    const view = await mount({ body: { ...VERDICT, feedback: "muted" } });
    const less = await screen.findByRole("button", { name: "Less like this" });
    expect(less).toHaveAttribute("aria-pressed", "true");
    const up = screen.getByRole("button", { name: "Thumbs up" });
    await userEvent.click(up);
    await waitFor(() => expect(up).toHaveAttribute("aria-pressed", "true"));
    expect(less).toHaveAttribute("aria-pressed", "false");
    await waitFor(() => expect(up).not.toHaveAttribute("aria-busy", "true"));
    await userEvent.click(up);
    await waitFor(() => expect(up).toHaveAttribute("aria-pressed", "false"));
    const sent = view.calls("/dashboard/api/feedback", "POST").map((request) => request.json);
    expect(sent).toEqual([
      { video_id: "kCc8FmEb1nY", state: "up" },
      { video_id: "kCc8FmEb1nY", state: "none" },
    ]);
  });

  it("says a video has no verdict yet in the API's words", async () => {
    await mount({
      status: 404,
      body: {
        error: "E_NO_VERDICT",
        message: 'Video "kCc8FmEb1nY" has no verdict yet.',
        next: "check again once its job is done.",
      },
    });
    expect(await screen.findByText('Video "kCc8FmEb1nY" has no verdict yet.')).toBeInTheDocument();
    expect(screen.getByText("E_NO_VERDICT")).toBeInTheDocument();
  });
});
