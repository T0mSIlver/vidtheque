// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Rail } from "./Rail";

describe("Rail", () => {
  it("sends the wordmark to the landing", () => {
    render(<Rail />);
    expect(screen.getByRole("link", { name: /vidtheque/ })).toHaveAttribute("href", "/");
  });

  // One link and not a nav (demo-site.md §6.1): this page is a search surface
  // and the search box is its one primary action, so the rail carries no nav.
  it("offers no nav", () => {
    render(<Rail />);
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("prints the corpus size beside the wordmark when meta named one", () => {
    render(<Rail count="473 talks watched" />);
    expect(screen.getByText("473 talks watched")).toBeInTheDocument();
  });

  // The link is hidden until `/api/meta` says the route group is there, so a
  // deployment running the dashboard off leaves no invitation to a dead page.
  it("offers the browsable corpus only where the server said there is one", () => {
    const { rerender } = render(<Rail />);
    expect(screen.queryByRole("link", { name: "Browse the corpus" })).not.toBeInTheDocument();

    rerender(<Rail browse="/dashboard" />);
    expect(screen.getByRole("link", { name: "Browse the corpus" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  // The accessible name says the whole thing whatever the rail has room to
  // print: the narrow label is a visual truncation, not a different link.
  it("keeps its accessible name at every width", () => {
    render(<Rail browse="/ops" />);
    const link = screen.getByRole("link", { name: "Browse the corpus" });
    expect(link.textContent).toContain("browse");
  });
});
