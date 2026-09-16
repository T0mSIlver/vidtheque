import { ROOT } from "@/lib/dashboard/client";
import { pick, withQuery } from "@/lib/dashboard/query";
import type { Library, LibraryFilters } from "@/lib/dashboard/schemas";
import { day } from "@/lib/format";

// The videos table's URL: what the read takes, what the band holds, and what
// every link carries (dashboard.md §5.2, §20).

export const FILTERS = [
  "q",
  "channel",
  "tags",
  "index_state",
  "has",
  "published_after",
  "published_before",
  "indexed_after",
  "indexed_before",
  "order",
  "limit",
] as const;

const PAGE_KEYS = [...FILTERS, "offset"];

/** `carried()`'s nine keys, empty or not: a key that vanishes when its box is
 *  empty makes two URLs for one query. */
export const CARRIED = [
  "q",
  "channel",
  "tags",
  "has",
  "index_state",
  "published_after",
  "published_before",
  "indexed_after",
  "indexed_before",
] as const;

export const DATE_KEYS = [
  "published_after",
  "published_before",
  "indexed_after",
  "indexed_before",
] as const;
export type DateKey = (typeof DATE_KEYS)[number];

/** Pickers resting on the API's own default are left off a link. */
export const DEFAULTS: Record<string, string> = { index_state: "all", has: "any" };

const DAY_S = 86_400;

/** Every control's value and every link's, after the server has had its say. */
export type Band = Record<(typeof FILTERS)[number], string>;

export function apiQuery(search: string | URLSearchParams): URLSearchParams {
  return pick(search, PAGE_KEYS);
}

/**
 * What the query ran with: the answer's `filters` (or a refusal's echo), the
 * URL until either lands. Dates are the server's resolved days; `_before` is
 * exclusive, so it reads back a day earlier. `tags` has no string echo and
 * stays the URL's.
 */
export function bandOf(params: URLSearchParams, data?: Library, resolved?: LibraryFilters): Band {
  const raw = (key: string, fallback = "") => params.get(key)?.trim() || fallback;
  const filters = data?.filters ?? resolved;

  const dates = {} as Record<DateKey, string>;
  for (const key of DATE_KEYS) {
    if (!filters) {
      dates[key] = raw(key);
      continue;
    }
    const echoed = filters[key];
    const asked = echoed === null ? null : key.endsWith("_before") ? echoed - DAY_S : echoed;
    // A floor of one second: `day` prints the dash for a falsy stamp.
    dates[key] = asked === null ? "" : day(Math.max(asked, 1));
  }

  return {
    q: filters ? (filters.q ?? "") : raw("q"),
    channel: filters ? (filters.channel ?? "") : raw("channel"),
    tags: raw("tags"),
    index_state: filters ? filters.index_state : raw("index_state", "all"),
    has: filters ? filters.has : raw("has", "any"),
    ...dates,
    order: data ? data.order : raw("order"),
    limit: data ? String(data.pagination.limit) : raw("limit"),
  };
}

/** The unchanging part of every link: the nine keys, the order, the limit and
 *  the offset. */
export function carriedOf(band: Band, offset: string): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of CARRIED) {
    if (band[key] === DEFAULTS[key]) continue;
    params.set(key, band[key]);
  }
  if (band.order) params.set("order", band.order);
  if (band.limit) params.set("limit", band.limit);
  if (offset.trim()) params.set("offset", offset.trim());
  return params;
}

/** The carried query with some parameters changed; `null` removes one. */
export function linkTo(carried: URLSearchParams, changes: Record<string, string | null>): string {
  const next = new URLSearchParams(carried);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null) next.delete(key);
    else next.set(key, value);
  }
  return withQuery(`${ROOT}/videos`, next);
}

/** A band submission as a URL: the nine keys, minus defaults, then order and
 *  limit; never the offset, since a new filter is a new set. */
export function bandUrl(form: FormData): string {
  const next = new URLSearchParams();
  const chosen = (key: string) => {
    const entry = form.get(key);
    return typeof entry === "string" ? entry.trim() : "";
  };
  for (const key of CARRIED) {
    const value = chosen(key);
    if (value !== DEFAULTS[key]) next.set(key, value);
  }
  for (const key of ["order", "limit"]) {
    const value = chosen(key);
    if (value) next.set(key, value);
  }
  return withQuery(`${ROOT}/videos`, next);
}
