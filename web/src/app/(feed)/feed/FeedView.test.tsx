// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeedShell } from "@/components/feed/FeedShell";
import { mountDashboard } from "@/test/dashboard/harness";
import { SKIPPED, TOP } from "@/test/feed/fixtures";
import { FeedView } from "./FeedView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The two bands: 2–3 listed, 0–1 behind "skipped (n)" and read only when opened.

function mount(routes = {}) {
  return mountDashboard(
    <FeedShell>
      <FeedView />
    </FeedShell>,
    {
      bare: true,
      path: "/feed",
      routes: {
        "/dashboard/api/feed": ({ url }) => ({
          body: new URL(url, "http://x").searchParams.get("band") === "skipped" ? SKIPPED : TOP,
        }),
        ...routes,
      },
    },
  );
}

describe("FeedView", () => {
  it("lists the top band with its score words, and folds the skipped", async () => {
    const view = await mount();
    const rows = await screen.findAllByRole("link", { name: /Let's build/ });
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("watch it whole")).toBeInTheDocument();
    expect(rows[0]).toHaveAttribute("href", "/feed/kCc8FmEb1nY");
    expect(within(rows[1]).getByText("outside your profile")).toBeInTheDocument();
    expect(within(rows[0]).queryByText("outside your profile")).toBeNull();

    const fold = screen.getByRole("button", { name: /Skipped \(2\)/ });
    expect(screen.queryByText("Visualizing transformers")).toBeNull();
    expect(view.calls("/dashboard/api/feed").map((r) => r.url)).not.toContain(
      expect.stringContaining("skipped"),
    );

    await userEvent.click(fold);
    expect(await screen.findByText("Visualizing transformers")).toBeInTheDocument();
    expect(screen.getByText("skip")).toBeInTheDocument();
  });

  it("pages on, only while the band says there is more", async () => {
    const first = { ...TOP, pagination: { limit: 1, offset: 0, has_more: true, next_offset: 1 } };
    const view = await mount({
      "/dashboard/api/feed": ({ url }: { url: string }) => ({
        body: new URL(url, "http://x").searchParams.get("offset") === "1" ? TOP : first,
      }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "More" }));
    await screen.findAllByText("Let's build the GPT Tokenizer");
    expect(view.calls("/dashboard/api/feed").some((r) => r.url.includes("offset=1"))).toBe(true);
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
  });
});
