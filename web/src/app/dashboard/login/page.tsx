import type { Metadata } from "next";
import { LoginView } from "./LoginView";

// `GET /dashboard/login` — the sign-in page (dashboard.md §21,
// frontend-migration.md §1d), and the last Jinja page with a reader on it.
//
// The `POST` to this same path stays **Python's**: it is the write that mints
// the session cookie, and an `HttpOnly` cookie is not a thing a React shell can
// set. So this is the third path under `/dashboard` whose two owners are split
// by method, after `/dashboard/following` and `/dashboard/index`.
//
// It reads nothing of its own. Which secrets this deployment accepts, and
// whether it registers a sign-in page at all, are on the session the chassis
// has already asked for.
export const metadata: Metadata = { title: "Sign in" };

export default function DashboardLoginPage() {
  return <LoginView />;
}
