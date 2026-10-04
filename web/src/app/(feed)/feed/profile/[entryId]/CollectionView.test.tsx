// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeedShell } from "@/components/feed/FeedShell";
import { mountDashboard } from "@/test/dashboard/harness";
import { COLLECTION } from "@/test/feed/fixtures";
import { CollectionView } from "./CollectionView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// One interest's moments, read from the top: what repeats a moment above opens after it.

function mount() {
  return mountDashboard(
    <FeedShell>
      <CollectionView entryId={3} />
    </FeedShell>,
    {
      bare: true,
      path: "/feed/profile/3",
      routes: {
        "/dashboard/api/collections/3": { body: COLLECTION },
        "POST /dashboard/api/signals": {
          body: { recorded: true, signal_id: 1, kind: "watch", video_id: "zduSFxRajkE" },
        },
      },
    },
  );
}

describe("CollectionView", () => {
  it("lists the moments with their videos and marks the repeat of an earlier one", async () => {
    const view = await mount();
    expect(
      await screen.findByRole("heading", { name: "Local inference on consumer GPUs" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 moments, 8 min")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /2\. GPU MODE · Making LLMs go brrr/ }),
    ).toHaveAttribute("href", "/feed/zduSFxRajkE");
    expect(screen.getByText("Skips 1:15 you saw in moment 1")).toBeInTheDocument();
    expect(screen.getByText(/these are the best 2/)).toBeInTheDocument();

    const moment = screen.getByRole("link", { name: /paged attention, after a recap/ });
    expect(moment).toHaveAttribute("href", "https://youtu.be/zduSFxRajkE?t=103");
    await userEvent.click(moment);
    await waitFor(() =>
      expect(view.calls("/dashboard/api/signals", "POST").map((r) => r.json)).toContainEqual({
        kind: "watch",
        video_id: "zduSFxRajkE",
        offset_s: 105,
      }),
    );
  });
});
