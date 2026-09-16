"use client";

import { useSyncExternalStore } from "react";
import { IndexOutcome } from "@/lib/dashboard/schemas";

// The last submission's receipt, kept for this tab so a reload still shows it
// (dashboard.md §5.5). Read as an external store: the server snapshot is "no
// receipt", and the browser's first commit brings in what is stored.

const RECEIPT_KEY = "vidtheque:index:receipt";

const listeners = new Set<() => void>();
let lastRaw: string | null = null;
let lastValue: IndexOutcome | null = null;

function stored(): IndexOutcome | null {
  let raw: string | null = null;
  try {
    raw = window.sessionStorage.getItem(RECEIPT_KEY);
  } catch {
    // Storage refused: the receipt only lives while it is on screen.
  }
  // Same raw string, same object, or the store re-renders forever.
  if (raw === lastRaw) return lastValue;
  lastRaw = raw;
  lastValue = null;
  if (raw) {
    try {
      const parsed = IndexOutcome.safeParse(JSON.parse(raw));
      lastValue = parsed.success ? parsed.data : null;
    } catch {
      lastValue = null;
    }
  }
  return lastValue;
}

/** Keep this outcome, or drop what is kept (a new submit drops the old one). */
function keep(outcome: IndexOutcome | null): void {
  try {
    if (outcome) window.sessionStorage.setItem(RECEIPT_KEY, JSON.stringify(outcome));
    else window.sessionStorage.removeItem(RECEIPT_KEY);
  } catch {
    // Nothing to keep it in.
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useStoredReceipt(): [IndexOutcome | null, (outcome: IndexOutcome | null) => void] {
  return [useSyncExternalStore(subscribe, stored, () => null), keep];
}
