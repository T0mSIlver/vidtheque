"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { SessionScope } from "@/components/dashboard/session";
import { dashboard, FEED } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Session } from "@/lib/dashboard/schemas";
import styles from "./feed.module.css";

// The feed's chassis: one narrow column, the wordmark and three destinations.
// It reads the console's session (companion.md §5), once per document.

const readSession = (signal: AbortSignal) => dashboard.session(signal);

export function FeedShell({ children }: { children: ReactNode }) {
  const session = useResource<Session>("session", readSession);
  const path = usePathname();
  const onProfile = path === `${FEED}/profile`;
  const onSearch = path === `${FEED}/search`;

  return (
    <SessionScope value={session}>
      <div className={styles.shell}>
        <header className={styles.top}>
          <Link className={styles.mark} href={FEED}>
            vidtheque<i className={styles.dot}>.</i>
          </Link>
          <nav className={styles.nav} aria-label="Feed">
            <Link
              className={styles.navlink}
              href={FEED}
              aria-current={path === FEED || path === `${FEED}/all` ? "page" : undefined}
            >
              Feed
            </Link>
            <Link
              className={styles.navlink}
              href={`${FEED}/search`}
              aria-current={onSearch ? "page" : undefined}
            >
              Search
            </Link>
            <Link
              className={styles.navlink}
              href={`${FEED}/brief`}
              aria-current={path === `${FEED}/brief` ? "page" : undefined}
            >
              Week
            </Link>
            <Link
              className={styles.navlink}
              href={`${FEED}/profile`}
              aria-current={onProfile ? "page" : undefined}
            >
              Profile
            </Link>
          </nav>
        </header>
        <main id="main" className={styles.main}>
          {children}
        </main>
      </div>
    </SessionScope>
  );
}
