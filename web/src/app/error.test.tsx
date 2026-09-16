// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RootError from "./error";

describe("the root error boundary", () => {
  it("carries the wordmark, one sentence and the way to the demo", () => {
    render(<RootError error={new Error("boom")} />);

    expect(screen.getByText(/^vidtheque/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "The projection room went dark. Reload, or go to the demo.",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "go to the demo" })).toHaveAttribute("href", "/demo");
  });

  it("prints the digest only when the server sent one", () => {
    const { rerender } = render(<RootError error={new Error("boom")} />);
    expect(screen.queryByText(/^ref/)).not.toBeInTheDocument();

    const withDigest = Object.assign(new Error("boom"), { digest: "1234567890" });
    rerender(<RootError error={withDigest} />);
    expect(screen.getByText("ref 1234567890")).toBeInTheDocument();
  });
});
