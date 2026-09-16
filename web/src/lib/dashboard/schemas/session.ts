import { z } from "zod";
import { localUrl } from "./common";

/** What this deployment is. Readable signed out (dashboard.md §6, §19). */
export const Session = z.object({
  version: z.string(),
  auth_mode: z.string(),
  readonly: z.boolean(),
  write_side: z.boolean(),
  writes_allowed: z.boolean(),
  authenticated: z.boolean(),
  is_owner: z.boolean(),
  /** The validated session row: will the next request be served. */
  signed_in: z.boolean(),
  /** The cookie's presence: is there a cookie to clear. Newer than the rest,
   *  so it defaults for an older instance. */
  has_session_cookie: z.boolean().optional().default(false),
  /** Why the database refuses writes, in the instance's words. */
  writes_refused_reason: z.string().nullable().optional().default(null),
  policy: z.string(),
  /** `null` where no write side is registered, which is also where
   *  `/dashboard/login` is not routed. */
  login_url: localUrl().nullable(),
  sign_in_hint: z.string().nullable(),
  accepts_password: z.boolean(),
  accepts_token: z.boolean(),
});
export type Session = z.infer<typeof Session>;

/** `POST /dashboard/login`'s answer. `next` is fenced again before use. */
export const SignedIn = z.object({
  signed_in: z.boolean(),
  next: z.string(),
});
export type SignedIn = z.infer<typeof SignedIn>;
