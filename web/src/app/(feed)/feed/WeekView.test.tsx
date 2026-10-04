// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeedShell } from "@/components/feed/FeedShell";
import { mountDashboard } from "@/test/dashboard/harness";
import { WEEK } from "@/test/feed/fixtures";
import { WeekView } from "./WeekView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The week fitted to the budget, then a stop, and every other video one tap away.

function mount(routes = {}, search = "") {
  return mountDashboard(
    <FeedShell>
      <WeekView />
    </FeedShell>,
    {
      bare: true,
      path: "/feed",
      search,
      routes: {
        "/dashboard/api/week": { body: WEEK },
        "POST /dashboard/api/budget": { body: { week_budget_min: 315 } },
        ...routes,
      },
    },
  );
}

describe("WeekView", () => {
  it("lists the fitted week in rank order, then stops and offers every video", async () => {
    await mount();
    const list = await screen.findByRole("list", { name: "Worth your time" });
    const rows = within(list).getAllByRole("link");
    expect(rows.map((r) => r.getAttribute("href"))).toEqual([
      "/feed/zduSFxRajkE",
      "/feed/kCc8FmEb1nY",
    ]);
    // The week's 3 asks the whole video; a 2 its moments.
    expect(within(rows[0]).getByText("1:00:00")).toBeInTheDocument();
    expect(within(rows[1]).getByText("6 of 116 min")).toBeInTheDocument();

    expect(screen.getByText("That is everything worth your time this week.")).toBeInTheDocument();
    expect(screen.getByText(/2 more verdicts ask for 1 h 30 past your budget/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Show all videos" })).toHaveAttribute(
      "href",
      "/feed/all",
    );
    expect(screen.getByRole("link", { name: "The week before" })).toHaveAttribute(
      "href",
      "/feed?week=2026-09-21",
    );
    expect(screen.queryByRole("link", { name: "The week after" })).toBeNull();
  });

  it("shows the budget per day and what each day asks", async () => {
    await mount();
    expect(await screen.findByText("1 h 05")).toBeInTheDocument();
    expect(screen.getByText(/30 min a day/)).toBeInTheDocument();
    const days = screen.getByRole("list", { name: "What each day asks" });
    expect(within(days).getAllByRole("listitem")).toHaveLength(7);
    expect(
      within(days).getByLabelText("Wednesday: 1 h 05, 2 of 4 worth your time"),
    ).toBeInTheDocument();
  });

  it("sets the budget as minutes a day and stores them as a week", async () => {
    const view = await mount();
    await userEvent.click(await screen.findByRole("button", { name: "Change" }));
    const box = screen.getByRole("spinbutton", { name: /Minutes a day/ });
    await userEvent.clear(box);
    await userEvent.type(box, "45");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(view.calls("/dashboard/api/budget", "POST")[0]?.json).toEqual({
        week_budget_min: 315,
      }),
    );
    await waitFor(() => expect(view.calls("/dashboard/api/week").length).toBeGreaterThan(1));
  });

  it("refuses an emptied budget box instead of saving zero", async () => {
    const view = await mount();
    await userEvent.click(await screen.findByRole("button", { name: "Change" }));
    await userEvent.clear(screen.getByRole("spinbutton", { name: /Minutes a day/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Not saved/)).toBeInTheDocument();
    expect(view.calls("/dashboard/api/budget", "POST")).toEqual([]);
  });

  it("says when the week holds more than it compares", async () => {
    await mount({ "/dashboard/api/week": { body: { ...WEEK, capped: true } } });
    expect(await screen.findByText(/more verdicts than the 40 it compares/)).toBeInTheDocument();
  });

  it("says so when a past week held nothing worth the time", async () => {
    await mount(
      {
        "/dashboard/api/week": {
          body: {
            ...WEEK,
            week: "2026-09-21",
            next: "2026-09-28",
            items: [],
            asks_s: 0,
            rest: { count: 0, asks_s: 0 },
          },
        },
      },
      "week=2026-09-21",
    );
    expect(await screen.findByText("Nothing was worth your time that week.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "The week after" })).toHaveAttribute(
      "href",
      "/feed?week=2026-09-28",
    );
    expect(screen.getByRole("link", { name: "Show all videos" })).toBeInTheDocument();
  });
});
