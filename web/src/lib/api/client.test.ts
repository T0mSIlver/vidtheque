import { describe, expect, it } from "vitest";
import { ZodError } from "zod";
import { ApiError, createClient } from "./client";

// A fetch that records the request and answers with a canned response.
function fake(status: number, body: unknown, headers: Record<string, string> = {}) {
  const calls: { url: URL; init: RequestInit }[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: new URL(String(input)), init: init ?? {} });
    const text = typeof body === "string" ? body : JSON.stringify(body);
    return new Response(text, {
      status,
      headers: { "content-type": "application/json", ...headers },
    });
  }) as typeof fetch;
  return { calls, fetchImpl };
}

const HIT = {
  source: "transcript",
  video_id: "abc",
  title: "t",
  channel: "c",
  start: 1.5,
  end: 3,
  match_start: 2,
  match_cue_id: 7,
  text: "hello",
  link: "https://youtu.be/abc?t=1",
  cue_ids: [7],
  frame_id: null,
  score: 0.5,
  timestamp: "0:01",
  thumb: null,
  thumb_large: null,
};

const SEARCH = {
  query: "hello",
  content_type: "all",
  results: [HIT],
  pagination: { limit: 10, offset: 0, has_more: false, approx_total: 1 },
  notes: [],
  data_status: null,
};

describe("createClient", () => {
  it("builds the URL from the base and drops empty params", async () => {
    const { calls, fetchImpl } = fake(200, SEARCH);
    const client = createClient({ baseUrl: "https://api.test/", fetch: fetchImpl });
    await client.search({ q: "hello", limit: 10, channel: undefined, video_id: "" });
    expect(calls[0].url.toString()).toBe("https://api.test/api/search?q=hello&limit=10");
  });

  it("forwards the visitor address under the header the API trusts", async () => {
    const { calls, fetchImpl } = fake(200, SEARCH);
    const client = createClient({ baseUrl: "https://api.test", fetch: fetchImpl });
    await client.search({ q: "hello" }, { clientIp: "203.0.113.9" });
    const headers = new Headers(calls[0].init.headers);
    expect(headers.get("CF-Connecting-IP")).toBe("203.0.113.9");
    expect(headers.get("accept")).toBe("application/json");
  });

  it("returns the parsed payload, typed", async () => {
    const { fetchImpl } = fake(200, SEARCH);
    const client = createClient({ baseUrl: "https://api.test", fetch: fetchImpl });
    const page = await client.search({ q: "hello" });
    expect(page.results[0].timestamp).toBe("0:01");
    expect(page.pagination.has_more).toBe(false);
  });

  it("rejects a payload that no longer matches the contract", async () => {
    const { fetchImpl } = fake(200, { ...SEARCH, results: [{ ...HIT, start: "1.5" }] });
    const client = createClient({ baseUrl: "https://api.test", fetch: fetchImpl });
    await expect(client.search({ q: "hello" })).rejects.toBeInstanceOf(ZodError);
  });

  it("turns the facade's error envelope into an ApiError", async () => {
    const { fetchImpl } = fake(400, {
      error: "E_EMPTY_QUERY",
      message: "search needs either a query or at least one filter.",
      next: "pass q, or use list-videos to browse the library.",
    });
    const client = createClient({ baseUrl: "https://api.test", fetch: fetchImpl });
    const err = await client.search({ q: "" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(400);
    expect(apiErr.code).toBe("E_EMPTY_QUERY");
    expect(apiErr.next).toMatch(/list-videos/);
  });

  it("keeps Retry-After on a 429", async () => {
    const { fetchImpl } = fake(
      429,
      { error: "E_RATE_LIMIT", message: "slow down" },
      { "retry-after": "17" },
    );
    const client = createClient({ baseUrl: "https://api.test", fetch: fetchImpl });
    const err = (await client.meta().catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(429);
    expect(err.retryAfter).toBe(17);
  });

  it("survives a non-JSON error body from a proxy", async () => {
    const { fetchImpl } = fake(502, "<html>bad gateway</html>");
    const client = createClient({ baseUrl: "https://api.test", fetch: fetchImpl });
    const err = (await client.meta().catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(502);
    expect(err.code).toBe("E_HTTP");
  });
});
