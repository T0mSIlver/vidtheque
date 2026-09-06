import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// An inherited production environment loads React without act(). Set this
// before Vite resolves React and starts test workers.
Object.assign(process.env, { NODE_ENV: "test" });

export default defineConfig({
  // JSX in test files and components under test.
  plugins: [react()],
  // Resolves the `@/*` alias from tsconfig.json for tests.
  resolve: { tsconfigPaths: true },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    // Pure `.ts` tests run in Node; a component test opts into a DOM with
    // `// @vitest-environment jsdom` on its first line.
    environment: "node",
    setupFiles: ["src/test/setup.ts"],
    // A starved worker is not a failing test. These files run in parallel, one
    // jsdom each, and on a box with other work on it a worker loses its slice
    // for seconds at a time: the 5s default expired on a *synchronous* test
    // that renders one component and asserts its heading. The ceiling is a
    // hang's, not a slow render's — nothing here is meant to take 30s, and a
    // test that does has stopped rather than slowed.
    testTimeout: 30_000,
  },
});
