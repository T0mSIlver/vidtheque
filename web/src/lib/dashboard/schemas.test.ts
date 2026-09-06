import { describe, expect, it } from "vitest";
import { OWNER_OVERVIEW } from "@/test/dashboard-fixtures";
import { Overview, Session } from "./schemas";

// `static/dashboard.js` had one function guarding every URL it put in the DOM:
//
//     function safeUrl(value) { … return url.protocol === "http:" || "https:" }
//
// Nothing carried it over, and a React page hands a payload's string straight
// to an `href` or a `src`. So the guarantee lives in the schema now — the one
// place every payload passes through — and this is where it is held: a
// `javascript:` URL in a payload must not become a live one, whichever field
// it arrives in.

const SESSION = {
  version: "0.0.6",
  auth_mode: "token",
  readonly: false,
  write_side: true,
  writes_allowed: true,
  authenticated: true,
  is_owner: true,
  signed_in: true,
  policy: "owner",
  login_url: "/dashboard/login",
  sign_in_hint: null,
  accepts_password: true,
  accepts_token: true,
};

const withThumb = (thumb: string | null) => ({
  ...OWNER_OVERVIEW,
  recent: [{ ...OWNER_OVERVIEW.recent[0], thumb }],
});

describe("the URL fields of the dashboard payloads", () => {
  it("takes an http(s) URL and a same-origin path", () => {
    for (const thumb of [
      "/frames/kCc8FmEb1nY-000000.jpg?w=96&sig=abc",
      "https://localhost:3000/frames/kCc8FmEb1nY-000000.jpg",
      "http://localhost:3000/frames/kCc8FmEb1nY-000000.jpg",
      null,
    ]) {
      expect(Overview.safeParse(withThumb(thumb)).success, String(thumb)).toBe(true);
    }
  });

  // A `src` the browser would run rather than fetch. The parse fails, which is
  // the loud answer: `DashboardShapeError` and the page's refusal, rather than
  // a scheme nobody looked at reaching the DOM.
  it("refuses a scheme the browser would execute", () => {
    for (const thumb of [
      "javascript:alert(1)",
      "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
      "vbscript:msgbox(1)",
      "file:///etc/passwd",
    ]) {
      expect(Overview.safeParse(withThumb(thumb)).success, thumb).toBe(false);
    }
  });

  // The sign-in page is where a refused reader is *sent*, so its field is
  // fenced harder than the rest: `//host` is an absolute URL wearing a path's
  // clothes, and it resolves to `https:` — the exact shape `safeNext` refuses
  // for `next`, said here for the field that carries the destination.
  it("keeps login_url on this instance", () => {
    for (const login_url of ["/dashboard/login", "https://box.example/dashboard/login", null]) {
      expect(Session.safeParse({ ...SESSION, login_url }).success, String(login_url)).toBe(true);
    }
    for (const login_url of ["//evil.example/dashboard/login", "/\\evil.example", "javascript:1"]) {
      expect(Session.safeParse({ ...SESSION, login_url }).success, login_url).toBe(false);
    }
  });
});
