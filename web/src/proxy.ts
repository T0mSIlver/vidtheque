import { NextResponse, type NextRequest } from "next/server";

// The document headers every page carries (frontend-migration.md §1b). React
// ships inline scripts, so script-src is the per-request nonce form; Next reads
// the nonce back off the request's CSP header and stamps each script with it.

// Development only (§1b): 'unsafe-eval' for React's dev stacks, the API origin
// because the two processes sit on two ports, and data:/blob:/ws: for the
// overlay and HMR.
function devSources(): { img: string; connect: string } {
  let api = "";
  try {
    if (process.env.VIDTHEQUE_API_URL) api = " " + new URL(process.env.VIDTHEQUE_API_URL).origin;
  } catch {
    // An unparseable URL is reported by the API client, not here.
  }
  return { img: `'self' data: blob:${api}`, connect: `'self' ws:${api}` };
}

function policy(nonce: string, isDev: boolean): string {
  const dev = isDev ? devSources() : null;
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src ${dev ? dev.img : "'self'"}`,
    "font-src 'self'",
    `connect-src ${dev ? dev.connect : "'self'"}`,
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
  ].join("; ");
}

export function proxy(request: NextRequest): NextResponse {
  const nonce = btoa(crypto.randomUUID());
  const csp = policy(nonce, process.env.NODE_ENV !== "production");

  // On the request for the renderer to stamp, on the response for the browser.
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "no-referrer");
  // Mirrors next.config.ts headers(), which is the copy that reaches the wire
  // on a rendered document; this one covers the RSC payloads.
  const path = request.nextUrl.pathname;
  if (path === "/dashboard" || path.startsWith("/dashboard/")) {
    response.headers.set("Cache-Control", "no-store");
  }
  return response;
}

// Documents only: not the build output, not Python's paths (next.config.ts
// PYTHON_PATHS), not public/ files, and not prefetches, whose later chunk loads
// 'strict-dynamic' already allows. The source must stay a literal for the build.
export const config = {
  matcher: [
    {
      source:
        "/((?!_next|api|frames|mcp|auth|\\.well-known|healthz|dashboard/api/|dashboard/logout$|landing|favicon\\.ico|icon\\.svg|videos/[^/]+/export\\.md).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
