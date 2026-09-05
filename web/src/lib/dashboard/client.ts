// The dashboard's reads and writes, from the browser.
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
//
// The write half arrived with the jobs pages (dashboard.md §21,
// frontend-migration.md §9) and adds no fourth rule: a write is a `POST` to the
// *same* `/dashboard/*` route the Jinja form posts to, with the same cookie,
// the same form-encoded body, and `Accept: application/json` — which is the
// only thing that switches the answer from a `303` to a typed outcome. There is
// no CSRF token on this surface and none is planned: `dashboard/access.py`
// asks an ambient credential for positive same-origin evidence instead, and a
// same-origin `fetch` sends `Sec-Fetch-Site: same-origin` itself — a header
// script cannot forge, so there is nothing for this file to attach.
import type { ZodType } from "zod";
// The search read's contract is the `/api` facade's, because the handler is
// (dashboard.md §14.2). `schemas` and not the package index: that index is
// `server-only`, and this module runs in the browser.
import { SearchResponse } from "../api/schemas";
import {
  CancelOutcome,
  CuePage,
  FollowCreated,
  FollowDeleted,
  FollowDetail,
  Following,
  FollowQueued,
  FollowWritten,
  JobDetail,
  Jobs,
  Ledger,
  Library,
  Overview,
  PartialRefusal,
  RetryOutcome,
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

  /**
   * One write, to the route the Jinja form posts to (frontend-migration.md §9).
   *
   * All three of these travel together or the write is not the one the
   * contract describes:
   *
   * * `credentials: "same-origin"` — `fetch`'s default only for a same-origin
   *   request, so it is said rather than assumed. Next never sees this cookie
   *   and the browser is the only thing that holds it.
   * * `Accept: application/json` — the whole switch. Nothing else picks the
   *   typed outcome over the `303` a form navigation gets, and a wildcard
   *   `Accept` will not: a request for anything is not a request for a typed
   *   outcome, which is what makes the strictness safe.
   * * a **form-encoded body**. None of the thirteen handlers parses a JSON
   *   body; they read a form on both branches.
   *
   * A refusal is the same envelope a read is refused with, at the code's own
   * status and with `Retry-After` when the refusal named a delay, so it lands
   * in `DashboardError` exactly as a read's does. `401` is the same signal too:
   * authorization is decided in one place and no page keeps a rule of its own.
   *
   * `alsoRead` is for the one status that is not a refusal: `retry` answers
   * `409` when *nothing* was accepted, and the body is still the receipt — the
   * job it came from, what it selected, and the refusals in `errors` (§21).
   */
  async function postForm<T>(
    path: string,
    fields: Record<string, string>,
    schema: ZodType<T>,
    alsoRead: number[] = [],
  ): Promise<T> {
    const res = await doFetch(path, {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams(fields).toString(),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!res.ok && !alsoRead.includes(res.status)) {
      const error = await toError(res);
      if (error.status === 401) void toSignIn();
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
    /**
     * The jobs table, and the 2 s tick that keeps it true.
     *
     * `query` is the page's own URL filtered to the parameters the view takes,
     * passed through untouched for the reason every other list here does:
     * `list_jobs` owns every predicate, and a value corrected on this side
     * would be a bound the reader is never told about.
     */
    jobs(query: URLSearchParams, signal?: AbortSignal) {
      return get(`${ROOT}/api/jobs${suffix(query)}`, Jobs, { signal });
    },
    /**
     * The corpus, queried — the facade's own handler under this prefix, behind
     * the read gate (dashboard.md §14.2).
     *
     * The one read on this surface whose schema is `lib/api`'s rather than this
     * folder's, because it is literally the same payload the public `/api`
     * facade answers with: one handler, two prefixes, differing only in the
     * gate in front of them and the one `note:` line the demo drops.
     *
     * `query` is the page's own URL filtered to the parameters the handler
     * takes, passed through untouched like every other list here. The clamp is
     * the *caller's* — `policy_for` gives a bearer, a session or a trusted peer
     * a page of up to 50 and everyone else up to 20, on this prefix as well —
     * so a `limit` corrected on this side would be a bound the reader is never
     * told about, and the accepted one comes back in `pagination`.
     */
    search(query: URLSearchParams, signal?: AbortSignal) {
      return get(`${ROOT}/api/search${suffix(query)}`, SearchResponse, { signal });
    },
    /** One job — its card, its items and the tail of its event log. */
    job(jobId: string, signal?: AbortSignal) {
      return get(`${ROOT}/api/jobs/${encodeURIComponent(jobId)}`, JobDetail, { signal });
    },
    /**
     * The follows table, its band and its budget.
     *
     * The one read on this surface that can be **absent**: both following
     * routes are registered with the write routes, so a deployment with no
     * write side answers `404` here exactly as it does on the page (§18.6,
     * §22). The pages read `write_side` off `/api/session` before rendering
     * the surface at all and treat that `404` as the same answer.
     */
    following(query: URLSearchParams, signal?: AbortSignal) {
      return get(`${ROOT}/api/following${suffix(query)}`, Following, { signal });
    },
    /** One follow: its rule, its checks, and what it passed over. */
    follow(slug: string, query: URLSearchParams, signal?: AbortSignal) {
      const path = `${ROOT}/api/following/${encodeURIComponent(slug)}`;
      return get(`${path}${suffix(query)}`, FollowDetail, { signal });
    },
    /** Outside the read gate: a signed-out browser may ask what this deployment is. */
    session(signal?: AbortSignal) {
      return get(`${ROOT}/api/session`, Session, { signal, gated: false });
    },

    // ------------------------------------------------------------- writes

    /** `POST /dashboard/jobs/{job_id}/cancel`. No fields: the Jinja form posts
     *  none either, and the job is named by the path. */
    cancelJob(jobId: string) {
      const path = `${ROOT}/jobs/${encodeURIComponent(jobId)}/cancel`;
      return postForm(path, {}, CancelOutcome);
    },
    /** `POST /dashboard/jobs/{job_id}/retry` — the failed and degraded items,
     *  and nothing else. `409` is read rather than thrown: it means every batch
     *  was refused, and the refusals are on the receipt. */
    retryJob(jobId: string) {
      const path = `${ROOT}/jobs/${encodeURIComponent(jobId)}/retry`;
      return postForm(path, {}, RetryOutcome, [409]);
    },

    // The six following writes (dashboard.md §18.5, §21). Not one of them
    // decides anything: five go through `tools/follows.follow_channel` — the
    // same call the model makes — and the sixth through the validator that
    // tool shares. Every clamp, the URL normalisation, the duration parser and
    // the tag rules are *there*, so this side sends the form as typed and
    // renders whatever came back.

    /** `POST /dashboard/following` — the add form's own route, which is also
     *  the list page's path. The fields are the Jinja form's, exactly. */
    followChannel(fields: Record<string, string>) {
      return postForm(`${ROOT}/following`, fields, FollowCreated);
    },
    /** `POST /dashboard/following/{slug}/state` — pause or resume.
     *
     *  One route with the verb in the body, not two URLs: they are the two
     *  directions of one control, and a surface with a URL for each is a
     *  surface where a page can offer the wrong one. */
    setFollowState(slug: string, action: "pause" | "resume") {
      return postForm(`${followPath(slug)}/state`, { action }, FollowWritten);
    },
    /** `POST /dashboard/following/{slug}/check` — make the clock due now.
     *
     *  It does not run a check; it moves `next_check_at`, and the queue claims
     *  a `follow_check` on its next tick. The row that comes back is the
     *  receipt for that, which is why it is read rather than assumed. */
    checkFollowNow(slug: string) {
      return postForm(`${followPath(slug)}/check`, {}, FollowWritten);
    },
    /** `POST /dashboard/following/{slug}/rules` — the edit disclosure.
     *
     *  The row it answers with carries every rule column, so the form reads
     *  back the rule the store *kept* rather than the one it sent. */
    setFollowRules(slug: string, fields: Record<string, string>) {
      return postForm(`${followPath(slug)}/rules`, fields, FollowWritten);
    },
    /** `POST /dashboard/following/{slug}/delete` — unfollow.
     *
     *  The one irreversible control on this surface, and `videos_kept` is what
     *  makes the asymmetry sayable: the rule and the ledger go, the videos
     *  stay. */
    deleteFollow(slug: string) {
      return postForm(`${followPath(slug)}/delete`, {}, FollowDeleted);
    },
    /** `POST /dashboard/following/{slug}/queue` — "Index anyway", one row.
     *
     *  `expand=none` and the follow's own channels and tags are Python's, so
     *  the only field this sends is the URL off the ledger row. */
    queueFollowUrl(slug: string, url: string) {
      return postForm(`${followPath(slug)}/queue`, { url }, FollowQueued);
    },
    postForm,
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

/** One follow's write prefix. The slug is a path segment the reader can have
 *  typed, so it is encoded exactly as a video id and a job id are. */
function followPath(slug: string): string {
  return `${ROOT}/following/${encodeURIComponent(slug)}`;
}

/** A query string, or nothing at all — never a bare `?` on a request with no
 *  parameters, which would make two spellings of one URL. */
function suffix(query: URLSearchParams): string {
  const search = query.toString();
  return search ? `?${search}` : "";
}
