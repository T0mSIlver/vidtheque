// The library's cached reads. Each function is a cache entry keyed by its
// arguments, with a named lifetime, so a page never decides caching policy:
// it calls a function, and the function says how fresh it is.
//
// `cacheLife('minutes')` is stale 5 min, revalidated every 1 min, expired
// after 1 h; `'hours'` revalidates hourly. Both serve the stale copy while a
// fresh one is fetched, so a visitor never waits on the Python API for a page
// someone else already loaded.
import { cacheLife, cacheTag } from "next/cache";
import { api, ApiError, type VideoDetail, type VideosResponse } from "@/lib/api";

export const PAGE = 48;

export async function listVideos(offset: number): Promise<VideosResponse> {
  "use cache";
  cacheLife("minutes");
  cacheTag("library");
  return api().videos({ limit: PAGE, offset });
}

// A missing video is `null`, not a throw: the page decides that null means
// 404, and `notFound()` has to be called from the page's own render path.
export async function getVideo(videoId: string): Promise<VideoDetail | null> {
  "use cache";
  cacheLife("hours");
  cacheTag("library", `video-${videoId}`);
  try {
    return await api().video(videoId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}
