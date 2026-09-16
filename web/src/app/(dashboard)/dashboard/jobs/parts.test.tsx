// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Held } from "./parts";

// The deferral countdown, on its own. The jobs table and the job page both
// assert what it says at a moment; what this file is about is the clock behind
// it — that it moves, that it stops, and that a new payload re-seeds it.

describe("the held countdown", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("counts the wait down and goes away when there is none left", () => {
    render(<Held seconds={3} />);
    expect(screen.getByText(/held/)).toHaveTextContent("held 3s more");

    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByText(/held/)).toHaveTextContent("held 1s more");

    act(() => vi.advanceTimersByTime(1000));
    // Absent rather than zeroed: a countdown showing `0s` is a wait that is
    // not happening.
    expect(screen.queryByText(/held/)).not.toBeInTheDocument();
  });

  // A wait that is over has nothing to count, and a table of deferred jobs
  // would otherwise keep a second-by-second wake-up per row for as long as the
  // page is open, drawing nothing.
  it("stops its interval at zero rather than ticking on under a blank row", () => {
    render(<Held seconds={2} />);
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(2000));
    expect(screen.queryByText(/held/)).not.toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
  });

  // The clock is arithmetic on the last payload's number, so every reading
  // re-seeds it and it cannot drift.
  it("takes the next reading's wait rather than continuing its own", () => {
    const { rerender } = render(<Held seconds={60} />);
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByText(/held/)).toHaveTextContent("held 57s more");

    rerender(<Held seconds={40} />);
    expect(screen.getByText(/held/)).toHaveTextContent("held 40s more");
  });

  // `moving` is the poll's own state: when the tick has stopped, the number
  // stands still rather than counting against a payload nothing is refreshing.
  it("stands still when the poll behind it has stopped", () => {
    render(<Held seconds={30} moving={false} />);
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByText(/held/)).toHaveTextContent("held 30s more");
    expect(vi.getTimerCount()).toBe(0);
  });
});
