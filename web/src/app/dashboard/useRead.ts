"use client";

import { useCallback, useEffect, useState } from "react";

// One read, in the browser, with its three outcomes named.
//
// The dashboard's pages fetch on mount rather than being rendered from data
// (Tom, 2026-09-05): Next serves a shell with nothing in it, the browser calls
// `/dashboard/api/*` with its session cookie, and this is the state machine
// every one of those pages needs — loading, the payload, or the typed refusal.
//
// `read` has to be stable across renders or the effect re-runs forever; the
// pages pass a module-level function, which is the simplest thing that is.

export type Read<T> =
  { status: "loading" } | { status: "ready"; data: T } | { status: "failed"; error: unknown };

export function useRead<T>(read: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<Read<T>>({ status: "loading" });
  // Bumped by `reload`, which is what the 429 countdown's retry does: there is
  // no route to refresh, because the data never came through the router.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    read(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ status: "ready", data });
      },
      (error: unknown) => {
        // An abort is this component going away, not a failure to report.
        if (!controller.signal.aborted) setState({ status: "failed", error });
      },
    );
    return () => controller.abort();
  }, [read, attempt]);

  // Back to `loading` here rather than at the top of the effect: an effect that
  // sets state synchronously is a cascading render, and this is the only path
  // that re-runs the read anyway.
  const reload = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);
  return { ...state, reload };
}
