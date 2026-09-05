// The dashboard's reads, from the browser.
//
// This is the one module in `web/` that talks to Python from the *client*, and
// it is deliberately not `lib/api`'s pattern. That client is server-only: it
// holds a base URL, forwards the visitor's address and reads the public
// facade. This one reads `/dashboard/api/*` **same-origin with the session
// cookie**, which is the whole design (frontend-migration.md §1a, and Tom,
// 2026-09-05): Next serves a data-free shell, the browser carries the cookie,
// and Next never sees it, so there is nothing per-user for this server to
// cache or to leak between two readers of the same page.
//
// Three rules live here so no page has to keep them:
//
// * `credentials: "same-origin"` and `cache: "no-store"`. The payloads describe
//   state that changes under the reader and Python already says `no-store`;
//   the request says it too, so a back/forward navigation re-reads rather than
//   painting a stale count.
// * A typed error carrying the status, the refusal code, the `next:` line and
//   `Retry-After`. The pages render the API's own message — policy text stays
//   Python's (§1 decision 5).
// * **The 401, in one place.** A refused read sends the browser to Python's
//   sign-in page with somewhere to come back to, and the page renders its
//   signed-out state meanwhile.
import type { ZodType } from "zod";
import {
  CuePage,
  Ledger,
  Library,
  Overview,
  PartialRefusal,
  Session,
  VideoDetail,
} from "./schemas";

/** `/dashboard`, this surface's root on both servers. */
export const ROOT = "/dashboard";

export class DashboardError extends Error {
  readonly status: number;
  /** The refusal's `E_*` code, or `E_HTTP` when the body was not an envelope. */
  readonly code: string;
  /** The refusal's "what to do next" line, when it sent one. */
  readonly next?: string;
  /** Seconds, from `Retry-After`, on a 429 or a 503. */
  readonly retryAfter?: number;

  constructor(status: number, envelope: PartialRefusal, retryAfter?: number) {
    super(envelope.message ?? `HTTP ${status}`);
    this.name = "DashboardError";
    this.status = status;
    this.code = envelope.error ?? "E_HTTP";
    // The wire says `null` for "no next step"; callers ask `err.next ? …`, so
    // the two absences become one.
    this.next = envelope.next ?? undefined;
    this.retryAfter = retryAfter;
  }
}

/** A body that did not parse against its schema — a contract change, loudly. */
export class DashboardShapeError extends Error {
  constructor(
    readonly path: string,
    cause: unknown,
  ) {
    super(`The dashboard API answered ${path} in a shape this page cannot read.`);
    this.name = "DashboardShapeError";
    this.cause = cause;
  }
}

// The one place this module leaves the page. A named holder rather than a call
// to `location.assign` inline, so a test can watch it without a jsdom
// navigation, and so there is exactly one line to read when the question is
// "what sends a reader to the sign-in page".
export const navigation = {
  go(url: string) {
    if (typeof window !== "undefined") window.location.assign(url);
  },
  path(): string {
    return typeof window === "undefined" ? ROOT : window.location.pathname;
  },
};

export interface DashboardClientConfig {
  fetch?: typeof fetch;
  navigate?: (url: string) => void;
  /** Where the reader should come back to after signing in. */
  currentPath?: () => string;
}

