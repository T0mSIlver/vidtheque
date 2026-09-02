// The search read. Deliberately NOT cached: a query is one visitor's request
// against a per-IP rate limit, and a shared cache entry would let one
// visitor's page answer another's, or hide a 429 that was that visitor's own.
// It runs at request time, inside a <Suspense> boundary, with the visitor's
// address forwarded so the API's limiter keys on them and not on this server.
import { headers } from "next/headers";
import { api, ApiError, type ContentType, type SearchResponse } from "@/lib/api";

export const SEARCH_PAGE = 10;

export type SearchOutcome =
  { kind: "ok"; page: SearchResponse } | { kind: "rate_limited"; retryAfter: number };

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
    // A 429 is a state the page renders, with the limiter's own countdown.
    // Anything else is a real failure and reaches the error boundary.
    if (err instanceof ApiError && err.status === 429) {
      return { kind: "rate_limited", retryAfter: err.retryAfter ?? 30 };
    }
    throw err;
  }
}

// The visitor's address as the edge reports it: the configured header first
// (Cloudflare's by default, the same name the API trusts), else the first hop
// of X-Forwarded-For, else nothing and the API falls back to the socket peer.
async function visitorIp(): Promise<string | undefined> {
  const h = await headers();
  const configured = process.env.VIDTHEQUE_CLIENT_IP_HEADER ?? "CF-Connecting-IP";
  const direct = h.get(configured);
  if (direct) return direct;
  const forwarded = h.get("x-forwarded-for");
  return forwarded?.split(",")[0]?.trim() || undefined;
}
