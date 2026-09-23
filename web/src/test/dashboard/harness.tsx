// Mounts a dashboard page with its chassis, a fetch answered by route and a
// movable URL. The file must declare:
//   vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule);
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, vi } from "vitest";
import { Chrome } from "@/components/dashboard/Chrome";
import { clearResources } from "@/lib/dashboard/resource";
import { OWNER_SESSION } from "./fixtures";
import { navigateTo, resetNavigation } from "@/test/next";

export type Answer = { status?: number; body?: unknown; headers?: Record<string, string> };

type Request = {
  method: string;
  path: string;
  url: string;
  fields: URLSearchParams;
  headers: Headers;
};

/** A canned answer, a queue of them (the last one repeats), or a function of
 *  the request. */
export type Route = Answer | Answer[] | ((request: Request) => Answer | Promise<Answer>);

/**
 * Keys are `"/dashboard/api/jobs"` (a GET, matched on the path alone, so any
 * query) or `"POST /dashboard/jobs/j1/cancel"`. A trailing `*` matches a prefix.
 * `/dashboard/api/session` answers `session` unless a route overrides it, and
 * anything unrouted answers `404 {}`.
 */
type Routes = Record<string, Route>;

export interface MountOptions {
  routes?: Routes;
  /** The page's path, which the rail reads for its section. */
  path?: string;
  search?: string;
  session?: unknown;
  /** Render without the chassis — for a component that is not a page. */
  bare?: boolean;
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearResources();
  try {
    sessionStorage.clear();
  } catch {
    // A node-environment file never mounts.
  }
});

export async function mountDashboard(ui: ReactElement, options: MountOptions = {}) {
  const { routes = {}, path = "/dashboard", search = "", session = OWNER_SESSION } = options;
  clearResources();
  const requests: Request[] = [];
  const queues = new Map<string, Answer[]>();

  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const parsed = new URL(url, "http://dashboard.test");
    const fields = new URLSearchParams(typeof init?.body === "string" ? init.body : "");
    const headers = new Headers(init?.headers);
    const request = { method, path: parsed.pathname, url, fields, headers };
    requests.push(request);

    const key = routeKey(routes, method, parsed.pathname);
    let answer: Answer;
    if (key === null) {
      answer =
        method === "GET" && parsed.pathname === "/dashboard/api/session"
          ? { body: session }
          : { status: 404, body: {} };
    } else {
      const route = routes[key];
      if (typeof route === "function") answer = await route(request);
      else if (Array.isArray(route)) {
        if (!queues.has(key)) queues.set(key, [...route]);
        const queue = queues.get(key)!;
        answer = queue.length > 1 ? queue.shift()! : queue[0];
      } else answer = route;
    }
    const text = typeof answer.body === "string" ? answer.body : JSON.stringify(answer.body ?? {});
    return new Response(text, {
      status: answer.status ?? 200,
      headers: { "content-type": "application/json", ...answer.headers },
    });
  });
  vi.stubGlobal("fetch", fetcher);

  const spies = resetNavigation(search, path, { follow: true });
  const view = render(options.bare ? ui : <Chrome>{ui}</Chrome>);

  return {
    ...view,
    ...spies,
    fetcher,
    requests,
    /** Requests to paths starting with `prefix`, GETs unless `method` says. */
    calls: (prefix: string, method = "GET") =>
      requests.filter((request) => request.method === method && request.path.startsWith(prefix)),
    posts: () => requests.filter((request) => request.method === "POST"),
    navigate: navigateTo,
  };
}

/** A text matcher over an element's whole text, however many elements it is
 *  split across: the deepest element that holds all of it matches. */
export function fullText(expected: string | RegExp) {
  const test = (text: string) => {
    const normal = text.replace(/\s+/g, " ").trim();
    return typeof expected === "string" ? normal === expected : expected.test(normal);
  };
  return (_content: string, element: Element | null) =>
    element !== null &&
    test(element.textContent ?? "") &&
    !Array.from(element.children).some((child) => test(child.textContent ?? ""));
}

/** A promise the test resolves: an answer held back until it says so. */
export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
}

function routeKey(routes: Routes, method: string, path: string): string | null {
  const keys = Object.keys(routes);
  const named = (key: string) => {
    const [verb, target] = key.includes(" ") ? key.split(" ", 2) : ["GET", key];
    return { verb: verb.toUpperCase(), target };
  };
  const exact = keys.find((key) => {
    const { verb, target } = named(key);
    return verb === method && target === path;
  });
  if (exact) return exact;
  return (
    keys.find((key) => {
      const { verb, target } = named(key);
      return verb === method && target.endsWith("*") && path.startsWith(target.slice(0, -1));
    }) ?? null
  );
}
