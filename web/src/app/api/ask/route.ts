// The one endpoint this server exposes to the browser: a streaming proxy for
// the API's `POST /api/ask`. A Route Handler rather than a Server Component
// because an answer is not a page — it is bytes arriving over ninety seconds
// that client code has to parse as they land — and rather than the browser
// calling Python directly because the Python host is server configuration.
//
// The upstream body is handed straight through: no re-framing, no buffering,
// so the event vocabulary in demo-site.md §3.5 is the API's alone.
import "server-only";
import type { NextRequest } from "next/server";

const ASK_TIMEOUT_MS = 200_000;

export async function POST(request: NextRequest): Promise<Response> {
  const base = process.env.VIDTHEQUE_API_URL;
  if (!base)
    return Response.json(
      { error: "E_CONFIG", message: "VIDTHEQUE_API_URL is not set" },
      { status: 500 },
    );

  let q = "";
  try {
    const body: unknown = await request.json();
    if (body && typeof body === "object" && "q" in body)
      q = String((body as { q: unknown }).q ?? "");
  } catch {
    /* an empty or non-JSON body is an empty question */
  }
  q = q.trim().slice(0, 400);
  if (!q)
    return Response.json(
      { error: "E_EMPTY_QUERY", message: "ask needs a question." },
      { status: 400 },
    );

  const headers = new Headers({
    accept: "text/event-stream",
    "content-type": "application/json",
  });
  const ipHeader = process.env.VIDTHEQUE_CLIENT_IP_HEADER ?? "CF-Connecting-IP";
  const ip =
    request.headers.get(ipHeader) ?? request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  if (ip) headers.set(ipHeader, ip);

  // The visitor closing the tab aborts this fetch, which closes the upstream
  // socket, which stops the model call (§3.5, "a disconnect stops the work").
  const upstream = await fetch(`${base.replace(/\/+$/, "")}/api/ask`, {
    method: "POST",
    headers,
    body: JSON.stringify({ q }),
    signal: AbortSignal.any([request.signal, AbortSignal.timeout(ASK_TIMEOUT_MS)]),
    cache: "no-store",
  });

  const out = new Headers({ "cache-control": "no-store" });
  const contentType = upstream.headers.get("content-type");
  if (contentType) out.set("content-type", contentType);
  const retryAfter = upstream.headers.get("retry-after");
  if (retryAfter) out.set("retry-after", retryAfter);
  if (contentType?.startsWith("text/event-stream")) {
    // Tell any proxy in front not to buffer; Cloudflare already exempts SSE.
    out.set("x-accel-buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}
