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

const meta = vi.hoisted(() => ({ outcome: {} as unknown }));
vi.mock("@/lib/api/search", () => ({ readMeta: async () => meta.outcome }));

async function facts(outcome: unknown) {
  meta.outcome = outcome;
  return import("./facts");
}

describe("the public chrome", () => {
  afterEach(() => vi.unstubAllGlobals());

  // The footer is a public commitment: whose the videos are, and how to ask
  // for one to go (demo-site.md §6 item 7).
  it("draws the rail, the panel and the footer without waiting on a read", async () => {
    vi.doMock("./facts", async () => ({
      ...(await vi.importActual<typeof import("./facts")>("./facts")),
      RailFacts: () => null,
      ConnectRows: () => null,
      RepoLink: () => null,
    }));
    const { PublicShell } = await import("./PublicShell");
    render(
      <PublicShell>
        <p>The corpus could not be reached.</p>
      </PublicShell>,
    );
    vi.doUnmock("./facts");

    expect(screen.getByRole("link", { name: /vidtheque/ })).toHaveAttribute("href", "/");
    expect(screen.getByText("The corpus could not be reached.")).toBeInTheDocument();
    expect(
      screen.getByText(/^The videos belong to the people who made them\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Removal on request" })).toHaveAttribute(
      "href",
      "https://github.com/T0mSIlver/vidtheque/blob/main/docs/takedown.md",
    );
    expect(screen.getByText("Add this corpus to your own agent")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Claude, ChatGPT and Vibe: add a custom connector and paste the endpoint. No sign-in.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the demo content before the default connect section", async () => {
    const { PublicShell } = await import("./PublicShell");
    render(
      <PublicShell>
        <section aria-label="demo console" />
      </PublicShell>,
    );
    const console = screen.getByLabelText("demo console");
    const connect = screen.getByRole("heading", {
      level: 2,
      name: "Add this corpus to your own agent",
    });
    expect(console.compareDocumentPosition(connect)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("prints the corpus size and the way into it, unless the surface asked for none", async () => {
    const { RailFacts } = await facts({ kind: "ok", meta: META });
    const { rerender } = render(await RailFacts({ showCount: true }));
    expect(screen.getByText("473 talks watched")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse the corpus" })).toHaveAttribute(
      "href",
      "/dashboard",
    );

    rerender(await RailFacts({ showCount: false }));
    expect(screen.queryByText("473 talks watched")).not.toBeInTheDocument();
  });

  it("links the instance's repo, and only over http(s)", async () => {
    const { RepoLink } = await facts({
      kind: "ok",
      meta: { ...META, repo: "javascript:alert(1)" },
    });
    render(await RepoLink());
    expect(screen.getByRole("link", { name: "Source on GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/T0mSIlver/vidtheque",
    );
  });

  it("prints the endpoint and all three client commands", async () => {
    const { ConnectRows } = await facts({ kind: "ok", meta: META });
    render(await ConnectRows());
    expect(screen.getAllByRole("button", { name: /^copy / })).toHaveLength(4);
    expect(screen.getByText("mcp endpoint")).toBeInTheDocument();
    expect(screen.getByText("claude code")).toBeInTheDocument();
    expect(screen.getByText("codex")).toBeInTheDocument();
    expect(screen.getByText("mistral vibe")).toBeInTheDocument();
    expect(screen.getByText("https://vidtheque.example.com/mcp")).toBeInTheDocument();
    expect(
      screen.getByText(
        "claude mcp add --transport http vidtheque https://vidtheque.example.com/mcp",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("codex mcp add vidtheque --url https://vidtheque.example.com/mcp"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("vibe mcp add vidtheque --url https://vidtheque.example.com/mcp"),
    ).toBeInTheDocument();
  });

  // /api/meta shares the search bucket: a spent one is not "undefined".
  it("says a spent bucket is a spent bucket, and disables a copy with nothing to copy", async () => {
    const { ConnectRows } = await facts({ kind: "rate_limited" });
    render(await ConnectRows());
    expect(screen.getAllByText("unavailable while rate limited — reload in a minute")).toHaveLength(
      4,
    );
    expect(document.body.textContent).not.toMatch(/undefined/);
    for (const button of screen.getAllByRole("button", { name: /^copy / })) {
      expect(button).toBeDisabled();
    }
  });

  it("distinguishes an unreachable server", async () => {
    const { ConnectRows } = await facts({ kind: "unreachable" });
    render(await ConnectRows());
    expect(screen.getAllByText("unavailable — reload the page")).toHaveLength(4);
    expect(screen.queryByText(/claude mcp add/)).not.toBeInTheDocument();
    expect(screen.queryByText(/codex mcp add/)).not.toBeInTheDocument();
    expect(screen.queryByText(/vibe mcp add/)).not.toBeInTheDocument();
  });

  describe("the copy buttons", () => {
    function clipboard(writeText: () => Promise<void>) {
      const user = userEvent.setup();
      vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
      return user;
    }

    it("confirms in the button and in a live region", async () => {
      const { CopyRows } = await import("./CopyRows");
      render(<CopyRows mcpUrl="https://x.test/mcp" unavailable="-" />);
      const writeText = vi.fn(async () => {});
      const user = clipboard(writeText);
      await user.click(screen.getAllByRole("button", { name: /^copy / })[0]);
      expect(writeText).toHaveBeenCalledWith("https://x.test/mcp");
      expect(await screen.findByText("copied")).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("mcp endpoint copied to the clipboard.");
    });

    it("selects the text when the clipboard refuses", async () => {
      const { CopyRows } = await import("./CopyRows");
      render(<CopyRows mcpUrl="https://x.test/mcp" unavailable="-" />);
      const user = clipboard(async () => {
        throw new Error("denied");
      });
      await user.click(screen.getAllByRole("button", { name: /^copy / })[1]);
      expect(await screen.findByText("select it")).toBeInTheDocument();
      expect(getSelection()?.getRangeAt(0).toString()).toContain("claude mcp add");
      expect(screen.getByRole("status")).toBeEmptyDOMElement();
    });
  });
});
