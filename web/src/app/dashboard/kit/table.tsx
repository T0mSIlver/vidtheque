import type { ReactNode } from "react";
import { ROOT } from "@/lib/dashboard/client";
import { count } from "@/lib/format";
import controls from "./controls.module.css";
import styles from "./table.module.css";
import { DashLink } from "./ui";

export { styles as table };

/** What a clamp or a fallback moved, in Python's own words (policy text). */
export function Notes({
  notes,
  className,
  label,
}: {
  notes: string[];
  className?: string;
  label?: string;
}) {
  if (!notes.length) return null;
  return (
    <ul className={className ?? styles.notes} aria-label={label}>
      {notes.map((note) => (
        <li key={note}>{note}</li>
      ))}
    </ul>
  );
}

/** `N shown[ of T][, more available].` and whatever the page adds after it. */
export function TableCount({
  shown,
  total,
  hasMore,
  children,
}: {
  shown: number;
  total?: number;
  hasMore: boolean;
  children?: ReactNode;
}) {
  return (
    <p className={styles.tablecount} role="status">
      <span>
        <span className={styles.shown}>{shown}</span> shown
        {total === undefined ? null : (
          <>
            {" "}
            of <span className={styles.shown}>{count(total)}</span>
          </>
        )}
        {hasMore ? ", more available" : null}.{children ? <> {children}</> : null}
      </span>
    </p>
  );
}

/** Previous and next pages, as real links at the server's own offsets. */
export function Pager({
  limit,
  offset,
  hasMore,
  href,
  previous = "← Previous",
  next = `Next ${limit} →`,
  label = "Pagination",
}: {
  limit: number;
  offset: number;
  hasMore: boolean;
  href: (offset: number) => string;
  previous?: string;
  next?: string;
  label?: string;
}) {
  if (!offset && !hasMore) return null;
  return (
    <nav className={styles.pager} aria-label={label}>
      {offset ? (
        <DashLink className={controls.ghostlink} href={href(Math.max(offset - limit, 0))}>
          {previous}
        </DashLink>
      ) : null}
      {hasMore ? (
        <DashLink className={controls.ghostlink} href={href(offset + limit)}>
          {next}
        </DashLink>
      ) : null}
    </nav>
  );
}

/** `Section / id` above a detail page's head. */
export function Crumbs({
  section,
  label,
  id,
}: {
  /** The list page, under `/dashboard`. */
  section: string;
  label: string;
  id: string;
}) {
  return (
    <p className={styles.crumbs}>
      <DashLink href={`${ROOT}/${section}`}>{label}</DashLink> <span aria-hidden="true">/</span>{" "}
      <code>{id}</code>
    </p>
  );
}
