import type { Metadata } from "next";
import { LoginView } from "./LoginView";

// The GET is Next's; the POST that mints the cookie stays Python's (dashboard.md §21).
export const metadata: Metadata = { title: "Sign in" };

export default function DashboardLoginPage() {
  return <LoginView />;
}
