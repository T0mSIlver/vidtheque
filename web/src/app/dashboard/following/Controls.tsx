"use client";

import { useRouter } from "next/navigation";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { FollowDetailRow } from "@/lib/dashboard/schemas";
import dash from "../dashboard.module.css";
import { DashLink, refusalOf, useWrite, type Write } from "../parts";
import styles from "./following.module.css";
import { RuleFields, RuleForm, ruleValues } from "./parts";

// The five writes on a follow's own page (dashboard.md §18.5, §21). Not one of
// them decides anything: four go through `tools/follows.follow_channel` — the
// same call the model makes — and the edit goes through the validator that tool
// shares, so the URL normalisation, the duration parser, the tag rules, the
// interval floor and both clamps are all Python's already.
//
// They are `fetch` rather than the Jinja page's real forms because they need
// the answer **inline**: this page does not poll, so the row that comes back is
// the only evidence a write leaves. It is re-read by Python after the write for
// exactly that reason — `set_state` re-arms the clock when it resumes, so a
// payload built from the row the handler read first would name the new state
// and the old `next_check_at` in one breath.
//
// `onWritten` is how the page catches up, and it is the *whole* catching up:
// §21's follow block carries `last_error_code` and `last_error_message`, so the
// row a write answers with is a complete one — including the failure a resume
// just cleared — and there is nothing left for a second read to find.

/** What a control hands back: the follow as it stands after the write. */
export type Written = (follow: FollowDetailRow) => void;

/**
 * Pause, resume and try-again — one route with the verb in the body.
 *
 * `failing` resumes for the same reason `paused` does: it is a follow the
 * scheduler will not enqueue, and resume is the one control that clears the
 * error and re-arms the clock. Offering only Pause here left a channel that
 * 404'd once reachable solely through pause-then-resume, while `Check now`
 * printed a time and queued nothing.
 */
