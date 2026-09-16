"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DashboardError } from "@/lib/dashboard/client";
import { useSession } from "../session";

/** One write, in the four states a control draws. */
export type Write<T> =
  | { status: "idle" }
  | { status: "sending" }
  | { status: "done"; outcome: T }
  | { status: "failed"; error: unknown };

/**
 * A control that POSTs and shows what came back, inline (dashboard.md §21).
 * `run(fields)` is ignored while a write is out, so a control can stay
 * focusable with `aria-disabled` instead of dropping focus with `disabled`.
 */
export function useWrite<T, F = void>(
  send: (fields: F) => Promise<T>,
  onDone?: (outcome: T) => void,
) {
  const [state, setState] = useState<Write<T>>({ status: "idle" });
  const latest = useRef({ send, onDone });
  const sending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    latest.current = { send, onDone };
  });
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const run = useCallback((fields: F) => {
    if (sending.current) return;
    sending.current = true;
    setState({ status: "sending" });
    latest.current.send(fields).then(
      (outcome) => {
        sending.current = false;
        if (!mounted.current) return;
        setState({ status: "done", outcome });
        latest.current.onDone?.(outcome);
      },
      (error: unknown) => {
        sending.current = false;
        if (mounted.current) setState({ status: "failed", error });
      },
    );
  }, []);

  return [state, run] as const;
}

/** A submitted form as the fields Python reads: an unticked box is absent, as
 *  in a browser's own submission, and nothing is corrected on the way out. */
export function formFields(form: HTMLFormElement): Record<string, string> {
  const fields: Record<string, string> = {};
  for (const [name, value] of new FormData(form).entries()) {
    if (typeof value === "string") fields[name] = value;
  }
  return fields;
}

export type Refusal = { code: string; message: string; next?: string };

/** Why something was refused, in the API's own words (policy text). */
export function refusalOf(error: unknown): Refusal {
  if (error instanceof DashboardError) {
    return { code: error.code, message: error.message, next: error.next };
  }
  return {
    code: "E_UNREACHABLE",
    message: error instanceof Error ? error.message : "The write did not reach the instance.",
  };
}

/** The three channel sets the index form and the follow form both tick
 *  (`dashboard/writes.py`'s `CHANNEL_BOXES`). */
export const CHANNEL_BOXES: [string, string, string][] = [
  ["transcript", "Transcript", "what was said, from the audio or the captions"],
  ["ocr", "On-screen text", "what the frames read, per keyframe"],
  ["frames", "Frame embeddings", "visual search over the keyframes"],
];

/**
 * `rendered`: the write routes are registered, so a control exists at all — a
 * button that 404s is worse than none (dashboard.md §2.3, §18.1).
 * `indexable`: the database takes writes that feed `index_video`; it disables
 * rather than removes.
 */
export function useWriteSide(): { rendered: boolean; indexable: boolean } {
  const session = useSession();
  return {
    rendered: Boolean(session?.write_side),
    indexable: Boolean(session?.writes_allowed),
  };
}

/**
 * A ref for what a write answered: it takes focus when the control that made
 * it lost focus (it was replaced) or still holds it, so the keyboard lands on
 * the answer rather than on `<body>`.
 */
export function focusOnArrival(element: HTMLElement | null): void {
  if (!element) return;
  const active = document.activeElement;
  const scope = element.closest("[data-write]");
  if (!active || active === document.body || (scope && scope.contains(active))) {
    element.focus({ preventScroll: true });
  }
}
