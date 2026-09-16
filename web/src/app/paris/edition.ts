import { cache } from "react";
import { api, ApiError, type EditionResponse } from "@/lib/api";
import { visitorIp } from "@/lib/api/search";

export const SLUG = "aie-paris-2026";
/** Every read on this page stays inside the edition (aie-paris-2026.md §4.3). */
export const TAG = "series:aie-paris-2026";

export type EditionOutcome =
  | { kind: "ok"; page: EditionResponse }
  | { kind: "refused"; word: "refused"; message: string; next?: string }
  | { kind: "unreachable"; word: "unavailable" };

// One read per request, shared by the console's talk labels and the programme.
export const readEdition = cache(async (): Promise<EditionOutcome> => {
  try {
    return {
      kind: "ok",
      page: await api().edition(
        SLUG,
        { limit: 100, video_limit: 50 },
        { clientIp: await visitorIp(), cache: "no-store" },
      ),
    };
  } catch (error) {
    if (error instanceof ApiError) {
      return { kind: "refused", word: "refused", message: error.message, next: error.next };
    }
    return { kind: "unreachable", word: "unavailable" };
  }
});
