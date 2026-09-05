// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RootError from "./error";

// The boundary exists so that a throw under `/` lands on a vidtheque page with
// a way out of it, rather than on the framework's own screen. What that costs
// is three assertions: the mark, the sentence, and the door.
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

  // In production the only thing the server sends about a render throw is the
  // digest, so the page prints it when there is one and says nothing when
  // there is not — never "ref undefined".
  it("prints the digest only when the server sent one", () => {
    const { rerender } = render(<RootError error={new Error("boom")} />);
    expect(screen.queryByText(/^ref/)).not.toBeInTheDocument();

    const withDigest = Object.assign(new Error("boom"), { digest: "1234567890" });
    rerender(<RootError error={withDigest} />);
    expect(screen.getByText("ref 1234567890")).toBeInTheDocument();
  });
});