export function createDashboardClient(config: DashboardClientConfig = {}) {
  // Bound through a wrapper: a bare `globalThis.fetch` called detached from
  // its receiver is an illegal invocation in some browsers.
  const doFetch: typeof fetch = config.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const navigate = config.navigate ?? ((url: string) => navigation.go(url));
  const currentPath = config.currentPath ?? (() => navigation.path());

  // One redirect per page load. A dashboard page can have several reads in
  // flight, and three simultaneous 401s must not mean three navigations.
  let leaving = false;

  /** Where a refused reader goes, when this deployment has anywhere to send them.
   *
   * `/dashboard/login` is registered only where the write side is
   * (`access.write_side_enabled`), and a read-only instance with a token still
   * gates its reads — so it can refuse a reader while having no sign-in page at
   * all, and sending them to one would 404. `session.login_url` is `null`
   * exactly then, which is why this asks rather than assumes. The endpoint is
   * outside the read gate, so this second request cannot itself be refused.
   *
   * The return path is `?next=`, the parameter `writes.login` already reads and
   * `writes._safe_next` already fences to this surface.
   */
  async function toSignIn(): Promise<void> {
    if (leaving) return;
    leaving = true;
    try {
      const session = await get(`${ROOT}/api/session`, Session, { gated: false });
      if (!session.login_url) {
        leaving = false;
        return;
      }
      navigate(`${session.login_url}?next=${encodeURIComponent(currentPath())}`);
    } catch {
      // The shell has nowhere reliable to send the reader; the page's
      // signed-out state is the answer instead.
      leaving = false;
    }
  }

  async function get<T>(
    path: string,
    schema: ZodType<T>,
    opts: { signal?: AbortSignal; gated?: boolean } = {},
  ): Promise<T> {
    const res = await doFetch(path, {
      headers: { accept: "application/json" },
      // The cookie is the whole point, and same-origin is the whole policy:
      // there is no CORS anywhere in this design (§1 decision 4).
      credentials: "same-origin",
      cache: "no-store",
      signal: opts.signal,
    });
    if (!res.ok) {
      const error = await toError(res);
      if (error.status === 401 && opts.gated !== false) void toSignIn();
      throw error;
    }
    const parsed = schema.safeParse(await res.json());
    if (!parsed.success) throw new DashboardShapeError(path, parsed.error);
    return parsed.data;
  }

  return {
    overview(signal?: AbortSignal) {
      return get(`${ROOT}/api/overview`, Overview, { signal });
    },
    ledger(signal?: AbortSignal) {
      return get(`${ROOT}/api/ledger`, Ledger, { signal });
    },
    /**
     * The videos table. `query` is the page's own URL, filtered down to the
     * parameters the contract lists and passed through untouched: every bound
     * is server-side (§20), so a value this client "helpfully" corrected would
     * be a clamp the reader is never told about.
     */
    library(query: URLSearchParams, signal?: AbortSignal) {
      return get(`${ROOT}/api/library${suffix(query)}`, Library, { signal });
    },
    /** One video's panels, minus the transcript — that is `cues`. */
    video(videoId: string, query: URLSearchParams, signal?: AbortSignal) {
      const path = `${ROOT}/api/library/${encodeURIComponent(videoId)}`;
      return get(`${path}${suffix(query)}`, VideoDetail, { signal });
    },
    /**
     * A page of one video's cues.
     *
     * The endpoint is named *by the detail payload* rather than built here
     * (§20: "the transcript is a pointer"), so the bounds and the path stay
     * Python's. It is still checked against this prefix before being fetched:
     * a payload naming somewhere else is a contract change, not a redirect
     * this client should follow.
     */
    cues(endpoint: string, query: URLSearchParams, signal?: AbortSignal) {
      const path = insideTheApi(endpoint);
      if (path === null) {
        return Promise.reject(new DashboardShapeError(endpoint, "not a /dashboard/api path"));
      }
      return get(`${path}${suffix(query)}`, CuePage, { signal });
    },
    /** Outside the read gate: a signed-out browser may ask what this deployment is. */
    session(signal?: AbortSignal) {
      return get(`${ROOT}/api/session`, Session, { signal, gated: false });
    },
  };
}

export type DashboardClient = ReturnType<typeof createDashboardClient>;

async function toError(res: Response): Promise<DashboardError> {
  const retryAfter = Number(res.headers.get("retry-after")) || undefined;
  let envelope: PartialRefusal = {};
  try {
    const parsed = PartialRefusal.safeParse(await res.json());
    if (parsed.success) envelope = parsed.data;
  } catch {
    // A non-JSON body — a proxy's HTML 502, or the rate limiter's bare 429 —
    // is still a typed error here, just one with no message of its own.
  }
  return new DashboardError(res.status, envelope, retryAfter);
}

/** The instance the pages use. Same origin, real cookie, real navigation. */
export const dashboard = createDashboardClient();

/** A path the payload named, resolved, or `null` when it lands outside the
 *  read slice this client is allowed to fetch.
 *
 *  Resolved first, because the string and the request are not the same thing:
 *  `/dashboard/api/../../frames/x` starts with the prefix and *arrives*
 *  somewhere else, and it is the arrival the browser makes with the session
 *  cookie attached. So the check is on what came out — the origin this page is
 *  already on, and the normalised path under the prefix — rather than on the
 *  characters that went in. The query is this client's to build, so an
 *  endpoint carrying one of its own is a contract change like any other.
 */
function insideTheApi(endpoint: string): string | null {
  const origin =
    typeof window === "undefined" ? "http://dashboard.invalid" : window.location.origin;
  let url: URL;
  try {
    url = new URL(endpoint, origin);
  } catch {
    return null;
  }
  if (url.origin !== origin || url.search || url.hash) return null;
  return url.pathname.startsWith(`${ROOT}/api/`) ? url.pathname : null;
}

/** A query string, or nothing at all — never a bare `?` on a request with no
 *  parameters, which would make two spellings of one URL. */
function suffix(query: URLSearchParams): string {
  const search = query.toString();
  return search ? `?${search}` : "";
}
