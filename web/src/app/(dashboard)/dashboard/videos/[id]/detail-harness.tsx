// The video detail mounted on the dashboard harness, and the fixtures its
// panel tests share. Each test file still declares its own
// `vi.mock("next/navigation", …)`.
import { screen } from "@testing-library/react";
import { mountDashboard, type Answer } from "@/test/dashboard/harness";
import { OWNER_SESSION } from "@/test/dashboard/fixtures";
import { REINDEXED } from "@/test/dashboard/index-fixtures";
import { OWNER_CUES, OWNER_VIDEO } from "@/test/dashboard/library-fixtures";
import { VideoDetailView } from "./VideoDetailView";

export function mountVideo(
  detail: Answer | ((url: string) => Answer | Promise<Answer>),
  {
    // A function where the answer depends on which page of cues was asked for.
    cues = { body: OWNER_CUES } as Answer | ((url: string) => Answer | Promise<Answer>),
    post = { body: REINDEXED } as Answer,
    search = "",
    session = OWNER_SESSION as unknown,
    videoId = "kCc8FmEb1nY",
  } = {},
) {
  return mountDashboard(<VideoDetailView videoId={videoId} />, {
    path: `/dashboard/videos/${videoId}`,
    search,
    session,
    routes: {
      "/dashboard/api/library/*": (request) =>
        typeof detail === "function" ? detail(request.url) : detail,
      "/dashboard/api/videos/*": (request) =>
        typeof cues === "function" ? cues(request.url) : cues,
      "POST /dashboard/videos/*": post,
    },
  });
}

/** `OWNER_VIDEO` with a second OCR line on frame 1: the pairing is by index,
 *  and one line cannot tell a working pairing from a lit-everything one. */
export const TWO_LINES = {
  ...OWNER_VIDEO,
  frames: {
    ...OWNER_VIDEO.frames,
    frames: OWNER_VIDEO.frames.frames.map((frame) =>
      frame.ord === 1
        ? {
            ...frame,
            lines: [
              ...frame.lines,
              { line_no: 1, text: "loss 3.14", conf: 0.71, box: [0.1, 0.6, 0.4, 0.68] },
            ],
          }
        : frame,
    ),
  },
};

/**
 * A page of cues for whatever `offset` and `limit` were asked for. Each cue's
 * text is its own ordinal, so an appended batch and a prepended one differ,
 * and `limit` is echoed back as the endpoint echoes the number it ran.
 */
export function cuePage(url: string): unknown {
  const asked = new URL(url, "http://localhost").searchParams;
  const offset = Number(asked.get("offset") ?? 0);
  const limit = Number(asked.get("limit") ?? 50);
  const rows = Array.from({ length: Math.min(limit, 25) }, (_, index) => ({
    ...OWNER_CUES.cues[1],
    start_s: offset + index,
    end_s: offset + index + 1,
    t: offset + index,
    text: `cue ${offset + index}`,
  }));
  return { cues: rows, offset, limit, has_more: true };
}

/** The shot band, with the geometry jsdom does not compute: 1000px wide, a
 *  hundred pixels down the viewport. */
export async function bandOf(): Promise<HTMLElement> {
  const band = await screen.findByRole("list", { name: "Shots across the runtime" });
  band.getBoundingClientRect = () =>
    ({ left: 0, top: 100, right: 1000, bottom: 148, width: 1000, height: 48 }) as DOMRect;
  return band;
}

/** `OWNER_VIDEO` one keyframe per strip page, at `offset`. */
export function stripPage(offset: number) {
  return {
    ...OWNER_VIDEO,
    frames: {
      ...OWNER_VIDEO.frames,
      limit: 1,
      offset,
      has_more: offset + 1 < OWNER_VIDEO.frames.frames.length,
      frames: [OWNER_VIDEO.frames.frames[offset]],
    },
  };
}
