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

// One scheme. A projection room does not have a day mode (DESIGN.md).
export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#040405",
  viewportFit: "cover",
};

// The ground and nothing else. The rail is not here: `/` is the landing and it
// carries its own, floating over the hero, so the reader's header belongs to
// the segments that read (`/demo`, `/videos`) rather than to every page.
//
// `connection()` here is what makes every page in the app render per request.
// The CSP that `proxy.ts` sets carries a nonce minted for that one request,
// and Next stamps it on the scripts it emits while rendering; HTML built at
// build time carries a nonce that was never issued, so its scripts are
// refused and the page never hydrates. One call in the one component every
// route renders, rather than a rule each new page has to remember.
export default async function RootLayout({ children }: LayoutProps<"/">) {
  await connection();
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
