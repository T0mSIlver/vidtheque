import { youtubeAt } from "@/landing/format";
import styles from "./landing.module.css";

// The receipt slab: `youtu.be/<id>?t=<second>`, printed rather than described.
// The product's signature artifact (positioning.md, pillar 3) — the link that
// lands on the second it was said.
export function Receipt({
  videoId,
  t,
  small = false,
}: {
  videoId: string;
  t: number;
  small?: boolean;
}) {
  const second = Math.floor(t);
  return (
    <a
      className={`${styles.receipt} ${small ? styles.sm : ""}`}
      href={youtubeAt(videoId, second)}
      rel="noopener"
    >
      <span>youtu.be/</span>
      <span className={`${styles.vid} ${styles.id}`}>{videoId}</span>
      <b>
        ?t={second}
        <ReceiptArrow />
      </b>
    </a>
  );
}

function ReceiptArrow() {
  return (
    <svg
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="square"
      aria-hidden="true"
    >
      <path d="M3.4 8.6 8.6 3.4M4.6 3.4h4v4" />
    </svg>
  );
}
