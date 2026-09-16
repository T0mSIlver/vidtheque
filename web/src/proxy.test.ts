import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isPorted } from "./app/dashboard/ported";
import { config, proxy } from "./proxy";

// frontend-migration.md §1b, verbatim, with the nonce filled in.
function documentPolicy(nonce: string): string {
  return (
    "default-src 'self'; " +
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'; ` +
    "style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; " +
    "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; " +
    "base-uri 'none'; object-src 'none'"
  );
}

// Where `NextResponse.next({ request: { headers } })` carries the nonce to the renderer.
function forwardedNonce(response: Response): string | null {
  return response.headers.get("x-middleware-request-x-nonce");
}

function send(path = "/demo") {
  return proxy(new NextRequest(`http://localhost:3000${path}`));
}

// Set in production cases too: production must ignore it.
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

  // The wire copy is next.config.ts headers(); next.config.test.ts asserts its twin.
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
    expect(response.headers.get("Content-Security-Policy")).toBe(documentPolicy(nonce!));
    expect(response.headers.get("Content-Security-Policy")).toContain("frame-ancestors 'none'");
  });

  it("mints a fresh nonce per request", () => {
    vi.stubEnv("NODE_ENV", "production");
    const first = forwardedNonce(send());
    const second = forwardedNonce(send("/demo"));
    expect(first).toBeTruthy();
    expect(first).not.toBe(second);
  });

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
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("base-uri 'none'");
    expect(csp).toContain("object-src 'none'");
  });

  // No source uses path-to-regexp parameters, so each is already the regex Next compiles.
  const matches = (path: string) =>
    config.matcher.some((entry) => new RegExp(`^${entry.source}$`).test(path));

  it("runs on the documents and nothing else", () => {
    for (const path of ["/", "/demo", "/paris"]) {
      expect(matches(path), path).toBe(true);
    }
    // The build output and next.config.ts PYTHON_PATHS.
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
      "/videos/kCc8FmEb1nY/export.md",
    ]) {
      expect(matches(path), path).toBe(false);
    }
  });

  // Every path under /dashboard is a document this app serves (a page or its
  // refusal), except the JSON the pages read and the logout write.
  it("covers every dashboard document, and neither of Python's two", () => {
    for (const path of [
      "/dashboard",
      "/dashboard/ledger",
      "/dashboard/search",
      "/dashboard/videos",
      "/dashboard/videos/kCc8FmEb1nY",
      "/dashboard/jobs",
      "/dashboard/jobs/job_finished01",
      // following, index and login: the GET is a page, the POST is Python's (§1d).
      "/dashboard/following",
      "/dashboard/following/andrej-karpathy",
      "/dashboard/index",
      "/dashboard/login",
    ]) {
      expect(matches(path), path).toBe(true);
    }
    // Anything else under the prefix renders the dashboard's not-found page. A
    // POST to one of these never reaches Next (§1d).
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
      // Python owns `logout` exactly, not every path starting with it.
      "/dashboard/logoutx",
      "/dashboard/logout-now",
      "/dashboard/logout.php",
    ]) {
      expect(matches(path), path).toBe(true);
    }
    for (const path of [
      "/dashboard/api/session",
      "/dashboard/api/overview",
      "/dashboard/api/library",
      "/dashboard/api/library/kCc8FmEb1nY",
      "/dashboard/api/videos/kCc8FmEb1nY/cues",
      "/dashboard/api/jobs",
      "/dashboard/api/jobs/job_finished01",
      "/dashboard/logout",
      "/dashboard/api/search",
    ]) {
      expect(matches(path), path).toBe(false);
    }
  });

  // ported.ts answers "is this a page" (a link target); the matcher answers "is
  // this a document", which also includes the refusal for any other path.
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
    for (const path of ["/dashboard/logout", "/dashboard/api/session"]) {
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
    expect(isPorted("/dashboard/following?offset=25")).toBe(true);
    expect(isPorted("/dashboard/following/andrej-karpathy?limit=5#passed")).toBe(true);
  });
});
