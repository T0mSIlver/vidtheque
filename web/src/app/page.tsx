import styles from "./page.module.css";

export default function Home() {
  return (
    <main className={styles.main}>
      <h1 className={styles.mark}>
        vidtheque<i className={styles.dot}>.</i>
      </h1>
      <p className={styles.lede}>
        The Next.js front end. Tokens, type and the two faces are in; the
        search box comes next.
      </p>
    </main>
  );
}
