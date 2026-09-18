import { Suspense } from "react";
import { Rail } from "@/components/public/Rail";
import { CopyRows } from "./CopyRows";
import { ConnectRows, RailFacts, RepoLink, SourceLink, REPO } from "./facts";
import connect from "./connect.module.css";
import styles from "./footer.module.css";

const TAKEDOWN = `${REPO}/blob/main/docs/takedown.md`;

/**
 * The reader's chrome around `/demo` and `/paris`: rail, connect panel and
 * footer, on every state (demo-site.md §6 items 6–7). Synchronous: the parts
 * `/api/meta` decides are small leaves that stream in without moving the page.
 */
export function PublicShell({
  children,
  showCount = true,
  showConnect = true,
}: {
  children: React.ReactNode;
  /** Off where a corpus total would read as an edition's size (aie-paris-2026.md §4.4). */
  showCount?: boolean;
  /** Off when a page places the shared connect section inside its own order. */
  showConnect?: boolean;
}) {
  return (
    <>
      <Rail>
        <Suspense fallback={null}>
          <RailFacts showCount={showCount} />
        </Suspense>
      </Rail>
      {children}
      {showConnect ? <ConnectSection /> : null}
      <footer className={styles.footer}>
        <div className={styles.inner}>
          <div className={styles.grid}>
            <div>
              <p className={styles.label}>attribution</p>
              <p className={styles.line}>
                The videos belong to the people who made them.{" "}
                <a className={styles.lnk} href={TAKEDOWN} rel="noopener">
                  Removal on request
                </a>
                .
              </p>
              <p className={styles.line}>
                <Suspense fallback={<SourceLink href={REPO} />}>
                  <RepoLink />
                </Suspense>{" "}
                · MIT · self-hosted.
              </p>
            </div>
            <div className={styles.mark}>
              <b className={styles.markWord}>
                vidtheque<i>.</i>
              </b>
            </div>
          </div>
          <div className={styles.base}>
            <span className={styles.dim}>the knowledge of the builders, on tap</span>
            <span className={styles.dim}>early development · schemas can still change</span>
          </div>
        </div>
      </footer>
    </>
  );
}

export function ConnectSection() {
  return (
    <section className={connect.connect}>
      <div className={connect.inner}>
        <h2 className={connect.head}>Add this corpus to your own agent</h2>
        <Suspense fallback={<CopyRows mcpUrl={null} unavailable="loading…" />}>
          <ConnectRows />
        </Suspense>
        <p className={connect.lede}>
          Claude, ChatGPT and Le Chat: add a custom connector and paste the endpoint. No sign-in.
        </p>
      </div>
    </section>
  );
}
