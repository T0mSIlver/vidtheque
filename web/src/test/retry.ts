import { act } from "@testing-library/react";
import { vi } from "vitest";

// The countdown's label, asserted the two ways a page test can honestly assert
// it.
//
// `RetryIn` starts a **real** one-second timer on mount, so between the render
// and the assertion the label can already have ticked: a test that spells out
// `retry in 24s` after an `await` is asserting the exact moment its own worker
// got scheduled, and on a loaded box it loses. Six page tests did.
//
// So the delay is pinned where nothing can tick, and only the band is asserted
// where something can:
//
// * `firstPaint` stops the clock *before* the page mounts. The first label a
//   surface paints is then the delay the limiter named, exactly, and the test
//   spells it out. That is the assertion that catches a page dividing
//   `Retry-After`, or a `?? 60` fallback firing because the header was read
//   under the wrong name — neither of which the matcher below can see, since
//   30 and 60 are both somewhere in `1..60`.
// * `countingDownFrom` is for the assertions that come after an `await`, where
//   the clock is running and the band is all that is knowable.
//
// The component's own ticking is `RetryIn.test.tsx`'s, under fake timers,
// which is where a clock belongs.

/** Mount under a stopped clock, and let the page's reads land.
 *
 *  Those reads are promises and not timers, so flushing React's queue is all
 *  this takes and the clock never has to move. Whatever the caller asserts
 *  next is the countdown's first paint. `setup.ts` puts the real timers back
 *  after every test. */
export async function firstPaint<T>(mount: () => Promise<T>): Promise<T> {
  vi.useFakeTimers();
  const mounted = await mount();
  await settled();
  return mounted;
}

/** Let what a write answered land, with the clock still stopped.
 *
 *  `findBy*` is the usual way to wait, and it cannot be used here: it polls on
 *  a timer that a stopped clock never fires. Nothing this waits for is on a
 *  timer either — it is one more request coming back. */
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
