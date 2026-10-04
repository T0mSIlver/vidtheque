// Request-time reads for the public pages. Never shared across visitors: each
// read spends that visitor's rate-limit bucket, forwarded by their address.
import { cache } from "react";
import { headers } from "next/headers";
import { api, ApiError, type ContentType, type FeedResponse } from "@/lib/api";
import type { MetaOutcome } from "@/lib/api/meta";
import { RETRY_FALLBACK, SEARCH_PAGE, type SearchOutcome } from "@/lib/api/outcome";

export async function searchCorpus(params: {
  q: string;
  type: ContentType;
  offset?: number;
  tags?: string;
}): Promise<SearchOutcome> {
  try {
    const page = await api().search(
      {
        q: params.q,
        content_type: params.type,
        limit: SEARCH_PAGE,
        offset: params.offset ?? 0,
        tags: params.tags,
      },
      { clientIp: await visitorIp() },
    );
    return { kind: "ok", page };
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) {
      return { kind: "rate_limited", retryAfter: err.retryAfter ?? RETRY_FALLBACK };
    }
    if (err instanceof ApiError) {
      return { kind: "refused", message: err.message || "Search failed.", next: err.next };
    }
    return { kind: "unreachable" };
  }
}

// Cached for one render only: `/api/meta` shares the search bucket, so one
// visitor's refusal must never answer another's page (demo-site.md §2.3).
export const readMeta = cache(async (): Promise<MetaOutcome> => {
  try {
    return { kind: "ok", meta: await api().meta({ clientIp: await visitorIp() }) };
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) return { kind: "rate_limited" };
    return { kind: "unreachable" };
  }
});

/** The configured edge header first, else the first X-Forwarded-For hop. */
export async function visitorIp(): Promise<string | undefined> {
  const h = await headers();
  const configured = process.env.VIDTHEQUE_CLIENT_IP_HEADER ?? "CF-Connecting-IP";
  const direct = h.get(configured);
  if (direct) return direct;
  const forwarded = h.get("x-forwarded-for");
  return forwarded?.split(",")[0]?.trim() || undefined;
}

export type FeedOutcome =
  | { kind: "ok"; feed: FeedResponse }
  | { kind: "rate_limited"; retryAfter: number }
  | { kind: "unreachable" };

/** The sample feed (demo-site.md §8.2); a refusal reads as unreachable, the page has no fix for it. */
export async function readFeed(params: {
  tags?: string;
  limit?: number;
  offset?: number;
}): Promise<FeedOutcome> {
  try {
    return { kind: "ok", feed: await api().feed(params, { clientIp: await visitorIp() }) };
  } catch (err) {
    if (err instanceof ApiError && err.status === 429) {
      return { kind: "rate_limited", retryAfter: err.retryAfter ?? RETRY_FALLBACK };
    }
    return { kind: "unreachable" };
  }
}
