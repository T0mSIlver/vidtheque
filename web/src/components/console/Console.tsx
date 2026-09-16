"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useEffectEvent, useReducer, useRef } from "react";
import type { SearchOutcome } from "@/lib/api/outcome";
import type { ContentType, EditionTalk } from "@/lib/api/schemas";
import { AskPane } from "./AskPane";
import { Cold, type Boot, type SearchExample } from "./Cold";
import { ConsoleForm, type MachineState } from "./ConsoleForm";
import { fetchSearch, streamAsk } from "./requests";
import { SearchResults, type SearchActions } from "./SearchResults";
import { holds, initialState, reducer, type ConsoleState } from "./state";
import { parseSnapshot, sameSnapshot, serializeSnapshot, type Mode, type Snapshot } from "./url";
import styles from "./console.module.css";

export interface ConsoleProps {
  /** The route the console's URL belongs to. */
  path: string;
  askEnabled: boolean;
  boot: Boot;
  initial: Snapshot;
  /** Page one for a search deep link, read by the server for the first paint. */
  initialSearch: SearchOutcome | null;
  /** The scope every request on this page carries (aie-paris-2026.md §4.3). */
  tags?: string;
  /** Edition talks, for naming hits and citations by the talk they land in. */
  talks?: EditionTalk[];
  searchExamples?: readonly SearchExample[];
  searchPrompt?: string;
  askExamples: readonly string[];
  noMatch: string;
  /** Server-rendered corpus listing for the cold pages. */
  corpus?: React.ReactNode;
  autoFocus?: boolean;
}

const NO_TALKS: EditionTalk[] = [];

/**
 * The public search/ask console: one form, one output region, and no App
 * Router navigation. Requests go straight to `/api/*`; the URL records each
 * committed snapshot with `history.pushState` (demo-site.md §6.2).
 */
