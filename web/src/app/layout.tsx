import type { Metadata, Viewport } from "next";
import { Rail } from "@/components/Rail";
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

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <Rail />
        {children}
      </body>
    </html>
  );
}
