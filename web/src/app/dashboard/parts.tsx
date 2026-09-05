"use client";

import Link from "next/link";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { Pill, type Tone } from "@/components/Pill";
import { RetryIn } from "@/components/RetryIn";
import { DashboardError } from "@/lib/dashboard/client";
import type { Readiness as ReadinessPayload } from "@/lib/dashboard/schemas";
import { at, count, iso } from "@/lib/format";
import styles from "./dashboard.module.css";
import { isPorted } from "./ported";
import { useSession } from "./session";

// The vocabulary both ported pages are built from. Every piece of it exists on
// the Jinja pages that still serve the rest of the surface, and none of it is a
// card: a panel is a label and a hairline, a figure is a label and a number,
// and a state is a word in its tone.

export function PageHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={styles.pagehead}>
      <div className={styles.pageheadLine}>
        {/* The type class is the global one from `styles/type.css`: the rule
            that would set it here is a descendant selector, and `composes`
            only works on a simple one. */}
        <h1 className="t-headline">{title}</h1>
        {children ? <p className={styles.meta}>{children}</p> : null}
      </div>
    </div>
  );
}

/** A label and the machine string it names — one unbreakable unit, so a strip
 *  that runs out of room wraps between facts and never through a clock. */
export function Fact({ label, value }: { label: string; value: string }) {
  return (
    <span className={styles.fact}>
      {label} <span className={styles.mono}>{value}</span>
    </span>
  );
}

/**
 * The middot between two facts, and the en dash between the two ends of a
 * range. Both are glued to the thing *before* them by whoever renders them, so
 * a strip that runs out of room leaves the mark at the end of the line it
 * belongs to rather than dangling at the start of the next one.
 */
export const Sep = ({ children = "·" }: { children?: string }) => (
  <span className={styles.sep}>{children}</span>
);

/** One unbreakable unit: whatever is inside it wraps as a whole or not at all. */
export const Unbroken = ({ children }: { children: ReactNode }) => (
  <span className={styles.fact}>{children}</span>
);

export function Panel({
  id,
  title,
  drift,
  aside,
  children,
}: {
  id: string;
  title: string;
  /** The one 2px rule on this surface: a panel whose halves disagree. */
  drift?: boolean;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`${styles.panel} ${drift ? styles.drift : ""}`} aria-labelledby={id}>
      {aside ? (
        <div className={styles.panelHeadline}>
          <h2 className={styles.panelTitle} id={id}>
            {title}
          </h2>
          {aside}
        </div>
      ) : (
        <h2 className={styles.panelTitle} id={id}>
          {title}
        </h2>
      )}
      {children}
    </section>
  );
}

export function Figure({
  label,
  children,
  notes,
}: {
  label: string;
  children: ReactNode;
  notes?: ReactNode[];
}) {
  return (
    <div className={styles.figure}>
      <dt className={styles.figureLabel}>{label}</dt>
      <dd className={styles.figureValue}>{children}</dd>
      {(notes ?? []).map((note, index) => (
        <dd className={styles.note} key={index}>
          {note}
        </dd>
      ))}
    </div>
  );
}

/**
 * A link into this surface, whichever half of it currently serves the target.
 *
 * A ported page is reached with `Link` and swaps the React tree; a page Python
 * still renders is a plain anchor, because a client-side navigation to it
 * would ask this app's router for a route it does not have. `ported.ts` is
 * where that is decided, so no caller has to keep the list.
 */
export function DashLink({
  href,
  className,
  children,
  ...rest
}: {
  href: string;
  className?: string;
  children: ReactNode;
} & Pick<AnchorHTMLAttributes<HTMLAnchorElement>, "aria-current" | "tabIndex" | "id">) {
  if (isPorted(href)) {
    return (
      <Link className={className} href={href} {...rest}>
        {children}
      </Link>
    );
  }
  return (
    <a className={className} href={href} {...rest}>
      {children}
    </a>
  );
}

/**
 * A count that is a door into a filtered page — the same idiom the gap
 * sentences use, because the number is the thing you want to go and look at.
 * A zero is not a door and does not wear the accent.
 */
export function CountLink({
  href,
  n,
  children,
}: {
  href: string;
  n: number;
  children?: ReactNode;
}) {
  return (
    <DashLink className={n ? undefined : styles.none} href={href}>
      {children ?? count(n)}
    </DashLink>
  );
}

export const Unit = ({ children }: { children: ReactNode }) => (
  <span className={styles.unit}>{children}</span>
);

/** A sentence with a number in it; the number is the link. */
export function GapLine({ href, n, children }: { href?: string; n: number; children: ReactNode }) {
  return (
    <li>
      {href ? (
        <CountLink href={href} n={n} />
      ) : (
        <span className={`${styles.figureCount} ${n ? "" : styles.none}`}>{count(n)}</span>
      )}
      <span>{children}</span>
    </li>
  );
}

/**
 * A key joined to its own pill. The state's *sentence* — why the worker is
 * unavailable, why search is full-text only — rides as the pill's tooltip with
 * an sr-only copy: the word is the reading, the sentence is the footnote.
 */
