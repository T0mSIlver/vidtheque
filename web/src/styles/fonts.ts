// Byte-identical to the document of record in mcp/.../public/static/fonts/
// (DESIGN.md Fonts rule 1; mcp/tests/test_web_assets.py). `optional`: a face
// that misses first paint is skipped for that load rather than swapped in, so
// the headline never reflows and never renders blank (DECISIONS.md, 2026-09-16).
import localFont from "next/font/local";

export const sans = localFont({
  src: "../fonts/archivo-latin-wght-normal.woff2",
  weight: "100 900",
  style: "normal",
  display: "optional",
  fallback: ["system-ui", "-apple-system", "sans-serif"],
  variable: "--font-sans",
});

export const mono = localFont({
  src: "../fonts/jetbrains-mono-latin-wght-normal.woff2",
  weight: "100 800",
  style: "normal",
  display: "optional",
  fallback: ["ui-monospace", "SFMono-Regular", "monospace"],
  variable: "--font-mono",
});
