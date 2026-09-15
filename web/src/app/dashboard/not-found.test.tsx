// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OWNER_SESSION } from "@/test/dashboard-fixtures";
import { mockNavigation } from "@/test/next";

// A mistyped path under `/dashboard`. Python answered it with the MCP mount's
// bare `text/plain` "Not Found" and Next answers it with its own stock page —
// `<h1>404</h1>` over "This page could not be found", which is a page from a
// different product. The assertion is that it is this surface's refusal: the
// header band, the code as a state in its tone, and somewhere to click.

async function mount(path = "/dashboard/no-such-page") {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(OWNER_SESSION), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    ),
  );
  mockNavigation("", path);
  const { Chrome } = await import("./Chrome");
  const { default: NotFound } = await import("./not-found");
  render(
    <Chrome>
      <NotFound />
    </Chrome>,
  );
}

describe("a path that is not a page here", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("refuses in this surface's own shape, not Next's", async () => {
    await mount();

    expect(
      await screen.findByRole("heading", { name: "That is not a page on this dashboard." }),
    ).toBeInTheDocument();
    expect(screen.getByText("E_NOT_FOUND")).toBeInTheDocument();
    // Next's stock page, which this replaces.
    expect(screen.queryByRole("heading", { name: "404" })).toBeNull();
    expect(screen.queryByText("This page could not be found.")).toBeNull();
  });

  it("names the document, and hands the name back on the way out", async () => {
    await mount();
    await screen.findByRole("heading", { name: "That is not a page on this dashboard." });

    expect(document.title).toBe("No such page — vidtheque");
  });

  // The recovery panel a refusal owes the operator: the section the request
  // belonged to, and the overview. A path no page of this surface claims is not
  // in a section at all, so it gets the corpus's own list — `error.html`'s
  // "everywhere else" branch.
  it("offers a way back, and the line that says where the pages are", async () => {
    await mount("/dashboard/nope");
    await screen.findByRole("heading", { name: "That is not a page on this dashboard." });

    expect(screen.getByRole("link", { name: "Everything that is indexed" })).toHaveAttribute(
      "href",
      "/dashboard/videos",
    );
    expect(screen.getByRole("link", { name: "Corpus overview" })).toBeInTheDocument();
    expect(screen.getByText("The rail lists every page this surface has.")).toBeInTheDocument();
  });
});
