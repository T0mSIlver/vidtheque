import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import { mono, sans } from "@/styles/fonts";
import "@/styles/tokens.css";
import "@/styles/type.css";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "vidtheque", template: "%s — vidtheque" },
  description:
    "The knowledge of the videos you follow, on tap. Your agent watched them — ask it something.",
};

// One scheme (DESIGN.md).
export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#040405",
  viewportFit: "cover",
};

// Per-request rendering: proxy.ts mints a CSP nonce per request, and a page
// built ahead of time would carry no valid nonce (frontend-migration.md §1b).
export default async function RootLayout({ children }: LayoutProps<"/">) {
  await connection();
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
