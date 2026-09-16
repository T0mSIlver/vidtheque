"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useReducer,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
} from "react";
import { dashboard, DashboardError } from "@/lib/dashboard/client";
import type { Cue, CuePage, VideoDetail } from "@/lib/dashboard/schemas";
import { clock, count } from "@/lib/format";
import controls from "../../kit/controls.module.css";
import { table } from "../../kit/table";
import { DashLink, Panel, Sep, ui } from "../../kit/ui";
import styles from "./detail.module.css";
import { cueLink, markCues } from "./query";

interface Cues {
  cues: Cue[];
  /** The offset of the first row on screen. */
  first: number;
  /** The server's offset after the last row on screen. */
  next: number;
  hasMore: boolean;
  busy: boolean;
  error: unknown;
  /** The last batch was prepended, so the box goes back to its top. */
  rewound: boolean;
}

type Action =
  | { type: "ask" }
  | { type: "landed"; page: CuePage; back: boolean }
  | { type: "failed"; error: unknown };

function reduce(state: Cues, action: Action): Cues {
  switch (action.type) {
    case "ask":
      return { ...state, busy: true, error: null };
    case "failed":
      return { ...state, busy: false, error: action.error };
    case "landed": {
      const { page } = action;
      if (action.back) {
        // Only rows earlier than what is on screen: the first page overlaps.
        const rows = page.cues.slice(0, Math.max(state.first - page.offset, 0));
        return {
          ...state,
          cues: [...rows, ...state.cues],
          first: page.offset,
          busy: false,
          rewound: true,
        };
      }
      return {
        ...state,
        cues: [...state.cues, ...page.cues],
        next: page.offset + page.cues.length,
        hasMore: page.has_more,
        busy: false,
        rewound: false,
      };
    }
  }
}

const plain = (event: MouseEvent) =>
  !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey);

/**
 * The transcript, a batch at a time from the endpoint the payload names (§20).
 * Nearing the end of the box appends the next batch; Earlier prepends, for a
 * panel seeded mid-transcript. The place is written back to the URL, so a
 * position is a link somebody can send.
 */
