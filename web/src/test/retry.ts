import { act } from "@testing-library/react";
import { vi } from "vitest";

// `RetryIn` ticks on a real timer, so a label spelled out after an `await` can
// already have moved (LESSONS.md). Pin the first paint under a stopped clock,
// and assert a band of values anywhere the clock runs.

/** Mount under a stopped clock and let the reads (promises, not timers) land. */
export async function firstPaint<T>(mount: () => Promise<T>): Promise<T> {
  vi.useFakeTimers();
  const mounted = await mount();
  await settled();
  return mounted;
}

/** Let what a request answered land, with the clock still stopped. */
export async function settled(): Promise<void> {
  await act(async () => {});
}

/** Matches `retry in Ns` for any `n` in `1..seconds`. */
export function countingDownFrom(seconds: number) {
  return (name: string) => {
    const match = /^retry in (\d+)s$/.exec(name);
    if (match === null) return false;
    const left = Number(match[1]);
    return left > 0 && left <= seconds;
  };
}
