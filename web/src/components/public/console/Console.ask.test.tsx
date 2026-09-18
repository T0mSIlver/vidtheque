// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ACTIVITY,
  answerFrame,
  ASK,
  found,
  frame,
  hit,
  mountConsole,
  never,
  openStream,
  QUESTION_BOX,
  SEARCH_BOX,
  streamResponse,
  wire,
} from "@/test/public/console";

vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);

// The exact bytes the API sent, from the project root where vitest runs.
const FIXTURE = readFileSync("src/lib/api/__fixtures__/ask.sse", "utf8");

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

const LOADED = {
  url: "/demo?ask=what+is+a+kv+cache",
  initial: { mode: "ask", q: "what is a kv cache", type: "all" },
} as const;

function body(fetchSpy: ReturnType<typeof vi.fn>, call = 0) {
  return (fetchSpy.mock.calls[call] as unknown as [string, RequestInit])[1];
}

describe("the console in ask mode", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("keeps both modes' cold panels and controls mounted, only the active one live", async () => {
    vi.stubGlobal("fetch", vi.fn(never));
    const user = userEvent.setup();
    const { container } = mountConsole();
    const askExample = screen.getByText("Why do agents write bad AGENTS.md?");
    const searchExample = screen.getByText("context window costs money tokens");
    const chips = screen.getByRole("group", { name: "Search which channel", hidden: true });
    expect(askExample.closest("[inert]")).toBeNull();
    expect(searchExample.closest("[inert]")).not.toBeNull();
    expect(chips.hasAttribute("inert")).toBe(true);

    await user.click(screen.getByRole("button", { name: "search" }));
    expect(screen.getByText("Why do agents write bad AGENTS.md?")).toBe(askExample);
    expect(askExample.closest("[inert]")).not.toBeNull();
    expect(searchExample.closest("[inert]")).toBeNull();
    expect(chips.hasAttribute("inert")).toBe(false);
    expect(container.querySelectorAll("button[type=submit]")).toHaveLength(1);
  });

  it("loads a shared question without firing, and fires on click", async () => {
    const fetchSpy = vi.fn(async () => streamResponse(FIXTURE));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    const { push } = mountConsole(LOADED);

    expect(screen.getByLabelText(QUESTION_BOX)).toHaveValue("what is a kv cache");
    expect(screen.getByRole("search")).toContainElement(screen.getByLabelText(QUESTION_BOX));
    expect(fetchSpy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", ASK));
    expect((fetchSpy.mock.calls[0] as unknown as [string])[0]).toBe("/api/ask");
    expect(body(fetchSpy).body).toBe(JSON.stringify({ q: "what is a kv cache" }));
    expect((body(fetchSpy).headers as Record<string, string>).accept).toBe(
      "text/event-stream, application/x-ndjson;q=0.9, application/json;q=0.8",
    );
    expect(push).not.toHaveBeenCalled();

    await waitFor(() => expect(screen.getByText("Sources")).toBeInTheDocument());
    expect(screen.getByLabelText("What the model is doing").querySelectorAll("li")).toHaveLength(5);
    // The log says what the model is doing, never what came back.
    expect(screen.queryByText(/10 hits in 8 talks/)).not.toBeInTheDocument();
  });

  it("carries the page's edition scope, and writes the asked question to the URL", async () => {
    const fetchSpy = vi.fn(async () => streamResponse(answerFrame("Scoped.")));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    const { push } = mountConsole({ url: "/paris", path: "/paris", tags: "series:aie-paris-2026" });

    await user.type(screen.getByLabelText(QUESTION_BOX), "what changed?{Enter}");
    expect(body(fetchSpy).body).toBe(
      JSON.stringify({ q: "what changed?", tags: "series:aie-paris-2026" }),
    );
    expect(push).toHaveBeenCalledWith(null, "", "/paris?ask=what+changed%3F");
    expect(await screen.findByText("Scoped.")).toBeInTheDocument();
  });

  it("reads NDJSON, and takes a plain JSON body as the same answer", async () => {
    const ndjson =
      `${JSON.stringify({ event: "activity", id: 1, phase: "start", text: "Searching…" })}\n` +
      `${JSON.stringify({ event: "activity", id: 1, phase: "done", result: "6 hits in 2 talks" })}\n` +
      `${JSON.stringify({ event: "answer", payload: { answer: "Paged.", citations: [], model: null } })}\n`;
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(streamResponse(ndjson, "application/x-ndjson"))
      .mockResolvedValueOnce(Response.json({ answer: "Whole.", citations: [], model: "m" }));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    mountConsole(LOADED);

    await user.click(screen.getByRole("button", ASK));
    expect(await screen.findByText("Paged.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", ASK));
    expect(await screen.findByText("Whole.")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("parks the idle line in the flow while a tool call runs, busy until the answer", async () => {
    const open = openStream();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => open.response),
    );
    const user = userEvent.setup();
    mountConsole(LOADED);
    await user.click(screen.getByRole("button", ASK));

    const pane = await screen.findByLabelText("Answer");
    const idle = screen.getByText("reading the corpus…");
    expect(pane).toHaveAttribute("aria-busy", "true");
    expect(idle.className).not.toMatch(/parked/);

    act(() => open.send(ACTIVITY));
    await waitFor(() => expect(idle.className).toMatch(/parked/));
    expect(screen.getByText("reading")).toHaveAttribute("data-s", "working");

    act(() => {
      open.send(answerFrame("Done."));
      open.close();
    });
    await waitFor(() => expect(pane).toHaveAttribute("aria-busy", "false"));
    const disclosure = screen.getByText("Show its work").closest("details");
    expect(disclosure).not.toHaveAttribute("open");
  });

  describe("the answer", () => {
    it("links [n] into the moment, badges the source and prints its receipt", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          streamResponse(
            answerFrame("Paged [1] and said [2].", {
              citations: [CITATION, { ...CITATION, n: 2, source: "transcript", link: null }],
            }),
          ),
        ),
      );
      const user = userEvent.setup();
      mountConsole(LOADED);
      await user.click(screen.getByRole("button", ASK));

      const marker = await screen.findByRole("link", {
        name: "Source 1: Making LLMs go brrr at 0:13",
      });
      expect(marker).toHaveAttribute("href", "https://youtu.be/zduSFxRajkE?t=11");
      // A marker naming a citation with no link stays text.
      expect(screen.queryByRole("link", { name: /^Source 2/ })).not.toBeInTheDocument();
      const snippets = screen.getAllByText("the block table keeps");
      expect(snippets[0].className).toMatch(/snipScreen/);
      expect(snippets[1].className).toMatch(/snipSpoken/);
      expect(
        document.querySelector('a[class*="rcpt"][href="https://youtu.be/zduSFxRajkE?t=11"]'),
      ).toHaveTextContent("youtu.be/zduSFxRajkE?t=11");
      const titles = screen.getAllByRole("link", { name: "Making LLMs go brrr" });
      expect(titles[1]).toHaveAttribute("href", "https://youtu.be/zduSFxRajkE");
    });

    // Corpus text is adversarial: it reaches the page as text nodes only.
    it("renders markup in an answer as text, and refuses a script link", async () => {
      const fetchSpy = vi
        .fn()
        .mockResolvedValueOnce(streamResponse(answerFrame('<img src=x onerror="alert(1)">')))
        .mockResolvedValueOnce(
          streamResponse(
            answerFrame("Paged [1].", {
              citations: [{ ...CITATION, link: "javascript:alert(1)" }],
            }),
          ),
        );
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      mountConsole(LOADED);
      await user.click(screen.getByRole("button", ASK));
      expect(await screen.findByText(/<img src=x/)).toBeInTheDocument();
      expect(document.querySelector("img[onerror]")).toBeNull();

      await user.click(screen.getByRole("button", ASK));
      expect(await screen.findByRole("status")).toHaveTextContent(
        "shape this page does not understand",
      );
      expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    });

    it("names a citation by the edition talk it landed in", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => streamResponse(answerFrame("Paged [1].", { citations: [CITATION] }))),
      );
      const user = userEvent.setup();
      mountConsole({
        ...LOADED,
        talks: [
          {
            session_id: "s1",
            day: "2026-09-24",
            scheduled_start: "10:00",
            scheduled_end: "10:30",
            title: "Serving without the tail",
            speakers: [{ name: "Alice Martin", company: "Mistral" }],
            category: "Inference",
            alignment_state: "aligned",
            video_id: "zduSFxRajkE",
            source_kind: "stream",
            start_s: 0,
            end_s: 600,
            source: "https://youtu.be/zduSFxRajkE?t=0",
          },
        ],
      });
      await user.click(screen.getByRole("button", ASK));
      expect(
        await screen.findByRole("link", { name: "Serving without the tail" }),
      ).toBeInTheDocument();
    });
  });

  describe("degraded", () => {
    it("shows the refusal's sentence, counts down, and offers search in place", async () => {
      vi.useFakeTimers();
      const fetchSpy = vi.fn(async () =>
        Response.json(
          { error: "E_RATE_LIMIT", message: "Too many requests", retry_after_s: 2 },
          { status: 429 },
        ),
      );
      vi.stubGlobal("fetch", fetchSpy);
      mountConsole(LOADED);
      fireEvent.submit(screen.getByRole("search"));
      await act(async () => {});

      const pane = screen.getByRole("status");
      expect(pane).toHaveTextContent("Too many requests");
      expect(pane).toHaveTextContent("Try again in 2s.");
      const retry = screen.getByRole("button", { name: "Try again" });
      expect(retry).toBeDisabled();
      act(() => vi.advanceTimersByTime(1000));
      act(() => vi.advanceTimersByTime(1000));
      expect(retry).toBeEnabled();
      expect(screen.getByRole("link", { name: "Search instead" })).toHaveAttribute(
        "href",
        "/demo?q=what+is+a+kv+cache",
      );
    });

    it("falls back to Retry-After, and a 503 with no key keeps its own sentence", async () => {
      const fetchSpy = vi
        .fn()
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ error: "E_RATE_LIMIT", message: "Too many requests" }), {
            status: 429,
            headers: { "content-type": "application/json", "retry-after": "9" },
          }),
        )
        .mockResolvedValueOnce(
          Response.json(
            {
              error: "llm_unavailable",
              reason: "no_key",
              message: "LLM mode unavailable.",
              retry_after_s: null,
            },
            { status: 503 },
          ),
        );
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      mountConsole(LOADED);
      await user.click(screen.getByRole("button", ASK));
      expect(await screen.findByText("Try again in 9s.")).toBeInTheDocument();
      fireEvent.submit(screen.getByRole("search"));
      expect(await screen.findByText("LLM mode unavailable.")).toBeInTheDocument();
    });

    it("says the answer was interrupted when the bytes stop, mid-frame or between", async () => {
      const cut = openStream();
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => cut.response),
      );
      const user = userEvent.setup();
      mountConsole(LOADED);
      await user.click(screen.getByRole("button", ASK));
      cut.send(ACTIVITY);
      cut.send('data: {"event":"answer","payload":{"answer":"It is the re');
      cut.close();

      const pane = await screen.findByRole("status");
      expect(pane).toHaveTextContent("Answer interrupted.");
      expect(pane).not.toHaveTextContent("Could not reach the server.");
    });

    it("skips an unknown event, and degrades on a known one arriving malformed", async () => {
      const fetchSpy = vi
        .fn()
        .mockResolvedValueOnce(
          streamResponse(frame({ event: "token_usage", in: 900 }) + answerFrame("Forty-two.")),
        )
        .mockResolvedValueOnce(streamResponse(frame({ event: "answer", payload: { answer: 42 } })));
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      mountConsole(LOADED);
      await user.click(screen.getByRole("button", ASK));
      expect(await screen.findByText("Forty-two.")).toBeInTheDocument();
      await user.click(screen.getByRole("button", ASK));
      expect(await screen.findByRole("status")).toHaveTextContent(
        "shape this page does not understand",
      );
    });
  });

  it("lets the newest ask win when an older one is still streaming", async () => {
    const first = openStream();
    const second = openStream();
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(first.response)
      .mockResolvedValueOnce(second.response);
    vi.stubGlobal("fetch", fetchSpy);
    mountConsole(LOADED);
    const form = screen.getByRole("search");

    fireEvent.submit(form);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
    fireEvent.submit(form);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    expect(body(fetchSpy).signal?.aborted).toBe(true);

    first.send(answerFrame("stale"));
    first.close();
    second.send(answerFrame("fresh"));
    second.close();
    expect(await screen.findByText("fresh")).toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
  });

  it("aborts the request it leaves behind when it unmounts", async () => {
    const open = openStream();
    const fetchSpy = vi.fn(async () => open.response);
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    const view = mountConsole(LOADED);
    await user.click(screen.getByRole("button", ASK));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
    view.unmount();
    expect(body(fetchSpy).signal?.aborted).toBe(true);
  });

  it("runs a cold-page question on a click", async () => {
    const fetchSpy = vi.fn(async () => streamResponse(answerFrame("Because.")));
    vi.stubGlobal("fetch", fetchSpy);
    const user = userEvent.setup();
    mountConsole();
    expect(screen.getByText(/None of these is answered by one talk/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Why do agents write bad AGENTS.md?" }));
    expect(body(fetchSpy).body).toBe(JSON.stringify({ q: "Why do agents write bad AGENTS.md?" }));
    expect(screen.getByLabelText(QUESTION_BOX)).toHaveValue("Why do agents write bad AGENTS.md?");
  });

  it("keeps the Paris cold page to the questions", () => {
    mountConsole({ path: "/paris", showColdIntro: false });
    expect(
      screen.getByRole("button", { name: "Why do agents write bad AGENTS.md?" }),
    ).toBeVisible();
    expect(screen.queryByText("start here")).not.toBeInTheDocument();
    expect(screen.queryByText("Ask one of these")).not.toBeInTheDocument();
    expect(screen.queryByText(/None of these is answered by one talk/)).not.toBeInTheDocument();
  });

  it("gives the Paris cold page something to search for without the intro", () => {
    mountConsole({
      path: "/paris",
      showColdIntro: false,
      initial: { mode: "search", q: "", type: "all" },
      searchExamples: [{ q: "vLLM" }],
    });
    expect(screen.getByRole("link", { name: /vLLM/ })).toBeVisible();
    expect(screen.queryByText("Try one of these")).not.toBeInTheDocument();
  });

  describe("the mode switch", () => {
    it("keeps the same input element, its value and its focus", async () => {
      const fetchSpy = vi.fn(async () => wire(found([hit()])));
      vi.stubGlobal("fetch", fetchSpy);
      const user = userEvent.setup();
      const { push } = mountConsole();

      const box = screen.getByLabelText(QUESTION_BOX);
      await user.type(box, "paged attention");
      expect(screen.getByRole("button", { name: "ask ✨" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );

      await user.click(screen.getByRole("button", { name: "search" }));
      box.focus();
      expect(screen.getByLabelText(SEARCH_BOX)).toBe(box);
      expect(box).toHaveValue("paged attention");
      expect(box).toHaveFocus();
      // The typed query runs in the mode it was carried into.
      expect(push).toHaveBeenLastCalledWith(null, "", "/demo?q=paged+attention");
      expect(await screen.findByText("1 result")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "ask ✨" }));
      expect(screen.getByLabelText(QUESTION_BOX)).toBe(box);
      expect(push).toHaveBeenLastCalledWith(null, "", "/demo?ask=paged+attention");
      // A loaded question does not fire.
      expect(fetchSpy).toHaveBeenCalledOnce();
    });

    it("is not drawn on a deployment with no ask", () => {
      mountConsole({ askEnabled: false, initial: { mode: "search", q: "", type: "all" } });
      expect(screen.getByLabelText(SEARCH_BOX)).toBeInTheDocument();
      expect(screen.queryByRole("group", { name: "Result mode" })).not.toBeInTheDocument();
    });
  });
});
