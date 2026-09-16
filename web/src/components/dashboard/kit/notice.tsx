"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Pill } from "@/components/ui/Pill";
import { RetryIn } from "@/components/ui/RetryIn";
import { DashboardError, ROOT } from "@/lib/dashboard/client";
import { sectionOf } from "../ported";
import { useSession } from "../session";
import controls from "./controls.module.css";
import styles from "./notice.module.css";
import { DashLink, PageHead, Panel, Title, ui } from "./ui";
import { focusOnArrival, refusalOf, type Refusal as RefusalText } from "./write";

export { styles as notice };

/** A band under a rule. Neutral unless it says otherwise. */
export function Notice({
  id,
  tone = "neutral",
  title,
  detail,
  next,
  children,
}: {
  id: string;
  tone?: "neutral" | "bad" | "warn";
  title: ReactNode;
  detail?: ReactNode;
  next?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className={styles.notice} data-tone={tone} aria-labelledby={id}>
      <h2 className={styles.title} id={id}>
        {title}
      </h2>
      {detail ? <p className={styles.detail}>{detail}</p> : null}
      {children}
      {next ? <p className={styles.next}>{next}</p> : null}
    </section>
  );
}

/** `next:` lines are sentence fragments; standing alone they take a capital. */
export function capitalise(sentence: string): string {
  return sentence ? sentence[0].toUpperCase() + sentence.slice(1) : sentence;
}

/**
 * A refusal in the API's own words, in one of three shapes: a `notice` band
 * where results would be, a `receipt` under a form's actions, or `inline`
 * beside a control in a row. The two write shapes take focus on arrival.
 */
export function RefusalNotice({
  error,
  variant = "notice",
  id = "refused",
}: {
  error: unknown;
  variant?: "notice" | "receipt" | "inline";
  id?: string;
}) {
  const refusal: RefusalText = refusalOf(error);
  if (variant === "inline") {
    return (
      <span
        className={styles.outcome}
        data-tone="bad"
        role="status"
        tabIndex={-1}
        ref={focusOnArrival}
      >
        <code>{refusal.code}</code> <span>{refusal.message}</span>
        {refusal.next ? <span className={styles.outcomeNext}>{refusal.next}</span> : null}
      </span>
    );
  }
  if (variant === "receipt") {
    return (
      <div className={styles.receipt} role="status" tabIndex={-1} ref={focusOnArrival}>
        <p className={styles.receiptLine} data-tone="bad">
          <code>{refusal.code}</code>
          <span>{refusal.message}</span>
        </p>
        {refusal.next ? <p className={styles.receiptNext}>{refusal.next}</p> : null}
      </div>
    );
  }
  return (
    <Notice
      id={id}
      tone="bad"
      title={refusal.message}
      detail={<code>{refusal.code}</code>}
      next={refusal.next}
    />
  );
}

export type Back = { href: string; label: string };

/**
 * A refusal as a page: the message as the title, the code as a state, and a
 * panel with somewhere to go — the section's own list and the overview.
 */
export function Refusal({
  code,
  message,
  title,
  next,
  back,
  onRetry,
}: {
  code: string;
  message: string;
  /** The document's name, when the message is too long for a tab. */
  title?: string;
  next?: string | null;
  back?: Back | null;
  onRetry?: () => void;
}) {
  const section = sectionOf(usePathname());
  return (
    <>
      <Title>{title ?? message}</Title>
      <PageHead title={message}>
        <Pill state={code} tone="bad" />
      </PageHead>

      <Panel id="recover" title="Where to go from here">
        {next ? <p className={ui.emptyLead}>{capitalise(next)}</p> : null}
        <p className={styles.actions}>
          {back ? (
            <DashLink className={controls.ghostlink} href={back.href}>
              {back.label}
            </DashLink>
          ) : null}
          {section === "jobs" ? (
            <DashLink className={controls.ghostlink} href={`${ROOT}/jobs`}>
              Every job this index has run
            </DashLink>
          ) : (
            <DashLink className={controls.ghostlink} href={`${ROOT}/videos`}>
              Everything that is indexed
            </DashLink>
          )}
          <DashLink className={controls.ghostlink} href={ROOT}>
            Corpus overview
          </DashLink>
          {onRetry ? (
            <button className={controls.ghostlink} type="button" onClick={onRetry}>
              Try again
            </button>
          ) : null}
        </p>
      </Panel>
    </>
  );
}

/**
 * A read that failed, replacing the page body: the limiter's countdown (the
 * cache retries on its own when it runs out), the sign-in refusal, or the
 * refusal page with a retry.
 */
export function ReadFailure({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const session = useSession();

  if (error instanceof DashboardError && error.status === 429) {
    return (
      <RetryIn
        seconds={error.retryAfter ?? 60}
        message={error.message || "Too many dashboard requests for now."}
        onRetry={onRetry}
      />
    );
  }

  if (error instanceof DashboardError && error.status === 401) {
    return (
      <Refusal
        code={error.code}
        message={error.message}
        next={error.next}
        back={session?.login_url ? { href: session.login_url, label: "Sign in" } : null}
      />
    );
  }

  const refusal = refusalOf(error);
  return (
    <Refusal
      code={refusal.code}
      message={
        error instanceof DashboardError || error instanceof Error
          ? refusal.message
          : "Unknown error."
      }
      next={refusal.next}
      onRetry={onRetry}
    />
  );
}

/**
 * A page that is part of the write side, on a deployment that registers none.
 * Not an error: the routes are not on this box at all (dashboard.md §2.3).
 */
export function Absent({
  title,
  heading,
  children,
}: {
  title: string;
  heading: string;
  children: ReactNode;
}) {
  return (
    <>
      <PageHead title={title} />
      <Notice
        id="absent"
        title={heading}
        detail={children}
        next={
          <>
            <DashLink href={ROOT}>The overview</DashLink> and{" "}
            <DashLink href={`${ROOT}/videos`}>the videos this index holds</DashLink> are what it
            does answer for.
          </>
        }
      />
    </>
  );
}
