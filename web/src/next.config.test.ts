import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match";
import { afterEach, describe, expect, it, vi } from "vitest";
import nextConfig from "../next.config";

// The development rewrites, as paths and not as prose. Everything this file
// asserts was once a comment in `next.config.ts` that nobody could run, and the
// one thing the comment got wrong cost every detail page in development: an
// `afterFiles` catch-all on `/dashboard/:path*` is consulted *before* dynamic
// routes, so `/dashboard/videos/{video_id}` went to Python — which has served
// no page since 2026-09-06 — and the browser got a `404` (frontend-migration.md
// §1c).

const API = "http://localhost:8080";

/** The rewrites as a dev server would build them, with all three phases
 *  present: an absent phase and an empty one are the same forwarding, and this
 *  file is about which paths travel, not about which key was written down. */
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

/** Whether one phase's sources match a pathname, compiled the way Next compiles
 *  them — `getPathMatch` is what its own router calls on a rewrite `source`, so
 *  a `:path*` that behaves differently here would be this test's bug. */
function matches(sources: { source: string }[], pathname: string): boolean {
  return sources.some((entry) => getPathMatch(entry.source)(pathname) !== false);
}

describe("the development rewrites", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("forwards nothing after the router has looked for a page", async () => {
    // `beforeFiles` is where a forward can be true of a path this app also has
    // a page for, because it runs first and on purpose. `afterFiles` runs
    // between the static routes and the dynamic ones, which is a window no
    // rewrite on this surface wants to sit in.
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

  it("sends nothing anywhere in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("VIDTHEQUE_API_URL", API);
    expect(await nextConfig.rewrites!()).toEqual([]);
  });
});
