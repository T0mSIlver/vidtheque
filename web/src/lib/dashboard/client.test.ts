import { describe, expect, it, vi } from "vitest";
import { createDashboardClient, DashboardError, DashboardShapeError } from "./client";

// A fetch that records the request and answers per path, so one stub can serve
// a gated read and the ungated session endpoint the 401 path consults.
type Canned = { status?: number; body?: unknown; headers?: Record<string, string> };

function fake(routes: Record<string, Canned>) {
  const calls: { path: string; init: RequestInit }[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    calls.push({ path, init: init ?? {} });
    const canned = routes[path];
    if (!canned) return new Response("{}", { status: 404 });
    const text = typeof canned.body === "string" ? canned.body : JSON.stringify(canned.body ?? {});
    return new Response(text, {
      status: canned.status ?? 200,
      headers: { "content-type": "application/json", ...canned.headers },
    });
  }) as typeof fetch;
  return { calls, fetchImpl };
}

const READINESS = {
  mcp: "ready",
  database: "ready",
  vectors: { enabled: true, reason: null },
  worker: { state: "ready", detail: "", models: [] },
  checked_at: 1788626080,
};

const OVERVIEW = {
  counted_at: 1788626080,
  redacted: false,
  corpus: {
    videos: 4,
    queryable_videos: 3,
    videos_by_index_state: { ready: 3, indexing: 1 },
    data_status: "indexing",
    cues: 10,
    keyframes: 3,
    ocr_lines: 5,
    duration_s: 17200,
    published: { oldest: 1673913600, newest: 1740000000 },
    last_indexed: 1750000000,
  },
  channels: [{ channel: "GPU MODE", videos: 1, seconds: 3600 }],
  tags: [{ tag: "topic:attention", videos: 3 }],
  gaps: { transcript_no_ocr: 1, indexing: 1, failed: 0 },
  embed_backlog: { text: 0, frame: 0 },
  jobs: { active: 2, running: 1, deferred: 1, failed_recent: 1, failed_window_s: 86400 },
  recent: [
    {
      video_id: "kCc8FmEb1nY",
      title: "Let's build GPT",
      channel: "Andrej Karpathy",
      duration_s: 7000,
      indexed_at: 1750000000,
      thumb: null,
    },
  ],
  readiness: READINESS,
  declared_models: [{ label: "transcription", key: "stt.model", value: "large-v3", dim: "" }],
  storage: { keyframe_bytes: 4306, database_bytes: 4653056 },
};

const SESSION = {
  version: "0.0.6",
  auth_mode: "token",
  readonly: false,
  write_side: true,
  writes_allowed: true,
  authenticated: false,
  is_owner: false,
  signed_in: false,
  has_session_cookie: false,
  policy: "public",
  login_url: "/dashboard/login",
  sign_in_hint: "Sign in at /dashboard/login, or send Authorization: Bearer $VIDTHEQUE_TOKEN.",
  accepts_password: true,
  accepts_token: true,
};

const OVERVIEW_PATH = "/dashboard/api/overview";
const SESSION_PATH = "/dashboard/api/session";

