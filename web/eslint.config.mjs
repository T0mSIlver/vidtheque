import { defineConfig, globalIgnores } from "eslint/config";
import prettier from "eslint-config-prettier";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// The two halves of src/ and what each may not import (DECISIONS.md,
// 2026-09-16). Both may draw with components/ui and read lib/format and
// lib/schemas; nothing else crosses.
const PUBLIC_FILES = [
  "src/app/(public)/**",
  "src/components/public/**",
  "src/lib/api/**",
  "src/test/public/**",
];

const DASHBOARD_FILES = [
  "src/app/(dashboard)/**",
  "src/components/dashboard/**",
  "src/lib/dashboard/**",
  "src/test/dashboard/**",
];

// Four spellings per forbidden directory: the alias and a relative path out of
// one, each as the directory itself (`@/lib/api`, the package index) and as
// anything under it.
function forbid(where, directories, message) {
  const group = directories.flatMap((dir) => [
    `@/${dir}`,
    `@/${dir}/**`,
    `**/${dir}`,
    `**/${dir}/**`,
  ]);
  return {
    files: where,
    rules: {
      "no-restricted-imports": ["error", { patterns: [{ group, message }] }],
    },
  };
}

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  forbid(
    PUBLIC_FILES,
    ["components/dashboard", "lib/dashboard"],
    "A public page may not read the dashboard. Share through components/ui, lib/format or lib/schemas.",
  ),
  forbid(
    DASHBOARD_FILES,
    ["components/public", "lib/api"],
    "The dashboard may not read the public surface. Share through components/ui, lib/format or lib/schemas.",
  ),
  // Formatting is Prettier's job; this turns off every ESLint rule that
  // would argue with it. Must come last.
  prettier,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
