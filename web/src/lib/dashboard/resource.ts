"use client";

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { DashboardError } from "./client";

// Every dashboard read, behind one stale-while-revalidate cache keyed by the
// request. The pages still fetch in the browser (DECISIONS.md, 2026-09-05); the
// cache is what lets a revisit, Back and Forward paint the last payload at once
// and revalidate underneath it (dashboard.md §5).

/** A read younger than this is not asked again when a page mounts on it. */
const FRESH_MS = 2_000;
/** Entries kept for the life of the document; the oldest idle ones go first. */
const MAX_ENTRIES = 64;
/** `Retry-After`-less 429s wait this long, in seconds. */
const FALLBACK_RETRY_S = 60;

export type Reader<T> = (signal: AbortSignal) => Promise<T>;

export interface ResourceState<T> {
  /** The last payload for this key: this visit's, or an earlier one's. */
  data: T | undefined;
  /** The last refusal for this key, cleared by the next answer. */
  error: unknown;
  /** No answer yet for this key, or a request for it is out. */
  isPending: boolean;
  /** `data` came from an earlier visit and this one has not re-read it yet. */
  isStale: boolean;
}

export interface Resource<T> extends ResourceState<T> {
  /** Ask again now, keeping `data` on screen meanwhile. */
  reload: () => void;
}

export interface ResourceOptions<T> {
  /** The delay before the next read of a payload, or `null` to stop. */
  pollMs?: (data: T) => number | null;
}

interface Entry {
  data: unknown;
  error: unknown;
  hasData: boolean;
  landedAt: number;
  stale: boolean;
  controller: AbortController | null;
  timer: ReturnType<typeof setTimeout> | null;
  /** When a 429's `Retry-After` runs out; 0 when no refusal is being waited out. */
  retryAt: number;
  release: ReturnType<typeof setTimeout> | null;
  subscribers: number;
  listeners: Set<() => void>;
  read: Reader<unknown> | null;
  poll: ((data: unknown) => number | null) | null;
  snapshot: ResourceState<unknown>;
}

const PENDING: ResourceState<never> = {
  data: undefined,
  error: undefined,
  isPending: true,
  isStale: false,
};

const entries = new Map<string, Entry>();

function entryOf(key: string): Entry {
  let entry = entries.get(key);
  if (!entry) {
    entry = {
      data: undefined,
      error: undefined,
      hasData: false,
      landedAt: 0,
      stale: false,
      controller: null,
      timer: null,
      retryAt: 0,
      release: null,
      subscribers: 0,
      listeners: new Set(),
      read: null,
      poll: null,
      snapshot: PENDING,
    };
    entries.set(key, entry);
    evict();
  }
  return entry;
}

function evict() {
  if (entries.size <= MAX_ENTRIES) return;
  for (const [key, entry] of entries) {
    if (entries.size <= MAX_ENTRIES) return;
    if (entry.subscribers === 0 && !entry.controller) entries.delete(key);
  }
}

function publish(entry: Entry) {
  entry.snapshot = {
    data: entry.data,
    error: entry.error,
    isPending: entry.controller !== null || (!entry.hasData && entry.error === undefined),
    isStale: entry.hasData && entry.stale,
  };
  for (const listener of entry.listeners) listener();
}

function clearTimer(entry: Entry) {
  if (entry.timer !== null) clearTimeout(entry.timer);
  entry.timer = null;
}

function schedule(entry: Entry, ms: number) {
  clearTimer(entry);
  entry.timer = setTimeout(() => {
    entry.timer = null;
    load(entry);
  }, ms);
}

