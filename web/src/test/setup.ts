// Runs before every test file. jest-dom adds matchers like
// `toBeInTheDocument()` and `toBeDisabled()`; cleanup unmounts what a test
// rendered so the next one starts from an empty document.
import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, beforeAll, vi } from "vitest";

// How long `findBy*` and `waitFor` keep asking. The default is one second,
// which is a bound on how slow the *box* is allowed to be rather than on how
// long a page may take to settle: under parallel load a worker can lose its
// slice for longer than that, and the test then reports a page that never
// arrived when what happened is that nobody rendered it. `testTimeout` in
// `vitest.config.mts` is the real ceiling and stays well above this.
configure({ asyncUtilTimeout: 5_000 });

// jsdom 30 has `HTMLDialogElement` and its `open` attribute but none of its
// three methods, so a component that opens a real `<dialog>` cannot be
// rendered at all. The stand-in is the spec's observable behaviour and nothing
// more: `open` reflects, `close()` fires `close` once, and the backdrop, the
// focus trap and Escape are the browser's — which is where they are verified.
beforeAll(() => {
  const dialog: Partial<HTMLDialogElement> | undefined = globalThis.HTMLDialogElement?.prototype;
  if (!dialog || typeof dialog.showModal === "function") return;
  const open = function (this: HTMLDialogElement) {
    this.open = true;
  };
  dialog.show = open;
  dialog.showModal = open;
  dialog.close = function (this: HTMLDialogElement, value?: string) {
    if (!this.open) return;
    if (value !== undefined) this.returnValue = value;
    this.open = false;
    this.dispatchEvent(new Event("close"));
  };
});

afterEach(() => {
  cleanup();
  // A test that stopped the clock — to pin a countdown's first paint, or to
  // drive a poll — hands it back here, so the next one starts with a running
  // one whether or not it remembered to.
  vi.useRealTimers();
});
