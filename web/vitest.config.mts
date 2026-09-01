import { defineConfig } from "vitest/config";

export default defineConfig({
  // Resolves the `@/*` alias from tsconfig.json for tests.
  resolve: { tsconfigPaths: true },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
