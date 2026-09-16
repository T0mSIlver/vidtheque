// What the console's first paint needs, resolved on the server: whether ask
// exists, which snapshot the URL names, and page one for a search deep link.
import type { SearchOutcome } from "@/lib/api/outcome";
import { readMeta, searchCorpus } from "@/lib/search";
import type { Boot } from "./Cold";
import { parseSnapshot, type Snapshot } from "./url";

export interface Bootstrap {
  askEnabled: boolean;
  boot: Boot;
  initial: Snapshot;
  initialSearch: SearchOutcome | null;
}

type SearchParams = Record<string, string | string[] | undefined>;

export async function loadConsole(
  searchParams: Promise<SearchParams>,
  { tags }: { tags?: string } = {},
): Promise<Bootstrap> {
  const params = await searchParams;
  // A `?q=` link is a search whatever meta says, so its read starts alongside.
  const early =
    params.ask === undefined && params.q !== undefined ? parseSnapshot(params, false) : null;
  const eager = early?.q ? searchCorpus({ q: early.q, type: early.type, tags }) : null;
  const meta = await readMeta();
  const askEnabled = meta.kind === "ok" && meta.meta.ask_enabled;
  const initial = parseSnapshot(params, askEnabled);
  const initialSearch =
    initial.mode === "search" && initial.q
      ? await (eager ?? searchCorpus({ q: initial.q, type: initial.type, tags }))
      : null;
  return { askEnabled, boot: meta.kind === "ok" ? "ok" : meta.kind, initial, initialSearch };
}
