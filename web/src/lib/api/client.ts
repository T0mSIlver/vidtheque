// The one module that reads bytes off the Python API. Everything above it
// works with parsed, typed values and a typed error.
//
// Built as a factory so a test can hand it a fake `fetch` and a fixed base
// URL; the app's instance lives in ./index.ts, which also marks the whole
// package server-only.
import type { ZodType } from "zod";
import {
  type ContentType,
  ErrorEnvelope,
  Meta,
  SearchResponse,
  VideosResponse,
} from "./schemas";

export class ApiError extends Error {
  readonly status: number;
  /** The facade's `E_*` code, or `E_HTTP` when the body was not an envelope. */
  readonly code: string;
  /** The facade's "what to do next" line, when it sent one. */
  readonly next?: string;
  /** Seconds, from `Retry-After`, on a 429 or 503. */
  readonly retryAfter?: number;

  constructor(status: number, envelope: Partial<ErrorEnvelope>, retryAfter?: number) {
    super(envelope.message ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = envelope.error ?? "E_HTTP";
    this.next = envelope.next;
    this.retryAfter = retryAfter;
  }
}

type Params = Record<string, string | number | undefined>;

export interface RequestOptions {
  /** The visitor's address, forwarded under the header the API trusts, so the
   *  per-IP rate limiter keys on the visitor and not on this server. */
  clientIp?: string;
  signal?: AbortSignal;
  /** Next's fetch extensions, e.g. `{ revalidate: 60 }`. */
  next?: NextFetchRequestConfig;
  cache?: RequestCache;
}

export interface ClientConfig {
  baseUrl: string;
  /** Header name the API reads the client IP from (its default is Cloudflare's). */
  clientIpHeader?: string;
  fetch?: typeof fetch;
}

export function createClient(config: ClientConfig) {
  const base = config.baseUrl.replace(/\/+$/, "");
  const ipHeader = config.clientIpHeader ?? "CF-Connecting-IP";
  const doFetch = config.fetch ?? fetch;

  async function get<T>(path: string, params: Params, schema: ZodType<T>, opts: RequestOptions = {}): Promise<T> {
    const url = new URL(base + path);
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
    }
    const headers = new Headers({ accept: "application/json" });
    if (opts.clientIp) headers.set(ipHeader, opts.clientIp);

    const res = await doFetch(url, {
      headers,
      signal: opts.signal,
      cache: opts.cache,
      next: opts.next,
    });
    if (!res.ok) throw await toError(res);
    return schema.parse(await res.json());
  }

  return {
    search(
      params: { q: string; content_type?: ContentType; limit?: number; offset?: number; channel?: string; video_id?: string },
      opts?: RequestOptions,
    ) {
      return get("/api/search", params, SearchResponse, opts);
    },
    videos(params: { q?: string; channel?: string; limit?: number; offset?: number } = {}, opts?: RequestOptions) {
      return get("/api/videos", params, VideosResponse, opts);
    },
    meta(opts?: RequestOptions) {
      return get("/api/meta", {}, Meta, opts);
    },
  };
}

export type Client = ReturnType<typeof createClient>;

async function toError(res: Response): Promise<ApiError> {
  const retryAfter = Number(res.headers.get("retry-after")) || undefined;
  let envelope: Partial<ErrorEnvelope> = {};
  try {
    const parsed = ErrorEnvelope.safeParse(await res.json());
    if (parsed.success) envelope = parsed.data;
  } catch {
    // A non-JSON body (a proxy's HTML 502, say) is still an ApiError, just a
    // bare one.
  }
  return new ApiError(res.status, envelope, retryAfter);
}
