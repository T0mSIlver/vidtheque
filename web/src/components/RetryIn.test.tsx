// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mockNavigation } from "@/test/next";

describe("RetryIn", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.resetModules();
  });

  it("counts down, then enables retry, which refreshes the route", async () => {
    const { refresh } = mockNavigation();
    const { RetryIn } = await import("./RetryIn");
    render(<RetryIn seconds={2} />);

    const button = screen.getByRole("button");
    expect(button).toHaveTextContent("retry in 2s");
    expect(button).toBeDisabled();

    act(() => vi.advanceTimersByTime(1000));
    expect(button).toHaveTextContent("retry in 1s");

    act(() => vi.advanceTimersByTime(1000));
    expect(button).toHaveTextContent("retry");
    expect(button).toBeEnabled();

    act(() => button.click());
    expect(refresh).toHaveBeenCalledOnce();
  });

  // The dashboard's refusals do not unmount this: the panel keeps its control
  // and hands it the new delay. A wait seeded once would then count the second
  // refusal down from the first one's remainder and re-arm the button early,
  // which is one more refused request.
  it("counts a second refusal from the delay the server just named", async () => {
    mockNavigation();
    const { RetryIn } = await import("./RetryIn");
    const { rerender } = render(<RetryIn seconds={4} />);
    const button = screen.getByRole("button");
    expect(button).toHaveTextContent("retry in 4s");

    act(() => vi.advanceTimersByTime(1000));
    act(() => vi.advanceTimersByTime(1000));
    expect(button).toHaveTextContent("retry in 2s");

    rerender(<RetryIn seconds={58} />);
    expect(screen.getByRole("button")).toHaveTextContent("retry in 58s");
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("stops the timer when unmounted", async () => {
    mockNavigation();
    const { RetryIn } = await import("./RetryIn");
    const { unmount } = render(<RetryIn seconds={5} />);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  // `retry_after_s` is a float on the wire. Rendered raw, a 0.4 paints "retry"
  // on a bucket that is still empty, and the click that follows is one more
  // refused request rather than a retry.
  it("floors the wait at a second and rounds a fraction up", async () => {
    mockNavigation();
    const { RetryIn } = await import("./RetryIn");

    const { unmount } = render(<RetryIn seconds={0} />);
    expect(screen.getByRole("button")).toHaveTextContent("retry in 1s");
    unmount();

    render(<RetryIn seconds={2.4} />);
    expect(screen.getByRole("button")).toHaveTextContent("retry in 3s");
  });

  // The demo's own shape (`app.js`'s `renderRateLimited`): the wait counts in a
  // detail line under the title, and the button keeps one word throughout.
  describe("the demo's notice", () => {
    it("counts in its detail line and gates a Try again beside it", async () => {
      mockNavigation();
      const { RetryIn } = await import("./RetryIn");
      render(<RetryIn seconds={2} variant="notice" />);

      expect(screen.getByText("Too many requests.")).toBeInTheDocument();
      const button = screen.getByRole("button", { name: "Try again" });
      expect(screen.getByText("Try again in 2s.")).toBeInTheDocument();
      expect(button).toBeDisabled();

      act(() => vi.advanceTimersByTime(1000));
      expect(screen.getByText("Try again in 1s.")).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(1000));
      expect(screen.getByText("Try again.")).toBeInTheDocument();
      expect(button).toBeEnabled();
    });

    it("hands the retry to the caller when it owns the reload", async () => {
      mockNavigation();
      const { RetryIn } = await import("./RetryIn");
      const onRetry = vi.fn();
      render(<RetryIn seconds={1} variant="notice" onRetry={onRetry} />);

      act(() => vi.advanceTimersByTime(1000));
      act(() => screen.getByRole("button", { name: "Try again" }).click());
      expect(onRetry).toHaveBeenCalledOnce();
    });
  });
});
