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

  it("stops the timer when unmounted", async () => {
    mockNavigation();
    const { RetryIn } = await import("./RetryIn");
    const { unmount } = render(<RetryIn seconds={5} />);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
