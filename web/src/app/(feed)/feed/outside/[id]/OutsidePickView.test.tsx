// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeedShell } from "@/components/feed/FeedShell";
import { OutsideList } from "@/components/feed/OutsidePicks";
import { mountDashboard } from "@/test/dashboard/harness";
import { OUTSIDE } from "@/test/feed/fixtures";
import { OutsidePickView } from "./OutsidePickView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// Discovery outside the follows: labelled picks, a speaker, and the 14-day follow.

describe("OutsideList", () => {
  it("labels each pick with the entry it was scouted for and offers the speaker", async () => {
    await mountDashboard(<OutsideList data={OUTSIDE} />, { bare: true, path: "/feed" });
    const band = screen.getByRole("region", { name: "From outside your follows" });
    const row = within(band).getByRole("link", { name: /An eval harness/ });
    expect(row).toHaveAttribute("href", "/feed/outside/4");
    expect(
      within(row).getByText("from outside, because of: Coding agent evals"),
    ).toBeInTheDocument();
    const speaker = within(band).getByRole("article", { name: "Speaker suggestion: Grace Hopper" });
    expect(
      within(speaker).getByRole("button", { name: "Follow Grace Hopper for 14 days" }),
    ).toBeInTheDocument();
  });
});

describe("OutsidePickView", () => {
  it("offers a 14-day follow after a thumbs up, and says until when", async () => {
    const until = 1_792_000_000;
    await mountDashboard(
      <FeedShell>
        <OutsidePickView id={4} />
      </FeedShell>,
      {
        bare: true,
        path: "/feed/outside/4",
        routes: {
          "/dashboard/api/outside/4": { body: OUTSIDE.picks[0] },
          "POST /dashboard/api/outside/feedback": {
            body: {
              id: 4,
              state: "up",
              offer: {
                channel: "Outside Talks",
                url: "https://www.youtube.com/@outsidetalks",
                days: 14,
              },
            },
          },
          "POST /dashboard/api/outside/follow": {
            body: {
              url: "https://www.youtube.com/@outsidetalks",
              title: "Outside Talks",
              trial_until: until,
              already: false,
            },
          },
        },
      },
    );
    expect(
      await screen.findByText("Judged on YouTube's captions only. It is not in your library."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Forty tasks on every pull request/ })).toHaveAttribute(
      "href",
      "https://youtu.be/outside0001?t=600",
    );
    await userEvent.click(screen.getByRole("button", { name: "Thumbs up" }));
    await userEvent.click(await screen.findByRole("button", { name: "Follow for 14 days" }));
    expect(await screen.findByText(/On a 14-day follow until/)).toBeInTheDocument();
  });
});
