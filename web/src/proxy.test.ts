import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isPorted } from "./app/dashboard/ported";
import { config, proxy } from "./proxy";

// The four headers are the whole of what this front end promises about how a
// document is allowed to behave, and demo-site.md §7 item 0 makes sending them
// the check that gates the traffic switch. Until this file existed the promise
// was made by a comment and kept by nobody.

// docs/design/frontend-migration.md §1b, transcribed with its own line breaks,
// with the per-request nonce filled in. If the two ever disagree, one of them
// is wrong and this says which.
function documentPolicy(nonce: string): string {
  return (
    "default-src 'self'; " +
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'; ` +
    "style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; " +
    "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; " +
    "base-uri 'none'; object-src 'none'"
  );
}

// `NextResponse.next({ request: { headers } })` does not hand the renderer a
// request; it tells the server which request headers to override, and names
// them here. This is where the nonce the page will stamp actually travels.
function forwardedNonce(response: Response): string | null {
  return response.headers.get("x-middleware-request-x-nonce");
}

function send(path = "/demo") {
  return proxy(new NextRequest(`http://localhost:3000${path}`));
}

// Set even in the production case: the point is that production ignores it.
const API = "http://localhost:8080";

describe("proxy", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("sends the four document headers", () => {
    vi.stubEnv("NODE_ENV", "production");
    const headers = send().headers;
    expect(headers.get("Content-Security-Policy")).toBeTruthy();
    expect(headers.get("X-Frame-Options")).toBe("DENY");
    expect(headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(headers.get("Referrer-Policy")).toBe("no-referrer");
  });

  // `dashboard/views.py` sent `Cache-Control: no-store` with every document it
  // rendered, for a reason the port does not change: a management page
  // describes state that moves under the reader, some of it behind a session
  // cookie, and a shared cache holding one is somebody else's dashboard.
  //
  // What *reaches the wire* is `next.config.ts`'s `headers()`, not this: a
  // header the middleware sets on a document is overwritten by the dynamic
  // render underneath it, which is how `no-cache, must-revalidate` was what a
  // `curl -D-` saw for the whole of the port. This says it first, and the two
  // agree — a second copy of one value is only worth keeping while both are
  // asserted, which is why the twin assertion is in `next.config.test.ts`.
  it("says no-store on a dashboard document and nothing else", () => {
    vi.stubEnv("NODE_ENV", "production");
    for (const path of [
      "/dashboard",
      "/dashboard/ledger",
      "/dashboard/videos/kCc8FmEb1nY",
      "/dashboard/login",
    ]) {
      expect(send(path).headers.get("Cache-Control"), path).toBe("no-store");
    }
    // The two public documents are the same page for every reader, and a
    // front-end cache in front of them is the point.
    for (const path of ["/", "/demo", "/paris"]) {
      expect(send(path).headers.get("Cache-Control"), path).toBeNull();
    }
  });

  it("sends §1b's policy verbatim in production, nonce and all", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("VIDTHEQUE_API_URL", API);
    const response = send();
    const nonce = forwardedNonce(response);
    expect(nonce).toBeTruthy();
    // The renderer stamps scripts with the nonce it reads off the request; a
    // policy naming a different one would allow nothing at all.
    expect(response.headers.get("Content-Security-Policy")).toBe(documentPolicy(nonce!));
    expect(response.headers.get("Content-Security-Policy")).toContain("frame-ancestors 'none'");
  });

  // A nonce reused across requests is a nonce an attacker can read off one
  // page and paste into the next, which is to say not a nonce.
  it("mints a fresh nonce per request", () => {
    vi.stubEnv("NODE_ENV", "production");
    const first = forwardedNonce(send());
    const second = forwardedNonce(send("/demo"));
    expect(first).toBeTruthy();
    expect(first).not.toBe(second);
  });

  // The dev widenings are the ones §1b lists, and production getting any of
  // them would be the whole policy quietly loosened by an environment.
  const DEV_ONLY = ["'unsafe-eval'", "data:", "blob:", "ws:", API];

  it("keeps the development widenings out of production", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("VIDTHEQUE_API_URL", API);
    const csp = send().headers.get("Content-Security-Policy") ?? "";
    for (const source of DEV_ONLY) expect(csp).not.toContain(source);
    expect(csp).toContain("img-src 'self';");
    expect(csp).toContain("connect-src 'self';");
  });

  it("widens exactly those three directives in development", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("VIDTHEQUE_API_URL", API);
    const csp = send().headers.get("Content-Security-Policy") ?? "";
    for (const source of DEV_ONLY) expect(csp).toContain(source);
    expect(csp).toContain(`img-src 'self' data: blob: ${API};`);
    expect(csp).toContain(`connect-src 'self' ws: ${API};`);
    // Widened, not abandoned: the directives that were never about the two
    // ports read the same as production.
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("base-uri 'none'");
    expect(csp).toContain("object-src 'none'");
  });

  // No entry's `source` carries a path-to-regexp parameter, so each one is
  // already the regular expression Next compiles it into and a test can just
  // run them. A path matches when any entry does, which is what Next does too.
  const matches = (path: string) =>
    config.matcher.some((entry) => new RegExp(`^${entry.source}$`).test(path));

  it("runs on the documents and nothing else", () => {
    for (const path of ["/", "/demo", "/paris"]) {
      expect(matches(path), path).toBe(true);
    }
    // The build's own output, and the prefixes `next.config.ts` gives Python.
    for (const path of [
      "/_next/static/chunks/main.js",
      "/api/ask",
      "/frames/kCc8FmEb1nY-000123.jpg",
      "/mcp",
      "/auth/login",
      "/.well-known/oauth-authorization-server",
      "/healthz",
      "/icon.svg",
      "/favicon.ico",
      // The one path left under `/videos` since the library pages went on
      // 2026-09-07, and Python's throughout — the one entry of that list the
      // prefixes above do not cover.
      "/videos/kCc8FmEb1nY/export.md",
    ]) {
      expect(matches(path), path).toBe(false);
    }
  });

  // `/dashboard` stopped being wholly Python's when the first two pages were
  // ported, and Python stopped rendering any of it on 2026-09-06. The split is
  // per path and not per prefix, and it is the same split `next.config.ts`
  // routes: what this app serves is a document and gets the policy, and what
  // Python still owns under the prefix — the JSON the pages read and the
  // session flow — reaches it untouched.
  it("covers every dashboard document, and neither of Python's two", () => {
    for (const path of [
      "/dashboard",
      "/dashboard/ledger",
      "/dashboard/search",
      "/dashboard/videos",
      "/dashboard/videos/kCc8FmEb1nY",
      "/dashboard/jobs",
      "/dashboard/jobs/job_finished01",
      // The follows table's path is here for its `GET`. The `POST` to the very
      // same path is the add form's and stays Python's — a split this matcher
      // cannot express, because it matches paths and not methods, and the one
      // the production proxy has to route by method.
      "/dashboard/following",
      "/dashboard/following/andrej-karpathy",
      // The second path split by method. `POST /dashboard/index` is the form's
      // route and stays Python's, which this matcher can no more say than it
      // can for the follows table above it.
      "/dashboard/index",
      // The third, and the last `/dashboard` GET to come off Jinja. `POST
      // /dashboard/login` is the write that mints the session cookie and stays
      // Python's for a reason no proxy rule could change: the cookie is on that
      // response and it is `HttpOnly`.
      "/dashboard/login",
    ]) {
      expect(matches(path), path).toBe(true);
    }
    // A path under the prefix that is not one of those pages is still a
    // document this app serves: `app/dashboard/[...rest]` catches it and
    // `dashboard/not-found.tsx` refuses it, with the rail on the page. A
    // refusal carries the policy like any other document, which is what the
    // last matcher entry is for — before it, a mistyped URL was the one page
    // on this surface shipped with no CSP on it.
    //
    // The `POST` paths are in this list because the matcher matches paths and
    // not methods: a `GET` to one of them is this refusal, and the `POST`
    // never reaches Next at all — the reverse proxy sends every
    // `POST /dashboard/*` to Python (§1d, `deploy/Caddyfile`).
    for (const path of [
      "/dashboard/no-such-page",
      "/dashboard/ledger/anything",
      "/dashboard/login/anything",
      "/dashboard/videos/kCc8FmEb1nY/reindex",
      "/dashboard/videos/kCc8FmEb1nY/tags",
      "/dashboard/jobs/job_running001/cancel",
      "/dashboard/jobs/job_finished01/retry",
      "/dashboard/following/andrej-karpathy/state",
      "/dashboard/following/andrej-karpathy/delete",
      // Python owns the word `logout` and nothing that merely starts with it.
      // A mistyped or crawled one of these is the refusal this app draws, so
      // the exclusion that keeps the real path out has to end at it.
      "/dashboard/logoutx",
      "/dashboard/logout-now",
      "/dashboard/logout.php",
    ]) {
      expect(matches(path), path).toBe(true);
    }
    // Python's two, and they are the only two left under this prefix: the JSON
    // every page reads, and the write that ends a session.
    for (const path of [
      "/dashboard/api/session",
      "/dashboard/api/overview",
      "/dashboard/api/library",
      "/dashboard/api/library/kCc8FmEb1nY",
      "/dashboard/api/videos/kCc8FmEb1nY/cues",
      "/dashboard/api/jobs",
      "/dashboard/api/jobs/job_finished01",
      "/dashboard/logout",
      // The search page's own JSON, which is the facade's handler under this
      // prefix — a read, not a document, and Python's like the other four.
      "/dashboard/api/search",
    ]) {
      expect(matches(path), path).toBe(false);
    }
  });

  // Porting a page adds it to three lists (frontend-migration.md §1d): this
  // matcher, `next.config.ts`'s comment, and `ported.ts`, which every link into
  // the surface asks. A page missing from one of them ships either a document
  // with no CSP on it or a `Link` into a route this app does not serve, so the
  // two lists that are code are asserted against each other here.
  //
  // They answer two questions, and since the catch-all landed the answers
  // differ by exactly one thing: `ported.ts` says "is this path a **page**",
  // which is what a link has to know, and the matcher says "is this path a
  // **document**", which now includes the refusal every other path under the
  // prefix renders. So a page is in both, Python's two are in neither, and a
  // non-page under `/dashboard` is a document and not a link target.
  it("agrees with the list every link into this surface asks", () => {
    for (const path of [
      "/dashboard",
      "/dashboard/ledger",
      "/dashboard/videos",
      "/dashboard/videos/kCc8FmEb1nY",
      "/dashboard/jobs",
      "/dashboard/jobs/job_finished01",
      "/dashboard/following",
      "/dashboard/following/andrej-karpathy",
      "/dashboard/search",
      "/dashboard/index",
      "/dashboard/login",
    ]) {
      expect(isPorted(path), path).toBe(true);
      expect(matches(path), path).toBe(true);
    }
    for (const path of [
      // The other half of the session flow, and the one with no page: signing
      // out is a write and nothing else, so both lists leave it out.
      "/dashboard/logout",
      "/dashboard/api/session",
    ]) {
      expect(isPorted(path), path).toBe(false);
      expect(matches(path), path).toBe(false);
    }
    for (const path of [
      "/dashboard/search/anything",
      "/dashboard/following/andrej-karpathy/delete",
      "/dashboard/jobs/job_finished01/retry",
    ]) {
      expect(isPorted(path), path).toBe(false);
      expect(matches(path), path).toBe(true);
    }
    // A query is not part of the question a link asks, and it is not part of
    // the one the matcher answers either.
    expect(isPorted("/dashboard/following?offset=25")).toBe(true);
    expect(isPorted("/dashboard/following/andrej-karpathy?limit=5#passed")).toBe(true);
  });
});
