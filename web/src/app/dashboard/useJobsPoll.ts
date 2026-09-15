"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DashboardError } from "@/lib/dashboard/client";

// The jobs view's 2 s tick, in React — `static/jobs.js`, minus the DOM patching.
//
// **Polling, not SSE**, and the argument is dashboard.md §5.4's: a long-lived
// connection per open tab, against a single-process server that also holds the
// only SQLite writer, for a page watched for minutes a week, is a lifecycle
// problem bought with nothing.
//
// What is the same as the script it replaces, deliberately:
//
// * the cadence is **the payload's** `poll_ms`, floored at a second here the
//   way `jobs.js` floors it. The server clamps the rate limiter around it too,
//   so a stuck tab cannot become a load generator;
// * `live` is the stop condition. When nothing is `queued|running` there is
//   nothing to poll for and the tab stops;
// * a hidden tab is **not** paused — `jobs.js` does not pause one either. What
//   it does, and this does, is re-read on the way *back*: a countdown is wrong
//   the moment the tab was backgrounded or the page came out of the bfcache,
//   and the fix is a fresh reading rather than a timer that kept running.
//
// What is new, and why. `jobs.js` stops on *any* refusal, which is right for a
// script whose page is already a complete server-rendered snapshot. This page
// has no snapshot — the rows are the payload — so the two refusals a reader can
// wait out are told apart:
//
// * **`429`** is the limiter saying when to come back, so this comes back then:
//   the delay is `Retry-After`'s, never one invented here.
// * **`401`** is the session going away, and it stops for good. The client has
//   already sent the browser to the sign-in page where there is one.
//
// Everything else — a 500, a shape change, the box going offline — stops, and
// the last payload stays on screen with the refusal beside it. A table that
// silently freezes is the thing to avoid; a table that says it stopped is not.

const MIN_TICK_MS = 1_000;
const FALLBACK_TICK_MS = 2_000;
/** What `Retry-After`-less 429 waits, matching `ReadFailure`'s own default. */
const FALLBACK_BACKOFF_S = 60;

/** The two fields a poll target has to carry for this hook to drive it. */
export type Live = { live: boolean; poll_ms: number };

/** The three outcomes, discriminated on `status` so a page that has checked it
 *  is holding a payload rather than a maybe.
 *
 *  `failed` is only when *nothing* has landed yet: a page that had rows keeps
 *  them through a refusal, with the refusal beside them. */
type Outcome<T> =
  | { status: "loading"; data: null }
  | { status: "ready"; data: T }
  | { status: "failed"; data: null };

export type Poll<T> = Outcome<T> & {
  /** The refusal that stopped or delayed the tick, or `null`. It outlives the
   *  tick that produced it, because it is the answer to "why has this stopped
   *  moving" — which is a question a reader asks a minute later. */
  error: unknown;
  /** Is another tick coming? `false` once everything is terminal, and once a
   *  refusal stopped it. */
  polling: boolean;
  /** Did *this view* watch it run? `false` on a page that opened on something
   *  already terminal, and `true` from the first reading that was live onwards.
   *
   *  It is the difference between a page whose reading went stale under the
   *  reader and a page that was a record when they opened it, which is what
   *  `job.html` kept its final-record note hidden for: a week-old job told
   *  nobody their view had gone out of date. Tracked here rather than in a ref
   *  on the page, because a ref read during a render is a value React is free
   *  to have not re-rendered for. */
  wasLive: boolean;
  reload: () => void;
};

export function useJobsPoll<T extends Live>(read: (signal: AbortSignal) => Promise<T>): Poll<T> {
  const [state, setState] = useState<
    Outcome<T> & { error: unknown; polling: boolean; wasLive: boolean }
  >({
    status: "loading",
    data: null,
    error: null,
    polling: true,
    wasLive: false,
  });
  // Bumped by `reload`, which re-runs the effect from the top: the payload
  // never came through the router, so there is no route to refresh.
  const [attempt, setAttempt] = useState(0);
  // The tick reads the last payload to decide whether to keep going without
  // making the payload a dependency of the effect — an effect that re-subscribes
  // on every poll would cancel the request it just made.
  const latest = useRef<T | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    function stop() {
      stopped = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
    }

    function later(ms: number) {
      if (stopped || controller.signal.aborted) return;
      timer = setTimeout(() => void tick(), ms);
    }

    async function tick() {
      timer = null;
      try {
        const data = await read(controller.signal);
        if (controller.signal.aborted) return;
        latest.current = data;
        setState((was) => ({
          status: "ready",
          data,
          error: null,
          polling: data.live,
          wasLive: was.wasLive || data.live,
        }));
        if (data.live) later(Math.max(MIN_TICK_MS, data.poll_ms || FALLBACK_TICK_MS));
        else stop();
      } catch (error) {
        // An abort is this component going away, not a failure to report.
        if (controller.signal.aborted) return;
        const refusal = error instanceof DashboardError ? error : null;
        const held = latest.current;
        const backingOff = refusal?.status === 429;
        setState((was) =>
          held
            ? { status: "ready", data: held, error, polling: backingOff, wasLive: was.wasLive }
            : { status: "failed", data: null, error, polling: backingOff, wasLive: was.wasLive },
        );
        if (backingOff)
          later(Math.max(MIN_TICK_MS, (refusal.retryAfter ?? FALLBACK_BACKOFF_S) * 1000));
        else stop();
      }
    }

    // The countdown and the wall clock are arithmetic on numbers the last
    // payload carried, so both are wrong by however long the tab was away.
    // A fresh reading is the answer, not a timer that ran while nobody looked.
    function onVisible() {
      if (document.hidden || stopped || timer === null) return;
      clearTimeout(timer);
      timer = null;
      void tick();
    }

    void tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      stop();
      controller.abort();
    };
  }, [read, attempt]);

  const reload = useCallback(() => {
    latest.current = null;
    setState({ status: "loading", data: null, error: null, polling: true, wasLive: false });
    setAttempt((n) => n + 1);
  }, []);

  // Rebuilt per branch rather than spread: spreading the union at once
  // collapses `status` back into all three words, and the whole point of the
  // discriminant is that a page which has checked it is holding a payload.
  const rest = { error: state.error, polling: state.polling, wasLive: state.wasLive, reload };
  return state.status === "ready"
    ? { ...rest, status: "ready", data: state.data }
    : { ...rest, status: state.status, data: null };
}
