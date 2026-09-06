import type { Metadata } from "next";
import { Rail } from "@/components/Rail";
import { browsePath, claudeCommand, corpusCount } from "@/lib/api/meta";
import { readMeta } from "@/lib/search";
import { Connect } from "./Connect";
import styles from "./layout.module.css";

const REPO = "https://github.com/T0mSIlver/vidtheque";
const TAKEDOWN = `${REPO}/blob/main/docs/takedown.md`;

// The instance names its own repo, and this is the one place that string
// reaches an href: only http(s) survives, so a `javascript:` in a payload
// becomes the link this build shipped with rather than code.
function httpOr(value: string | undefined, fallback: string): string {
  return value && /^https?:\/\//i.test(value) ? value : fallback;
}

// The `<head>` is part of the deliverable, not boilerplate: a title that says
// what the thing is, a description, and the unfurl tags — no `og:image`, since
// a wrong one is worse than none (demo-site.md §6.1). The scheme, the theme
// colour and the drawn favicon are the root layout's, one set for both pages.
export const metadata: Metadata = {
  title: { absolute: "vidtheque — AI Engineer 2026, on tap" },
  description:
    "The knowledge of AI Engineer 2026, on tap. Your agent watched it — ask it something.",
  openGraph: {
    type: "website",
    siteName: "vidtheque",
    title: "vidtheque — AI Engineer 2026, on tap",
    description:
      "The knowledge of AI Engineer 2026, on tap. Your agent watched it — ask it something.",
  },
  twitter: { card: "summary" },
};

// The reader's chrome. The landing at `/` has its own rail — one that floats
// over the room and carries the corpus readout instead of navigation — so the
// header belongs to the surfaces that are used rather than to the root layout.
//
// `/api/meta` is read here as well as in the page, and `readMeta` is cached for
// the render, so the two are one call: a layout cannot hand a prop to its
// children, and the rail's corpus count and the connect panel's endpoint both
// come out of the same answer as the page's ask gating.
//
// The connect panel and the footer are here rather than in `page.tsx` because
// demo-site.md §6 items 6 and 7 ask for a line and a panel that never get
// culled: the layout wraps every `/demo` state, so results, the examples, an
// empty corpus and the error boundary all carry them.
export default async function DemoLayout({ children }: LayoutProps<"/demo">) {
  const outcome = await readMeta();
  const meta = outcome.kind === "ok" ? outcome.meta : null;
  return (
    <>
      <Rail count={corpusCount(meta?.videos)} browse={browsePath(meta?.browse)} />
      {children}
      <Connect
        mcpUrl={meta?.mcp_url ?? null}
        command={meta ? claudeCommand(meta.mcp_url) : null}
        // The endpoint is the server's to state, so an unreachable server gets
        // no guessed URL — and the two silences are different facts: one is
        // over in a minute, the other is the server being down.
        unavailable={
          outcome.kind === "rate_limited"
            ? "unavailable while rate limited — reload in a minute"
            : "unavailable — reload the page"
        }
      />
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
                <a className={styles.lnk} href={httpOr(meta?.repo, REPO)} rel="noopener">
                  Source on GitHub
                </a>{" "}
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
