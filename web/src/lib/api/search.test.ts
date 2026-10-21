import { afterEach, describe, expect, it, vi } from "vitest";

const incoming = vi.hoisted(() => ({ headers: new Headers() }));
vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({ headers: async () => incoming.headers }));

import { visitorIp } from "./search";

describe("visitorIp", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    incoming.headers = new Headers();
  });

  it("reads the configured edge header", async () => {
    vi.stubEnv("VIDTHEQUE_CLIENT_IP_HEADER", "X-Real-IP");
    incoming.headers = new Headers({ "x-real-ip": "198.51.100.4", "cf-connecting-ip": "1.2.3.4" });
    expect(await visitorIp()).toBe("198.51.100.4");
  });

  it("defaults to Cloudflare's header when the variable is unset", async () => {
    incoming.headers = new Headers({ "cf-connecting-ip": "198.51.100.4" });
    expect(await visitorIp()).toBe("198.51.100.4");
  });

  // Empty means forward nothing: the API then buckets on this server.
  it("forwards nothing when the variable is empty", async () => {
    vi.stubEnv("VIDTHEQUE_CLIENT_IP_HEADER", "");
    incoming.headers = new Headers({ "cf-connecting-ip": "198.51.100.4", "x-forwarded-for": "6.6.6.6" });
    expect(await visitorIp()).toBeUndefined();
  });

  // X-Forwarded-For's first hop is whatever the client wrote.
  it("never falls back to X-Forwarded-For", async () => {
    incoming.headers = new Headers({ "x-forwarded-for": "6.6.6.6, 198.51.100.4" });
    expect(await visitorIp()).toBeUndefined();
  });
});
