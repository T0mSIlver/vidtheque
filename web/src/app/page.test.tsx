// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";
import LandingPage from "./page";

// jsdom below the version that ships matchMedia still has to answer the motion
// query the hero and the booth log ask before they decide to move.
beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        addEventListener() {},
        removeEventListener() {},
        addListener() {},
        removeListener() {},
        onchange: null,
        dispatchEvent: () => false,
      }),
    });
  }
});

describe("the landing at /", () => {
  it("leads with the locked H1 and exits into the demo", () => {
    render(<LandingPage />);

    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("Builders talk.");
    expect(h1).toHaveTextContent("Your agent listens.");

    expect(screen.getByRole("link", { name: /Open the demo/ })).toHaveAttribute("href", "/demo");
  });

  it("is the landing, not the reader: no search box lives here", () => {
    render(<LandingPage />);
    expect(screen.queryByLabelText("Search the corpus")).toBeNull();
    expect(screen.queryByRole("link", { name: "library" })).toBeNull();
  });

  it("prints the corpus readout rather than fetching one", () => {
    render(<LandingPage />);
    // The rail's readout, the ledger and its note all come from the same file.
    expect(screen.getByText(/talks watched/)).toHaveTextContent("310 talks watched");
    expect(screen.getByText(/sentences spoken/)).toHaveTextContent(
      "69,080 sentences spoken · 1,026,030 words · 12,855 frames read",
    );
    expect(screen.getByText("moments kept")).toBeInTheDocument();
  });

  it("every still ends in a receipt that lands on the second", () => {
    const { container } = render(<LandingPage />);
    // Beat 2's four stills, from the readout's own `video_id` and `t`.
    for (const href of [
      "https://youtu.be/9HbzAWnKbo4?t=566",
      "https://youtu.be/RjfbvDXpFls?t=327",
      "https://youtu.be/AMiyLItEtLA?t=685",
      "https://youtu.be/FWMJQDH3iK0?t=1492",
    ]) {
      const receipt = container.querySelector(`a[href="${href}"]`);
      expect(receipt).not.toBeNull();
      expect(receipt).toHaveTextContent("youtu.be/");
    }
  });

  it("hangs the whole wall band, each row rendered twice for a seamless loop", () => {
    const { container } = render(<LandingPage />);
    // 70 keyframes, each row's list duplicated: 140 tiles.
    expect(container.querySelectorAll('img[src^="/landing/wall/"]')).toHaveLength(140);
    const tile = container.querySelectorAll('a[href="https://youtu.be/CoEIs6Xm8m8?t=45"]');
    expect(tile).toHaveLength(2);
    expect(tile[0]).toHaveAttribute("rel", "noopener");
  });

  it("keeps the quickstart's two commands copyable", () => {
    render(<LandingPage />);
    expect(screen.getByText(/docker compose up -d/)).toBeInTheDocument();
    expect(
      screen.getByText(/claude mcp add --transport http vidtheque https:\/\/vidtheque\.dev\/mcp/),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "copy" })).toHaveLength(2);
  });

  // The footer is where a public commitment is made, not decoration.
  // `research/positioning-2026-08-10.md` §9.1 counts "an unfollow/remove path
  // exists and is documented" as the obligation the attribution line creates,
  // and `mcp/tests/test_public.py` asserted it until the pages left Python.
  // The named path — `docs/takedown.md`, linked as "Removal on request" — was
  // the Python demo's footer, and `/demo` has no footer here; what the landing
  // promises in its own words is what this asserts.
  it("keeps the footer's promise: the videos are theirs, and no needs no appeal", () => {
    render(<LandingPage />);

    expect(screen.getByText(/^The videos belong to the people who made them\./)).toHaveTextContent(
      "sends you back to the source",
    );
    expect(
      screen.getByText(/A creator who would rather not be followed is a complete reason/),
    ).toHaveTextContent("there is no appeal to make");
  });
});
