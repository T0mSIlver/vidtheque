import { NextResponse, type NextRequest } from "next/server";

// Every document this server sends carries the headers the Python instance
// sent with the landing and the demo while it served them: a strict CSP,
// `frame-ancestors 'none'` with the `X-Frame-Options` twin for the
// middleboxes that still only read that one, no referrer, no sniffing.
// `auth/login.py` still carries the framing pair for the consent screen. The
// two front doors are the same two front doors, and they should not become a
// different security surface for being rendered by React — this file is now
// the only place they get one at all.
//
// The difference is that a React page cannot say `script-src 'self'` and mean
// it: the framework ships an inline bootstrap script and streams the RSC
// payload as more inline scripts. So the policy is the nonce form instead —
// a fresh nonce per request, which Next reads back out of the request's own
// CSP header and stamps on every script it emits. `'strict-dynamic'` then
// covers the chunks those scripts load, and the host allowlist that CSP3
// browsers ignore next to it (`'self'`) stays for the ones that do not.
//
// A nonce is only worth anything if it is new every time, which means every
// document is rendered per request — `app/layout.tsx` says so with
// `connection()`, and `cacheComponents` is off because a partial prerender
// would serve a shell whose scripts were stamped with somebody else's nonce.
//
// `style-src` is the one directive looser than Python's. `'unsafe-inline'`
// buys two things the design actually does: EvidenceFrame positions each OCR
// box with a `style=` attribute computed from the box's own coordinates, and
// the hero's lift and Frame's 16:9 box do the same. React renders those as
// inline style attributes, which `style-src 'self'` refuses. CSSOM writes from
// the hero's script — `el.style.transform` and friends — are not governed by
// CSP at all and are not what this is for.

const PROD_IMG = "'self'";

// Development only, and never the shape production runs:
// - `'unsafe-eval'`, because React rebuilds server stacks in the browser with it;
// - the API origin on `img-src`, because the two processes are on two ports and
//   the frame URLs the API hands back point at its own host rather than at this
//   one the way they do behind the production proxy;
// - `data:`/`blob:` and `ws:` for the dev overlay and the HMR socket.
function devSources(): { img: string; connect: string } {
  let api = "";
  try {
    if (process.env.VIDTHEQUE_API_URL) api = " " + new URL(process.env.VIDTHEQUE_API_URL).origin;
  } catch {
    // An unparseable URL is the app's problem to report, not this file's.
  }
  return { img: `'self' data: blob:${api}`, connect: `'self' ws:${api}` };
}

function policy(nonce: string, isDev: boolean): string {
  const dev = isDev ? devSources() : null;
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src ${dev ? dev.img : PROD_IMG}`,
    "font-src 'self'",
    // The ask stream is same-origin: the browser posts to `/api/ask` on this
    // origin and the proxy in front hands it to Python.
    `connect-src ${dev ? dev.connect : "'self'"}`,
    "frame-ancestors 'none'",
    // The demo's search box is a real form, and so is the dashboard rail's
    // Sign out — a POST to Python's `/dashboard/logout`, same origin, which
    // this allows and which needs no JavaScript to work. A policy that only
    // holds while the JavaScript works is the wrong shape.
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
  ].join("; ");
}

export function proxy(request: NextRequest): NextResponse {
  const nonce = btoa(crypto.randomUUID());
  const csp = policy(nonce, process.env.NODE_ENV !== "production");

  // On the request, because that is where the renderer looks for the nonce to
  // stamp; on the response, because that is where the browser reads the policy.
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}

// Every entry is spelled out, and the `missing` pair is repeated on each: the
// build parses this object statically and refuses a `matcher` built from a
// constant or a `map`, so the repetition is the price of the export being
// readable at compile time rather than a style choice.
//
// The `missing` pair excludes prefetches, which fetch an RSC payload rather
// than a document; the scripts a navigation then loads are loaded by scripts
// that already ran, which is exactly what `'strict-dynamic'` allows.
export const config = {
  matcher: [
    {
      // Documents only. Nothing under `/_next` is one — it is the build's own
      // output, the image optimizer and, in development, the HMR websocket,
      // whose upgrade this proxy breaks by touching it. `public/` is static
      // files that need no nonce. The paths the production proxy hands to
      // Python must reach it untouched: in development they are rewritten
      // upstream, and a policy written for these pages has no business riding
      // along. That list is `PYTHON_PATHS` in `next.config.ts`, and the export
      // is the one entry of it that is not a prefix — three segments under
      // `/videos`, so it needs the id spelled out to be excluded at all.
      source:
        "/((?!_next|api|frames|mcp|auth|\\.well-known|healthz|dashboard|landing|favicon\\.ico|icon\\.svg|videos/[^/]+/export\\.md).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
    // The `/dashboard` pages this app serves. The exclusion above keeps the
    // whole prefix out — `/dashboard/api/*` is JSON the pages fetch, and every
    // page not yet ported is Python's HTML, which carries its own policy — so
    // the ported ones are named back in, one entry each. Deliberately not a
    // prefix: this list is the record of what has been ported, and a port that
    // forgets to add its path ships a document with no CSP on it.
    // `next.config.ts` says the same thing from the routing side.
    {
      source: "/dashboard",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
    {
      source: "/dashboard/ledger",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
    {
      source: "/dashboard/videos",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
    {
      // The one ported page with an id in it. Written as a group rather than
      // `/dashboard/videos/:id` for the same reason the exclusion above is a
      // lookahead: every source here is already the regular expression Next
      // compiles it into, which is what lets `proxy.test.ts` run the matcher
      // rather than reimplement it. One segment, so `/videos/{id}/reindex` —
      // a POST that stays Python's — is not matched.
      source: "/dashboard/videos/([^/]+)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
