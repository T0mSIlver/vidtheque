// Whether this deployment registers the dashboard at all. The public box does
// not (demo-site.md §8.4), and its pages then answer 404 rather than a shell
// whose every read fails.
import "server-only";
import { notFound } from "next/navigation";

export async function refuseWithoutDashboard(
  doFetch: typeof fetch = fetch,
  base = process.env.VIDTHEQUE_API_URL,
): Promise<void> {
  if (!base) return;
  let status: number;
  try {
    const res = await doFetch(`${base.replace(/\/+$/, "")}/dashboard/api/session`, {
      cache: "no-store",
    });
    status = res.status;
  } catch {
    // An unreachable API is the shell's to report, as before.
    return;
  }
  if (status === 404) notFound();
}
