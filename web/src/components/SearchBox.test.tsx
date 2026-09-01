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
});
