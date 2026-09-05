"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { Session } from "@/lib/dashboard/schemas";
import styles from "./chrome.module.css";
import { DashLink } from "./parts";
import { SessionScope } from "./session";
import { useRead } from "./useRead";

// The management chassis — `templates/base.html`, in React. It holds the two
// things that are true of every page on this surface: where you are, and what
// this deployment is allowed to do.
//
// It is a Client Component because the second of those is read in the browser:
// `/dashboard/api/session` is outside the read gate, so a signed-out visitor
// gets an answer, and the shell can render the right chrome without Next ever
// seeing the session cookie. Everything that does not depend on it — the
// wordmark, the five sections — renders immediately and does not wait.

type Item = { href: string; label: string };

// Which of these five this app serves and which are still Jinja is `ported.ts`'s
// answer, not a flag repeated here: `DashLink` asks it, and so does every other
// link into this surface. Porting a page adds its path to that list, names it in
// `proxy.ts`'s matcher, and nothing in the rail changes at all.
const SECTIONS: Item[] = [
  { href: ROOT, label: "Overview" },
  { href: `${ROOT}/ledger`, label: "Ledger" },
  { href: `${ROOT}/search`, label: "Search" },
  { href: `${ROOT}/videos`, label: "Videos" },
  { href: `${ROOT}/jobs`, label: "Jobs" },
];

// The write side, as its own group. It appears exactly when the routes behind
// it are registered — in the demo projection and in `AUTH=none` there is no
// group at all, rather than a link to a page that 404s (dashboard.md §2.3,
// §3.2 rule 3). A dead link is not room.
const MANAGE: Item[] = [
  { href: `${ROOT}/index`, label: "Add videos" },
  { href: `${ROOT}/following`, label: "Following" },
];

const readSession = (signal: AbortSignal) => dashboard.session(signal);

export function Chrome({ children }: { children: ReactNode }) {
  const session = useRead(readSession);
  const deployment = session.status === "ready" ? session.data : null;
  const path = usePathname();

  // The rail offers Sign out when there is a cookie to clear, which is not the
  // same question as whether the next request will be served: a session row
  // that expired under a browser still holding the cookie reads `signed_in:
  // false` and `has_session_cookie: true`, and that is exactly the reader who
  // needs the button (dashboard.md §19). Either field is enough.
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
            {/* The first group has no heading: a rail of five words is its own
                label. The groups that *change* what the rail means keep theirs,
                because there an item's absence is a fact about the deployment. */}
            <NavList items={SECTIONS} path={path} />
            {deployment?.write_side ? (
              <>
                <p className={styles.group}>Manage</p>
                <NavList items={MANAGE} path={path} />
              </>
            ) : null}
            {/* The demo's way back. In read-only mode this surface is the
                browsable corpus behind the reader at `/demo`, and a visitor who
                followed the link in ought to be able to follow one back out. */}
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

          <div className={styles.foot}>
            {deployment ? <Deployment session={deployment} /> : null}
            {canSignOut ? (
              // The only control in the chassis, and it is a POST: signing out
              // changes state, and dashboard.md §3.3 has no state-changing GET
              // in it. A real form, so it needs no JavaScript — and the browser
              // sends `Sec-Fetch-Site: same-origin` with it, which is the
              // positive evidence §3.3's Origin rule asks of an ambient
              // credential. `form-action 'self'` in `proxy.ts` allows it.
              <form className={styles.signout} method="post" action={`${ROOT}/logout`}>
                <button className={styles.ghost} type="submit">
                  Sign out
                </button>
              </form>
            ) : deployment?.login_url ? (
              <a className={styles.signin} href={deployment.login_url}>
                Sign in
              </a>
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
  return (
    <ul className={styles.navlist}>
      {items.map((item) => {
        // A section owns what is under it — a reader on a video's detail page
        // is still in Videos — except the root, which is under everything and
        // owns only itself.
        const inSection = item.href !== ROOT && path?.startsWith(`${item.href}/`);
        const current = path === item.href || inSection ? "page" : undefined;
        return (
          <li key={item.href}>
            <DashLink className={styles.navlink} href={item.href} aria-current={current}>
              {item.label}
            </DashLink>
          </li>
        );
      })}
    </ul>
  );
}

// What this deployment is allowed to do, in the rail's foot.
//
// The projection's line says what the *reader* is allowed to do and stops
// there: `auth=…` names an environment variable and its value, and "indexing
// refused" is a sentence about a worker nobody visiting the demo can reach —
// both are the operator's console leaking onto a page a stranger can screenshot
// (dashboard.md §2.4). What survives is the one line that changes what a
// visitor should expect: nothing here writes.
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
      {/* §3.2 rule 3 asks the `none`-mode dashboard to say *why* it is
          read-only and give the one-line fix. It lives in the rail because
          DESIGN.md gives the rail's foot the job of carrying what this
          deployment is allowed to do; as a banner it would be on every page. */}
      {session.write_side ? null : (
        <p className={styles.why}>
          Adding to the index needs a credential to check. Set <code>VIDTHEQUE_AUTH=token</code> and{" "}
          <code>VIDTHEQUE_TOKEN</code>, then restart.
        </p>
      )}
    </>
  );
}
