"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Session } from "@/lib/dashboard/schemas";
import styles from "./chrome.module.css";
import { DashLink } from "./kit/ui";
import { sectionOf, type Section } from "./ported";
import { SessionScope } from "./session";

// The chassis: where you are, and what this deployment may do. The session is
// read here once per document (dashboard.md §19); everything that does not
// depend on it renders at once, and everything that does reserves its space
// until it lands, so the column never moves.

type Item = { href: string; label: string; section: Section; hook?: string };

const SECTIONS: Item[] = [
  { href: ROOT, label: "Overview", section: "corpus" },
  { href: `${ROOT}/ledger`, label: "Ledger", section: "ledger" },
  { href: `${ROOT}/search`, label: "Search", section: "search" },
  { href: `${ROOT}/videos`, label: "Videos", section: "videos" },
  { href: `${ROOT}/jobs`, label: "Jobs", section: "jobs" },
];

// Present exactly when the write routes are registered (§2.3, §3.2 rule 3).
// `data-add-videos` is the smoke check's hook for the first link.
const MANAGE: Item[] = [
  { href: `${ROOT}/index`, label: "Add videos", section: "index", hook: "" },
  { href: `${ROOT}/following`, label: "Following", section: "following" },
];

const readSession = (signal: AbortSignal) => dashboard.session(signal);

export function Chrome({ children }: { children: ReactNode }) {
  const session = useResource<Session>("session", readSession);
  const deployment = session.data ?? null;
  const known = deployment !== null || session.error !== undefined;
  const path = usePathname();

  // A stale cookie reads `signed_in: false` and still needs Sign out (§19).
  const canSignOut = Boolean(deployment?.signed_in || deployment?.has_session_cookie);

  return (
    <SessionScope value={session}>
      <div className={styles.shell}>
        <a className={styles.skip} href="#main">
          Skip to content
        </a>

        <header className={styles.rail}>
          <div className={styles.brand}>
            <Link className={styles.mark} href={ROOT}>
              <b>
                vidtheque<i className={styles.dot}>.</i>
              </b>
            </Link>
          </div>

          <nav className={styles.nav} aria-label="Sections">
            <NavList items={SECTIONS} path={path} />
            {deployment?.write_side || !known ? (
              // Held, invisible, until the session says whether it exists.
              <div className={known ? undefined : styles.reserved} inert={!known}>
                <p className={styles.group}>Manage</p>
                <NavList items={MANAGE} path={path} />
              </div>
            ) : null}
            {deployment?.readonly ? (
              <>
                <p className={styles.group}>This demo</p>
                <ul className={styles.navlist}>
                  <li>
                    <Link className={styles.navlink} href="/demo">
                      Search the corpus
                    </Link>
                  </li>
                </ul>
              </>
            ) : null}
          </nav>

          <div className={`${styles.foot} ${known ? "" : styles.footPending}`}>
            {deployment ? <Deployment session={deployment} /> : null}
            {canSignOut ? (
              // A real POST form: signing out changes state (§3.3).
              <form className={styles.signout} method="post" action={`${ROOT}/logout`}>
                <button className={styles.ghost} type="submit">
                  Sign out
                </button>
              </form>
            ) : deployment?.login_url ? (
              <DashLink className={styles.signin} href={deployment.login_url}>
                Sign in
              </DashLink>
            ) : null}
          </div>
        </header>

        <div className={styles.col}>
          <main id="main">{children}</main>
          <footer>
            <p className={styles.footline}>
              <b>
                vidtheque<i className={styles.dot}>.</i>
              </b>
              {deployment ? <code className={styles.version}>{deployment.version}</code> : null}
            </p>
          </footer>
        </div>
      </div>
    </SessionScope>
  );
}

function NavList({ items, path }: { items: Item[]; path: string | null }) {
  const here = sectionOf(path);
  return (
    <ul className={styles.navlist}>
      {items.map((item) => (
        <li key={item.href}>
          <DashLink
            className={styles.navlink}
            href={item.href}
            aria-current={here === item.section ? "page" : undefined}
            data-add-videos={item.hook}
          >
            {item.label}
          </DashLink>
        </li>
      ))}
    </ul>
  );
}

/** What the deployment allows, in the rail's foot. The projection says only
 *  that nothing writes: `auth=` and "indexing refused" are the operator's
 *  console (§2.4). */
function Deployment({ session }: { session: Session }) {
  if (session.readonly) {
    return (
      <p className={styles.deployment}>
        <span>read-only demo</span>
      </p>
    );
  }
  return (
    <>
      <p className={styles.deployment}>
        <span title="VIDTHEQUE_AUTH">auth={session.auth_mode}</span>
        {session.writes_allowed ? null : <span className={styles.refused}>indexing refused</span>}
        {session.write_side ? null : <span>no write side</span>}
      </p>
      {/* §3.2 rule 3: say why there is no write side, and the fix, once. */}
      {session.write_side ? null : (
        <p className={styles.why}>
          Adding to the index needs a credential to check. Set <code>VIDTHEQUE_AUTH=token</code> and{" "}
          <code>VIDTHEQUE_TOKEN</code>, then restart.
        </p>
      )}
    </>
  );
}
