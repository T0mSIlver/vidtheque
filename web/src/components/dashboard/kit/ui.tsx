"use client";

import Link from "next/link";
import {
  useEffect,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type CSSProperties,
  type ReactNode,
} from "react";
import { Pill, type Tone } from "@/components/ui/Pill";
import { count, DASH, day } from "@/lib/format";
import { isPorted } from "../ported";
import styles from "./ui.module.css";

export { styles as ui };

/** The document's name while this is on screen (React 19 hoists it). */
export function Title({ children }: { children: string }) {
  return <title>{`${children} — vidtheque`}</title>;
}

export function PageHead({
  title,
  note,
  children,
}: {
  title: ReactNode;
  /** A second line under the title's band. */
  note?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={styles.pagehead}>
      <div className={styles.pageheadLine}>
        <h1 className="t-headline">{title}</h1>
        {children ? <p className={styles.meta}>{children}</p> : null}
      </div>
      {note ? <p className={styles.meta}>{note}</p> : null}
    </div>
  );
}

/** A label and the machine string it names, wrapping as one unit. */
export function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <span className={styles.fact}>
      {label} <span className={styles.mono}>{value}</span>
    </span>
  );
}

/** The middot between facts, or the en dash of a range. Glued to the thing
 *  before it by its caller, so a wrapped strip never starts with one. */
export const Sep = ({ children = "·" }: { children?: string }) => (
  <span className={styles.sep}>{children}</span>
);

export const Unbroken = ({ children }: { children: ReactNode }) => (
  <span className={styles.fact}>{children}</span>
);

/** A strip of facts, each followed by its separator and a real space so JSX
 *  keeps a break opportunity between them. */
export function Facts({ facts }: { facts: [string, ReactNode][] }) {
  return (
    <>
      {facts.map(([label, value], index) => (
        <span key={label}>
          <Unbroken>
            <Fact label={label} value={value} />
            {index < facts.length - 1 ? <Sep /> : null}
          </Unbroken>{" "}
        </span>
      ))}
    </>
  );
}

/** When the corpus's videos were published, as a figure note — or nothing for
 *  an empty corpus. Half a span keeps the dash on its missing end. */
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
  subject,
  drift,
  aside,
  className,
  children,
}: {
  id: string;
  title: string;
  /** A corpus string in the heading, kept in its own case. */
  subject?: string | null;
  /** The 2px rule: a panel whose halves disagree. */
  drift?: boolean;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const heading = (
    <h2 className={styles.panelTitle} id={id}>
      {title}
      {subject ? (
        <>
          {" — "}
          <span className={styles.subject}>{subject}</span>
        </>
      ) : null}
    </h2>
  );
  return (
    <section
      className={[styles.panel, drift ? styles.drift : "", className ?? ""].join(" ").trim()}
      aria-labelledby={id}
    >
      {aside ? (
        <div className={styles.panelHeadline}>
          {heading}
          {aside}
        </div>
      ) : (
        heading
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

/** A link into this surface: `Link` to a page this app serves, a plain anchor
 *  to anything Python still answers under `/dashboard`. */
export function DashLink({
  href,
  className,
  children,
  scroll,
  ...rest
}: {
  href: string;
  className?: string;
  children: ReactNode;
  /** `false` keeps the scroll position across the navigation. */
  scroll?: boolean;
  "data-add-videos"?: string;
  "data-empty-add"?: string;
} & Pick<
  AnchorHTMLAttributes<HTMLAnchorElement>,
  "aria-current" | "aria-hidden" | "tabIndex" | "id" | "onClick"
>) {
  if (isPorted(href)) {
    return (
      <Link className={className} href={href} scroll={scroll} {...rest}>
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

/** A count that is a door into a filtered page; a zero is not a door. */
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
export function GapLine({
  href,
  n,
  figure,
  children,
}: {
  href?: string;
  n: number;
  /** A rendering of `n` other than `count(n)`, such as a capped `5+`. */
  figure?: ReactNode;
  children: ReactNode;
}) {
  return (
    <li>
      {href ? (
        <CountLink href={href} n={n}>
          {figure}
        </CountLink>
      ) : (
        <span className={`${styles.figureCount} ${n ? "" : styles.none}`}>
          {figure ?? count(n)}
        </span>
      )}
      <span>{children}</span>
    </li>
  );
}

/** A key joined to its pill; the state's sentence rides as a tooltip with an
 *  sr-only copy. */
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
 * The body of a page whose read has not answered yet, holding the space the
 * answer takes so the footer does not jump when it lands. A word for a screen
 * reader, nothing to animate (DESIGN.md: a state is a word).
 */
export function Pending({ height }: { height?: string }) {
  return (
    <div
      className={styles.pending}
      style={height ? ({ "--pending": height } as CSSProperties) : undefined}
      role="status"
    >
      <span className={styles.srOnly}>reading…</span>
    </div>
  );
}

/** A pending value that holds the width of the one it stands for, so a fact
 *  strip does not re-wrap when the read lands. */
export function Slot({ ch }: { ch: number }) {
  return (
    <span className={styles.slot} style={{ "--ch": `${ch}ch` } as CSSProperties}>
      {DASH}
    </span>
  );
}

/**
 * A page body that, while its read is out, holds the height its last answer
 * took, so a filter change or a new query neither collapses the page nor
 * clamps the scroll position.
 */
export function Body({
  ready,
  height,
  children,
}: {
  ready: boolean;
  /** The reservation before any answer has been measured. */
  height?: string;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [held, setHeld] = useState<number | null>(null);
  useEffect(() => {
    const element = box.current;
    if (!ready || !element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setHeld(element.offsetHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, [ready]);
  if (ready) return <div ref={box}>{children}</div>;
  return <Pending height={held ? `${held}px` : height} />;
}
