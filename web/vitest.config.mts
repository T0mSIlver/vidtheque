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
  },
});
