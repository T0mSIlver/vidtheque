import type { Metadata } from "next";
import { PublicShell } from "@/components/public/PublicShell";

export const metadata: Metadata = {
  title: { absolute: "vidtheque — run your own" },
  description:
    "Run vidtheque on a machine at home, connect your agent over MCP, and install the Android app.",
};

export default function DocsLayout({ children }: LayoutProps<"/docs">) {
  return <PublicShell>{children}</PublicShell>;
}
