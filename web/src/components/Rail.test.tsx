// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Rail } from "./Rail";

describe("Rail", () => {
  it("sends the wordmark to the landing and search to the demo", () => {
    render(<Rail />);
    expect(screen.getByRole("link", { name: /vidtheque/ })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "search" })).toHaveAttribute("href", "/demo");
    expect(screen.getByRole("link", { name: "library" })).toHaveAttribute("href", "/videos");
  });
});
