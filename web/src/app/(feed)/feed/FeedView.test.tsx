// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FeedShell } from "@/components/feed/FeedShell";
import { mountDashboard } from "@/test/dashboard/harness";
import { FACETS, SKIPPED, TOP } from "@/test/feed/fixtures";
import { FeedView } from "./FeedView";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The two bands: 2–3 listed, 0–1 behind "skipped (n)" and read only when opened.

function mount(routes = {}, search = "") {
  return mountDashboard(
    <FeedShell>
      <FeedView />
    </FeedShell>,
    {
      bare: true,
      path: "/feed",
      search,
      routes: {
        "/dashboard/api/feed": ({ url }) => ({
          body: new URL(url, "http://x").searchParams.get("band") === "skipped" ? SKIPPED : TOP,
        }),
        "/dashboard/api/feed/facets": { body: FACETS },
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
    // Scored 2, shown 3: the week's ranking makes the 3s.
    expect(within(rows[1]).getByText("watch it whole")).toBeInTheDocument();
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

  it("shows a verdict's matches as chips that say their direction", async () => {
    await mount();
    const [row] = await screen.findAllByRole("link", { name: /Let's build GPT/ });
    expect(
      within(row).getByLabelText("Strongly matches an interest: Evals for coding agents"),
    ).toHaveAttribute("data-direction", "up");
    expect(within(row).getByLabelText("Matches something you avoid: Launch hype")).toHaveAttribute(
      "data-direction",
      "down",
    );
  });

  it("narrows by search, entry and order through the URL, and reads the feed with them", async () => {
    const view = await mount();
    await userEvent.type(screen.getByRole("searchbox", { name: /Search titles/ }), "tokenizer");
    await vi.waitFor(() =>
      expect(view.replace).toHaveBeenCalledWith("/feed?q=tokenizer", { scroll: false }),
    );
    await vi.waitFor(() =>
      expect(view.calls("/dashboard/api/feed").some((r) => r.url.includes("q=tokenizer"))).toBe(
        true,
      ),
    );

    await userEvent.click(await screen.findByRole("button", { name: /Launch hype/ }));
    expect(view.replace).toHaveBeenLastCalledWith("/feed?q=tokenizer&entry=36", { scroll: false });
    expect(screen.getByRole("button", { name: /Launch hype/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Order" }), "oldest");
    await vi.waitFor(() =>
      expect(view.calls("/dashboard/api/feed").some((r) => r.url.includes("order=oldest"))).toBe(
        true,
      ),
    );
  });

  it("keeps a narrowed feed's filters from the URL and says when nothing matches", async () => {
    await mount(
      { "/dashboard/api/feed": { body: { ...TOP, items: [] } } },
      "channel=andrej+karpathy",
    );
    expect(await screen.findByText("Nothing here matches.")).toBeInTheDocument();
    // Any ASCII case narrows on the server; the select shows the facet's spelling.
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Channel" })).toHaveValue("Andrej Karpathy"),
    );
  });
});
