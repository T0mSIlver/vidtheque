// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Fold } from "./Fold";

describe("a fold", () => {
  it("toggles its content and says which way it is", async () => {
    render(<Fold label="the filters">inside</Fold>);
    const toggle = screen.getByRole("button", { name: "Show the filters" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle.parentElement).not.toHaveAttribute("data-open");

    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide the filters" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(toggle.parentElement).toHaveAttribute("data-open");
  });

  // A selected frame is a deep link into the strip, so the strip opens; the
  // toggle still closes it, and losing the selection does not.
  it("opens when told to, and stays the reader's to close", async () => {
    const { rerender } = render(
      <Fold phone label="24 frames">
        inside
      </Fold>,
    );
    rerender(
      <Fold phone open label="24 frames">
        inside
      </Fold>,
    );
    const toggle = screen.getByRole("button", { name: "Hide 24 frames" });
    rerender(
      <Fold phone label="24 frames">
        inside
      </Fold>,
    );
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
});
