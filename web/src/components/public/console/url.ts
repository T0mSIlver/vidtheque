// The console's shareable state. Search is `?q=…&type=…` (no `type` for all);
// a loaded question is `?ask=…` and never fires on load. A bare path is the
// default mode: ask where the deployment has one (demo-site.md §6.2).
import type { ContentType } from "@/lib/api/schemas";

export type Mode = "search" | "ask";

export interface Snapshot {
  mode: Mode;
  q: string;
  type: ContentType;
}

const CHANNELS: readonly ContentType[] = ["all", "transcript", "ocr", "frame"];

function isChannel(value: unknown): value is ContentType {
  return typeof value === "string" && (CHANNELS as readonly string[]).includes(value);
}

type Params = URLSearchParams | Record<string, string | string[] | undefined>;

function first(params: Params, key: string): string | undefined {
  if (params instanceof URLSearchParams) return params.get(key) ?? undefined;
  const value = params[key];
  return Array.isArray(value) ? value[0] : value;
}

export function parseSnapshot(params: Params, askEnabled: boolean): Snapshot {
  const ask = first(params, "ask");
  const q = first(params, "q");
  const raw = first(params, "type");
  const type = isChannel(raw) ? raw : "all";
  if (ask !== undefined && askEnabled) return { mode: "ask", q: ask.trim(), type: "all" };
  // A question on a deployment with no ask is searched instead of dropped.
  if (q !== undefined || ask !== undefined) {
    return { mode: "search", q: (q ?? ask ?? "").trim(), type };
  }
  return { mode: askEnabled ? "ask" : "search", q: "", type: "all" };
}

/** The query string for a snapshot, `""` when it is the page's default. */
export function serializeSnapshot(snapshot: Snapshot, askEnabled: boolean): string {
  const params = new URLSearchParams();
  if (snapshot.mode === "ask") {
    if (snapshot.q) params.set("ask", snapshot.q);
  } else {
    // An empty search still has to say "search" where ask is the default.
    if (snapshot.q || askEnabled || snapshot.type !== "all") params.set("q", snapshot.q);
    if (snapshot.type !== "all") params.set("type", snapshot.type);
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function sameSnapshot(a: Snapshot, b: Snapshot): boolean {
  return a.mode === b.mode && a.q === b.q && (a.mode === "ask" || a.type === b.type);
}
