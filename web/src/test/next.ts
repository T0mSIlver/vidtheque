// Stand-ins for the App Router hooks a Client Component reads, over a URL that
// can change inside one mount.
//
// Two ways in. A file that mounts statically declares
// `vi.mock("next/navigation", async () => (await import("@/test/next")).navigationModule)`
// and drives the URL with `navigateTo`. An older file calls `mockNavigation()`
// and imports the component under test afterwards.
import { act } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import { vi } from "vitest";

const spies = {
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  prefetch: vi.fn(),
};

let pathname = "/demo";
let params = new URLSearchParams();
/** Whether `push` and `replace` move the URL, as a real router does. */
let follows = false;
const listeners = new Set<() => void>();

function setUrl(url: string) {
  const parsed = new URL(url, "http://dashboard.test");
  pathname = parsed.pathname;
  params = new URLSearchParams(parsed.search);
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const router = {
  push: (url: string, options?: unknown) => {
    if (options === undefined) spies.push(url);
    else spies.push(url, options);
    if (follows) setUrl(url);
  },
  replace: (url: string, options?: unknown) => {
    if (options === undefined) spies.replace(url);
    else spies.replace(url, options);
    if (follows) setUrl(url);
  },
  refresh: () => spies.refresh(),
  back: () => spies.back(),
  forward: () => spies.forward(),
  prefetch: (url: string) => spies.prefetch(url),
};

export const navigationModule = {
  useRouter: () => router,
  useSearchParams: () =>
    useSyncExternalStore(
      subscribe,
      () => params,
      () => params,
    ),
  usePathname: () =>
    useSyncExternalStore(
      subscribe,
      () => pathname,
      () => pathname,
    ),
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
};

/** Start a test at this URL, with fresh spies. */
export function resetNavigation(search = "", path = "/demo", options: { follow?: boolean } = {}) {
  for (const spy of Object.values(spies)) spy.mockReset();
  follows = options.follow ?? false;
  pathname = path;
  params = new URLSearchParams(search);
  return spies;
}

/** Move the URL inside a mounted test, the way a link or Back would. */
export async function navigateTo(url: string) {
  await act(async () => setUrl(url));
}

export function mockNavigation(search = "", path = "/demo") {
  const { push, replace, refresh } = resetNavigation(search, path);
  vi.doMock("next/navigation", () => navigationModule);
  return { push, replace, refresh };
}
