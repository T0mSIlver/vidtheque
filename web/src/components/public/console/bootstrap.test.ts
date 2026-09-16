import { afterEach, describe, expect, it, vi } from "vitest";

const reads = vi.hoisted(() => ({ readMeta: vi.fn(), searchCorpus: vi.fn() }));
vi.mock("@/lib/api/search", () => reads);
import { loadConsole } from "./bootstrap";

const OK = { kind: "ok", meta: { ask_enabled: true } };
const KEYLESS = { kind: "ok", meta: { ask_enabled: false } };
const PAGE = { kind: "ok", page: { results: [] } };

describe("the console bootstrap", () => {
  afterEach(() => {
    reads.readMeta.mockReset();
    reads.searchCorpus.mockReset();
  });

  it("reads page one for a search deep link, scoped to the page", async () => {
    reads.readMeta.mockResolvedValue(OK);
    reads.searchCorpus.mockResolvedValue(PAGE);
    const boot = await loadConsole(Promise.resolve({ q: "kv", type: "ocr" }), { tags: "t" });
    expect(reads.searchCorpus).toHaveBeenCalledWith({ q: "kv", type: "ocr", tags: "t" });
    expect(boot).toEqual({
      askEnabled: true,
      boot: "ok",
      initial: { mode: "search", q: "kv", type: "ocr" },
      initialSearch: PAGE,
    });
  });

  // An answer costs model budget, so a loaded question never spends it.
  it("loads a question without reading anything but meta", async () => {
    reads.readMeta.mockResolvedValue(OK);
    const boot = await loadConsole(Promise.resolve({ ask: "why?" }));
    expect(reads.searchCorpus).not.toHaveBeenCalled();
    expect(boot.initial).toEqual({ mode: "ask", q: "why?", type: "all" });
    expect(boot.initialSearch).toBeNull();
  });

  it("opens a keyless deployment in search from the first paint", async () => {
    reads.readMeta.mockResolvedValue(KEYLESS);
    reads.searchCorpus.mockResolvedValue(PAGE);
    const cold = await loadConsole(Promise.resolve({}));
    expect(cold).toMatchObject({ askEnabled: false, initial: { mode: "search", q: "" } });

    const asked = await loadConsole(Promise.resolve({ ask: "paged attention" }));
    expect(asked.initial).toEqual({ mode: "search", q: "paged attention", type: "all" });
    expect(asked.initialSearch).toBe(PAGE);
  });

  it("says which way the boot call failed", async () => {
    reads.readMeta.mockResolvedValue({ kind: "rate_limited" });
    expect(await loadConsole(Promise.resolve({}))).toMatchObject({
      askEnabled: false,
      boot: "rate_limited",
    });
  });
});
