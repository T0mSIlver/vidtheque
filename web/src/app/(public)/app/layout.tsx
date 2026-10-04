import type { Metadata } from "next";
import { PublicShell } from "@/components/public/PublicShell";

export const metadata: Metadata = {
  title: { absolute: "vidtheque — Android app" },
  description: "The vidtheque feed on Android, for your own instance.",
};

export default function AppLayout({ children }: LayoutProps<"/app">) {
  return <PublicShell showConnect={false}>{children}</PublicShell>;
}
