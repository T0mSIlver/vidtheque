// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// A stream the test opens and feeds by hand, so "the bytes stopped" and "a
// second ask started" are things a test can actually stage.
function openStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: new Response(body, {
      status: 200,
      headers: { "content-type": "text/event-stream; charset=utf-8" },
    }),
    send(text: string) {
      controller.enqueue(new TextEncoder().encode(text));
    },
    close() {
      controller.close();
    },
  };
}

function frame(event: unknown) {
  return `data: ${JSON.stringify(event)}\n\n`;
}

function answerFrame(answer: string) {
  return frame({ event: "answer", payload: { answer, citations: [], model: null } });
}

const ACTIVITY = frame({ event: "activity", id: 1, phase: "start", text: "Searching…" });

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
          {
            error: "llm_unavailable",
            reason: "no_key",
            message: "LLM mode unavailable — use search.",
            retry_after_s: 60,
          },
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
    expect(screen.getByRole("link", { name: "Search instead" })).toHaveAttribute(
      "href",
      "/?q=anything",
    );
  });

  // The limiter answers with the general error envelope, which has no
  // `reason`. Requiring one turned its sentence and its delay into "the
  // corpus could not be reached", which was neither true nor useful.
  it("shows the rate limiter's own sentence and delay", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            error: "E_RATE_LIMIT",
            message: "Too many requests",
            retry_after_s: 17,
            bucket: "ask",
          },
          { status: 429 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<AskMode initialQ="anything" />);
    await user.click(screen.getByRole("button", { name: "ask" }));

    const pane = await screen.findByRole("status");
    expect(pane).toHaveTextContent("Too many requests");
    expect(pane).toHaveTextContent("try again in 17s");
  });

  it("falls back to Retry-After when the body carries no delay", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "E_RATE_LIMIT", message: "Too many requests" }), {
            status: 429,
            headers: { "content-type": "application/json", "retry-after": "9" },
          }),
      ),
    );
    const user = userEvent.setup();
    render(<AskMode initialQ="anything" />);
    await user.click(screen.getByRole("button", { name: "ask" }));

    const pane = await screen.findByRole("status");
    expect(pane).toHaveTextContent("Too many requests");
    expect(pane).toHaveTextContent("try again in 9s");
  });

  // Bytes running out is not an answer. Before this, the log kept its
  // spinner and the page waited forever for a frame that was never coming.
  it("says so when the stream ends before the answer, and retries", async () => {
    const first = openStream();
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(first.response)
      .mockResolvedValueOnce(streamResponse(answerFrame("It is the reused attention state.")));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    render(<AskMode initialQ="what is a kv cache" />);

    await user.click(screen.getByRole("button", { name: "ask" }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
    first.send(ACTIVITY);
    first.close();

    const pane = await screen.findByRole("status");
    expect(pane).toHaveTextContent("The answer stopped before it finished.");
    // The work log keeps what did arrive: the failure is the missing end.
    expect(screen.getByLabelText("What the model is doing").querySelectorAll("li")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByText("It is the reused attention state.")).toBeInTheDocument();
  });

  it("skips an event kind it has never heard of", async () => {
    const wire = frame({ event: "token_usage", in: 900, out: 40 }) + answerFrame("Forty-two.");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => streamResponse(wire)),
    );
    const user = userEvent.setup();
    render(<AskMode initialQ="anything" />);
    await user.click(screen.getByRole("button", { name: "ask" }));

    await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
    expect(screen.getByText("Forty-two.")).toBeInTheDocument();
  });

  it("degrades on a known event that arrived malformed", async () => {
    // An `answer` whose payload is not an answer: dropping it silently would
    // leave the page waiting on a frame that already came and went.
    const wire = frame({ event: "answer", payload: { answer: 42 } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => streamResponse(wire)),
    );
    const user = userEvent.setup();
    render(<AskMode initialQ="anything" />);
    await user.click(screen.getByRole("button", { name: "ask" }));

    const pane = await screen.findByRole("status");
    expect(pane).toHaveTextContent("shape this page does not understand");
  });

  it("lets the newest ask win when an older one is still streaming", async () => {
    const first = openStream();
    const second = openStream();
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(first.response)
      .mockResolvedValueOnce(second.response);
    vi.stubGlobal("fetch", fetchSpy);
    render(<AskMode initialQ="what is a kv cache" />);
    const form = document.querySelector("form") as HTMLFormElement;

    fireEvent.submit(form);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
    fireEvent.submit(form);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));

    // The superseded request finishes anyway — a mocked fetch does not care
    // about the abort — and must not land on the newer one's state.
    first.send(answerFrame("stale"));
    first.close();
    second.send(answerFrame("fresh"));
    second.close();

    await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
    expect(screen.getByText("fresh")).toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
  });

  it("aborts the request it leaves behind when it unmounts", async () => {
    const open = openStream();
    const fetchSpy = vi.fn(async () => open.response);
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    const view = render(<AskMode initialQ="what is a kv cache" />);

    await user.click(screen.getByRole("button", { name: "ask" }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    view.unmount();

    expect(init.signal?.aborted).toBe(true);
    // Whatever arrives now has nowhere to go, and going nowhere is the point.
    open.send(answerFrame("too late"));
    open.close();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByLabelText("Answer")).not.toBeInTheDocument();
  });
});
