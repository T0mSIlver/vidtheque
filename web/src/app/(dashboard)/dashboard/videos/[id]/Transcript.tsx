"use client";

import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useReducer,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import { dashboard, DashboardError } from "@/lib/dashboard/client";
import type { Cue, CuePage, VideoDetail } from "@/lib/dashboard/schemas";
import { clock } from "@/lib/format";
import controls from "@/components/dashboard/kit/controls.module.css";
import { table } from "@/components/dashboard/kit/table";
import { DashLink, Panel, ui } from "@/components/dashboard/kit/ui";
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
  /** The last batch was prepended: the reader goes to its first line. */
  rewound: boolean;
  error: unknown;
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

/** A paragraph closes at a new speaker, or after a sentence's end once it is
 *  long enough or a pause follows (§28.7). */
const PAUSE_S = 2;
const PARA_S = 60;
const PARA_CHARS = 600;
/** A run-on sentence still breaks somewhere. */
const RUN_ON_CHARS = 3 * PARA_CHARS;

/** A full stop, question or exclamation mark, or ellipsis, before any closing
 *  quote or bracket. */
const SENTENCE_END = /[.!?…]["'”’)\]]*$/;

export const endsSentence = (text: string) => SENTENCE_END.test(text.trim());

export interface Paragraph {
  cues: Cue[];
  speaker: string | null;
}

/** Consecutive cues as paragraphs a person can skim (§28.7). A cue is a
 *  caption line, not a sentence, so a paragraph closes only at a sentence end.
 *  A transcript with no sentence end at all (auto captions) has no such place,
 *  and closes at a pause or a reading length instead. */
export function paragraphsOf(cues: Cue[]): Paragraph[] {
  const punctuated = cues.some((cue) => endsSentence(cue.text));
  const paragraphs: Paragraph[] = [];
  let open: Paragraph | null = null;
  let chars = 0;
  for (const cue of cues) {
    const last = open?.cues[open.cues.length - 1];
    let fresh = !open || !last || cue.speaker !== open.speaker;
    if (open && last && !fresh) {
      const long = last.end_s - open.cues[0].start_s >= PARA_S || chars >= PARA_CHARS;
      const pause = cue.start_s - last.end_s >= PAUSE_S;
      fresh = punctuated
        ? (endsSentence(last.text) && (long || pause)) || chars >= RUN_ON_CHARS
        : long || pause;
    }
    if (fresh) {
      open = { cues: [cue], speaker: cue.speaker };
      paragraphs.push(open);
      chars = cue.text.length;
    } else if (open) {
      open.cues.push(cue);
      chars += cue.text.length + 1;
    }
  }
  return paragraphs;
}

const plain = (event: MouseEvent) =>
  !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey);