function load(entry: Entry) {
  if (entry.controller || !entry.read || entry.subscribers === 0) return;
  clearTimer(entry);
  const controller = new AbortController();
  entry.controller = controller;
  publish(entry);
  entry.read(controller.signal).then(
    (data) => {
      if (controller.signal.aborted) return;
      entry.controller = null;
      entry.data = data;
      entry.hasData = true;
      entry.error = undefined;
      entry.retryAt = 0;
      entry.stale = false;
      entry.landedAt = Date.now();
      const next = entry.poll?.(data) ?? null;
      if (next !== null) schedule(entry, next);
      publish(entry);
    },
    (error: unknown) => {
      // An abort is the page leaving, not a failure to report.
      if (controller.signal.aborted) return;
      entry.controller = null;
      entry.error = error;
      // The limiter names when to come back; every other refusal stops here.
      if (error instanceof DashboardError && error.status === 429) {
        const ms = (error.retryAfter ?? FALLBACK_RETRY_S) * 1000;
        entry.retryAt = Date.now() + ms;
        schedule(entry, ms);
      }
      publish(entry);
    },
  );
}

function subscribe(key: string, listener: () => void): () => void {
  const entry = entryOf(key);
  entry.listeners.add(listener);
  entry.subscribers += 1;
  if (entry.release !== null) {
    // Re-subscribed inside the grace period: StrictMode's second effect pass,
    // or a key that came straight back.
    clearTimeout(entry.release);
    entry.release = null;
  } else if (entry.subscribers === 1) {
    const fresh = entry.hasData && Date.now() - entry.landedAt < FRESH_MS;
    if (entry.hasData && !fresh) {
      entry.stale = true;
      publish(entry);
    }
    const waitMs = entry.retryAt - Date.now();
    if (!fresh && !entry.controller && waitMs > 0) schedule(entry, waitMs);
    else if (!fresh && !entry.controller) queueMicrotask(() => load(entry));
    else if (fresh && entry.poll && entry.timer === null && entry.hasData) {
      const next = entry.poll(entry.data);
      if (next !== null) schedule(entry, next);
    }
  }
  return () => {
    entry.listeners.delete(listener);
    entry.subscribers -= 1;
    if (entry.subscribers > 0) return;
    entry.release = setTimeout(() => {
      entry.release = null;
      if (entry.subscribers > 0) return;
      clearTimer(entry);
      if (entry.controller) {
        entry.controller.abort();
        entry.controller = null;
        entry.snapshot = { ...entry.snapshot, isPending: !entry.hasData };
      }
    }, 0);
  };
}

if (typeof document !== "undefined") {
  // A poll waiting on its timer reads again the moment the tab is back; a 429's
  // Retry-After is still waited out.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    const now = Date.now();
    for (const entry of entries.values()) {
      if (entry.subscribers > 0 && entry.timer !== null && entry.poll && entry.retryAt <= now) {
        load(entry);
      }
    }
  });
}

/**
 * One read, keyed by what it asks for. `read` must be determined by `key`: the
 * latest one is used, but a new function alone does not re-read.
 */
export function useResource<T>(
  key: string,
  read: Reader<T>,
  options: ResourceOptions<T> = {},
): Resource<T> {
  const latest = useRef({ read, poll: options.pollMs });
  useEffect(() => {
    latest.current = { read, poll: options.pollMs };
  });

  const listen = useCallback(
    (listener: () => void) => {
      const entry = entryOf(key);
      entry.read = (signal) => latest.current.read(signal);
      entry.poll = latest.current.poll ? (data) => latest.current.poll?.(data as T) ?? null : null;
      return subscribe(key, listener);
    },
    [key],
  );
  const snapshot = useSyncExternalStore(
    listen,
    () => entries.get(key)?.snapshot ?? PENDING,
    () => PENDING,
  ) as ResourceState<T>;

  const reload = useCallback(() => {
    const entry = entries.get(key);
    if (!entry) return;
    entry.error = undefined;
    clearTimer(entry);
    if (entry.controller) publish(entry);
    else load(entry);
  }, [key]);

  return { ...snapshot, reload };
}

/** Is this refusal the limiter's, which the cache is already waiting out? */
export function isRateLimited(error: unknown): error is DashboardError {
  return error instanceof DashboardError && error.status === 429;
}

/** Forget every read. Tests start from an empty cache. */
export function clearResources(): void {
  for (const entry of entries.values()) {
    clearTimer(entry);
    if (entry.release !== null) clearTimeout(entry.release);
    entry.controller?.abort();
  }
  entries.clear();
}