export function StateControl({
  follow,
  onWritten,
}: {
  follow: FollowDetailRow;
  onWritten: Written;
}) {
  const paused = follow.state === "paused" || follow.state === "failing";
  const action = paused ? "resume" : "pause";
  const send = useCallback(
    () => dashboard.setFollowState(follow.slug, action),
    [follow.slug, action],
  );
  const [write, run] = useWrite(send, (outcome) => onWritten(outcome.follow));
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

/** Check now: it does not run a check, it makes the clock due, and the queue
 *  claims a `follow_check` on its next tick. The row that comes back says so —
 *  `next_check_at: 0` is due immediately — which is why the button answers
 *  with the clock rather than with a sentence about what will happen. */
export function CheckControl({
  follow,
  onWritten,
}: {
  follow: FollowDetailRow;
  onWritten: Written;
}) {
  const send = useCallback(() => dashboard.checkFollowNow(follow.slug), [follow.slug]);
  const [write, run] = useWrite(send, (outcome) => onWritten(outcome.follow));

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

/**
 * Unfollow — the one irreversible control on this surface, so it asks twice.
 *
 * The Jinja page's single button could afford not to: it navigated away, and
 * the sentence under it said what survives. A `fetch` does neither, so the
 * confirmation carries that sentence instead — the videos this follow brought
 * in stay, because they are corpus and not membership, and the rule and the
 * ledger are what go.
 *
 * On success it lands on the list, which is where §18.5 puts it: a page for a
 * follow that no longer exists is not a page to stay on.
 */
export function DeleteControl({ slug }: { slug: string }) {
  const router = useRouter();
  const [asked, setAsked] = useState(false);
  const send = useCallback(() => dashboard.deleteFollow(slug), [slug]);
  const [write, run] = useWrite(send, () => router.push(`${ROOT}/following`));

  if (write.status === "done") {
    return (
      <span className={styles.outcome} role="status">
        <span>
          Unfollowed. {write.outcome.videos_kept} video(s) it brought in stayed in the corpus.
        </span>
      </span>
    );
  }
  if (write.status === "failed") return <Refusal error={write.error} />;

  if (!asked) {
    return (
      <button className={styles.rowbutton} type="button" onClick={() => setAsked(true)}>
        Unfollow
      </button>
    );
  }
  return (
    <span className={styles.confirm}>
      <span className={styles.confirmWord}>Stop the checks and delete the ledger?</span>
      <button
        className={`${styles.rowbutton} ${styles.danger}`}
        type="button"
        onClick={run}
        disabled={write.status === "sending"}
      >
        {write.status === "sending" ? "unfollowing…" : "Unfollow"}
      </button>
      <button className={styles.rowbutton} type="button" onClick={() => setAsked(false)}>
        Keep it
      </button>
    </span>
  );
}

/**
 * The edit disclosure — the one write here that is not a `follow_channel`
 * action, because the tool deliberately has none: `action="follow"` on an
 * already-followed URL returns the existing follow and changes nothing, which
 * is what makes a retried request safe.
 *
 * A `<details>` element, exactly as in Jinja: the sentence-length rule is what
 * is read and the eleven fields are what is occasionally changed, and a
 * disclosure needs no script at all. The form is re-keyed on the row, so the
 * controls reseed themselves from what the store kept after a save.
 */
export function RulesDisclosure({
  follow,
  onWritten,
}: {
  follow: FollowDetailRow;
  onWritten: Written;
}) {
  const fields = useRef<Record<string, string>>({});
  const send = useCallback(
    () => dashboard.setFollowRules(follow.slug, fields.current),
    [follow.slug],
  );
  const [write, run] = useWrite(send, (outcome) => onWritten(outcome.follow));

  return (
    <details className={styles.disclose}>
      <summary>Edit the rule</summary>
      <RuleForm
        onFields={(next) => {
          fields.current = next;
          run();
        }}
      >
        <RuleFields
          ns="e"
          key={`${follow.check_interval_s}-${follow.min_duration_s}-${follow.max_per_check}`}
          values={ruleValues(follow)}
        />
        <div className={`${dash.field} ${dash.actions}`}>
          <button className={dash.ghostlink} type="submit" disabled={write.status === "sending"}>
            {write.status === "sending" ? "saving…" : "Save the rule"}
          </button>
        </div>
      </RuleForm>
      {write.status === "done" ? (
        <div className={styles.receipt} role="status">
          <p className={styles.receiptLine}>Saved. The rule above is the one the store kept.</p>
        </div>
      ) : null}
      {write.status === "failed" ? (
        <div className={styles.receipt}>
          <Refusal error={write.error} block />
        </div>
      ) : null}
    </details>
  );
}

/**
 * `Index anyway` — one row of the ledger, overruled.
 *
 * This is the whole argument for the band it sits in: a rule that turned
 * something away is only honest if the person who wrote it can overrule it in
 * one click. `expand=none`, and the follow's own channels and tags, are
 * Python's — a video rescued from the ledger is built the way the follow would
 * have built it and filed where the follow files things.
 *
 * The Jinja form redirects to the new job. This one names it instead, because
 * the reader is part-way down a ledger they are still reading.
 */
export function QueueControl({ slug, url }: { slug: string; url: string }) {
  const send = useCallback(() => dashboard.queueFollowUrl(slug, url), [slug, url]);
  const [write, run] = useWrite(send);

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
          // Nothing asked for is nothing done, on both branches.
          <span>nothing was queued</span>
        )
      }
    />
  );
}

/** A button, its busy word, and what the route answered in its place — the
 *  shape every control on this page shares. The outcome replaces the control
 *  rather than sitting beside it: none of these actions is repeatable in the
 *  same breath, and a button that comes back invites a second POST. */
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
  if (write.status === "done") {
    return (
      <span className={styles.outcome} role="status">
        {done(write.outcome)}
      </span>
    );
  }
  if (write.status === "failed") return <Refusal error={write.error} />;
  return (
    <button
      className={styles.rowbutton}
      type="button"
      onClick={run}
      disabled={write.status === "sending"}
    >
      {write.status === "sending" ? busy : label}
    </button>
  );
}

/** Why a write was refused, in the API's own words — the code, the message and
 *  the `next:` line are policy text and stay Python's. */
function Refusal({ error, block }: { error: unknown; block?: boolean }) {
  const refusal = refusalOf(error);
  if (block) {
    return (
      <>
        <p className={`${styles.receiptLine} ${styles.outcomeBad}`}>
          <code>{refusal.code}</code>
          <span>{refusal.message}</span>
        </p>
        {refusal.next ? <p className={styles.receiptNext}>{refusal.next}</p> : null}
      </>
    );
  }
  return (
    <span className={`${styles.outcome} ${styles.outcomeBad}`} role="status">
      <code>{refusal.code}</code> <span>{refusal.message}</span>
    </span>
  );
}