/**
 * The transcript, a batch at a time from the endpoint the payload names (§20).
 * It flows in the page, last on it: nearing its end appends the next batch;
 * Earlier prepends, for a panel seeded mid-transcript. The place is written back to the URL, so a
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
  const [size] = useState(seedSize ?? transcript.max_limit);
  const [seed] = useState(seedOffset);
  const [state, dispatch] = useReducer(reduce, seed, (first) => ({
    cues: [],
    first,
    next: first,
    // Predicted from the totals, so the pager does not appear and then vanish.
    hasMore: first + size < total,
    busy: total > 0,
    error: null,
    rewound: false,
  }));
  const request = useRef<AbortController | null>(null);
  const top = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  // The transcript's end is within a screen of the viewport.
  const [near, setNear] = useState(false);

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
    if (state.rewound) top.current?.scrollIntoView?.({ block: "start" });
  }, [state.cues, state.rewound]);

  useEffect(() => {
    const element = end.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), {
      rootMargin: "0px 0px 100% 0px",
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [total]);

  // The page reads on while its end is near, so the next batch is arriving
  // before the reader gets there; one that leaves the end near reads again.
  useEffect(() => {
    if (!near || state.busy || !state.hasMore || state.error) return;
    dispatch({ type: "ask" });
    read(state.next, false);
  }, [near, state.busy, state.hasMore, state.error, state.next, read]);

  function loadMore() {
    dispatch({ type: "ask" });
    read(state.next, false);
  }

  function loadEarlier(offset: number) {
    dispatch({ type: "ask" });
    read(offset, true);
  }

  const { cues, first, busy, error, hasMore } = state;

  // The empty page, not the empty transcript: `?cue_offset=` can land past the
  // end of one that exists.
  if (total === 0 || (!busy && !error && cues.length === 0)) {
    const backFromEnd = Math.max(Math.min(first, total) - size, 0);
    return (
      <Panel id="transcript" title="Transcript">
        <div className={styles.empty}>
          <p className={styles.emptyLead}>No transcript cues on this page.</p>
          <p className={ui.emptyNote}>
            The <code>stt</code> row in Provenance says whether one was produced.
          </p>
        </div>
        {/* A transcript that exists has cues behind this page: the way back.
            It steps off the end rather than off an offset the transcript never
            had, so one click reaches rows however far past the end this is. */}
        {total > 0 && first > 0 ? (
          <nav className={table.pager} aria-label="Transcript pages">
            <DashLink
              className={controls.ghostlink}
              href={cueLink(search, videoId, backFromEnd)}
              onClick={(event) => {
                if (!plain(event)) return;
                event.preventDefault();
                loadEarlier(backFromEnd);
              }}
            >
              ← Earlier
            </DashLink>
          </nav>
        ) : null}
      </Panel>
    );
  }

  return (
    <Panel id="transcript" title="Transcript">
      {first > 0 ? (
        <nav className={table.pager} aria-label="Transcript pages">
          <DashLink
            className={controls.ghostlink}
            href={cueLink(search, videoId, Math.max(first - size, 0))}
            onClick={(event) => {
              if (!plain(event)) return;
              event.preventDefault();
              loadEarlier(Math.max(first - size, 0));
            }}
          >
            ← Earlier
          </DashLink>
        </nav>
      ) : null}
      <div className={styles.cues} ref={top}>
        {paragraphsOf(cues).map((paragraph) => (
          <Para key={paragraph.cues[0].start_s} paragraph={paragraph} videoId={videoId} />
        ))}
        {busy ? <p className={styles.cueload}>loading</p> : null}
        {/* A real link at the server's offset, for a browser that never
            scrolls; scrolling near it reads the same batch in place. */}
        {hasMore && !busy ? (
          <DashLink
            className={`${controls.ghostlink} ${styles.cuemore}`}
            href={cueLink(search, videoId, first + cues.length)}
            onClick={(event) => {
              if (!plain(event)) return;
              event.preventDefault();
              loadMore();
            }}
          >
            Read on
          </DashLink>
        ) : null}
        <div aria-hidden="true" ref={end} />
      </div>
      {error ? (
        <p className={styles.panelNote}>
          {error instanceof DashboardError ? error.message : "The next batch did not arrive."}
        </p>
      ) : null}
    </Panel>
  );
}

/** One paragraph: its first second, linked, then its cues as running text,
 *  each titled with its own second. */
function Para({ paragraph, videoId }: { paragraph: Paragraph; videoId: string }) {
  const head = paragraph.cues[0];
  return (
    <div className={styles.para}>
      {/* `t` is the whole second the endpoint sends for the deeplink. */}
      <a
        className={styles.at}
        href={`https://youtu.be/${encodeURIComponent(videoId)}?t=${head.t}`}
        rel="noopener noreferrer"
        target="_blank"
      >
        {clock(head.start_s)}
      </a>
      <p className={styles.paratext}>
        {paragraph.speaker ? <span className={styles.speaker}>{paragraph.speaker} </span> : null}
        {paragraph.cues.map((cue, index) => (
          <Fragment key={`${cue.t}-${index}`}>
            {index ? " " : null}
            <span title={clock(cue.start_s)}>{cue.text}</span>
          </Fragment>
        ))}
      </p>
    </div>
  );
}
