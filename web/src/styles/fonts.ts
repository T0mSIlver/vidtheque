// The two faces, loaded once. next/font/local self-hosts the files under
// /_next/static, writes the @font-face itself, preloads them, and exposes each
// face as a CSS variable that globals.css binds to `--sans` and `--mono`.
//
// The woff2 files are byte-identical copies of the document of record at
// mcp/src/vidtheque_mcp/public/static/fonts/ (DESIGN.md, Fonts rule 1);
// mcp/tests/test_web_assets.py fails the suite if they drift.
import localFont from "next/font/local";

export const sans = localFont({
  src: "../fonts/archivo-latin-wght-normal.woff2",
  weight: "100 900",
  style: "normal",
  display: "block",
  fallback: ["system-ui", "-apple-system", "sans-serif"],
  variable: "--font-sans",
});

export const mono = localFont({
  src: "../fonts/jetbrains-mono-latin-wght-normal.woff2",
  weight: "100 800",
  style: "normal",
  display: "block",
  fallback: ["ui-monospace", "SFMono-Regular", "monospace"],
  variable: "--font-mono",
});
