"use client";

import Link from "next/link";
import { useCallback, useState, type AnchorHTMLAttributes, type ReactNode } from "react";
import { Pill, type Tone } from "@/components/Pill";
import { RetryIn } from "@/components/RetryIn";
import { DashboardError } from "@/lib/dashboard/client";
import type { Readiness as ReadinessPayload } from "@/lib/dashboard/schemas";
import { at, count, day, iso } from "@/lib/format";
import styles from "./dashboard.module.css";
import { isPorted } from "./ported";
import { useSession } from "./session";

// The vocabulary both ported pages are built from. Every piece of it exists on
// the Jinja pages that still serve the rest of the surface, and none of it is a
// card: a panel is a label and a hairline, a figure is a label and a number,
// and a state is a word in its tone.

export function PageHead({
  title,
  note,
  children,
}: {
  title: string;
  /** A second line, under the title's own band and above the hairline. Only
   *  the index form has one: it is the page whose title names a verb, and the
   *  line says what may be handed to it. */
  note?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={styles.pagehead}>
      <div className={styles.pageheadLine}>
        {/* The type class is the global one from `styles/type.css`: the rule
            that would set it here is a descendant selector, and `composes`
            only works on a simple one. */}
        <h1 className="t-headline">{title}</h1>
        {children ? <p className={styles.meta}>{children}</p> : null}
      </div>
      {note ? <p className={styles.meta}>{note}</p> : null}
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

/** The video count's own note: when *these videos* were published.
 *
 *  A `Figure` note rather than a component, because both pages that print this
 *  fact print it in the same place under the same figure and a reader moving
 *  between them must not meet one fact in two spellings. It is a list so the
 *  empty corpus can return nothing: `published —–—` is a line whose entire
 *  content is the absence of one, and a corpus with no videos has no oldest
 *  and no newest — the number above it already says none.
 *
 *  Half a span is still a span (a video the store has no date for), and it
 *  keeps the dash on the end that is missing.
 */
export function publishedNote(span: { oldest: number | null; newest: number | null }): ReactNode[] {
  if (span.oldest === null && span.newest === null) return [];
  return [
    <>
      published{" "}
      <Unbroken>
        <span className={styles.mono}>{day(span.oldest)}</span>
        <Sep>–</Sep>
        <span className={styles.mono}>{day(span.newest)}</span>
      </Unbroken>
    </>,
  ];
}

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

// --------------------------------------------------------------- the write

// The three pieces every write control on this surface is built from. They
// arrived with the jobs pages' Cancel and Retry and moved here when the
// following pages started writing too: one control drawn twice is how two
// pages start disagreeing about what a refusal looks like.

/** One write, in the four states a control has to be able to draw. */
export type Write<T> =
  | { status: "idle" }
  | { status: "sending" }
  | { status: "done"; outcome: T }
  | { status: "failed"; error: unknown };

/**
 * A control that POSTs once and shows what came back.
 *
 * The outcome is rendered inline because that is the reason these routes
 * answer inline at all: a cancel whose only evidence is the next 2 s tick is a
 * button that looks broken, and the state the job is *now* in — settled
 * `cancelled`, or `running` with the request recorded — is precisely what a
 * poll cannot tell an operator (dashboard.md §21). The following pages have no
 * poll at all, so for them it is the only evidence there is.
 *
 * `onDone` is what a page does with the receipt: the jobs pages bring the tick
 * back with it, and the following pages put the returned row back in the table
 * — the row is the follow as it stands *after* the write, re-read by Python.
 */
export function useWrite<T>(send: () => Promise<T>, onDone?: (outcome: T) => void) {
  const [state, setState] = useState<Write<T>>({ status: "idle" });

  const run = useCallback(() => {
    setState({ status: "sending" });
    send().then(
      (outcome) => {
        setState({ status: "done", outcome });
        onDone?.(outcome);
      },
      (error: unknown) => setState({ status: "failed", error }),
    );
  }, [send, onDone]);

  return [state, run] as const;
}

/** The three sets a human ticks, and the CSV `index-video` reads — the labels
 *  and the sentences under them, from `dashboard/writes.py`'s `CHANNEL_BOXES`.
 *
 *  Here rather than on either form for the reason that module gives for owning
 *  them: the index form and the follow form tick the same three boxes for the
 *  same parameter, and a second copy of the labels is how one thing gets
 *  described two ways. All three ticked, or none, is the tool's word `all`, and
 *  which of those a submission meant is the *handler's* reading of it — nothing
 *  here collapses anything. */
export const CHANNEL_BOXES: [string, string, string][] = [
  ["transcript", "Transcript", "what was said, from the audio or the captions"],
  ["ocr", "On-screen text", "what the frames read, per keyframe"],
  ["frames", "Frame embeddings", "visual search over the keyframes"],
];

/** A submitted `<form>`, as the fields Python reads.
 *
 *  Straight off the form, so an unticked checkbox is absent exactly as it is in
 *  a browser's own submission — which is what both `_follow_rule_form` and
 *  `writes._submitted` count on when they collapse three ticked channels to the
 *  tool's word `all`. Nothing is corrected on the way out: every bound belongs
 *  to the handler, and one applied here would be a second validator, which is
 *  the thing §5.5 exists to argue against. */
export function formFields(form: HTMLFormElement): Record<string, string> {
  const fields: Record<string, string> = {};
  for (const [name, value] of new FormData(form).entries()) {
    if (typeof value === "string") fields[name] = value;
  }
  return fields;
}

/** Why a write was refused, in the API's own words. Code, message and the
 *  `next:` line are policy text and stay Python's; `403 E_BAD_ORIGIN` is the
 *  one that is a bug on this side rather than in the reader's session. */
export function refusalOf(error: unknown): { code: string; message: string; next?: string } {
  if (error instanceof DashboardError) {
    return { code: error.code, message: error.message, next: error.next };
  }
  return {
    code: "E_UNREACHABLE",
    message: error instanceof Error ? error.message : "The write did not reach the instance.",
  };
}

/**
 * Should this deployment draw a write control at all?
 *
 * `write_side` is whether the routes are registered — in
 * `VIDTHEQUE_PUBLIC_READONLY=1` and `VIDTHEQUE_AUTH=none` they are not, so
 * there is **no control**, disabled or otherwise: a button that 404s is worse
 * than a button that is not there (dashboard.md §2.3, §3.2 rule 3, §18.1). On
 * the following pages it decides more than a control: the whole surface is
 * registered with the writes, so `false` means the page itself is not there.
 *
 * `writes_allowed` is the database's own flag and is a different question, so
 * it disables rather than removes — and only the controls that feed
 * `index_video`. Cancel writes the job row, not the index, and stays live
 * exactly when an operator most needs it to (§5.5, and `job.html`'s own rule).
 */
export function useWriteSide(): { rendered: boolean; indexable: boolean } {
  const session = useSession();
  return {
    rendered: Boolean(session?.write_side),
    indexable: Boolean(session?.writes_allowed),
  };
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
