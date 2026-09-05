// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockNavigation } from "@/test/next";

describe("SearchBox", () => {
  afterEach(() => vi.resetModules());

  it("navigates to the query URL on Enter, with the chosen evidence type", async () => {
    const { push } = mockNavigation();
    const { SearchBox } = await import("./SearchBox");
    const user = userEvent.setup();
    render(<SearchBox />);

    await user.click(screen.getByRole("radio", { name: "on-screen" }));
    await user.type(screen.getByLabelText("Search the corpus"), "kv cache{Enter}");

    expect(push).toHaveBeenCalledWith("/?q=kv+cache&type=ocr");
  });

  it("reads the query and type back from the URL", async () => {
    mockNavigation("q=paged+attention&type=frame");
    const { SearchBox } = await import("./SearchBox");
    render(<SearchBox />);

    expect(screen.getByLabelText("Search the corpus")).toHaveValue("paged attention");
    expect(screen.getByRole("radio", { name: "frames" })).toHaveAttribute("aria-checked", "true");
  });

  it("does not spend a request while typing", async () => {
    const { push } = mockNavigation();
    const { SearchBox } = await import("./SearchBox");
    const user = userEvent.setup();
    render(<SearchBox />);

    await user.type(screen.getByLabelText("Search the corpus"), "kv");
    expect(push).not.toHaveBeenCalled();
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

      expect(screen.getByLabelText("Search the corpus")).toHaveValue("");

      nav.navigate("q=paged+attention&type=frame");
      rerender(<SearchBox />);

      expect(screen.getByLabelText("Search the corpus")).toHaveValue("paged attention");
      expect(screen.getByRole("radio", { name: "frames" })).toHaveAttribute("aria-checked", "true");
    });

    it("follows the back button to the previous query", async () => {
      const nav = movingNavigation("q=kv+cache&type=ocr");
      const { SearchBox } = await import("./SearchBox");
      const { rerender } = render(<SearchBox />);

      nav.navigate("q=paged+attention");
      rerender(<SearchBox />);
      expect(screen.getByLabelText("Search the corpus")).toHaveValue("paged attention");
      expect(screen.getByRole("radio", { name: "all" })).toHaveAttribute("aria-checked", "true");

      nav.navigate("q=kv+cache&type=ocr");
      rerender(<SearchBox />);
      expect(screen.getByLabelText("Search the corpus")).toHaveValue("kv cache");
      expect(screen.getByRole("radio", { name: "on-screen" })).toHaveAttribute(
        "aria-checked",
        "true",
      );
    });

    it("keeps a half-typed query when the URL has not moved", async () => {
      movingNavigation("q=kv+cache");
      const { SearchBox } = await import("./SearchBox");
      const user = userEvent.setup();
      const { rerender } = render(<SearchBox />);

      await user.clear(screen.getByLabelText("Search the corpus"));
      await user.type(screen.getByLabelText("Search the corpus"), "paged att");
      rerender(<SearchBox />);

      expect(screen.getByLabelText("Search the corpus")).toHaveValue("paged att");
    });
  });
});
