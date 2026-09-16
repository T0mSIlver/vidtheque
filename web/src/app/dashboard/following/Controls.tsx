"use client";

import { useRouter } from "next/navigation";
import { useId, useState, type ReactNode } from "react";
import { Pill } from "@/components/ui/Pill";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { FollowDetailRow } from "@/lib/dashboard/schemas";
import { hours } from "@/lib/format";
import controls from "../kit/controls.module.css";
import { notice, RefusalNotice } from "../kit/notice";
import { DashLink } from "../kit/ui";
import { focusOnArrival, useWrite, type Write } from "../kit/write";
import styles from "./following.module.css";
import { RuleFields, RuleForm, ruleValues } from "./parts";

// The five writes on a follow's own page (dashboard.md §18.5, §21). Every
// answer is inline because the page does not poll, and `onWritten` re-reads
// the page: the row is not the page, and the checks, jobs and ledger move too.

/** What a control does when its write lands: catch the page up. */
export type Written = () => void;

/** Pause, resume, and try again on a `failing` follow: one route, the verb in
 *  the body. */
export function StateControl({
  follow,
  onWritten,
}: {
  follow: FollowDetailRow;
  onWritten: Written;
}) {
  const paused = follow.state === "paused" || follow.state === "failing";
  const action = paused ? "resume" : "pause";
  const [write, run] = useWrite(() => dashboard.setFollowState(follow.slug, action), onWritten);
  const label = paused ? (follow.state === "failing" ? "Try again" : "Resume") : "Pause";

  return (
    <Control
      write={write}
      run={run}
      label={label}
      busy={paused ? "resuming…" : "pausing…"}
      done={(outcome) => <Pill state={outcome.follow.state} />}
    />
  );
}

/** Check now makes the clock due; it does not run a check. On a follow that
 *  gave up it stays, disabled, with Python's refusal beside it as its help. */
export function CheckControl({
  follow,
  onWritten,
}: {
  follow: FollowDetailRow;
  onWritten: Written;
}) {
  const [write, run] = useWrite(() => dashboard.checkFollowNow(follow.slug), onWritten);
  const reasonId = useId();

  if (follow.state === "failing" && !follow.retrying) {
    // Visible, not a `title`: a native tooltip is unreachable by touch and by
    // keyboard, the same reading that moved §15's state detail off `title`.
    return (
      <span className={styles.stopped}>
        <button
          className={notice.rowbutton}
          type="button"
          disabled
          aria-describedby={follow.not_schedulable_reason ? reasonId : undefined}
        >
          Check now
        </button>
        {follow.not_schedulable_reason ? (
          <span className={styles.stoppedWhy} id={reasonId}>
            {follow.not_schedulable_reason}
          </span>
        ) : null}
      </span>
    );
  }

  return (
    <Control
      write={write}
      run={run}
      label="Check now"
      busy="asking…"
      done={(outcome) => (
        <>
          <Pill state={outcome.follow.state} />
          <span>{outcome.follow.next_check_at ? "clock moved" : "due now"}</span>
        </>
      )}
    />
  );
}

/** Unfollow asks twice and lands on the list; the videos it brought in stay,
 *  and so does the budget it spent (0007). */
export function DeleteControl({ slug }: { slug: string }) {
  const router = useRouter();
  const [asked, setAsked] = useState(false);
  const [write, run] = useWrite(
    () => dashboard.deleteFollow(slug),
    () => router.push(`${ROOT}/following`),
  );
  const sending = write.status === "sending";

  if (write.status === "done") {
    return (
      <span
        className={notice.outcome}
        data-write=""
        role="status"
        tabIndex={-1}
        ref={focusOnArrival}
      >
        <span>
          Unfollowed. {write.outcome.videos_kept} video(s) it brought in stayed in the corpus.
        </span>
        {write.outcome.spent_s > 0 ? (
          <span className={notice.outcomeNext}>
            Not a refund: the {hours(write.outcome.spent_s)}h this follow accepted in the last 24h
            stay spent. They were downloaded and indexed; deleting the rule does not un-spend the
            day.
          </span>
        ) : null}
      </span>
    );
  }
  if (write.status === "failed") return <RefusalNotice error={write.error} variant="inline" />;

  if (!asked) {
    return (
      <button className={notice.rowbutton} type="button" onClick={() => setAsked(true)}>
        Unfollow
      </button>
    );
  }
  return (
    <span className={styles.confirm} data-write="">
      <span className={styles.confirmWord}>Stop the checks and delete the ledger?</span>
      <button
        className={`${notice.rowbutton} ${styles.danger}`}
        type="button"
        onClick={() => run()}
        aria-disabled={sending || undefined}
      >
        {sending ? "unfollowing…" : "Unfollow"}
      </button>
      <button className={notice.rowbutton} type="button" onClick={() => setAsked(false)}>
        Keep it
      </button>
    </span>
  );
}

