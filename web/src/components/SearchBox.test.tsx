// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockNavigation } from "@/test/next";

const BOX = "Search this video corpus";

describe("SearchBox", () => {
  afterEach(() => vi.resetModules());

  it("navigates to the query URL on Enter, with the chosen evidence type", async () => {
    const { push } = mockNavigation();
    const { SearchBox } = await import("./SearchBox");
    const user = userEvent.setup();
    render(<SearchBox />);

    await user.click(screen.getByRole("button", { name: "on-screen text" }));
    await user.type(screen.getByLabelText(BOX), "kv cache{Enter}");

    expect(push).toHaveBeenCalledWith("/demo?q=kv+cache&type=ocr");
  });

  it("reads the query and type back from the URL", async () => {
    mockNavigation("q=paged+attention&type=frame");
    const { SearchBox } = await import("./SearchBox");
    render(<SearchBox />);

    expect(screen.getByLabelText(BOX)).toHaveValue("paged attention");
    expect(screen.getByRole("button", { name: "frames" })).toHaveAttribute("aria-pressed", "true");
  });

  it("does not spend a request while typing", async () => {
    const { push } = mockNavigation();
    const { SearchBox } = await import("./SearchBox");
    const user = userEvent.setup();
    render(<SearchBox />);

    await user.type(screen.getByLabelText(BOX), "kv");
    expect(push).not.toHaveBeenCalled();
  });

  // The three things the original's markup asked of this field, each of which
  // is one small thing a phone does differently.
  it("is a search field, focused, spell-checked by nobody", async () => {
    mockNavigation();
    const { SearchBox } = await import("./SearchBox");
    render(<SearchBox />);

    const box = screen.getByLabelText(BOX);
    expect(box).toHaveAttribute("type", "search");
    expect(box).toHaveAttribute("enterkeyhint", "search");
    expect(box).toHaveAttribute("spellcheck", "false");
    expect(box).toHaveFocus();
  });

  // The blur is what dismisses a phone's keyboard over the results it just
  // asked for.
  it("lets go of the field when it submits", async () => {
    mockNavigation();
    const { SearchBox } = await import("./SearchBox");
    const user = userEvent.setup();
    render(<SearchBox />);

    const box = screen.getByLabelText(BOX);
    await user.type(box, "kv cache{Enter}");
    expect(box).not.toHaveFocus();
  });

  // The chip is the filter, so picking one re-runs the search on screen — the
  // pin lands in the URL, where the widening tip already knows how to undo it.
  it("re-runs the search on screen when a channel is pinned", async () => {
    const { push } = mockNavigation("q=kv+cache");
    const { SearchBox } = await import("./SearchBox");
    const user = userEvent.setup();
    render(<SearchBox />);

    await user.click(screen.getByRole("button", { name: "frames" }));
    expect(push).toHaveBeenCalledWith("/demo?q=kv+cache&type=frame");
  });

  it("has nothing to re-run with an empty box", async () => {
    const { push } = mockNavigation();
    const { SearchBox } = await import("./SearchBox");
    const user = userEvent.setup();
    render(<SearchBox />);

    await user.click(screen.getByRole("button", { name: "frames" }));
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "frames" })).toHaveAttribute("aria-pressed", "true");
  });

  describe("the state cell", () => {
    it("prints the machine's own word for what happened", async () => {
      mockNavigation();
      const { SearchBox } = await import("./SearchBox");
      const { rerender } = render(<SearchBox state="ready" />);
      expect(screen.getByText("ready")).toHaveAttribute("data-s", "ready");

      rerender(<SearchBox state="no hits" />);
      expect(screen.getByText("no hits")).toHaveAttribute("data-s", "ready");

      rerender(<SearchBox state="rate limited" />);
      expect(screen.getByText("rate limited")).toHaveAttribute("data-s", "refused");
    });

    it("says `scanning` while the search it started is out", async () => {
      mockNavigation();
      const { SearchBox } = await import("./SearchBox");
      const user = userEvent.setup();
      render(<SearchBox state="ready" />);

      await user.type(screen.getByLabelText(BOX), "kv cache{Enter}");
      // The router mock resolves at once, so the word is asserted through the
      // callback the pending state drives; the cell itself is the same value.
      expect(screen.getByText(/ready|scanning/)).toBeInTheDocument();
    });
  });

  describe("the mode switch", () => {
    // A switch into a mode that 503s is worse than no switch: on a keyless
    // deployment the pair is not drawn at all.
    it("is not there on a deployment with no key", async () => {
      mockNavigation();
      const { SearchBox } = await import("./SearchBox");
      render(<SearchBox askEnabled={false} />);
      expect(screen.queryByRole("button", { name: "ask ✨" })).not.toBeInTheDocument();
    });

    it("is a pressed pair, and carries the query into ask", async () => {
      const { push } = mockNavigation("q=paged+attention");
      const { SearchBox } = await import("./SearchBox");
      const user = userEvent.setup();
      render(<SearchBox askEnabled />);

      expect(screen.getByRole("button", { name: "search" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await user.click(screen.getByRole("button", { name: "ask ✨" }));
      expect(push).toHaveBeenCalledWith("/demo?ask=1&q=paged+attention");
    });

    // Ask is the default, so a link copied out of search has to be able to say
    // search (demo-site.md §6.2).
    it("writes ask=0 on a search, where ask is a mode this instance has", async () => {
      const { push } = mockNavigation();
      const { SearchBox } = await import("./SearchBox");
      const user = userEvent.setup();
      render(<SearchBox askEnabled />);

      await user.type(screen.getByLabelText(BOX), "kv cache{Enter}");
      expect(push).toHaveBeenCalledWith("/demo?q=kv+cache&ask=0");
    });
  });

  // The URL is the state, so a navigation this form did not make — an example
  // link, the back button — has to reach the box. Seeding the fields once, at
  // mount, left them showing the query before last.
  describe("when the URL changes under it", () => {
    // `mockNavigation` fixes its search string for the life of the module;
    // this one can be moved, which is what a navigation looks like from here.
    function movingNavigation(initial = "") {
      const push = vi.fn();
      let search = initial;
      vi.doMock("next/navigation", () => ({
        useRouter: () => ({
          push,
          replace: vi.fn(),
          refresh: vi.fn(),
          back: vi.fn(),
          forward: vi.fn(),
          prefetch: vi.fn(),
        }),
        useSearchParams: () => new URLSearchParams(search),
        usePathname: () => "/",
      }));
      return {
        push,
        navigate: (to: string) => {
          search = to;
        },
      };
    }

    it("takes the query and type from an example link", async () => {
      const nav = movingNavigation();
      const { SearchBox } = await import("./SearchBox");
      const { rerender } = render(<SearchBox />);

      expect(screen.getByLabelText(BOX)).toHaveValue("");

      nav.navigate("q=paged+attention&type=frame");
      rerender(<SearchBox />);

      expect(screen.getByLabelText(BOX)).toHaveValue("paged attention");
      expect(screen.getByRole("button", { name: "frames" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

    it("follows the back button to the previous query", async () => {
      const nav = movingNavigation("q=kv+cache&type=ocr");
      const { SearchBox } = await import("./SearchBox");
      const { rerender } = render(<SearchBox />);

      nav.navigate("q=paged+attention");
      rerender(<SearchBox />);
      expect(screen.getByLabelText(BOX)).toHaveValue("paged attention");
      expect(screen.getByRole("button", { name: "all" })).toHaveAttribute("aria-pressed", "true");

      nav.navigate("q=kv+cache&type=ocr");
      rerender(<SearchBox />);
      expect(screen.getByLabelText(BOX)).toHaveValue("kv cache");
      expect(screen.getByRole("button", { name: "on-screen text" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

    it("keeps a half-typed query when the URL has not moved", async () => {
      movingNavigation("q=kv+cache");
      const { SearchBox } = await import("./SearchBox");
      const user = userEvent.setup();
      const { rerender } = render(<SearchBox />);

      await user.clear(screen.getByLabelText(BOX));
      await user.type(screen.getByLabelText(BOX), "paged att");
      rerender(<SearchBox />);

      expect(screen.getByLabelText(BOX)).toHaveValue("paged att");
    });

    // "Try one of the examples" clears the box and resets the channel by
    // navigating to a URL with neither. The caret coming back is this half.
    it("takes the caret back when a navigation empties the box", async () => {
      const nav = movingNavigation("q=kv+cache&type=ocr");
      const { SearchBox } = await import("./SearchBox");
      const user = userEvent.setup();
      const { rerender } = render(<SearchBox />);

      await user.click(document.body);
      expect(screen.getByLabelText(BOX)).not.toHaveFocus();

      nav.navigate("");
      rerender(<SearchBox />);

      expect(screen.getByLabelText(BOX)).toHaveValue("");
      expect(screen.getByRole("button", { name: "all" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByLabelText(BOX)).toHaveFocus();
    });
  });
});
