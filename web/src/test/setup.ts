// Runs before every test file. jest-dom adds matchers like
// `toBeInTheDocument()` and `toBeDisabled()`; cleanup unmounts what a test
// rendered so the next one starts from an empty document.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());