export function Transcript({
  transcript,
  videoId,
  search,
  seedSize,
  seedOffset,
}: {
  transcript: VideoDetail["transcript"];
  videoId: string;
  search: string;
  /** `?cues=`, or `null` for the endpoint's default. */
  seedSize: number | null;
  /** `?cue_offset=`, the first cue asked for. */
  seedOffset: number;
}) {
  const total = transcript.cues;
  const endpoint = transcript.endpoint;
  // Read once: the URL is rewritten as the reader pages, and must not re-seed.
  const [size] = useState(seedSize ?? transcript.default_limit);
  const [seed] = useState(seedOffset);
  const [state, dispatch] = useReducer(reduce, seed, (first) => ({
    cues: [],
    first,
    next: first,
    // The totals predict the pager, so it is not there and gone when they agree.
    hasMore: first + size < total,
    busy: total > 0,
    error: null,
    rewound: false,
  }));
  const request = useRef<AbortController | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const read = useCallback(
    (offset: number, back: boolean) => {
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      const query = new URLSearchParams({ offset: String(offset), limit: String(size) });
      dashboard.cues(endpoint, query, controller.signal).then(
        (page) => {
          if (!controller.signal.aborted) dispatch({ type: "landed", page, back });
        },
        (error: unknown) => {
          if (!controller.signal.aborted) dispatch({ type: "failed", error });
        },
      );
    },
    [endpoint, size],
  );

  useEffect(() => {
    if (total === 0) return;
    read(seed, false);
    return () => request.current?.abort();
  }, [read, seed, total]);

  useEffect(() => {
    if (state.cues.length) markCues(state.first, size);
  }, [state.cues, state.first, size]);

  useLayoutEffect(() => {
    if (state.rewound && box.current) box.current.scrollTop = 0;
  }, [state.cues, state.rewound]);

  function loadMore() {
    dispatch({ type: "ask" });
    read(state.next, false);
  }

  function loadEarlier() {
    dispatch({ type: "ask" });
    read(Math.max(state.first - size, 0), true);
  }

  // One boxful short of the end, so the next batch is arriving before the
  // scroll would stop.
  function onScroll() {
    const element = box.current;
    if (!element || state.busy || !state.hasMore || state.error) return;
    if (element.scrollTop + element.clientHeight * 2 >= element.scrollHeight) loadMore();
  }

  const { cues, first, busy, error, hasMore } = state;

  // The empty page, not the empty transcript: `?cue_offset=` can land past the
  // end of one that exists.
  if (total === 0 || (!busy && !error && cues.length === 0)) {
    return (
      <Panel id="transcript" title="Transcript">
        <div className={styles.empty}>
          <p className={styles.emptyLead}>No transcript cues on this page.</p>
          <p className={ui.emptyNote}>
            The <code>stt</code> row in Provenance says whether one was produced.
          </p>
        </div>
      </Panel>
    );
  }

  return (
    <Panel id="transcript" title="Transcript">
      {/* Totals, not position: the scrollbar already says where you are. */}
      <p className={styles.cuepos}>
        <span className={ui.mono}>{count(total)}</span> cues
        <Sep /> <span className={ui.mono}>{count(transcript.words)}</span> words
        <Sep /> <span className={ui.mono}>{count(transcript.chars)}</span> chars
      </p>
      <div
        className={styles.cuebox}
        onScroll={onScroll}
        ref={box}
        style={{ "--cue-rows": Math.max(Math.min(size, total - seed), 1) } as CSSProperties}
        tabIndex={0}
      >
        <ol className={styles.cues}>
          {cues.map((cue, index) => (
            <CueRow key={`${cue.t}-${index}`} cue={cue} videoId={videoId} />
          ))}
        </ol>
        {busy ? <p className={styles.cueload}>loading</p> : null}
      </div>
      {error ? (
        <p className={styles.panelNote}>
          {error instanceof DashboardError ? error.message : "The next batch did not arrive."}
        </p>
      ) : null}
      {/* Real links at the server's offsets, so a page can go to a new tab; a
          plain click appends in place instead. */}
      {first > 0 || hasMore ? (
        <nav className={table.pager} aria-label="Transcript pages">
          {first > 0 ? (
            <DashLink
              className={controls.ghostlink}
              href={cueLink(search, videoId, Math.max(first - size, 0))}
              onClick={(event) => {
                if (!plain(event)) return;
                event.preventDefault();
                loadEarlier();
              }}
            >
              ← Earlier
            </DashLink>
          ) : null}
          {hasMore ? (
            <DashLink
              className={controls.ghostlink}
              href={cueLink(search, videoId, first + cues.length)}
              onClick={(event) => {
                if (!plain(event)) return;
                event.preventDefault();
                loadMore();
              }}
            >
              Next {size} cues →
            </DashLink>
          ) : null}
        </nav>
      ) : null}
    </Panel>
  );
}

function CueRow({ cue, videoId }: { cue: Cue; videoId: string }) {
  const opens = cue.chunk_opens;
  const confidence = cue.avg_logprob !== null ? cue.avg_logprob.toFixed(2) : null;

  return (
    <>
      {opens ? (
        <li className={styles.chunkmark} aria-hidden="true">
          {`chunk ${opens.seq} · ${clock(opens.start_s)}–${clock(opens.end_s)} · ${opens.n_words} words · ${opens.n_chars} chars`}
        </li>
      ) : null}
      <li className={`${styles.cue} ${cue.in_chunk ? styles.inChunk : ""}`}>
        {/* `t` is the whole second the endpoint sends for the deeplink. */}
        <a
          className={styles.at}
          href={`https://youtu.be/${encodeURIComponent(videoId)}?t=${cue.t}`}
          rel="noopener noreferrer"
          target="_blank"
        >
          {clock(cue.start_s)}
        </a>
        <span className={styles.cuetext}>{cue.text}</span>
        {cue.speaker ? <span className={styles.speaker}>{cue.speaker}</span> : null}
        {confidence ? (
          <span className={styles.conf} title="avg_logprob">
            {confidence}
          </span>
        ) : null}
      </li>
    </>
  );
}
