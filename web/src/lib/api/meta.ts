// What the demo's chrome renders about *itself*, derived from `/api/meta`
// (demo-site.md §2.3): the corpus count beside the wordmark, the way into the
// browsable corpus, and the line somebody actually pastes into Claude Code.
//
// Pure, and no `server-only` import in its dependency graph, because these are
// the rules the read is worth having — the read itself is `readMeta` in
// `lib/search.ts`, beside the demo's other request-time read.
import type { Meta } from "./schemas";

// Rate-limited and unreachable are different facts and the page says which: one
// is over in a minute, the other is the server being down.
//
// That distinction is the whole of the 2026-08-28 fix. `/api/meta` shares the
// search rate bucket, so a visitor who spent it and reloaded got a page whose
// every meta-derived field was `undefined` — the MCP line read "undefined", the
// CLI snippet read "undefined", and the ask switch hid itself as though the
// deployment had no key, with nothing on screen saying why. A limiter 429
// answers with a JSON *error* body, so a read that guards only against a parse
// failure never notices it. Any non-2xx takes the honest path.
export type MetaOutcome =
  | { kind: "ok"; meta: Meta }
  | { kind: "rate_limited" }
  | { kind: "unreachable" };

/** The masthead line. The size of the corpus is a fact about the corpus, not
 *  about this search, so it belongs beside the wordmark and not in the count
 *  line under the box. */
export function corpusCount(videos: number | null | undefined): string | null {
  if (!videos) return null;
  return `${videos} talk${videos === 1 ? "" : "s"} watched`;
}

/** `browse` as an href, or nothing — the link is hidden until the server says
 *  the route group is there, so a deployment running with the dashboard off (or
 *  an edge rule that 404s it) leaves no invitation to a dead page.
 *
 *  A same-origin *path* check and not `safeUrl`: that one resolves against the
 *  current document and would happily accept an absolute URL on another host.
 *  One leading slash, never two (`//host` is cross-origin), and nothing but the
 *  characters a route prefix is made of (demo-site.md §2.3). */
export function browsePath(browse: string | null | undefined): string | null {
  if (typeof browse !== "string") return null;
  return /^\/[a-z0-9][a-z0-9/_-]*$/i.test(browse) ? browse : null;
}

/** The one-liner under the endpoint (demo-site.md §6 item 6). */
export function claudeCommand(mcpUrl: string): string {
  return `claude mcp add --transport http vidtheque ${mcpUrl}`;
}
