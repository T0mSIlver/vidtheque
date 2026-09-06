// The search read. Deliberately NOT cached: a query is one visitor's request
// against a per-IP rate limit, and a shared cache entry would let one
// visitor's page answer another's, or hide a 429 that was that visitor's own.
// It runs at request time, inside a <Suspense> boundary, with the visitor's
// address forwarded so the API's limiter keys on them and not on this server.
import { cache } from "react";
import { headers } from "next/headers";
import { api, ApiError, type ContentType, type SearchResponse } from "@/lib/api";
import type { MetaOutcome } from "@/lib/api/meta";

export const SEARCH_PAGE = 10;

// What the demo waits when a 429 arrived with nothing to say how long. 60s is
// the limiter's own minute and the number `app.js` used; the 30 that stood here
// was half of it, so a page that had been refused invited a second refusal.
export const RETRY_FALLBACK = 60;

// The four ways a search ends, because the page draws four different screens.
// A failed read is a *state*, not a thrown render: the facade's `message` and
// `next` are the only account a visitor gets of why, and an error boundary
// replaces them with one generic line (demo-site.md §6.1).
export type SearchOutcome =
  | { kind: "ok"; page: SearchResponse }
  | { kind: "rate_limited"; retryAfter: number }
  | { kind: "refused"; message: string; next?: string }
  | { kind: "unreachable" };

export async function searchCorpus(params: {
  q: string;
  type: ContentType;
  offset: number;
}): Promise<SearchOutcome> {
  try {
    const page = await api().search(
      { q: params.q, content_type: params.type, limit: SEARCH_PAGE, offset: params.offset },
      { clientIp: await visitorIp() },
    );
    return { kind: "ok", page };
  } catch (err) {
    // A 429 is a state the page renders, with the limiter's own countdown; a
    // typed refusal is a state too, in the facade's own words. Anything else is
    // the server not answering at all.
    if (err instanceof ApiError && err.status === 429) {
      return { kind: "rate_limited", retryAfter: err.retryAfter ?? RETRY_FALLBACK };
    }
    if (err instanceof ApiError) {
      return { kind: "refused", message: err.message || "Search failed.", next: err.next };
    }
    return { kind: "unreachable" };
  }
}

// The boot call. The rail, the page and the connect panel each need it and none
// of them can hand it to the others, so it is `cache`d for the life of one
// render — and only that long: `/api/meta` shares the search bucket, so one
// visitor can be refused where another is not, and a cache that outlived the
// request would answer one of them with the other's page (demo-site.md §2.3).
export const readMeta = cache(async (): Promise<MetaOutcome> => {
  try {
    return { kind: "ok", meta: await api().meta({ clientIp: await visitorIp() }) };
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) return { kind: "rate_limited" };
    return { kind: "unreachable" };
  }
});

// The visitor's address as the edge reports it: the configured header first
// (Cloudflare's by default, the same name the API trusts), else the first hop
// of X-Forwarded-For, else nothing and the API falls back to the socket peer.
export async function visitorIp(): Promise<string | undefined> {
  const h = await headers();
  const configured = process.env.VIDTHEQUE_CLIENT_IP_HEADER ?? "CF-Connecting-IP";
  const direct = h.get(configured);
  if (direct) return direct;
  const forwarded = h.get("x-forwarded-for");
  return forwarded?.split(",")[0]?.trim() || undefined;
}
