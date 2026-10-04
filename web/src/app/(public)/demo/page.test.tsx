// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ feed: [] as unknown[], console: [] as unknown[] }));

vi.mock("@/lib/api/search", () => ({
  readFeed: async (params: unknown) => {
    calls.feed.push(params);
    return { kind: "ok", feed: { profile: null, items: [], has_more: false, next_offset: null } };
  },
}));
vi.mock("@/components/public/console/bootstrap", () => ({
  loadConsole: async () => ({
    askEnabled: true,
    boot: "ok",
    initial: { mode: "ask", q: "", type: "all" },
    initialSearch: null,
  }),
}));
vi.mock("@/components/public/console/Console", () => ({
  Console: (props: { path: string; tags?: string }) => {
    calls.console.push(props);
    return <div data-testid="console" />;
  },
}));
vi.mock("./edition", () => ({
  TAG: "series:aie-paris-2026",
  readEdition: async () => ({ kind: "ok", page: { talks: [] } }),
}));

import DemoPage from "./page";

beforeEach(() => {
  calls.feed.length = 0;
  calls.console.length = 0;
});

const open = async (query: Record<string, string>) =>
  render(await DemoPage({ params: Promise.resolve({}), searchParams: Promise.resolve(query) }));

it("puts the sample feed above Ask, over the whole corpus by default", async () => {
  await open({});
  const feed = screen.getByRole("region", { name: "Sample feed" });
  const ask = screen.getByRole("heading", { name: "Ask the same talks" });
  expect(feed.compareDocumentPosition(ask)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  expect(calls.feed).toEqual([{ tags: undefined, limit: 10, offset: 0 }]);
  expect(calls.console[0]).toMatchObject({ path: "/demo", tags: undefined });
});

it("narrows the feed and Ask to Paris, and keeps the filter in Ask's URL", async () => {
  await open({ edition: "paris", offset: "10" });
  expect(calls.feed).toEqual([{ tags: "series:aie-paris-2026", limit: 10, offset: 10 }]);
  expect(calls.console[0]).toMatchObject({
    path: "/demo?edition=paris",
    tags: "series:aie-paris-2026",
  });
  expect(screen.getByRole("link", { name: "AI Engineer Paris" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});
