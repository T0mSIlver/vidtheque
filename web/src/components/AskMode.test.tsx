// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AskMode } from "./AskMode";

// The mode switch is a router push, so the hooks it reads have to exist. One
// hoisted mock for the file: no test here asserts a navigation except the one
// that asserts the switch.
const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: nav.push,
    replace: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/demo",
}));

// Under jsdom `import.meta.url` is an http: URL, so the path is from the
// project root, which is where vitest runs.
const FIXTURE = readFileSync("src/lib/__fixtures__/ask.sse", "utf8");

const ASK = { name: /^Ask/ };

function streamResponse(text: string, contentType = "text/event-stream; charset=utf-8") {
  // jsdom's Blob has no stream(); build the body from the platform stream.
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": contentType } });
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

function answerFrame(answer: string, over: Record<string, unknown> = {}) {
  return frame({
    event: "answer",
    payload: { answer, citations: [], model: null, ...over },
  });
}

const ACTIVITY = frame({ event: "activity", id: 1, phase: "start", text: "Searching…" });

const CITATION = {
  n: 1,
  video_id: "zduSFxRajkE",
  title: "Making LLMs go brrr",
  channel: "GPU MODE",
  t: 13,
  timestamp: "0:13",
  link: "https://youtu.be/zduSFxRajkE?t=11",
  thumb: "https://api.test/frames/z-1.jpg?w=320",
  thumb_large: "https://api.test/frames/z-1.jpg?w=960",
  source: "ocr",
  text: "the block table keeps",
};

describe("AskMode", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    nav.push.mockClear();
  });

  it("loads a shared question without firing, and fires on click", async () => {
    const fetchSpy = vi.fn(async () => streamResponse(FIXTURE));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    render(<AskMode initialQ="what is a kv cache" />);

    expect(screen.getByLabelText("Your question")).toHaveValue("what is a kv cache");
    expect(fetchSpy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", ASK));
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/ask");
    expect(init.body).toBe(JSON.stringify({ q: "what is a kv cache" }));

    // The work log fills as frames land, then the answer with its sources.
    await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
    expect(screen.getByLabelText("What the model is doing").querySelectorAll("li")).toHaveLength(5);
    expect(screen.getByText(/10 hits in 8 talks/)).toBeInTheDocument();
    expect(screen.getByText("Sources")).toBeInTheDocument();
  });

  // Both framings over one POST, and the fallback is not a worse answer — it
  // is the same answer with nothing to watch on the way (demo-site.md §3.5).
  describe("the framings", () => {
    it("asks for the stream that survives a CDN, then the one that parses", async () => {
      const fetchSpy = vi.fn(async () => streamResponse(FIXTURE));
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
      expect((init.headers as Record<string, string>).accept).toBe(
        "text/event-stream, application/x-ndjson;q=0.9, application/json;q=0.8",
      );
    });

    it("reads NDJSON, where one line is one event", async () => {
      const wire =
        `${JSON.stringify({ event: "activity", id: 1, phase: "start", text: "Searching…" })}\n` +
        `${JSON.stringify({ event: "activity", id: 1, phase: "done", result: "6 hits in 2 talks" })}\n` +
        `${JSON.stringify({ event: "answer", payload: { answer: "Paged.", citations: [], model: null } })}\n`;
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => streamResponse(wire, "application/x-ndjson")),
      );
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
      expect(screen.getByText("Paged.")).toBeInTheDocument();
      expect(screen.getByText(/6 hits in 2 talks/)).toBeInTheDocument();
    });

    // A server that does not stream, or a client that did not ask: the §3
    // body, whole. It is an answer, not a degraded pane.
    it("takes the plain JSON body when nothing streamed", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          Response.json({ answer: "Answered in one piece.", citations: [], model: "m" }),
        ),
      );
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
      expect(screen.getByText("Answered in one piece.")).toBeInTheDocument();
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
  });

  describe("the work log", () => {
    it("parks the idle line while a tool call owns the caret, in the flow", async () => {
      const open = openStream();
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => open.response),
      );
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      const idle = await screen.findByText("reading the corpus…");
      // Nothing running yet: the idle line carries the caret.
      expect(idle.className).not.toMatch(/parked/);

      act(() => open.send(ACTIVITY));
      await waitFor(() => expect(idle.className).toMatch(/parked/));
      // Parked, not removed: it still holds its line, so the document below
      // does not move six times an ask.
      expect(idle).toBeInTheDocument();

      act(() =>
        open.send(frame({ event: "activity", id: 1, phase: "done", result: "2 hits in 1 talk" })),
      );
      await waitFor(() => expect(idle.className).not.toMatch(/parked/));
    });

    it("folds under the answer once the answer owns the pane", async () => {
      const wire = ACTIVITY + answerFrame("Because the block table is paged.");
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => streamResponse(wire)),
      );
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
      const disclosure = screen.getByText("Show its work").closest("details");
      expect(disclosure).toBeInTheDocument();
      expect(disclosure).not.toHaveAttribute("open");
      expect(disclosure).toContainElement(screen.getByLabelText("What the model is doing"));
      // The pane's own idle line is gone with the working state.
      expect(screen.queryByText("reading the corpus…")).not.toBeInTheDocument();
    });

    it("holds the announcement to one while the work is running", async () => {
      const open = openStream();
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => open.response),
      );
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      const pane = await screen.findByLabelText("Answer");
      expect(pane).toHaveAttribute("aria-live", "polite");
      expect(pane).toHaveAttribute("aria-busy", "true");

      act(() => {
        open.send(answerFrame("Done."));
        open.close();
      });
      await waitFor(() => expect(pane).toHaveAttribute("aria-busy", "false"));
    });
  });

  describe("the answer", () => {
    it("renders [n] as a link into the moment it cites", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          streamResponse(answerFrame("The block table is paged [1].", { citations: [CITATION] })),
        ),
      );
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      const marker = await screen.findByRole("link", {
        name: "Source 1: Making LLMs go brrr at 0:13",
      });
      expect(marker).toHaveTextContent("[1]");
      expect(marker).toHaveAttribute("href", "https://youtu.be/zduSFxRajkE?t=11");
      expect(marker).toHaveAttribute("target", "_blank");
    });

    it("badges a source with the channel it came from, and prints its receipt", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => streamResponse(answerFrame("Paged [1].", { citations: [CITATION] }))),
      );
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      await waitFor(() => expect(screen.getByText("Sources")).toBeInTheDocument());
      expect(screen.getByText("on-screen")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "youtu.be/zduSFxRajkE?t=11" })).toBeInTheDocument();
    });

    // The title went to `/videos/{id}` while that page existed; it goes back to
    // the moment it cites now that it does not (2026-09-07).
    it("sends a source's title out to the talk", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => streamResponse(answerFrame("Paged [1].", { citations: [CITATION] }))),
      );
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      const title = await screen.findByRole("link", { name: "Making LLMs go brrr" });
      expect(title).toHaveAttribute("href", "https://youtu.be/zduSFxRajkE?t=11");
      expect(title).toHaveAttribute("target", "_blank");
      expect(title).toHaveAttribute("rel", "noopener noreferrer");
    });

    // A citation with no honest deep link still names a video, and the video's
    // own URL is the one thing the page can build without guessing.
    it("falls back to the video's own URL when a citation carries no link", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          streamResponse(answerFrame("Paged [1].", { citations: [{ ...CITATION, link: null }] })),
        ),
      );
      const user = userEvent.setup();
      render(<AskMode initialQ="anything" />);
      await user.click(screen.getByRole("button", ASK));

      const title = await screen.findByRole("link", { name: "Making LLMs go brrr" });
      expect(title).toHaveAttribute("href", "https://youtu.be/zduSFxRajkE");
    });
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
    await user.click(screen.getByRole("button", ASK));

    const pane = await screen.findByRole("status");
    expect(pane).toHaveTextContent("LLM mode unavailable — use search.");
    expect(pane).toHaveTextContent("Try again in 60s.");
    expect(screen.getByRole("link", { name: "Search instead" })).toHaveAttribute(
      "href",
      "/demo?ask=0&q=anything",
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
          { error: "E_RATE_LIMIT", message: "Too many requests", retry_after_s: 17, bucket: "ask" },
          { status: 429 },
        ),
      ),
    );
    const user = userEvent.setup();
    render(<AskMode initialQ="anything" />);
    await user.click(screen.getByRole("button", ASK));

    const pane = await screen.findByRole("status");
    expect(pane).toHaveTextContent("Too many requests");
    expect(pane).toHaveTextContent("Try again in 17s.");
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
    await user.click(screen.getByRole("button", ASK));

    const pane = await screen.findByRole("status");
    expect(pane).toHaveTextContent("Try again in 9s.");
  });

  // A retry that fires into a refusal is one more refusal, so the wait gates
  // it and lets go at zero.
  it("ticks the refusal down and re-enables the retry at zero", async () => {
    vi.useFakeTimers();
    try {
      const fetchSpy = vi.fn(async () =>
        Response.json(
          { error: "E_RATE_LIMIT", message: "Too many questions for now.", retry_after_s: 2 },
          { status: 429 },
        ),
      );
      vi.stubGlobal("fetch", fetchSpy);
      render(<AskMode initialQ="anything" />);
      fireEvent.submit(document.querySelector("form") as HTMLFormElement);
      await act(async () => {});

      const retry = screen.getByRole("button", { name: "Try again" });
      expect(screen.getByText("Try again in 2s.")).toBeInTheDocument();
      expect(retry).toBeDisabled();

      act(() => vi.advanceTimersByTime(1000));
      expect(screen.getByText("Try again in 1s.")).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(1000));
      expect(screen.getByText("Try again.")).toBeInTheDocument();
      expect(retry).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
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

    await user.click(screen.getByRole("button", ASK));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
    first.send(ACTIVITY);
    first.close();

    const pane = await screen.findByRole("status");
    expect(pane).toHaveTextContent("Answer interrupted.");
    // The work log keeps what did arrive: the failure is the missing end, and
    // it is folded where a finished answer folds it.
    await user.click(screen.getByText("Show its work"));
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
    await user.click(screen.getByRole("button", ASK));

    await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
    expect(screen.getByText("Forty-two.")).toBeInTheDocument();
  });

  // The answer is the end of the read. Before this, a late `activity` frame
  // was still consumed, and it put the pane back into `working` — spinner
  // running, ask button disabled, over an answer already on the screen.
  it("stays answered when an activity event trails the answer", async () => {
    const wire = answerFrame("It is the reused attention state.") + ACTIVITY;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => streamResponse(wire)),
    );
    const user = userEvent.setup();
    render(<AskMode initialQ="what is a kv cache" />);
    await user.click(screen.getByRole("button", ASK));

    await waitFor(() => expect(screen.getByLabelText("Answer")).toBeInTheDocument());
    // Give the trailing frame every chance to land on the settled phase.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("It is the reused attention state.")).toBeInTheDocument();
    expect(screen.getByRole("button", ASK)).toBeEnabled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
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
    await user.click(screen.getByRole("button", ASK));

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

    await user.click(screen.getByRole("button", ASK));
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

  describe("the cold page", () => {
    // A keyword chip under an ask box teaches the wrong thing twice: it tells a
    // stranger this is a search box, and clicking it spends a model call on a
    // phrase nobody would ever ask out loud.
    it("offers the questions, says what an answer is made of, and lists the corpus", () => {
      render(
        <AskMode initialQ="" examples={["Why do agents write bad AGENTS.md?"]}>
          <p>in this corpus</p>
        </AskMode>,
      );
      expect(
        screen.getByRole("button", { name: "Why do agents write bad AGENTS.md?" }),
      ).toBeInTheDocument();
      expect(screen.getByText(/None of these is answered by one talk/)).toBeInTheDocument();
      expect(screen.getByText("in this corpus")).toBeInTheDocument();
    });

    it("runs an example in the mode that is on screen, and only on a click", async () => {
      const fetchSpy = vi.fn(async () => streamResponse(answerFrame("Because.")));
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      render(<AskMode initialQ="" examples={["Is the harness or the model more important?"]} />);
      expect(fetchSpy).not.toHaveBeenCalled();

      await user.click(
        screen.getByRole("button", { name: "Is the harness or the model more important?" }),
      );

      const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
      expect(init.body).toBe(JSON.stringify({ q: "Is the harness or the model more important?" }));
      expect(screen.getByLabelText("Your question")).toHaveValue(
        "Is the harness or the model more important?",
      );
    });
  });

  // The switch is the mode, pressed, and it takes the question with it so a
  // visitor who asked and then thought better of it does not retype anything.
  it("switches to search as a pressed pair, carrying the question", async () => {
    const user = userEvent.setup();
    render(<AskMode initialQ="paged attention" />);

    expect(screen.getByRole("button", { name: "ask ✨" })).toHaveAttribute("aria-pressed", "true");
    const search = screen.getByRole("button", { name: "search" });
    expect(search).toHaveAttribute("aria-pressed", "false");

    await user.click(search);
    expect(nav.push).toHaveBeenCalledWith("/demo?ask=0&q=paged+attention");
  });
});
