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
// the answer **inline**: this page does not poll, so what comes back is the
// only evidence a write leaves. It is re-read by Python after the write for
// exactly that reason — `set_state` re-arms the clock when it resumes, so a
// payload built from the row the handler read first would name the new state
// and the old `next_check_at` in one breath.
//
// **`onWritten` re-reads the page**, which is what `writes.py` did by
// redirecting back to it. The follow row a write answers with is complete, but
// the row is not the page: a resume re-arms a clock the *in-flight* line reads,
// a check queues a `follow_check` that belongs in "Recent checks", and
// "Index anyway" makes a job that belongs in the band below it and moves the
// decision counts and the ledger with it. Swapping the row alone left five
// bands describing the instance as it was before the button was pressed.

/** What a control does when the write lands: catch the page up. */
export type Written = () => void;

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
  const [write, run] = useWrite(send, onWritten);
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
  const [write, run] = useWrite(send, onWritten);

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
  const [write, run] = useWrite(send, onWritten);

  return (
    <details className={styles.disclose}>
      <summary>Edit the rule</summary>
      <RuleForm
        onFields={(next) => {
          fields.current = next;
          run();
        }}
      >
        {/* Keyed on **every** rule column, not three of them. The store clamps,
            parses and normalises what it is sent — a `min_duration` of `banana`
            comes back unset, a `check_interval_s` under the floor comes back at
            the floor, a tag list comes back deduplicated — and a form that
            reseeded only where the interval or the floor moved left the other
            eight controls showing what was typed rather than what was kept. */}
        <RuleFields ns="e" key={ruleKey(follow)} values={ruleValues(follow)} />
        <div className={`${dash.field} ${dash.actions}`}>
          <button className={dash.ghostlink} type="submit" disabled={write.status === "sending"}>
            {write.status === "sending" ? "saving…" : "Save the rule"}
          </button>
          {/* The way out of a disclosure that has been opened and thought
              better of — `follow.html`'s own link, and a navigation rather than
              a close, because it also throws away whatever was typed. */}
          <DashLink
            className={dash.ghostlink}
            href={`${ROOT}/following/${encodeURIComponent(follow.slug)}`}
          >
            Cancel
          </DashLink>
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
/** Every column a rule is made of, as one string — what the eleven controls
 *  are seeded from, so a save that changed any of them reseeds all of them. */
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

export function QueueControl({
  slug,
  url,
  onWritten,
}: {
  slug: string;
  url: string;
  onWritten: Written;
}) {
  const send = useCallback(() => dashboard.queueFollowUrl(slug, url), [slug, url]);
  const [write, run] = useWrite(send, onWritten);

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

/** A button, its busy word, and what the route answered **beside** it — the
 *  shape every control on this page shares.
 *
 *  The button stays, which is `follow.html`'s own shape: these controls
 *  redirected back to a page that drew them again, and none of them is a
 *  one-shot. Pause is followed by Resume, a rate limit is retried, a check is
 *  asked for twice when the first one found nothing. The one control that does
 *  not come back is Unfollow, because there is nothing left to press it on. */
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
  return (
    <>
      <button
        className={styles.rowbutton}
        type="button"
        onClick={run}
        disabled={write.status === "sending"}
      >
        {write.status === "sending" ? busy : label}
      </button>
      {write.status === "done" ? (
        <span className={styles.outcome} role="status">
          {done(write.outcome)}
        </span>
      ) : null}
      {write.status === "failed" ? <Refusal error={write.error} /> : null}
    </>
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
      {/* The third sentence, which says what to do instead. Inline it is the
          line after the message rather than a heading on a page of its own,
          and it is Python's either way — the jobs table's cancel refusal has
          printed it since the port, and this one had been dropping it. */}
      {refusal.next ? <span className={dash.outcomeNext}>{refusal.next}</span> : null}
    </span>
  );
}
