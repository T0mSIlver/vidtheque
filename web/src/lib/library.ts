// The library's cached reads. Each function is a cache entry keyed by its
// arguments, with a named lifetime, so a page never decides caching policy:
// it calls a function, and the function says how fresh it is.
//
// These were `"use cache"` with `cacheLife('minutes')` and `cacheLife('hours')`
// until the pages had to render per request for the CSP nonce (`proxy.ts`).
// Cache Components is off with them, and `"use cache"` is a Cache Components
// feature, so the same two entries are `unstable_cache` — which is the caching
// model that does not depend on the page being prerenderable, and which keeps
// the tags. `fetch`-level caching would not have survived the move: a
// per-request page sets the default of every fetch under it to `no-store`.
//
// The revalidation periods are the ones the named lifetimes had: `'minutes'`
// revalidated every 60s, `'hours'` every hour, and both serve the stale copy
// while the fresh one is fetched, so a visitor never waits on the Python API
// for a page someone else already loaded. What a named lifetime also carried
// and this does not is `stale` (how long the client's router may reuse the
// page without asking) and `expire` (the age at which a stale copy stops being
// served at all) — the router half is gone with the prerender either way.
import { unstable_cache } from "next/cache";
import { api, ApiError, type VideoDetail, type VideosResponse } from "@/lib/api";

export const PAGE = 48;

const MINUTES = 60;
const HOURS = 60 * 60;

export const listVideos = unstable_cache(
  async (offset: number): Promise<VideosResponse> => api().videos({ limit: PAGE, offset }),
  ["library", "videos"],
  { revalidate: MINUTES, tags: ["library"] },
);

// A missing video is `null`, not a throw: the page decides that null means
// 404, and `notFound()` has to be called from the page's own render path.
//
// The entry is built per id rather than once, because `unstable_cache` fixes
// its tags at the call site and `video-${id}` has to name one video — the tag
// `cacheTag` used to write. The cache key is the same either way.
export function getVideo(videoId: string): Promise<VideoDetail | null> {
  return unstable_cache(fetchVideo, ["library", "video", videoId], {
    revalidate: HOURS,
    tags: ["library", `video-${videoId}`],
  })(videoId);
}

async function fetchVideo(videoId: string): Promise<VideoDetail | null> {
  try {
    return await api().video(videoId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}
