import styles from "./console.module.css";

// A read that did not land, in the facade's own words (demo-site.md §6.1). The
// console draws two of these and `/demo`'s error boundary a third, which is why
// it is a component: a route file has no business importing console.module.css.
export function BadNotice({
  title,
  detail,
  onRetry,
  disabled = false,
}: {
  title: React.ReactNode;
  detail?: React.ReactNode;
  onRetry?: () => void;
  disabled?: boolean;
}) {
  return (
    <div className={`${styles.notice} ${styles.noticeBad}`}>
      <p className={styles.noticeTitle}>{title}</p>
      {detail ? <p className={styles.noticeDetail}>{detail}</p> : null}
      {onRetry ? (
        <button type="button" className={styles.ghost} disabled={disabled} onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}
