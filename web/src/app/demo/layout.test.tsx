// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DemoLayout from "./layout";

// The footer is a public commitment, not decoration.
// `research/positioning-2026-08-10.md` §9.1 counts "an unfollow/remove path
// exists and is documented" as the obligation the attribution line creates, and
// demo-site.md §6 item 7 puts the line, and the path it names, on this page.
// `mcp/tests/test_public.py` asserted it until the pages left Python.
describe("the reader's chrome at /demo", () => {
  it("says whose the videos are, and where to ask for one to go", () => {
    render(<DemoLayout params={Promise.resolve({})}>{null}</DemoLayout>);

    expect(
      screen.getByText(/^The videos belong to the people who made them\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Removal on request" })).toHaveAttribute(
      "href",
      "https://github.com/T0mSIlver/vidtheque/blob/main/docs/takedown.md",
    );
    expect(screen.getByRole("link", { name: "Source on GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/T0mSIlver/vidtheque",
    );
  });

  it("wraps whatever state the page is in, so no state loses the line", () => {
    render(
      <DemoLayout params={Promise.resolve({})}>
        <p>The corpus could not be reached.</p>
      </DemoLayout>,
    );

    expect(screen.getByText("The corpus could not be reached.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Removal on request" })).toBeInTheDocument();
  });
});
