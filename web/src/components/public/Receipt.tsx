import { receiptParts } from "@/lib/format";
import styles from "./Receipt.module.css";

/**
 * The receipt slab: `youtu.be/` · the talk's id · `?t=` the second, printed as
 * three spans (demo-site.md §6.5). `lg` is the filled slab; `sm` is the
 * bordered one a list of moments uses. A link that is not http(s) prints none.
 */
export function Receipt({
  href,
  size = "sm",
  className = "",
}: {
  href: string;
  size?: "sm" | "lg";
  className?: string;
}) {
  const parts = receiptParts(href);
  if (!parts) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={[styles.rcpt, size === "lg" ? styles.lg : "", className].filter(Boolean).join(" ")}
    >
      <span className={styles.host}>{parts.host}</span>
      <span className={styles.vid}>{parts.id}</span>
      <span className={styles.t}>{`${parts.query} ↗`}</span>
    </a>
  );
}