export function Console(props: ConsoleProps) {
  const { path, askEnabled, boot, tags, talks = NO_TALKS } = props;
  const [state, dispatch] = useReducer(reducer, props, (p) =>
    initialState(p.initial, p.initialSearch),
  );
  const input = useRef<HTMLInputElement>(null);
  // One box for the in-flight request and the sequence, created once so the
  // unmount cleanup reads the live controller.
  const flight = useRef({ seq: 0, controller: null as AbortController | null });

  function begin() {
    const box = flight.current;
    box.controller?.abort();
    box.controller = new AbortController();
    box.seq += 1;
    return { id: box.seq, signal: box.controller.signal };
  }

  function stop() {
    flight.current.controller?.abort();
    flight.current.controller = null;
  }

  const href = (snapshot: Snapshot) => path + serializeSnapshot(snapshot, askEnabled);

  function commit(snapshot: Snapshot) {
    dispatch({ type: "commit", snapshot });
    writeUrl(href(snapshot), "pushState");
  }

  async function search(q: string, type: ContentType, push = true) {
    if (push) commit({ mode: "search", q, type });
    if (!q) {
      stop();
      dispatch({ type: "cold" });
      return;
    }
    const { id, signal } = begin();
    dispatch({ type: "search", id });
    try {
      const outcome = await fetchSearch({ q, type, offset: 0, tags }, signal);
      dispatch({ type: "searched", id, q, channel: type, outcome });
    } catch {
      // Aborted: a newer request or a mode switch owns the screen now.
    }
  }

  async function more() {
    // Page one of a newer search is still out: paging or retrying the view
    // under it would abort that request and page the query the URL left.
    if (state.pending) return;
    const { search: view } = state;
    if (view.outcome?.kind !== "ok") return;
    // The server's own cursor: a dropped malformed row still took its slot.
    const { pagination } = view.more.at(-1) ?? view.outcome.page;
    const offset = pagination.offset + pagination.limit;
    const { id, signal } = begin();
    dispatch({ type: "more", id });
    try {
      const outcome = await fetchSearch({ q: view.q, type: view.type, offset, tags }, signal);
      dispatch({ type: "moreDone", id, outcome });
    } catch {
      // Aborted.
    }
  }

  function ask(question: string) {
    if (!question) return;
    commit({ mode: "ask", q: question, type: state.type });
    const { id, signal } = begin();
    dispatch({ type: "ask", id, question });
    void streamAsk({ q: question, tags }, signal, (phase) =>
      dispatch({ type: "askPhase", id, phase }),
    );
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Focus stays put; only a touch keyboard is dismissed over the results.
    if (window.matchMedia?.("(pointer: coarse)").matches) input.current?.blur();
    const q = state.draft.trim();
    if (state.mode === "ask") ask(q);
    else void search(q, state.type);
  }

  // A chip is the filter, so it re-runs the search on screen. A cold page has
  // nothing to re-run, but the chip is a committing action either way
  // (DECISIONS.md 2026-09-16): the URL carries the channel it pinned.
  function pickChannel(value: ContentType) {
    dispatch({ type: "channel", value });
    const q = state.draft.trim();
    if (q) void search(q, value);
    else commit({ mode: "search", q: "", type: value });
  }

  function switchMode(mode: Mode) {
    if (mode === state.mode) return;
    stop();
    dispatch({ type: "mode", mode });
    const q = state.draft.trim();
    if (mode === "ask") {
      commit({ mode, q, type: state.type });
    } else if (q && !holds(state, q, state.type)) {
      void search(q, state.type);
    } else {
      commit({ mode, q, type: state.type });
      if (!q) dispatch({ type: "cold" });
    }
  }

  function runExample(q: string, type: ContentType) {
    dispatch({ type: "draft", value: q });
    dispatch({ type: "channel", value: type });
    void search(q, type);
  }

  // The URL is the snapshot: Back, Forward and a page restored from the
  // router's cache arrive as search params unlike the committed ones.
  const query = useSearchParams().toString();
  const restore = useEffectEvent((query: string) => {
    // A render the router has not caught up on names an older entry.
    if (query !== window.location.search.slice(1)) return;
    const snapshot = parseSnapshot(new URLSearchParams(query), askEnabled);
    if (sameSnapshot(snapshot, state.committed)) {
      // Same state, different spelling (a question on a keyless deployment).
      writeUrl(href(snapshot), "replaceState");
      return;
    }
    stop();
    dispatch({ type: "restore", snapshot });
    if (snapshot.mode === "search" && snapshot.q && !holds(state, snapshot.q, snapshot.type)) {
      void search(snapshot.q, snapshot.type, false);
    }
  });

  useEffect(() => restore(query), [query]);

  useEffect(() => {
    const box = flight.current;
    return () => box.controller?.abort();
  }, []);

  const actions: SearchActions = {
    href: (q, type) => href({ mode: "search", q, type }),
    retry: () => {
      if (state.pending) return;
      void search(state.search.q, state.search.type, false);
    },
    more: () => void more(),
    searchAll: () => runExample(state.search.q, "all"),
    examples: props.searchExamples?.length
      ? () => {
          runExample("", "all");
          input.current?.focus();
        }
      : null,
  };

  const busy = state.mode === "ask" ? state.ask.phase.kind === "working" : state.pending;
  return (
    <div className={styles.console}>
      <ConsoleForm
        mode={state.mode}
        askEnabled={askEnabled}
        draft={state.draft}
        channel={state.type}
        word={machineWord(state, boot)}
        busy={busy}
        inputRef={input}
        // The search box takes the caret on load; a loaded question waits for a reader.
        autoFocus={props.autoFocus && props.initial.mode === "search"}
        onDraft={(value) => dispatch({ type: "draft", value })}
        onSubmit={submit}
        onChannel={pickChannel}
        onMode={switchMode}
      />
      {state.mode === "search" && state.search.outcome ? (
        // The previous screen stays until the new one lands, dimmed while busy.
        <div className={state.pending ? styles.stale : undefined} aria-busy={state.pending}>
          <SearchResults
            view={{ ...state.search, outcome: state.search.outcome }}
            talks={talks}
            noMatch={props.noMatch}
            actions={actions}
            pending={state.pending}
          />
        </div>
      ) : state.mode === "search" || state.ask.phase.kind === "idle" ? (
        <div className={state.pending ? styles.stale : undefined} aria-busy={state.pending}>
          <Cold
            mode={state.mode}
            boot={boot}
            searchExamples={props.searchExamples ?? []}
            searchPrompt={props.searchPrompt}
            askExamples={props.askExamples}
            corpus={props.corpus}
            href={(q, type) => href({ mode: "search", q, type })}
            onSearchExample={(example) => runExample(example.q, example.type ?? "all")}
            onAskExample={(question) => {
              dispatch({ type: "draft", value: question });
              ask(question);
            }}
          />
        </div>
      ) : (
        <AskPane
          key={state.ask.id}
          phase={state.ask.phase}
          draft={state.draft}
          talks={talks}
          searchHref={href({ mode: "search", q: state.draft.trim(), type: state.type })}
          onRetry={() => ask(state.draft.trim())}
          onSearch={() => switchMode("search")}
        />
      )}
    </div>
  );
}

// Next.js's documented native History API integration: the router follows the
// URL without navigating (demo-site.md §6.2).
function writeUrl(url: string, method: "pushState" | "replaceState") {
  if (window.location.pathname + window.location.search === url) return;
  window.history[method](null, "", url);
}

function machineWord(state: ConsoleState, boot: Boot): MachineState {
  if (state.mode === "ask") {
    const { kind } = state.ask.phase;
    return kind === "working" ? "reading" : kind === "degraded" ? "refused" : "ready";
  }
  if (state.pending) return "scanning";
  const { outcome } = state.search;
  if (!outcome)
    return boot === "rate_limited" ? "rate limited" : boot === "ok" ? "ready" : "no reply";
  if (outcome.kind === "ok") return outcome.page.results.length ? "ready" : "no hits";
  return outcome.kind === "unreachable" ? "no reply" : "refused";
}
