// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Rail, RailMeta } from "./Rail";

describe("Rail", () => {
  it("sends the wordmark to the landing", () => {
    render(<Rail />);
    expect(screen.getByRole("link", { name: /vidtheque/ })).toHaveAttribute("href", "/");
  });

  it("offers no nav", () => {
    render(<Rail />);
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("prints the corpus size beside the wordmark when meta named one", () => {
    render(
      <Rail>
        <RailMeta count="473 talks watched" />
      </Rail>,
    );
    expect(screen.getByText("473 talks watched")).toBeInTheDocument();
  });

  // Hidden until `/api/meta` says the route group is there.
  it("offers the browsable corpus only where the server said there is one", () => {
    const { rerender } = render(<RailMeta />);
    expect(screen.queryByRole("link", { name: "Browse the corpus" })).not.toBeInTheDocument();

    rerender(<RailMeta browse="/dashboard" />);
    expect(screen.getByRole("link", { name: "Browse the corpus" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  it("keeps its accessible name at every width", () => {
    render(<RailMeta browse="/ops" />);
    const link = screen.getByRole("link", { name: "Browse the corpus" });
    expect(link.textContent).toContain("browse");
  });
});
