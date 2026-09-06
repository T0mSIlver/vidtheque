// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Meta } from "@/lib/api/schemas";

const META: Meta = {
  name: "vidtheque",
  version: "0.0.6",
  browse: "/dashboard",
  mcp_url: "https://vidtheque.example.com/mcp",
  auth: "none",
  ask_enabled: true,
  ask_model: "deepseek/deepseek-v4-flash-0731",
  videos: 473,
  clamps: { policy: "public", search_max_limit: 20, videos_max_limit: 50 },
  limits: { search_per_min: 30, ask_per_min: 5, ask_per_day: 50 },
  repo: "https://github.com/T0mSIlver/vidtheque",
};

// `lib/search` is `server-only` all the way down, and the whole of what this
// layout wants from it is the boot answer. Mocked before the import, so the
// package index — and the `server-only` marker in it — is never loaded.
async function mountWith(outcome: unknown, children: React.ReactNode = null) {
  vi.doMock("@/lib/search", () => ({ readMeta: async () => outcome }));
  const { default: DemoLayout } = await import("./layout");
  render(await DemoLayout({ params: Promise.resolve({}), children }));
}

describe("the reader's chrome at /demo", () => {
  afterEach(() => vi.resetModules());

  // The footer is a public commitment, not decoration.
  // `research/positioning-2026-08-10.md` §9.1 counts "an unfollow/remove path
  // exists and is documented" as the obligation the attribution line creates,
  // and demo-site.md §6 item 7 puts the line, and the path it names, on this
  // page. `mcp/tests/test_public.py` asserted it until the pages left Python.
  it("says whose the videos are, and where to ask for one to go", async () => {
    await mountWith({ kind: "ok", meta: META });

    expect(
      screen.getByText(/^The videos belong to the people who made them\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Removal on request" })).toHaveAttribute(
      "href",
      "https://github.com/T0mSIlver/vidtheque/blob/main/docs/takedown.md",
    );
    expect(screen.getByRole("link", { name: "Source on GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/T0mSIlver/vidtheque",
    );
    // The rest of that line, and the two base lines under it.
    expect(screen.getByText(/· MIT · self-hosted\./)).toBeInTheDocument();
    expect(screen.getByText("the knowledge of the builders, on tap")).toBeInTheDocument();
    expect(
      screen.getByText("early development · schemas can still change"),
    ).toBeInTheDocument();
  });

  it("wraps whatever state the page is in, so no state loses the line", async () => {
    await mountWith({ kind: "ok", meta: META }, <p>The corpus could not be reached.</p>);

    expect(screen.getByText("The corpus could not be reached.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Removal on request" })).toBeInTheDocument();
  });

  it("prints the corpus size and the way into it from /api/meta", async () => {
    await mountWith({ kind: "ok", meta: META });

    expect(screen.getByText("473 talks watched")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse the corpus" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  it("leaves no invitation to a dead page when the routes are not there", async () => {
    await mountWith({ kind: "ok", meta: { ...META, browse: null } });
    expect(screen.queryByRole("link", { name: "Browse the corpus" })).not.toBeInTheDocument();
  });

  it("hands over the endpoint the server stated, and the line to paste", async () => {
    await mountWith({ kind: "ok", meta: META });

    expect(screen.getByText("https://vidtheque.example.com/mcp")).toBeInTheDocument();
    expect(
      screen.getByText(
        "claude mcp add --transport http vidtheque https://vidtheque.example.com/mcp",
      ),
    ).toBeInTheDocument();
  });

  // The 2026-08-28 defect, on this surface: /api/meta shares the search bucket,
  // so a visitor who spent it and reloaded got "undefined" in the MCP line with
  // nothing on screen saying why.
  it("says a spent bucket is a spent bucket, and disables a copy with nothing to copy", async () => {
    await mountWith({ kind: "rate_limited" });

    expect(
      screen.getAllByText("unavailable while rate limited — reload in a minute"),
    ).toHaveLength(2);
    expect(document.body.textContent).not.toMatch(/undefined/);
    for (const button of screen.getAllByRole("button", { name: "copy" })) {
      expect(button).toBeDisabled();
    }
  });

  it("distinguishes an unreachable server from a spent bucket", async () => {
    await mountWith({ kind: "unreachable" });
    expect(screen.getAllByText("unavailable — reload the page")).toHaveLength(2);
  });

  describe("the copy buttons", () => {
    // `userEvent.setup()` installs a clipboard of its own, so the one under
    // test goes on after it and comes off in `afterEach`.
    function clipboard(writeText: () => Promise<void>) {
      const user = userEvent.setup();
      vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
      return user;
    }

    afterEach(() => vi.unstubAllGlobals());

    it("confirms in the button and in a live region", async () => {
      await mountWith({ kind: "ok", meta: META });
      const writeText = vi.fn(async () => {});
      const user = clipboard(writeText);

      await user.click(screen.getAllByRole("button", { name: "copy" })[0]);

      expect(writeText).toHaveBeenCalledWith("https://vidtheque.example.com/mcp");
      expect(await screen.findByText("copied")).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("Endpoint copied to the clipboard.");
    });

    // A clipboard that refuses — no permission, no secure context — selects the
    // text instead, so there is always a way to take it.
    it("selects the text when the clipboard refuses", async () => {
      await mountWith({ kind: "ok", meta: META });
      const user = clipboard(async () => {
        throw new Error("denied");
      });

      await user.click(screen.getAllByRole("button", { name: "copy" })[1]);

      expect(await screen.findByText("select it")).toBeInTheDocument();
      expect(getSelection()?.rangeCount).toBe(1);
      expect(getSelection()?.getRangeAt(0).toString()).toContain("claude mcp add");
      // Nothing was copied, so nothing is announced as copied.
      expect(screen.getByRole("status")).toBeEmptyDOMElement();
    });
  });
});
