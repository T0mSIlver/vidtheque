// The dashboard's reads and writes, same-origin from the browser with the
// session cookie Next never sees (frontend-migration.md §1a, §9). No CSRF
// token: a same-origin `fetch` carries `Sec-Fetch-Site` (dashboard.md §3.3).
import type { ZodType } from "zod";
import {
  CancelOutcome,
  CuePage,
  FollowCreated,
  FollowDeleted,
  FollowDetail,
  Following,
  FollowQueued,
  FollowWritten,
  IndexOutcome,
  JobDetail,
  Jobs,
  Ledger,
  Library,
  Overview,
  PartialRefusal,
  ReindexOutcome,
  RetryOutcome,
  SearchResponse,
  Session,
  SignedIn,
  TagsOutcome,
  VideoDetail,
} from "./schemas";

/** `/dashboard`, this surface's root on both servers. */
export const ROOT = "/dashboard";

export class DashboardError extends Error {
  readonly status: number;
  /** The refusal's `E_*` code, or `E_HTTP` when the body was not an envelope. */
  readonly code: string;
  readonly next?: string;
  /** Seconds, from `Retry-After`. */
  readonly retryAfter?: number;
  /** The refused body, unparsed: two routes echo what they resolved beside the
   *  envelope, and only the caller knows the shape (`echoOf`). */
  readonly body: unknown;

  constructor(status: number, envelope: PartialRefusal, retryAfter?: number, body?: unknown) {
    super(envelope.message ?? `HTTP ${status}`);
    this.name = "DashboardError";
    this.status = status;
    this.code = envelope.error ?? "E_HTTP";
    this.next = envelope.next ?? undefined;
    this.retryAfter = retryAfter;
    this.body = body;
  }
}

/** What a refusal echoed, or `null` — never a throw: the page has a refusal to
 *  render either way. */
export function echoOf<T>(error: unknown, schema: ZodType<T>): T | null {
  if (!(error instanceof DashboardError)) return null;
  const parsed = schema.safeParse(error.body);
  return parsed.success ? parsed.data : null;
}

/** A body that did not parse: a contract change, loudly. */
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

