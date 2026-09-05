import { describe, expect, it, vi } from "vitest";
import {
  ALREADY_FOLLOWING,
  BAD_DURATION,
  CHECKED_OUTCOME,
  CLAMPED_FOLLOWING,
  CREATED_OUTCOME,
  DELETED_OUTCOME,
  FOLLOW_DETAIL,
  FOLLOWING,
  NOT_A_CHANNEL,
  PAUSED_OUTCOME,
  QUEUED_OUTCOME,
  QUIET_DETAIL,
  RULES_OUTCOME,
  UNKNOWN_FOLLOW,
} from "@/test/following-fixtures";
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

  // ----------------------------------------------------------- the writes

  // One URL answers both the Jinja form and this `fetch` (dashboard.md §21),
  // and exactly three things on the request decide which answer comes back. So
  // the first assertion is the request itself, and the rest are the four
  // shapes a caller has to handle by shape rather than by route.
  describe("a write", () => {
    const CANCEL = "/dashboard/jobs/job_running001/cancel";
    const OUTCOME = { job_id: "job_running001", state: "running", cancel_requested: true };

    it("posts a form to Python's own route, with the cookie and a typed Accept", async () => {
      const { calls, fetchImpl } = fake({ [CANCEL]: { body: OUTCOME } });

      const outcome = await createDashboardClient({ fetch: fetchImpl }).cancelJob("job_running001");

      expect(outcome).toEqual(OUTCOME);
      const { path, init } = calls[0];
      expect(path).toBe(CANCEL);
      expect(init.method).toBe("POST");
      // The cookie is ambient and the browser attaches it; `same-origin` is
      // `fetch`'s default only for a same-origin request, so it is said.
      expect(init.credentials).toBe("same-origin");
      expect(init.cache).toBe("no-store");
      const headers = init.headers as Record<string, string>;
      expect(headers.accept).toBe("application/json");
      expect(headers["content-type"]).toBe("application/x-www-form-urlencoded");
      // No CSRF token, because there is none to send: `access.require_write`
      // asks an ambient credential for positive same-origin evidence, and the
      // browser sends `Sec-Fetch-Site` itself — a header script cannot forge.
      expect(Object.keys(headers)).toHaveLength(2);
      expect(init.body).toBe("");
    });

    // A path with a `/` in the id would otherwise reach a different route with
    // the session cookie attached.
    it("encodes the id it was given", async () => {
      const { calls, fetchImpl } = fake({});
      await createDashboardClient({ fetch: fetchImpl })
        .cancelJob("../../logout")
        .catch(() => undefined);
      expect(calls[0].path).toBe("/dashboard/jobs/..%2F..%2Flogout/cancel");
    });

    it("throws the refusal envelope, with its code and its next step", async () => {
      const { fetchImpl } = fake({
        [CANCEL]: {
          status: 400,
          body: {
            error: "E_BAD_PARAM",
            message: 'Job "job_running001" is already failed.',
            next: "only queued or running jobs can be cancelled.",
          },
        },
      });

      const error = await createDashboardClient({ fetch: fetchImpl })
        .cancelJob("job_running001")
        .catch((e: unknown) => e);

      expect(error).toBeInstanceOf(DashboardError);
      expect((error as DashboardError).status).toBe(400);
      expect((error as DashboardError).code).toBe("E_BAD_PARAM");
      // Policy text, rendered by the page and composed in Python.
      expect((error as DashboardError).message).toContain("already failed");
      expect((error as DashboardError).next).toContain("only queued or running");
    });

    it("carries Retry-After off a refused write", async () => {
      const { fetchImpl } = fake({
        [CANCEL]: {
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many requests.", retry_after_s: 30 },
          headers: { "retry-after": "30" },
        },
      });

      const error = await createDashboardClient({ fetch: fetchImpl })
        .cancelJob("job_running001")
        .catch((e: unknown) => e);

      expect((error as DashboardError).status).toBe(429);
      expect((error as DashboardError).retryAfter).toBe(30);
    });

    // The same rule as a refused read: the 401 is what sends the browser to the
    // sign-in page, and it is decided in one place for both.
    it("sends the browser to sign in when the session went away", async () => {
      const navigate = vi.fn();
      const { fetchImpl } = fake({
        [CANCEL]: { status: 401, body: { error: "E_AUTH_REQUIRED" } },
        [SESSION_PATH]: { body: SESSION },
      });

      const error = await createDashboardClient({
        fetch: fetchImpl,
        navigate,
        currentPath: () => "/dashboard/jobs",
      })
        .cancelJob("job_running001")
        .catch((e: unknown) => e);

      expect((error as DashboardError).status).toBe(401);
      await vi.waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
      expect(navigate).toHaveBeenCalledWith("/dashboard/login?next=%2Fdashboard%2Fjobs");
    });

    it("is still an error the page can print when the box is unreachable", async () => {
      const fetchImpl = (async () => {
        throw new TypeError("Failed to fetch");
      }) as typeof fetch;

      const error = await createDashboardClient({ fetch: fetchImpl })
        .cancelJob("job_running001")
        .catch((e: unknown) => e);

      // Not a `DashboardError`: nothing refused it, so there is no code and no
      // `next:` line to print. The page says what it has.
      expect(error).toBeInstanceOf(TypeError);
      expect((error as Error).message).toBe("Failed to fetch");
    });

    // `retry` answers `409` when every batch was refused, and the body is still
    // the receipt — the refusals are on it, in `errors`.
    it("reads the retry receipt out of a 409 rather than throwing it", async () => {
      const receipt = {
        from_job_id: "job_finished01",
        selected: 2,
        jobs: [],
        errors: [{ error: "E_RATE_LIMIT", message: "the source rate-limited this box." }],
        preserved: { channels: "all", tags: [], priority: "normal" },
      };
      const { fetchImpl } = fake({
        "/dashboard/jobs/job_finished01/retry": { status: 409, body: receipt },
      });

      const outcome = await createDashboardClient({ fetch: fetchImpl }).retryJob("job_finished01");
      expect(outcome.jobs).toHaveLength(0);
      expect(outcome.errors[0].error).toBe("E_RATE_LIMIT");
      expect(outcome.selected).toBe(2);
    });

    // The thirteenth write, and the only one whose `401` is not the signal to
    // go and sign in: it *is* the sign-in, and obeying the rule would send the
    // reader back to the page they typed the secret into (§21).
    describe("the sign-in", () => {
      const LOGIN = "/dashboard/login";

      it("sends the secret and where the reader was going", async () => {
        const { calls, fetchImpl } = fake({
          [LOGIN]: { body: { signed_in: true, next: "/dashboard/jobs" } },
        });

        const outcome = await createDashboardClient({ fetch: fetchImpl }).signIn({
          password: "hunter2",
          next: "/dashboard/jobs",
        });

        expect(outcome.signed_in).toBe(true);
        expect(outcome.next).toBe("/dashboard/jobs");
        const init = calls[0].init;
        expect(init.method).toBe("POST");
        expect(init.body).toBe("password=hunter2&next=%2Fdashboard%2Fjobs");
        expect((init.headers as Record<string, string>).accept).toBe("application/json");
      });

      it("does not send a refused secret to the sign-in page", async () => {
        const navigate = vi.fn();
        const { calls, fetchImpl } = fake({
          [LOGIN]: {
            status: 401,
            body: {
              error: "E_BAD_CREDENTIAL",
              message: "That secret does not match this instance.",
            },
          },
          [SESSION_PATH]: { body: SESSION },
        });

        const error = await createDashboardClient({
          fetch: fetchImpl,
          navigate,
          currentPath: () => "/dashboard/login",
        })
          .signIn({ password: "wrong" })
          .catch((e: unknown) => e);

        expect((error as DashboardError).status).toBe(401);
        expect((error as DashboardError).code).toBe("E_BAD_CREDENTIAL");
        expect(navigate).not.toHaveBeenCalled();
        // And it does not go and ask the session endpoint either: the whole
        // 401 path is off, not just its last step.
        expect(calls.map((call) => call.path)).toEqual([LOGIN]);
      });
    });
  });

  // -------------------------------------------------------- the follows

  // The two reads that can be *absent*, and the six writes under them
  // (dashboard.md §21, §22). What is asserted here is the wire: the shapes are
  // the pages' business, and the pages' own suites read them.
  describe("the following pair", () => {
    const LIST = "/dashboard/api/following";
    const DETAIL = "/dashboard/api/following/andrej-karpathy";

    it("reads the list, typed, and sends the bounds as the reader asked them", async () => {
      const { calls, fetchImpl } = fake({ [`${LIST}?limit=100000`]: { body: FOLLOWING } });

      const data = await createDashboardClient({ fetch: fetchImpl }).following(
        new URLSearchParams({ limit: "100000" }),
      );

      // Values, all of them: the seconds spent, the hours configured, the
      // window they are counted over, and the two deployment booleans.
      expect(data.budget).toEqual({ spent_s: 3600, ceiling_h: 16, window_s: 86400 });
      expect(data.checks_enabled).toBe(true);
      expect(data.follows[0].min_duration_s).toBe(480);
      expect(data.order).toBe("failing_first");
      // Uncorrected on the way out: the clamp is Python's, and one applied
      // here would be a bound the reader is never told about.
      expect(calls[0].path).toBe(`${LIST}?limit=100000`);
    });

    it("reads back what a clamp moved", async () => {
      const { fetchImpl } = fake({ [LIST]: { body: CLAMPED_FOLLOWING } });
      const data = await createDashboardClient({ fetch: fetchImpl }).following(
        new URLSearchParams(),
      );

      expect(data.pagination.limit).toBe(100);
      expect(data.notes).toEqual(["limit=100000 → 100", "offset=-3 → 0"]);
    });

    // §18.6: both `GET`s sit inside the write-route list, so a deployment with
    // no write side answers `404` on the JSON exactly as it does on the page.
    // The refusal has to arrive typed, because "there is no such surface here"
    // is what the page renders from.
    it("throws a typed 404 where the deployment registers no write side", async () => {
      const { fetchImpl } = fake({ [LIST]: { status: 404, body: {} } });

      const error = await createDashboardClient({ fetch: fetchImpl })
        .following(new URLSearchParams())
        .catch((e: unknown) => e);

      expect(error).toBeInstanceOf(DashboardError);
      expect((error as DashboardError).status).toBe(404);
    });

    it("carries the limiter's delay off a refused list", async () => {
      const { fetchImpl } = fake({
        [LIST]: {
          status: 429,
          body: { error: "E_RATE_LIMIT", message: "Too many dashboard requests for now." },
          headers: { "retry-after": "12" },
        },
      });

      const error = (await createDashboardClient({ fetch: fetchImpl })
        .following(new URLSearchParams())
        .catch((e: unknown) => e)) as DashboardError;
      expect(error.status).toBe(429);
      expect(error.retryAfter).toBe(12);
    });

    it("sends the browser to sign in when the list is refused", async () => {
      const navigate = vi.fn();
      const { fetchImpl } = fake({
        [LIST]: { status: 401, body: { error: "E_AUTH_REQUIRED" } },
        [SESSION_PATH]: { body: SESSION },
      });

      const error = await createDashboardClient({
        fetch: fetchImpl,
        navigate,
        currentPath: () => "/dashboard/following",
      })
        .following(new URLSearchParams())
        .catch((e: unknown) => e);

      expect((error as DashboardError).status).toBe(401);
      await vi.waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
      expect(navigate).toHaveBeenCalledWith("/dashboard/login?next=%2Fdashboard%2Ffollowing");
    });

    it("reads one follow, its ledger and its near miss", async () => {
      const { calls, fetchImpl } = fake({ [`${DETAIL}?offset=25`]: { body: FOLLOW_DETAIL } });

      const data = await createDashboardClient({ fetch: fetchImpl }).follow(
        "andrej-karpathy",
        new URLSearchParams({ offset: "25" }),
      );

      expect(calls[0].path).toBe(`${DETAIL}?offset=25`);
      expect(data.follow.last_error_message).toBe("the source rate-limited this box");
      expect(data.near_miss).toEqual({ count: 2, of: 6, within_s: 60, edge: "floor" });
      // Verbatim, with the number that made the decision inside it.
      expect(data.seen[5].reason).toBe("7:48, shorter than your 8:00 floor");
      expect(data.caps).toEqual({ checks: 10, index_jobs: 10 });
    });

    // `null` is the contract, not an empty object: a follow with no length
    // rule, or a page of rows with nothing near the edge, has no finding.
    it("reads a null near miss as a null", async () => {
      const { fetchImpl } = fake({ [DETAIL]: { body: QUIET_DETAIL } });
      const data = await createDashboardClient({ fetch: fetchImpl }).follow(
        "andrej-karpathy",
        new URLSearchParams(),
      );

      expect(data.near_miss).toBeNull();
      expect(data.counts).toEqual({});
    });

    it("throws the unknown slug typed, from the read", async () => {
      const { fetchImpl } = fake({
        "/dashboard/api/following/nope": { status: 404, body: UNKNOWN_FOLLOW },
      });

      const error = (await createDashboardClient({ fetch: fetchImpl })
        .follow("nope", new URLSearchParams())
        .catch((e: unknown) => e)) as DashboardError;

      expect(error.status).toBe(404);
      expect(error.code).toBe("E_UNKNOWN_FOLLOW");
      expect(error.next).toContain("lists every channel");
    });

    it("encodes the slug it was given, on the read and on every write", async () => {
      const { calls, fetchImpl } = fake({});
      const client = createDashboardClient({ fetch: fetchImpl });
      await client.follow("../../logout", new URLSearchParams()).catch(() => undefined);
      await client.deleteFollow("../../logout").catch(() => undefined);

      expect(calls[0].path).toBe("/dashboard/api/following/..%2F..%2Flogout");
      expect(calls[1].path).toBe("/dashboard/following/..%2F..%2Flogout/delete");
    });

    // The add form's route *is* the list page's path, with a different method.
    // Nothing about that changes the request: same cookie, same form encoding,
    // same `Accept` that switches the answer away from the 303.
    it("posts the add form to the list's own path", async () => {
      const { calls, fetchImpl } = fake({ "/dashboard/following": { body: CREATED_OUTCOME } });

      const outcome = await createDashboardClient({ fetch: fetchImpl }).followChannel({
        url: "https://www.youtube.com/@newone",
        title: "New One",
        tab_videos: "1",
      });

      expect(outcome.already_following).toBe(false);
      expect(outcome.follow?.slug).toBe("new-one");
      const { path, init } = calls[0];
      expect(path).toBe("/dashboard/following");
      expect(init.method).toBe("POST");
      expect(init.credentials).toBe("same-origin");
      expect((init.headers as Record<string, string>).accept).toBe("application/json");
      expect(init.body).toBe(
        "url=https%3A%2F%2Fwww.youtube.com%2F%40newone&title=New+One&tab_videos=1",
      );
    });

    it("reads the tool's own already-following back", async () => {
      const { fetchImpl } = fake({ "/dashboard/following": { body: ALREADY_FOLLOWING } });
      const outcome = await createDashboardClient({ fetch: fetchImpl }).followChannel({
        url: "https://www.youtube.com/@karpathy",
      });

      expect(outcome.already_following).toBe(true);
      expect(outcome.follow?.slug).toBe("andrej-karpathy");
    });

    it("throws the add form's refusal with its next step", async () => {
      const { fetchImpl } = fake({
        "/dashboard/following": { status: 400, body: NOT_A_CHANNEL },
      });

      const error = (await createDashboardClient({ fetch: fetchImpl })
        .followChannel({ url: "https://youtu.be/kCc8FmEb1nY" })
        .catch((e: unknown) => e)) as DashboardError;

      expect(error.code).toBe("E_BAD_PARAM");
      expect(error.message).toContain("is a single video");
      expect(error.next).toContain("index-video");
    });

    it("puts the verb in the body of the one state route", async () => {
      const { calls, fetchImpl } = fake({
        "/dashboard/following/andrej-karpathy/state": { body: PAUSED_OUTCOME },
      });

      const outcome = await createDashboardClient({ fetch: fetchImpl }).setFollowState(
        "andrej-karpathy",
        "pause",
      );

      expect(outcome.follow.state).toBe("paused");
      expect(calls[0].init.body).toBe("action=pause");
    });

    // `check_now` moves the clock rather than running anything, and the row is
    // the receipt: `next_check_at: 0` is due immediately.
    it("reads the clock a check-now moved", async () => {
      const { calls, fetchImpl } = fake({
        "/dashboard/following/andrej-karpathy/check": { body: CHECKED_OUTCOME },
      });

      const outcome = await createDashboardClient({ fetch: fetchImpl }).checkFollowNow(
        "andrej-karpathy",
      );

      expect(outcome.follow.next_check_at).toBe(0);
      expect(calls[0].init.body).toBe("");
    });

    // The row comes back from the store, so what a client reads is the rule
    // that was *kept* — the parser's own seconds, not the string that was sent.
    it("reads the rule the store kept, not the one it sent", async () => {
      const { calls, fetchImpl } = fake({
        "/dashboard/following/andrej-karpathy/rules": { body: RULES_OUTCOME },
      });

      const outcome = await createDashboardClient({ fetch: fetchImpl }).setFollowRules(
        "andrej-karpathy",
        { min_duration: "9:00", max_per_check: "4", tab_videos: "1" },
      );

      expect(outcome.follow.min_duration_s).toBe(540);
      expect(outcome.follow.max_per_check).toBe(4);
      expect(calls[0].init.body).toBe("min_duration=9%3A00&max_per_check=4&tab_videos=1");
    });

    it("throws the shared validator's refusal", async () => {
      const { fetchImpl } = fake({
        "/dashboard/following/andrej-karpathy/rules": { status: 400, body: BAD_DURATION },
      });

      const error = (await createDashboardClient({ fetch: fetchImpl })
        .setFollowRules("andrej-karpathy", { min_duration: "banana" })
        .catch((e: unknown) => e)) as DashboardError;

      expect(error.code).toBe("E_BAD_TIME_FORMAT");
      expect(error.message).toContain("min_duration='banana'");
    });

    it("reads what an unfollow left behind", async () => {
      const { fetchImpl } = fake({
        "/dashboard/following/andrej-karpathy/delete": { body: DELETED_OUTCOME },
      });

      const outcome = await createDashboardClient({ fetch: fetchImpl }).deleteFollow(
        "andrej-karpathy",
      );

      expect(outcome.deleted).toBe(true);
      expect(outcome.videos_kept).toBe(1);
    });

    it("queues one rescued row and reads the job it made", async () => {
      const { calls, fetchImpl } = fake({
        "/dashboard/following/andrej-karpathy/queue": { body: QUEUED_OUTCOME },
      });

      const outcome = await createDashboardClient({ fetch: fetchImpl }).queueFollowUrl(
        "andrej-karpathy",
        "https://youtu.be/nearmiss001",
      );

      expect(outcome.job_id).toBe("job_02e028870c97");
      expect(calls[0].init.body).toBe("url=https%3A%2F%2Fyoutu.be%2Fnearmiss001");
    });

    // Nothing asked for is nothing done, on both branches: an empty `url`
    // answers `200` with a null job rather than a refusal.
    it("reads a null job id as an outcome rather than an error", async () => {
      const { fetchImpl } = fake({
        "/dashboard/following/andrej-karpathy/queue": {
          body: { slug: "andrej-karpathy", url: null, job_id: null },
        },
      });

      const outcome = await createDashboardClient({ fetch: fetchImpl }).queueFollowUrl(
        "andrej-karpathy",
        "",
      );
      expect(outcome.job_id).toBeNull();
    });
  });
});
