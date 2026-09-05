// Runs before every test file. jest-dom adds matchers like
// `toBeInTheDocument()` and `toBeDisabled()`; cleanup unmounts what a test
// rendered so the next one starts from an empty document.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  // A test that stopped the clock — to pin a countdown's first paint, or to
  // drive a poll — hands it back here, so the next one starts with a running
  // one whether or not it remembered to.
  vi.useRealTimers();
});
