import { expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));

import { refuseWithoutDashboard } from "./presence";

const answering = (status: number) => (async () => new Response(null, { status })) as typeof fetch;

it("404s the owner's pages where the server registers no dashboard", async () => {
  await expect(refuseWithoutDashboard(answering(404), "http://mcp:8080")).rejects.toThrow(
    "NEXT_NOT_FOUND",
  );
});

it("renders them where the dashboard answers, signed in or not, or the API is down", async () => {
  await expect(refuseWithoutDashboard(answering(200), "http://mcp:8080")).resolves.toBeUndefined();
  await expect(refuseWithoutDashboard(answering(401), "http://mcp:8080")).resolves.toBeUndefined();
  const down = (async () => {
    throw new TypeError("fetch failed");
  }) as typeof fetch;
  await expect(refuseWithoutDashboard(down, "http://mcp:8080")).resolves.toBeUndefined();
});
