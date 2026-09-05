// The countdown's label, as an assertion that does not race the clock.
//
// `RetryIn` starts a **real** one-second timer on mount, so between the render
// and the assertion the label can already have ticked: a test that spells out
// `retry in 24s` is asserting the exact moment its own worker got scheduled,
// and on a loaded box it loses. Six page tests did, intermittently.
//
// What those tests are actually about is that the countdown started from the
// delay *the limiter named* and has not run out — a page inventing sixty
// seconds where `Retry-After` said nine is the defect worth catching. So the
// matcher accepts any second from that delay down to one, and nothing else:
// `retry` (finished) does not match, and neither does a larger number.
//
// The component's own exact ticking is `RetryIn.test.tsx`'s, under fake timers,
// which is where a clock belongs.

/** Matches `retry in Ns` for any `n` in `1..seconds`. */
export function countingDownFrom(seconds: number) {
  return (name: string) => {
    const match = /^retry in (\d+)s$/.exec(name);
    if (match === null) return false;
    const left = Number(match[1]);
    return left > 0 && left <= seconds;
  };
}
