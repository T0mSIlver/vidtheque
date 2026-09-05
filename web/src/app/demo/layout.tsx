import { Rail } from "@/components/Rail";
import styles from "./layout.module.css";

const REPO = "https://github.com/T0mSIlver/vidtheque";
const TAKEDOWN = `${REPO}/blob/main/docs/takedown.md`;

// The reader's chrome. The landing at `/` has its own rail — one that floats
// over the room and carries the corpus readout instead of navigation — so the
// header belongs to the surfaces that are used rather than to the root layout.
//
// The footer is here rather than in `page.tsx` because demo-site.md §6 item 7
// asks for a line that never gets culled: the layout wraps every `/demo` state,
// so results, the examples, an empty corpus and the error boundary all carry it.
// `/videos` keeps the rail and not this footer — the contract writes the footer
// for the demo page, and the library pages are not in §6.
export default function DemoLayout({ children }: LayoutProps<"/demo">) {
  return (
    <>
      <Rail />
      {children}
      <footer className={styles.footer}>
        <div className={styles.inner}>
          <div>
            <p className={styles.line}>
              The videos belong to the people who made them.{" "}
              <a className={styles.lnk} href={TAKEDOWN} rel="noopener">
                Removal on request
              </a>
              .
            </p>
            <p className={styles.line}>
              <a className={styles.lnk} href={REPO} rel="noopener">
                Source on GitHub
              </a>
            </p>
          </div>
          <div className={styles.mark}>
            <b className={styles.markWord}>
              vidtheque<i>.</i>
            </b>
          </div>
        </div>
      </footer>
    </>
  );
}
