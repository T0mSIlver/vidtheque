// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mountDashboard } from "@/test/dashboard/harness";
import NotFound from "./not-found";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// A mistyped path gets this surface's refusal — head band, code, somewhere to
// click — never Next's stock `<h1>404</h1>`.

function mount(path = "/dashboard/no-such-page") {
  return mountDashboard(<NotFound />, { path });
}

describe("a path that is not a page here", () => {
  it("refuses in this surface's own shape, not Next's", async () => {
    await mount();

    expect(
      await screen.findByRole("heading", { name: "That is not a page on this dashboard." }),
    ).toBeInTheDocument();
    expect(screen.getByText("E_NOT_FOUND")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "404" })).toBeNull();
    expect(screen.queryByText("This page could not be found.")).toBeNull();
  });

  it("names the document while it is on screen, and not after", async () => {
    const { unmount } = await mount();
    await screen.findByRole("heading", { name: "That is not a page on this dashboard." });

    expect(document.title).toBe("No such page — vidtheque");
    unmount();
    expect(document.title).not.toBe("No such page — vidtheque");
  });

  // A path in no section gets the corpus's list (`sectionOf`).
  it("offers a way back, and the line that says where the pages are", async () => {
    await mount("/dashboard/nope");
    await screen.findByRole("heading", { name: "That is not a page on this dashboard." });

    expect(screen.getByRole("link", { name: "Everything that is indexed" })).toHaveAttribute(
      "href",
      "/dashboard/videos",
    );
    expect(screen.getByRole("link", { name: "Corpus overview" })).toBeInTheDocument();
    expect(screen.getByText("The rail lists every page this surface has.")).toBeInTheDocument();
  });
});