describe("the dashboard client", () => {
  // The cookie is the whole design: the browser carries it, Next never sees it,
  // and nothing here is cacheable because every figure changes under the
  // reader (dashboard.md §3, and Python answers `no-store` already).
  it("reads same-origin, with the cookie, uncached", async () => {
    const { calls, fetchImpl } = fake({ [OVERVIEW_PATH]: { body: OVERVIEW } });
    await createDashboardClient({ fetch: fetchImpl }).overview();

    expect(calls[0].path).toBe(OVERVIEW_PATH);
    expect(calls[0].init.credentials).toBe("same-origin");
    expect(calls[0].init.cache).toBe("no-store");
    expect(new Headers(calls[0].init.headers).get("accept")).toBe("application/json");
  });

  it("returns the owner payload, typed", async () => {
    const { fetchImpl } = fake({ [OVERVIEW_PATH]: { body: OVERVIEW } });
    const data = await createDashboardClient({ fetch: fetchImpl }).overview();

    expect(data.corpus.videos).toBe(4);
    expect(data.storage?.database_bytes).toBe(4653056);
    expect(data.readiness.worker?.state).toBe("ready");
  });

  // The projection redacts by omission, and omission is `null` on the wire.
  // A schema that refused it would make the demo instance unreadable.
  it("accepts the projection's nulls", async () => {
    const projection = {
      ...OVERVIEW,
      redacted: true,
      readiness: { ...READINESS, worker: null },
      declared_models: null,
      storage: null,
    };
    const { fetchImpl } = fake({ [OVERVIEW_PATH]: { body: projection } });
    const data = await createDashboardClient({ fetch: fetchImpl }).overview();

    expect(data.redacted).toBe(true);
    expect(data.storage).toBeNull();
    expect(data.declared_models).toBeNull();
    expect(data.readiness.worker).toBeNull();
  });

  // An instance that predates the field must still render a rail.
  it("defaults has_session_cookie to false when the instance is older than the field", async () => {
    const older = { ...SESSION };
    delete (older as { has_session_cookie?: boolean }).has_session_cookie;
    const { fetchImpl } = fake({ [SESSION_PATH]: { body: older } });
    const session = await createDashboardClient({ fetch: fetchImpl }).session();

    expect(session.has_session_cookie).toBe(false);
  });

  describe("a refused read", () => {
    it("throws the typed refusal and sends the browser to sign in, with a way back", async () => {
      const navigate = vi.fn();
      const { fetchImpl } = fake({
        [OVERVIEW_PATH]: {
          status: 401,
          body: {
            error: "E_AUTH_REQUIRED",
            message: "This dashboard needs the owner's password, token or session.",
            next: "Sign in at /dashboard/login.",
          },
        },
        [SESSION_PATH]: { body: SESSION },
      });
      const client = createDashboardClient({
        fetch: fetchImpl,
        navigate,
        currentPath: () => "/dashboard/ledger",
      });

      const error = await client.overview().catch((e: unknown) => e);
      expect(error).toBeInstanceOf(DashboardError);
      expect((error as DashboardError).status).toBe(401);
      expect((error as DashboardError).code).toBe("E_AUTH_REQUIRED");
      expect((error as DashboardError).next).toBe("Sign in at /dashboard/login.");

      // The redirect is a second, ungated request; let it settle.
      await vi.waitFor(() => expect(navigate).toHaveBeenCalled());
      expect(navigate).toHaveBeenCalledWith("/dashboard/login?next=%2Fdashboard%2Fledger");
    });

    // A read-only instance with a token gates its reads and registers no login
    // page, so `login_url` is null and there is nowhere to send the reader.
    // Sending them to a 404 would be the refusal telling a second untruth.
    it("stays put when the deployment has no sign-in page", async () => {
      const navigate = vi.fn();
      const { fetchImpl } = fake({
        [OVERVIEW_PATH]: { status: 401, body: { error: "E_AUTH_REQUIRED" } },
        [SESSION_PATH]: { body: { ...SESSION, write_side: false, login_url: null } },
      });
      const client = createDashboardClient({ fetch: fetchImpl, navigate });

      await expect(client.overview()).rejects.toBeInstanceOf(DashboardError);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(navigate).not.toHaveBeenCalled();
    });

    // Several reads can be in flight on one page; three 401s are one refusal.
    it("navigates once however many reads are refused", async () => {
      const navigate = vi.fn();
      const { fetchImpl } = fake({
        [OVERVIEW_PATH]: { status: 401, body: { error: "E_AUTH_REQUIRED" } },
        "/dashboard/api/ledger": { status: 401, body: { error: "E_AUTH_REQUIRED" } },
        [SESSION_PATH]: { body: SESSION },
      });
      const client = createDashboardClient({ fetch: fetchImpl, navigate });

      await Promise.allSettled([client.overview(), client.ledger()]);
      await vi.waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
    });
  });

  // The limiter's own header, carried through so the page can count down
  // instead of inventing a wait (`RetryIn`).
  it("carries Retry-After off a 429", async () => {
    const { fetchImpl } = fake({
      [OVERVIEW_PATH]: {
        status: 429,
        body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests.", next: null },
        headers: { "retry-after": "37" },
      },
    });

    const error = (await createDashboardClient({ fetch: fetchImpl })
      .overview()
      .catch((e: unknown) => e)) as DashboardError;
    expect(error.status).toBe(429);
    expect(error.code).toBe("E_RATE_LIMIT");
    expect(error.retryAfter).toBe(37);
    expect(error.message).toBe("Too many dashboard requests.");
    expect(error.next).toBeUndefined();
  });

  it("is still a typed error when the body is not an envelope", async () => {
    const { fetchImpl } = fake({
      [OVERVIEW_PATH]: { status: 502, body: "<html>bad gateway</html>" },
    });

    const error = (await createDashboardClient({ fetch: fetchImpl })
      .overview()
      .catch((e: unknown) => e)) as DashboardError;
    expect(error.status).toBe(502);
    expect(error.code).toBe("E_HTTP");
    expect(error.message).toBe("HTTP 502");
  });

  // The cues endpoint is named by the detail payload, so this client fetches a
  // path Python chose. It fences that path to the read slice — and the fence
  // is on the *resolved* URL, because a `..` segment starts inside the prefix
  // and arrives somewhere else, with the session cookie attached.
  describe("the endpoint the detail payload names", () => {
    const CUES = "/dashboard/api/videos/kCc8FmEb1nY/cues";
    const PAGE = { cues: [], offset: 0, limit: 50, has_more: false };

    it("fetches one under the read slice", async () => {
      const { calls, fetchImpl } = fake({ [`${CUES}?limit=50`]: { body: PAGE } });

      const page = await createDashboardClient({ fetch: fetchImpl }).cues(
        CUES,
        new URLSearchParams({ limit: "50" }),
      );
      expect(page.has_more).toBe(false);
      expect(calls.map((call) => call.path)).toEqual([`${CUES}?limit=50`]);
    });

    it("refuses one that climbs out of it, and asks for nothing", async () => {
      const { calls, fetchImpl } = fake({});

      const error = await createDashboardClient({ fetch: fetchImpl })
        .cues("/dashboard/api/../../frames/kCc8FmEb1nY-00000.jpg", new URLSearchParams())
        .catch((e: unknown) => e);
      expect(error).toBeInstanceOf(DashboardShapeError);
      expect(calls).toHaveLength(0);
    });

    it("refuses one on another origin", async () => {
      const { calls, fetchImpl } = fake({});

      const error = await createDashboardClient({ fetch: fetchImpl })
        .cues("https://example.invalid/dashboard/api/videos/x/cues", new URLSearchParams())
        .catch((e: unknown) => e);
      expect(error).toBeInstanceOf(DashboardShapeError);
      expect(calls).toHaveLength(0);
    });
  });

  // A payload that does not match the contract is a change on the other side,
  // and it must fail here rather than three components deep as `undefined`.
  it("refuses a malformed body loudly", async () => {
    const { fetchImpl } = fake({
      [OVERVIEW_PATH]: { body: { ...OVERVIEW, corpus: { ...OVERVIEW.corpus, videos: "four" } } },
    });

    const error = await createDashboardClient({ fetch: fetchImpl })
      .overview()
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(DashboardShapeError);
    expect((error as DashboardShapeError).path).toBe(OVERVIEW_PATH);
  });
});
