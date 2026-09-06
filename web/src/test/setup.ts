// Runs before every test file. jest-dom adds matchers like
// `toBeInTheDocument()` and `toBeDisabled()`; cleanup unmounts what a test
// rendered so the next one starts from an empty document.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeAll, vi } from "vitest";

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
