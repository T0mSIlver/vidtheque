// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeedShell } from "@/components/feed/FeedShell";
import { mountDashboard } from "@/test/dashboard/harness";
import { PAUSED } from "@/test/dashboard/following-fixtures";
import { BRIEF, PROFILE, PROPOSED } from "@/test/feed/fixtures";
import { BriefView } from "./BriefView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// What the brief writes: the check-in, an audit answer and the reweight it
// proposes, a pause the owner asked for, and a revert.

function mount() {
  return mountDashboard(
    <FeedShell>
      <BriefView week={null} />
    </FeedShell>,
    {
      bare: true,
      path: "/feed/brief",
      routes: {
        "/dashboard/api/brief": { body: BRIEF },
        "POST /dashboard/api/brief/checkin": {
          body: { week: BRIEF.week, rating: 4, missing: "GPU talks" },
        },
        "POST /dashboard/api/skips": { body: PROPOSED },
        "POST /dashboard/api/profile": {
          body: { ...PROFILE, applied: { events: [9], duplicates: [] } },
        },
        "POST /dashboard/api/profile/revert": { body: { ...PROFILE, reverted: { events: [9] } } },
        "POST /dashboard/following/hype-daily/state": {
          body: { follow: { ...PAUSED, last_error_message: null } },
        },
      },
    },
  );
}

describe("BriefView", () => {
  it("rates the week, then saves what was missing with it", async () => {
    const view = await mount();
    expect(
      await screen.findByText(/Against YouTube: 43% hit rate · 17% regret · 2 misses/),
    ).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "4" }));
    await userEvent.type(screen.getByLabelText("What was missing (optional)"), "GPU talks");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(view.calls("/dashboard/api/brief/checkin", "POST")).toHaveLength(2));
    expect(view.calls("/dashboard/api/brief/checkin", "POST")[1].json).toEqual({
      week: "2026-09-28",
      rating: 4,
      missing: "GPU talks",
    });
  });

  it("an audit yes proposes easing the entry that sank it, applied only on a tap", async () => {
    const view = await mount();
    expect(
      await screen.findByText("Sunk by “Model launch hype with no benchmarks”"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect(view.calls("/dashboard/api/skips", "POST")[0].json).toEqual({
      video_id: "eMlx5fFNoYc",
      answer: "wrong",
      source: "audit",
    });
    await screen.findByText(/from −0.8 to −0.5\?/);
    expect(view.calls("/dashboard/api/profile", "POST")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Ease it" }));
    await waitFor(() =>
      expect(view.calls("/dashboard/api/profile", "POST")[0]?.json).toMatchObject({
        reweight: [{ id: 7, weight: -0.5 }],
      }),
    );
  });

  it("offers a pause only on a flagged channel, and reverts a nightly change", async () => {
    const view = await mount();
    await screen.findByText("Hype Daily");
    expect(screen.queryByRole("button", { name: "Pause Andrej Karpathy" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Pause Hype Daily" }));
    await waitFor(() =>
      expect(
        view.calls("/dashboard/following/hype-daily/state", "POST")[0]?.fields.get("action"),
      ).toBe("pause"),
    );
    await userEvent.click(screen.getByRole("button", { name: /Revert: reweight/ }));
    await waitFor(() =>
      expect(view.calls("/dashboard/api/profile/revert", "POST")[0]?.json).toEqual({ event_id: 3 }),
    );
    expect(await screen.findByText("reverted")).toBeInTheDocument();
  });
});
