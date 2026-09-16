// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Receipt } from "./Receipt";

// The slab is the one thing on this surface that is *printed* rather than
// described, so what is tested is the printing: three parts, in order, with
// the second in its own block and the arrow that says the link leaves.

function slab(href: string, size?: "sm" | "lg") {
  const { container } = render(<Receipt href={href} size={size} />);
  return container.querySelector("a");
}

describe("the receipt", () => {
  it("prints the host, the talk and the second as three parts", () => {
    const link = slab("https://youtu.be/kCc8FmEb1nY?t=705");
    expect([...(link?.children ?? [])].map((part) => part.textContent)).toEqual([
      "youtu.be/",
      "kCc8FmEb1nY",
      "?t=705 ↗",
    ]);
    expect(link).toHaveTextContent("youtu.be/kCc8FmEb1nY?t=705");
  });

  it("leaves the page, in a tab of its own", () => {
    const link = slab("https://youtu.be/kCc8FmEb1nY?t=705");
    expect(link).toHaveAttribute("href", "https://youtu.be/kCc8FmEb1nY?t=705");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  // A link with no `?t=` is still a receipt for *a talk*; it just has no second
  // to name, and the block prints the arrow alone rather than a made-up one.
  it("names no second where the link names none", () => {
    expect(slab("https://youtu.be/kCc8FmEb1nY")).toHaveTextContent("youtu.be/kCc8FmEb1nY ↗");
  });

  // Two sizes and the difference is the fill: ten filled gold blocks down a
  // page of moments would spend the accent on the list instead of on the hit.
  it("fills the block only at the size that is the payoff", () => {
    const small = slab("https://youtu.be/a?t=1", "sm");
    const large = slab("https://youtu.be/b?t=2", "lg");
    expect(large?.className).not.toEqual(small?.className);
    expect(large?.className.split(" ").length).toBeGreaterThan(
      small?.className.split(" ").length ?? 0,
    );
  });

  // A URL the page cannot parse — or one whose scheme the DOM would run — has
  // no honest receipt, and a guessed one proves nothing.
  it("prints nothing for a link it cannot vouch for", () => {
    render(
      <>
        <Receipt href="not a url" />
        <Receipt href="javascript:alert(1)" />
      </>,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