/** The one place this module leaves the page, so a test can watch it. */
export const navigation = {
  go(url: string) {
    if (typeof window !== "undefined") window.location.assign(url);
  },
  /** No history entry, as a `303` leaves none. */
  replace(url: string) {
    if (typeof window !== "undefined") window.location.replace(url);
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
  // A wrapper: a detached `globalThis.fetch` is an illegal invocation in some
  // browsers.
  const doFetch: typeof fetch = config.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const navigate = config.navigate ?? ((url: string) => navigation.go(url));
  const currentPath = config.currentPath ?? (() => navigation.path());

  // Several reads can be refused at once; one navigation is enough.
  let leaving = false;

  /** Send a refused reader to sign in — when this deployment has a sign-in page
   *  (`login_url` is `null` where no write side is registered). */
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
      credentials: "same-origin",
      // The payloads change under the reader; the in-memory cache in
      // `resource.ts` is the only one.
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
   * A form write (§21); `Accept: application/json` turns its `303` into a typed
   * outcome. `alsoRead`: statuses whose body is still a receipt. `gated: false`:
   * the sign-in write, whose `401` must not bounce to sign-in.
   */
  async function postForm<T>(
    path: string,
    fields: Record<string, string>,
    schema: ZodType<T>,
    opts: { alsoRead?: number[]; gated?: boolean } = {},
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
    if (!res.ok && !(opts.alsoRead ?? []).includes(res.status)) {
      const error = await toError(res);
      if (error.status === 401 && opts.gated !== false) void toSignIn();
      throw error;
    }
    const parsed = schema.safeParse(await res.json());
    if (!parsed.success) throw new DashboardShapeError(path, parsed.error);
    return parsed.data;
  }

  // Queries are the page's URL filtered to the parameters each read takes and
  // sent as typed: every clamp is Python's.
  return {
    overview(signal?: AbortSignal) {
      return get(`${ROOT}/api/overview`, Overview, { signal });
    },
    ledger(signal?: AbortSignal) {
      return get(`${ROOT}/api/ledger`, Ledger, { signal });
    },
    library(query: URLSearchParams, signal?: AbortSignal) {
      return get(`${ROOT}/api/library${suffix(query)}`, Library, { signal });
    },
    /** One video's panels; the transcript is `cues`. */
    video(videoId: string, query: URLSearchParams, signal?: AbortSignal) {
      const path = `${ROOT}/api/library/${encodeURIComponent(videoId)}`;
      return get(`${path}${suffix(query)}`, VideoDetail, { signal });
    },
    /** A page of cues from the endpoint the detail payload names (§20), which
     *  must resolve inside `/dashboard/api/`. */
    cues(endpoint: string, query: URLSearchParams, signal?: AbortSignal) {
      const path = insideTheApi(endpoint);
      if (path === null) {
        return Promise.reject(new DashboardShapeError(endpoint, "not a /dashboard/api path"));
      }
      return get(`${path}${suffix(query)}`, CuePage, { signal });
    },
    jobs(query: URLSearchParams, signal?: AbortSignal) {
      return get(`${ROOT}/api/jobs${suffix(query)}`, Jobs, { signal });
    },
    /** The facade's search handler behind the read gate (§14.2). */
    search(query: URLSearchParams, signal?: AbortSignal) {
      return get(`${ROOT}/api/search${suffix(query)}`, SearchResponse, { signal });
    },
    job(jobId: string, signal?: AbortSignal) {
      return get(`${ROOT}/api/jobs/${encodeURIComponent(jobId)}`, JobDetail, { signal });
    },
    /** Registered with the writes: `404` where there is no write side (§22). */
    following(query: URLSearchParams, signal?: AbortSignal) {
      return get(`${ROOT}/api/following${suffix(query)}`, Following, { signal });
    },
    follow(slug: string, query: URLSearchParams, signal?: AbortSignal) {
      const path = `${ROOT}/api/following/${encodeURIComponent(slug)}`;
      return get(`${path}${suffix(query)}`, FollowDetail, { signal });
    },
    /** Outside the read gate: a signed-out browser may ask. */
    session(signal?: AbortSignal) {
      return get(`${ROOT}/api/session`, Session, { signal, gated: false });
    },

    /** The one write without a session; its `401` is `E_BAD_CREDENTIAL`. */
    signIn(fields: Record<string, string>) {
      return postForm(`${ROOT}/login`, fields, SignedIn, { gated: false });
    },
    cancelJob(jobId: string) {
      return postForm(`${ROOT}/jobs/${encodeURIComponent(jobId)}/cancel`, {}, CancelOutcome);
    },
    retryJob(jobId: string) {
      const path = `${ROOT}/jobs/${encodeURIComponent(jobId)}/retry`;
      return postForm(path, {}, RetryOutcome, { alsoRead: [409] });
    },
    indexUrls(fields: Record<string, string>) {
      return postForm(`${ROOT}/index`, fields, IndexOutcome, { alsoRead: [409] });
    },
    reindexVideo(videoId: string) {
      return postForm(`${videoPath(videoId)}/reindex`, {}, ReindexOutcome);
    },
    setVideoTags(videoId: string, fields: Record<string, string>) {
      return postForm(`${videoPath(videoId)}/tags`, fields, TagsOutcome);
    },
    followChannel(fields: Record<string, string>) {
      return postForm(`${ROOT}/following`, fields, FollowCreated);
    },
    /** Pause or resume: one route, the verb in the body. */
    setFollowState(slug: string, action: "pause" | "resume") {
      return postForm(`${followPath(slug)}/state`, { action }, FollowWritten);
    },
    /** Makes the clock due; the queue runs the check. */
    checkFollowNow(slug: string) {
      return postForm(`${followPath(slug)}/check`, {}, FollowWritten);
    },
    setFollowRules(slug: string, fields: Record<string, string>) {
      return postForm(`${followPath(slug)}/rules`, fields, FollowWritten);
    },
    deleteFollow(slug: string) {
      return postForm(`${followPath(slug)}/delete`, {}, FollowDeleted);
    },
    /** "Index anyway" for one ledger row. */
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
  let body: unknown;
  try {
    body = await res.json();
    const parsed = PartialRefusal.safeParse(body);
    if (parsed.success) envelope = parsed.data;
  } catch {
    // A proxy's HTML 502 or a bare 429 is still a typed error, without a message.
  }
  return new DashboardError(res.status, envelope, retryAfter, body);
}

/** The instance the pages use. */
export const dashboard = createDashboardClient();

/** A path the payload named, resolved on this origin, or `null` when it lands
 *  outside `/dashboard/api/`. Resolved first: `/dashboard/api/../../frames/x`
 *  starts with the prefix and arrives elsewhere. */
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

function followPath(slug: string): string {
  return `${ROOT}/following/${encodeURIComponent(slug)}`;
}

function videoPath(videoId: string): string {
  return `${ROOT}/videos/${encodeURIComponent(videoId)}`;
}

/** Never a bare `?`: two spellings of one URL. */
function suffix(query: URLSearchParams): string {
  const search = query.toString();
  return search ? `?${search}` : "";
}