export function StatePair({
  label,
  word,
  tone,
  detail,
}: {
  label: string;
  word: string;
  tone?: Tone;
  detail?: string | null;
}) {
  return (
    <span
      className={`${styles.statepair} ${detail ? styles.detail : ""}`}
      data-detail={detail || undefined}
      tabIndex={detail ? 0 : undefined}
    >
      <span className={styles.statepairKey}>{label}</span>
      <Pill state={word} tone={tone} />
      {detail ? <span className={styles.srOnly}>{detail}</span> : null}
    </span>
  );
}

/**
 * The pipeline observation, identical on both pages because it is the same
 * reading (dashboard.md §15). One flat observation, never a history.
 *
 * Two of the five states are the deployment's rather than the corpus's, so
 * they come from the session: whether this box will accept work at all. The
 * projection carries neither the worker probe nor that state — "indexing
 * refused" is a sentence about a worker nobody visiting the demo can reach
 * (§2.4) — and the block is simply absent rather than redacted in place.
 */
export function Readiness({
  readiness,
  redacted,
  drift,
  children,
}: {
  readiness: ReadinessPayload;
  redacted: boolean;
  drift?: boolean;
  /** The overview's declared-against-served diff. The ledger's panel is the
   *  strip alone, which is what its template shows. */
  children?: ReactNode;
}) {
  const session = useSession();
  const checked = iso(readiness.checked_at);
  return (
    <Panel
      id="readiness"
      title="Pipeline readiness"
      drift={drift}
      aside={
        <p className={styles.clock}>
          last health check <time dateTime={checked}>{at(readiness.checked_at)}</time>
        </p>
      }
    >
      <p className={styles.states}>
        <StatePair label="MCP" word={readiness.mcp} tone="ok" />
        <StatePair label="Database" word={readiness.database} tone="ok" />
        {readiness.worker ? (
          <StatePair
            label="Worker"
            word={readiness.worker.state}
            tone={
              readiness.worker.state === "ready"
                ? "ok"
                : readiness.worker.state === "unavailable"
                  ? "bad"
                  : "neutral"
            }
            detail={readiness.worker.detail}
          />
        ) : null}
        <StatePair
          label="Vector search"
          word={readiness.vectors.enabled ? "ready" : "full-text only"}
          tone={readiness.vectors.enabled ? "ok" : "bad"}
          detail={readiness.vectors.reason}
        />
        {!redacted && session ? (
          <StatePair
            label="Indexing"
            word={session.writes_allowed ? "allowed" : "refused"}
            tone={session.writes_allowed ? "ok" : "bad"}
          />
        ) : null}
      </p>
      {children}
    </Panel>
  );
}

// ---------------------------------------------------------------- the read

/** The request is out. A word, not a spinner: nothing is known yet to animate. */
export const Reading = () => <p className={styles.reading}>reading…</p>;

/**
 * What a failed read looks like, in the four shapes it comes in.
 *
 * The message is the API's own, never one written here: refusal codes,
 * messages and their `next:` line are policy text and stay Python's
 * (frontend-migration.md §1 decision 5).
 */
export function ReadFailure({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const session = useSession();

  if (error instanceof DashboardError && error.status === 429) {
    // The limiter's own Retry-After, ticking. Retrying re-runs the read rather
    // than refreshing a route: the payload never came through the router.
    return (
      <RetryIn
        seconds={error.retryAfter ?? 60}
        message={error.message || "Too many dashboard requests for now."}
        onRetry={onRetry}
      />
    );
  }

  // Signed out. The client has already sent the browser to the sign-in page
  // where this deployment has one; this is what the page says meanwhile, and
  // what it keeps saying on an instance that gates its reads and registers no
  // login page at all.
  if (error instanceof DashboardError && error.status === 401) {
    return (
      <div className={styles.refusal}>
        <p className={styles.refusalTitle}>This dashboard is not open to this browser.</p>
        <p className={styles.refusalMessage}>{error.message}</p>
        {error.next ? <p className={styles.refusalNext}>{error.next}</p> : null}
        {session?.login_url ? (
          <p className={styles.refusalAction}>
            <a className={styles.signin} href={session.login_url}>
              Sign in
            </a>
          </p>
        ) : null}
      </div>
    );
  }

  const refusal = error instanceof DashboardError ? error : null;
  return (
    <div className={styles.refusal}>
      <p className={styles.refusalTitle}>This page could not read the instance.</p>
      <p className={styles.refusalMessage}>
        {refusal ? refusal.message : error instanceof Error ? error.message : "Unknown error."}
      </p>
      {refusal?.next ? <p className={styles.refusalNext}>{refusal.next}</p> : null}
      {refusal ? (
        <p className={styles.refusalCode}>
          {refusal.code} · HTTP {refusal.status}
        </p>
      ) : null}
      <p className={styles.refusalAction}>
        <button className={styles.action} type="button" onClick={onRetry}>
          try again
        </button>
      </p>
    </div>
  );
}
