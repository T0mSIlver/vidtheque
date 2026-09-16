// The wire contract of `/dashboard/api/*` (dashboard.md §19-§22,
// frontend-migration.md §§4-7). The projection redacts by omission, and
// omission is `null`: a nullable field needs a designed absent state, never the
// word "null" on a screen. `z.object` strips unknown keys, so a field Python
// adds later is ignored until a schema asks for it.
import { z } from "zod";

// These parse in the browser under a `script-src` without `'unsafe-eval'`, so
// Zod's `new Function` probe would be a CSP violation for nothing.
z.config({ jitless: true });

export const epoch = () => z.number().int();
export const seconds = () => z.number();
export const count = () => z.number().int();
/** A clock the store may not have: an empty or half-built corpus. */
export const clockOf = () => epoch().nullable();

const BASE = "http://dashboard.invalid/";

function isHttpUrl(value: string): boolean {
  try {
    const { protocol } = new URL(value, BASE);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

/** An absolute `http(s)` URL or a path on this instance's origin. `javascript:`
 *  and `data:` never reach an `href` or a `src`. */
export const httpUrl = () =>
  z.string().refine(isHttpUrl, "must be an http(s) URL or a same-origin path");

/** A destination a refusal sends a browser to: never `//host` or `/\host`,
 *  which are absolute URLs wearing a path's clothes. */
export const localUrl = () =>
  z
    .string()
    .refine(
      (value) => isHttpUrl(value) && !value.startsWith("//") && !value.startsWith("/\\"),
      "must be a same-origin path or an http(s) URL",
    );

/** The refusal envelope. Partial, because a proxy's HTML 502 is still an error
 *  this client carries. */
export const PartialRefusal = z.object({
  error: z.string().optional(),
  message: z.string().optional(),
  next: z.string().nullable().optional(),
});
export type PartialRefusal = z.infer<typeof PartialRefusal>;

/** A tool's typed refusal riding on a payload (`code`, not `error`). */
export const ToolError = z.object({
  code: z.string(),
  message: z.string(),
  next: z.string().nullable(),
  retry_after_s: z.number().nullable().optional(),
});
export type ToolError = z.infer<typeof ToolError>;

// One observation of the pipeline, never a history (dashboard.md §15). Words
// are strings rather than enums so a new one renders neutral.
export const WorkerModel = z.object({
  task: z.string(),
  model: z.string(),
  loaded: z.boolean(),
});
export type WorkerModel = z.infer<typeof WorkerModel>;

export const Worker = z.object({
  state: z.string(),
  detail: z.string(),
  models: z.array(WorkerModel),
});
export type Worker = z.infer<typeof Worker>;

export const Readiness = z.object({
  mcp: z.string(),
  database: z.string(),
  vectors: z.object({
    enabled: z.boolean(),
    // The operator's sentence; the projection drops it and keeps `enabled`.
    reason: z.string().nullable(),
  }),
  // `null` in the projection: the probe is not made.
  worker: Worker.nullable(),
  checked_at: epoch(),
});
export type Readiness = z.infer<typeof Readiness>;

export const Storage = z.object({
  keyframe_bytes: count(),
  database_bytes: count(),
});
export type Storage = z.infer<typeof Storage>;

export const Pagination = z.object({ limit: count(), offset: count(), has_more: z.boolean() });
