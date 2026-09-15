import { receiptParts } from "@/lib/format";
import styles from "./Receipt.module.css";

// **The product's signature artifact** (`demo/style.css`, "the receipts"), and
// the landing's own slab drawn against live data. Three spans and never one
// string: `youtu.be/` in the muted ink says which surface, the id in the
// reading ink says which talk, and the block says which second — with the
// arrow that says this one leaves the page.
//
// Two sizes, and the difference is the fill. The small slab is border and ink,
// because ten filled gold blocks down a page of moments would spend the accent
// on the list instead of on the hit; the large one fills its `?t=` block and
// there are three of it — one per Sources row, one in the lightbox.
//
// A link the page cannot parse has no honest receipt: it prints none rather
// than a URL it guessed at.
export function Receipt({
  href,
  size = "sm",
  className = "",
}: {
  href: string;
  /** `lg` is the filled slab (demo-site.md §6.5); `sm` is the moment's own. */
  size?: "sm" | "lg";
  /** Where the caller puts it — the slab owns its face, the row its place. */
  className?: string;
}) {
  const parts = receiptParts(href);
  if (!parts) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={[styles.rcpt, size === "lg" ? styles.lg : "", className]
        .filter(Boolean)
        .join(" ")}
    >
      <span className={styles.host}>{parts.host}</span>
      <span className={styles.vid}>{parts.id}</span>
      <span className={styles.t}>{`${parts.query} ↗`}</span>
    </a>
  );
}
