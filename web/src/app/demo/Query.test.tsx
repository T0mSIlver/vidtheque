// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Query } from "./Query";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    // A push that never settles is what a search in flight looks like from
    // here: the transition stays pending, which is the state under test.
    push: () => new Promise(() => {}),
    replace: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams("q=kv+cache"),
  usePathname: () => "/demo",
}));

function cards(container: HTMLElement) {
  return [...container.querySelectorAll("[class*='skelCard']")].map(
    (card) => card.querySelectorAll("[class*='skelMoment']").length,
  );
}

describe("the query bar and the space under it", () => {
  it("shows the results it was handed until a search is out", () => {
    const { container } = render(
      <Query state="ready" askEnabled={false}>
        <p>ten rows</p>
      </Query>,
    );
    expect(screen.getByText("ten rows")).toBeInTheDocument();
    expect(cards(container)).toEqual([]);
  });

  // Reserving the wrong shape shifts everything below it, so the first search
  // of a session guesses a ten-hit page over three talks — this corpus's usual
  // answer — rather than ten headers.
  it("guesses a shape before there has been one", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <Query state="ready" askEnabled={false} shape={[]}>
        <p>the cold page</p>
      </Query>,
    );

    await user.type(screen.getByLabelText("Search this video corpus"), "{Enter}");

    expect(cards(container)).toEqual([4, 3, 3]);
    expect(screen.getByRole("status")).toHaveTextContent("searching…");
    expect(screen.queryByText("the cold page")).not.toBeInTheDocument();
  });

  // …and after that, the best evidence available is what the last search
  // actually returned.
  it("reserves the shape the last search had", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <Query state="ready" askEnabled={false} shape={[2, 2]}>
        <p>two cards</p>
      </Query>,
    );

    await user.type(screen.getByLabelText("Search this video corpus"), "{Enter}");

    expect(cards(container)).toEqual([2, 2]);
  });
});
