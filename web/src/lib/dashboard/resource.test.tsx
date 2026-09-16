// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardError } from "./client";
import { clearResources, useResource, type Reader } from "./resource";

function Probe({ id, read, poll }: { id: string; read: Reader<string>; poll?: number }) {
  const resource = useResource(id, read, poll ? { pollMs: () => poll } : {});
  return (
    <p>
      <span data-testid="data">{resource.data ?? "none"}</span>
      <span data-testid="pending">{String(resource.isPending)}</span>
      <span data-testid="stale">{String(resource.isStale)}</span>
      <span data-testid="error">{resource.error ? "error" : "ok"}</span>
      <button type="button" onClick={resource.reload}>
        reload
      </button>
    </p>
  );
}

const text = (id: string) => screen.getByTestId(id).textContent;

/** A reader whose answers the test releases one at a time. */
function held() {
  const calls: {
    signal: AbortSignal;
    resolve: (value: string) => void;
    reject: (e: unknown) => void;
  }[] = [];
  const read = vi.fn(
    (signal: AbortSignal) =>
      new Promise<string>((resolve, reject) => calls.push({ signal, resolve, reject })),
  );
  return { read, calls };
}

async function flush() {
  await act(async () => {});
}

describe("the dashboard read cache", () => {
  afterEach(() => {
    clearResources();
    vi.useRealTimers();
  });

  it("is pending until the first answer, then holds it", async () => {
    const { read, calls } = held();
    render(<Probe id="a" read={read} />);
    await flush();
    expect(text("data")).toBe("none");
    expect(text("pending")).toBe("true");

    await act(async () => calls[0].resolve("first"));
    expect(text("data")).toBe("first");
    expect(text("pending")).toBe("false");
  });

  it("paints the last payload at once on a revisit, and re-reads under it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { read, calls } = held();
    const first = render(<Probe id="a" read={read} />);
    await flush();
    await act(async () => calls[0].resolve("first"));
    first.unmount();
    await act(() => vi.advanceTimersByTimeAsync(5_000));

    render(<Probe id="a" read={read} />);
    expect(text("data")).toBe("first");
    await flush();
    expect(text("stale")).toBe("true");
    expect(calls).toHaveLength(2);

    await act(async () => calls[1].resolve("second"));
    expect(text("data")).toBe("second");
    expect(text("stale")).toBe("false");
  });

  it("does not ask again for a read that just landed", async () => {
    const { read, calls } = held();
    const first = render(<Probe id="a" read={read} />);
    await flush();
    await act(async () => calls[0].resolve("first"));
    first.unmount();
    await flush();

    render(<Probe id="a" read={read} />);
    await flush();
    expect(calls).toHaveLength(1);
    expect(text("stale")).toBe("false");
  });

  it("shows only the current key's data and aborts the request it left", async () => {
    const { read, calls } = held();
    const view = render(<Probe id="a" read={read} />);
    await flush();

    view.rerender(<Probe id="b" read={read} />);
    await flush();
    await act(() => new Promise((done) => setTimeout(done, 5)));
    expect(calls[0].signal.aborted).toBe(true);
    expect(text("data")).toBe("none");

    await act(async () => calls[1].resolve("bee"));
    expect(text("data")).toBe("bee");
  });

  it("keeps the data on screen while a reload is out", async () => {
    const { read, calls } = held();
    render(<Probe id="a" read={read} />);
    await flush();
    await act(async () => calls[0].resolve("first"));

    await act(async () => screen.getByRole("button", { name: "reload" }).click());
    expect(text("data")).toBe("first");
    expect(text("pending")).toBe("true");
    await act(async () => calls[1].resolve("second"));
    expect(text("data")).toBe("second");
  });

  it("waits out a 429 by its Retry-After, then asks again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { read, calls } = held();
    render(<Probe id="a" read={read} />);
    await flush();
    const limited = new DashboardError(429, { error: "E_RATE_LIMIT", message: "slow down" }, 3);
    await act(async () => calls[0].reject(limited));
    expect(text("error")).toBe("error");

    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(calls).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(1_500));
    expect(calls).toHaveLength(2);
  });

  it("reads a waiting poll when the tab comes back, but never inside a Retry-After", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { read, calls } = held();
    render(<Probe id="a" read={read} poll={2_000} />);
    await flush();
    await act(async () => calls[0].resolve("first"));
    const show = async () => {
      vi.spyOn(document, "hidden", "get").mockReturnValue(false);
      await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    };

    await show();
    expect(calls).toHaveLength(2);
    const limited = new DashboardError(429, { error: "E_RATE_LIMIT", message: "slow down" }, 60);
    await act(async () => calls[1].reject(limited));

    await act(() => vi.advanceTimersByTimeAsync(1_000));
    await show();
    expect(calls).toHaveLength(2);
    await act(() => vi.advanceTimersByTimeAsync(59_500));
    expect(calls).toHaveLength(3);
  });

  it("waits out a Retry-After across a remount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { read, calls } = held();
    const first = render(<Probe id="a" read={read} />);
    await flush();
    const limited = new DashboardError(429, { error: "E_RATE_LIMIT", message: "slow down" }, 30);
    await act(async () => calls[0].reject(limited));
    first.unmount();
    await act(() => vi.advanceTimersByTimeAsync(5_000));

    render(<Probe id="a" read={read} />);
    await flush();
    expect(calls).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(25_500));
    expect(calls).toHaveLength(2);
  });

  it("polls on the cadence the payload names, and not once unmounted", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const read = vi.fn(async () => "tick");
    const view = render(<Probe id="a" read={read} poll={2_000} />);
    await flush();
    expect(read).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(2_100));
    expect(read).toHaveBeenCalledTimes(2);

    view.unmount();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(read).toHaveBeenCalledTimes(2);
  });
});
