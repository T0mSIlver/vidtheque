import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match";
import { afterEach, describe, expect, it, vi } from "vitest";
import nextConfig from "../next.config";

// Runs the rewrites and headers the way Next compiles them (frontend-migration.md §1c).

const API = "http://localhost:8080";

async function devRewrites() {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("VIDTHEQUE_API_URL", API);
  const rewrites = await nextConfig.rewrites!();
  if (Array.isArray(rewrites)) throw new Error("development returns the three-phase shape");
  return {
    beforeFiles: rewrites.beforeFiles ?? [],
    afterFiles: rewrites.afterFiles ?? [],
    fallback: rewrites.fallback ?? [],
  };
}

/** getPathMatch is what Next's router calls on a rewrite source. */
function matches(sources: { source: string }[], pathname: string): boolean {
  return sources.some((entry) => getPathMatch(entry.source)(pathname) !== false);
}

describe("the development rewrites", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("forwards nothing after the router has looked for a page", async () => {
    // afterFiles runs ahead of the dynamic routes and would shadow them.
    expect((await devRewrites()).afterFiles).toEqual([]);
  });

  it("leaves the dashboard's dynamic pages to the router", async () => {
    const { beforeFiles, afterFiles, fallback } = await devRewrites();
    for (const path of [
      "/dashboard",
      "/dashboard/videos/4AyM_3SK31w",
      "/dashboard/jobs/j-1",
      "/dashboard/following/karpathy",
    ]) {
      expect(matches(beforeFiles, path), `${path} is forwarded before files`).toBe(false);
      expect(matches(afterFiles, path), `${path} is forwarded after files`).toBe(false);
      expect(matches(fallback, path), `${path} is forwarded as a fallback`).toBe(false);
    }
  });

  it("still forwards the dashboard's non-pages, before the router runs", async () => {
    const { beforeFiles } = await devRewrites();
    expect(matches(beforeFiles, "/dashboard/api/library")).toBe(true);
    expect(matches(beforeFiles, "/dashboard/logout")).toBe(true);
    expect(beforeFiles.find((entry) => entry.source === "/dashboard/api/:path*")?.destination).toBe(
      `${API}/dashboard/api/:path*`,
    );
  });

  it("forwards the root OAuth endpoints, exactly", async () => {
    const { beforeFiles } = await devRewrites();
    for (const path of ["/authorize", "/token", "/register", "/revoke", "/dashboard/api"]) {
      expect(matches(beforeFiles, path), path).toBe(true);
    }
    for (const path of ["/tokens", "/authorize/x", "/registered"]) {
      expect(matches(beforeFiles, path), path).toBe(false);
    }
  });

  it("sends nothing anywhere in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("VIDTHEQUE_API_URL", API);
    expect(await nextConfig.rewrites!()).toEqual([]);
  });
});

describe("the dev server's allowed origins", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  /** Read at module load, so the environment is set before the import. */
  async function origins(): Promise<string[] | undefined> {
    vi.resetModules();
    return (await import("../next.config")).default.allowedDevOrigins;
  }

  it("allows the loopback address and nothing else when nothing is named", async () => {
    expect(await origins()).toEqual(["127.0.0.1"]);
  });

  it("takes the hosts this box is reached on from the environment", async () => {
    vi.stubEnv("VIDTHEQUE_DEV_ORIGINS", "127.0.0.1, 192.168.0.10 ,dev.example.test");
    expect(await origins()).toEqual(["127.0.0.1", "192.168.0.10", "dev.example.test"]);
  });

  it("falls back rather than allowing an empty host", async () => {
    vi.stubEnv("VIDTHEQUE_DEV_ORIGINS", " , ");
    expect(await origins()).toEqual(["127.0.0.1"]);
  });
});

describe("the document cache policy", () => {
  async function policyFor(pathname: string): Promise<string | undefined> {
    const rules = await nextConfig.headers!();
    const hit = rules.find((rule) => getPathMatch(rule.source)(pathname) !== false);
    return hit?.headers.find((header) => header.key === "Cache-Control")?.value;
  }

  it("keeps every dashboard document out of every cache", async () => {
    for (const path of [
      "/dashboard",
      "/dashboard/ledger",
      "/dashboard/search",
      "/dashboard/videos",
      "/dashboard/videos/kCc8FmEb1nY",
      "/dashboard/jobs",
      "/dashboard/jobs/job_20260909abc",
      "/dashboard/following",
      "/dashboard/following/andrej-karpathy",
      "/dashboard/index",
      "/dashboard/login",
      "/dashboard/no-such-page",
    ]) {
      expect(await policyFor(path)).toBe("no-store");
    }
  });

  const YEAR = "public, max-age=31536000, immutable";

  it("gives the landing's stills the year they had", async () => {
    expect(await policyFor("/landing/wall/t00.jpg")).toBe(YEAR);
    expect(await policyFor("/landing/grid/-561cZmir5Q.jpg")).toBe(YEAR);
  });

  // A missing still matches too; Next's 404 overwrites Cache-Control on the wire
  // (measured on the release build, 2026-09-16).
  it("matches a still that is not there too, which the 404 then overwrites", async () => {
    expect(await policyFor("/landing/nope.jpg")).toBe(YEAR);
  });

  it("claims nothing outside those two", async () => {
    expect(await policyFor("/")).toBeUndefined();
    expect(await policyFor("/demo")).toBeUndefined();
  });
});
