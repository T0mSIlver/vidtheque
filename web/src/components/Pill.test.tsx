// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Pill } from "./Pill";

describe("Pill", () => {
  it("prints the state's own word", () => {
    render(<Pill state="degraded" />);
    expect(screen.getByText("degraded")).toBeInTheDocument();
  });

  it("carries a tone class for a known state and the neutral one otherwise", () => {
    const { rerender } = render(<Pill state="failed" />);
    expect(screen.getByText("failed").className).toMatch(/bad/);
    rerender(<Pill state="something-new" />);
    expect(screen.getByText("something-new").className).toMatch(/neutral/);
  });
});
