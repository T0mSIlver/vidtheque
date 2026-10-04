import Link from "next/link";
import { REPO } from "@/components/public/facts";
import styles from "@/components/public/page/page.module.css";

// The Android app (demo-site.md §8.4): the APK on GitHub releases, and what it needs.

const RELEASE = `${REPO}/releases/tag/android-v0.2.0`;

export default function AppPage() {
  return (
    <main className={styles.main}>
      <header>
        <p className={styles.kick}>
          <s />
          <span>android</span>
        </p>
        <h1 className={styles.big}>Your feed, on your phone.</h1>
        <p className={styles.lede}>
          The app shows the videos your instance recommends, why, and the minutes to jump to. Tap a
          moment and YouTube opens at that second.
        </p>
      </header>

      <a className={styles.cta} href={RELEASE} rel="noopener">
        Download the APK, version 0.2.0
      </a>

      <section className={styles.section} aria-labelledby="needs">
        <h2 className={styles.h2} id="needs">
          What it needs
        </h2>
        <ul className={styles.list}>
          <li>
            Your own vidtheque instance with sign-in on (
            <span className={styles.inline}>VIDTHEQUE_AUTH=oauth</span>).{" "}
            <Link href="/docs">Run your own</Link>.
          </li>
          <li>Android. The app has no account of its own and talks only to your instance.</li>
          <li>
            Push notifications work only on the developer&apos;s instance for now; on yours, the app
            works without them.
          </li>
        </ul>
      </section>

      <section className={styles.section} aria-labelledby="install">
        <h2 className={styles.h2} id="install">
          Install
        </h2>
        <ol className={styles.list}>
          <li>
            On the phone, download <span className={styles.inline}>vidtheque-0.2.0.apk</span> from{" "}
            <a href={RELEASE} rel="noopener">
              the release
            </a>{" "}
            and open it.
          </li>
          <li>Allow installs from your browser or file manager when Android asks.</li>
          <li>Open vidtheque, enter your instance&apos;s address and sign in.</li>
        </ol>
        <p className={styles.p}>
          The release page lists the signing certificate&apos;s SHA-256, to check the APK before you
          install it.
        </p>
      </section>
    </main>
  );
}
