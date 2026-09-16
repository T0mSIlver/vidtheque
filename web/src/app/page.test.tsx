// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { renderToString } from "react-dom/server";
import { beforeAll, describe, expect, it, vi } from "vitest";
import LandingPage from "./page";

// Older jsdom has no matchMedia; the hero and the booth log ask the motion query.
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

  // `prefetch` never reaches the DOM, so a stand-in `Link` records it.
  it("does not fetch the demo before anyone asks for it", async () => {
    vi.resetModules();
    vi.doMock("next/link", () => ({
      default: ({
        prefetch,
        children,
        ...rest
      }: {
        prefetch?: boolean;
        children: ReactNode;
      } & Record<string, unknown>) => (
        <a data-prefetch={String(prefetch)} {...rest}>
          {children}
        </a>
      ),
    }));
    try {
      const { default: Page } = await import("./page");
      render(<Page />);
      expect(screen.getByRole("link", { name: /Open the demo/ })).toHaveAttribute(
        "data-prefetch",
        "false",
      );
    } finally {
      vi.doUnmock("next/link");
      vi.resetModules();
    }
  });

  it("server-renders the hero wall, its chips and every light-table answer", () => {
    const html = renderToString(<LandingPage />);
    const doc = new DOMParser().parseFromString(html, "text/html");
    const wall = doc.querySelector('[data-hero="wall"]')!;
    expect(wall.querySelectorAll("[data-vid] > img").length).toBeGreaterThanOrEqual(200);
    expect(doc.querySelectorAll('[data-hero="chips"] button')).toHaveLength(3);
    const panels = doc.querySelectorAll("[data-panel]");
    expect(panels).toHaveLength(3);
    expect(panels[0].querySelector('a[href="https://youtu.be/Sir59K8ZDPU?t=1136"]')).not.toBeNull();
    // Final values in the HTML: the ledger and the booth question never start empty.
    expect(doc.querySelector("dl dd")?.textContent).toBe("310");
    expect(html).toContain("What do speakers disagree about when it comes to LLM as a judge?");
  });

  it("is the landing, not the reader: no search box lives here", () => {
    render(<LandingPage />);
    expect(screen.queryByLabelText("Search the corpus")).toBeNull();
    expect(screen.queryByRole("link", { name: "search" })).toBeNull();
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

  // A public commitment (research/positioning-2026-08-10.md §9.1).
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
