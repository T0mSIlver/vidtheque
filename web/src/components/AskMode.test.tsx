// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AskMode } from "./AskMode";

// Under jsdom `import.meta.url` is an http: URL, so the path is from the
// project root, which is where vitest runs.
const FIXTURE = readFileSync("src/lib/__fixtures__/ask.sse", "utf8");

function streamResponse(text: string) {
  // jsdom's Blob has no stream(); build the body from the platform stream.
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream; charset=utf-8" },
  });
}

describe("AskMode", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads a shared question without firing, and fires on click", async () => {
    const fetchSpy = vi.fn(async () => streamResponse(FIXTURE));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    render(<AskMode initialQ="what is a kv cache" />);

    expect(screen.getByLabelText("Your question")).toHaveValue("what is a kv cache");
    expect(fetchSpy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "ask" }));
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/ask");
    expect(init.body).toBe(JSON.stringify({ q: "what is a kv cache" }));

    // The work log fills as frames land, then the answer with its sources.
    await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
    expect(screen.getByLabelText("What the model is doing").querySelectorAll("li")).toHaveLength(5);
    expect(screen.getByText("10 hits in 8 talks")).toBeInTheDocument();
    expect(screen.getByText("Sources")).toBeInTheDocument();
  });

  it("renders the degraded pane from a 503 that arrived before any stream", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { error: "llm_unavailable", reason: "no_key", message: "LLM mode unavailable — use search.", retry_after_s: 60 },
          { status: 503 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<AskMode initialQ="anything" />);
    await user.click(screen.getByRole("button", { name: "ask" }));

    const pane = await screen.findByRole("status");
    expect(pane).toHaveTextContent("LLM mode unavailable — use search.");
    expect(pane).toHaveTextContent("try again in 60s");
    expect(screen.getByRole("link", { name: "Search instead" })).toHaveAttribute("href", "/?q=anything");
  });
});
