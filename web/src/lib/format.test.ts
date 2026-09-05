import { describe, expect, it } from "vitest";
import { at, bytes, clock, count, DASH, day, duration, hours, iso, receipt } from "./format";

// These are the Jinja filters the dashboard read out of Python until the JSON
// slice landed, so the cases are the ones `render.py` and `text.py` document:
// the two-branch span, base-10 bytes, grouped counts, UTC clocks. What is new
// is the absent state — Python printed `-` from a filter that could not be
// reached with `None`, and the React pages render against a public projection
// where a clock genuinely is `null`.

describe("duration", () => {
  it("is one span, in three shapes", () => {
    expect(duration(0)).toBe("0s");
    expect(duration(12)).toBe("12s");
    expect(duration(59.9)).toBe("59s");
    expect(duration(60)).toBe("1m 00s");
    expect(duration(252)).toBe("4m 12s");
    expect(duration(3600)).toBe("1h 00m");
    expect(duration(7000)).toBe("1h 56m");
  });

  it("prints the dash rather than a guess", () => {
    expect(duration(null)).toBe(DASH);
    expect(duration(undefined)).toBe(DASH);
    expect(duration(-1)).toBe(DASH);
    expect(duration(Number.NaN)).toBe(DASH);
  });
});

describe("the ledger's figures", () => {
  it("rounds hours once, on the wire's seconds", () => {
    expect(hours(17200)).toBe("4.8");
    expect(hours(0)).toBe("0.0");
    expect(hours(null)).toBe(DASH);
  });

  it("groups a count, and a missing count is zero rather than nothing", () => {
    expect(count(0)).toBe("0");
    expect(count(1204)).toBe("1,204");
    expect(count(1234567)).toBe("1,234,567");
    expect(count(null)).toBe("0");
  });

  it("reports bytes the way a disk does", () => {
    expect(bytes(0)).toBe("0 B");
    expect(bytes(null)).toBe("0 B");
    expect(bytes(4306)).toBe("4.3 kB");
    expect(bytes(4653056)).toBe("4.7 MB");
    expect(bytes(2_400_000_000)).toBe("2.4 GB");
  });
});

describe("clocks", () => {
  it("prints UTC, never the reader's zone", () => {
    expect(at(1750000000)).toBe("2025-06-15 15:06");
    expect(day(1673913600)).toBe("2023-01-17");
    expect(iso(1750000000)).toBe("2025-06-15T15:06:40Z");
  });

  // An empty corpus has no oldest video and no last index. `0` reaches this the
  // same way `null` does, and neither may render as 1970.
  it("says nothing rather than 1970", () => {
    expect(at(null)).toBe(DASH);
    expect(day(null)).toBe(DASH);
    expect(at(0)).toBe(DASH);
    expect(iso(null)).toBeUndefined();
  });
});

describe("the receipt's own strings", () => {
  it("keeps the timecode and drops the scheme", () => {
    expect(clock(705)).toBe("11:45");
    expect(clock(3725)).toBe("1:02:05");
    expect(receipt("https://youtu.be/kCc8FmEb1nY?t=705")).toBe("youtu.be/kCc8FmEb1nY?t=705");
  });
});