/** Every column a rule is made of: a save that changed any reseeds all eleven. */
function ruleKey(follow: FollowDetailRow): string {
  return [
    follow.tabs.join(","),
    follow.mode,
    follow.channels,
    follow.tags.join(","),
    follow.min_duration_s,
    follow.max_duration_s,
    follow.title_include.join(","),
    follow.title_exclude.join(","),
    follow.backfill,
    follow.max_per_check,
    follow.check_interval_s,
  ].join("|");
}

/** The edit disclosure: a `<details>`, and the controls reseed from the row
 *  the store kept (it clamps and normalises what it is sent). */
export function RulesDisclosure({
  follow,
  onWritten,
}: {
  follow: FollowDetailRow;
  onWritten: Written;
}) {
  const [write, run] = useWrite(
    (fields: Record<string, string>) => dashboard.setFollowRules(follow.slug, fields),
    onWritten,
  );
  const sending = write.status === "sending";

  return (
    <details className={styles.disclose}>
      <summary>Edit the rule</summary>
      <RuleForm onFields={run}>
        <RuleFields ns="e" key={ruleKey(follow)} values={ruleValues(follow)} />
        <div data-write="">
          <div className={`${controls.field} ${controls.actions}`}>
            <button
              className={controls.ghostlink}
              type="submit"
              aria-disabled={sending || undefined}
            >
              {sending ? "saving…" : "Save the rule"}
            </button>
            {/* A navigation rather than a close: it throws away what was typed. */}
            <DashLink
              className={controls.ghostlink}
              href={`${ROOT}/following/${encodeURIComponent(follow.slug)}`}
            >
              Cancel
            </DashLink>
          </div>
          {write.status === "done" ? (
            <div className={notice.receipt} role="status" tabIndex={-1} ref={focusOnArrival}>
              <p className={notice.receiptLine}>Saved. The rule above is the one the store kept.</p>
            </div>
          ) : null}
          {write.status === "failed" ? (
            <RefusalNotice error={write.error} variant="receipt" />
          ) : null}
        </div>
      </RuleForm>
    </details>
  );
}

/** Index anyway: one ledger row overruled, built the way the follow would have
 *  built it (Python's `expand=none`, channels and tags). */
export function QueueControl({
  slug,
  url,
  onWritten,
}: {
  slug: string;
  url: string;
  onWritten: Written;
}) {
  const [write, run] = useWrite(() => dashboard.queueFollowUrl(slug, url), onWritten);

  return (
    <Control
      write={write}
      run={run}
      label="Index anyway"
      busy="queueing…"
      done={(outcome) =>
        outcome.job_id ? (
          <DashLink href={`${ROOT}/jobs/${encodeURIComponent(outcome.job_id)}`}>
            <code>{outcome.job_id}</code>
          </DashLink>
        ) : (
          <span>nothing was queued</span>
        )
      }
    />
  );
}

/** A button, its busy word, and what the route answered beside it. The button
 *  stays: none of these writes is a one-shot. */
function Control<T>({
  write,
  run,
  label,
  busy,
  done,
}: {
  write: Write<T>;
  run: () => void;
  label: string;
  busy: string;
  done: (outcome: T) => ReactNode;
}) {
  const sending = write.status === "sending";
  return (
    <span data-write="">
      <button
        className={notice.rowbutton}
        type="button"
        onClick={() => run()}
        aria-disabled={sending || undefined}
      >
        {sending ? busy : label}
      </button>
      {write.status === "done" ? (
        <span
          className={notice.outcome}
          role="status"
          tabIndex={-1}
          ref={focusOnArrival}
          // Re-mount per answer, so a second write's outcome takes focus too.
          key={JSON.stringify(write.outcome)}
        >
          {done(write.outcome)}
        </span>
      ) : null}
      {write.status === "failed" ? <RefusalNotice error={write.error} variant="inline" /> : null}
    </span>
  );
}
