// Stand-ins for the App Router hooks a Client Component reads. A test calls
// `mockNavigation()` and gets the spies back, so it can assert what URL a
// submit pushed without a real router.
import { vi } from "vitest";

export function mockNavigation(search = "") {
  const push = vi.fn();
  const replace = vi.fn();
  const refresh = vi.fn();
  vi.doMock("next/navigation", () => ({
    useRouter: () => ({
      push,
      replace,
      refresh,
      back: vi.fn(),
      forward: vi.fn(),
      prefetch: vi.fn(),
    }),
    useSearchParams: () => new URLSearchParams(search),
    usePathname: () => "/",
    notFound: () => {
      throw new Error("NEXT_NOT_FOUND");
    },
  }));
  return { push, replace, refresh };
}
