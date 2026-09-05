import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
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
    const second = forwardedNonce(send("/videos"));
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
    for (const path of ["/", "/demo", "/videos", "/videos/kCc8FmEb1nY"]) {
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
      // Three segments, so it is not the `/videos/[id]` page and Python owns
      // it — the one entry of that list the prefixes above do not cover.
      "/videos/kCc8FmEb1nY/export.md",
    ]) {
      expect(matches(path), path).toBe(false);
    }
  });

  // `/dashboard` stopped being wholly Python's when the first two pages were
  // ported. The split is per path and not per prefix, and it is the same split
  // `next.config.ts` routes: the pages this app serves are documents and get
  // the policy, and everything else under the prefix reaches Python untouched —
  // the JSON the pages read, the stylesheet and fonts the unported pages load,
  // the session flow, and every page still rendered by Jinja.
  it("covers the dashboard pages this app serves, and no other dashboard path", () => {
    for (const path of ["/dashboard", "/dashboard/ledger", "/dashboard/videos"]) {
      expect(matches(path), path).toBe(true);
    }
    for (const path of [
      "/dashboard/api/session",
      "/dashboard/api/overview",
      "/dashboard/api/library",
      "/dashboard/api/library/kCc8FmEb1nY",
      "/dashboard/videos/kCc8FmEb1nY",
      "/dashboard/static/dashboard.css",
      "/dashboard/static/fonts/archivo-latin-wght-normal.woff2",
      "/dashboard/login",
      "/dashboard/logout",
      "/dashboard/index",
      "/dashboard/jobs",
      "/dashboard/following",
      // Not a page here either: a sub-path of one that is.
      "/dashboard/ledger/anything",
    ]) {
      expect(matches(path), path).toBe(false);
    }
  });
});
