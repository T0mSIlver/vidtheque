// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeedShell } from "@/components/feed/FeedShell";
import { mountDashboard } from "@/test/dashboard/harness";
import { COLLECTIONS, PROFILE } from "@/test/feed/fixtures";
import { ProfileView } from "./ProfileView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// What the profile screen writes: pasted lines as one `add` batch, a drop by
// id, a revert by event id; each write re-reads the profile.

function mount() {
  return mountDashboard(
    <FeedShell>
      <ProfileView />
    </FeedShell>,
    {
      bare: true,
      path: "/feed/profile",
      routes: {
        "/dashboard/api/profile": { body: PROFILE },
        "/dashboard/api/collections": { body: COLLECTIONS },
        "POST /dashboard/api/profile": {
          body: { ...PROFILE, applied: { events: [4], duplicates: [] } },
        },
        "POST /dashboard/api/profile/revert": { body: { ...PROFILE, reverted: { events: [5] } } },
      },
    },
  );
}

describe("ProfileView", () => {
  it("shows entries by weight with their sign, and the history's change", async () => {
    await mount();
    expect(await screen.findByText("+0.6")).toBeInTheDocument();
    expect(screen.getByText("−0.8")).toHaveAttribute("data-negative", "true");
    expect(screen.getByText("+0.3 → +0.6")).toBeInTheDocument();
    expect(screen.getByText(/2 of 40/)).toBeInTheDocument();
  });

  it("opens a wanted entry's moments from its count and minutes", async () => {
    await mount();
    const link = await screen.findByRole("link", { name: /4 moments, 19 min/ });
    expect(link).toHaveAttribute("href", "/feed/profile/3");
  });

  it("adds pasted lines as one batch and reads the profile again", async () => {
    const view = await mount();
    const box = await screen.findByLabelText(/Paste a list/);
    await userEvent.type(box, "- Eval harnesses (+0.9){enter}Rust tooling");
    await userEvent.click(screen.getByRole("button", { name: "Add 2 interests" }));
    await waitFor(() =>
      expect(view.calls("/dashboard/api/profile", "POST")[0]?.json).toEqual({
        add: [
          { text: "Eval harnesses", weight: 0.9 },
          { text: "Rust tooling", weight: 0.5 },
        ],
        reason: "pasted on the profile screen",
      }),
    );
    await waitFor(() => expect(view.calls("/dashboard/api/profile").length).toBeGreaterThan(1));
    expect(box).toHaveValue("");
  });

  it("reverts one event and drops one entry by id", async () => {
    const view = await mount();
    await userEvent.click(await screen.findByRole("button", { name: /^Revert: reweight/ }));
    await waitFor(() =>
      expect(view.calls("/dashboard/api/profile/revert", "POST")[0]?.json).toEqual({
        event_id: 3,
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^Drop Model launch hype/ }));
    await waitFor(() =>
      expect(view.calls("/dashboard/api/profile", "POST").map((r) => r.json)).toContainEqual({
        drop: [7],
        reason: "dropped on the profile screen",
      }),
    );
  });

  it("opens Claude with the interview prompt", async () => {
    await mount();
    const ask = await screen.findByRole("link", { name: "Ask Claude to interview me" });
    const q = new URL(ask.getAttribute("href")!).searchParams.get("q")!;
    expect(q).toContain("vidtheque profile tool");
  });
});
