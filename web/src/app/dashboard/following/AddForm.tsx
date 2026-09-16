"use client";

import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { FollowCreated } from "@/lib/dashboard/schemas";
import controls from "@/components/dashboard/kit/controls.module.css";
import { notice, RefusalNotice } from "@/components/dashboard/kit/notice";
import { DashLink, Panel, ui } from "@/components/dashboard/kit/ui";
import { focusOnArrival, useWrite, useWriteSide } from "@/components/dashboard/kit/write";
import styles from "./following.module.css";
import { RuleFields, RuleForm, ruleValues } from "./parts";

/**
 * Follow a channel (`POST /dashboard/following`). With nothing followed this
 * is the empty state. A made follow re-reads the listing, so the row lands
 * where the store's `failing_first` order puts it.
 */
export function AddForm({
  follows,
  vectorsReason,
  onMade,
}: {
  follows: number;
  vectorsReason: string | null;
  onMade: () => void;
}) {
  const { indexable } = useWriteSide();
  const [write, run] = useWrite(
    (fields: Record<string, string>) => dashboard.followChannel(fields),
    (outcome) => {
      if (outcome.follow && !outcome.already_following) onMade();
    },
  );
  const sending = write.status === "sending";

  return (
    <Panel id="add" title="Follow a channel">
      <p className={ui.emptyNote}>
        {follows
          ? "A follow checks a channel or a playlist on its own clock, judges every new upload against a rule you write, and records what it decided either way."
          : "Nothing is followed yet. A follow checks a channel or a playlist on its own clock, indexes the new uploads that match a rule you write, and keeps a ledger of every one it passed over and the number that made the decision."}
      </p>

      {/* §5.5: refused above the controls, in the instance's words; the fields
          stay live, because a rule is worth writing down while that is fixed. */}
      {indexable ? null : (
        <p className={notice.panelNote}>
          Indexing is disabled on this instance
          {vectorsReason ? ` (${vectorsReason})` : ""}, so a follow would queue videos it cannot
          build. Fix the config or dimension mismatch and restart.
        </p>
      )}

      <RuleForm onFields={run}>
        <div className={styles.formrow}>
          <div className={`${controls.field} ${controls.wide}`}>
            <label htmlFor="f-url">Channel or playlist URL</label>
            <input
              id="f-url"
              name="url"
              type="text"
              required
              autoComplete="off"
              spellCheck={false}
              placeholder="https://www.youtube.com/@handle"
            />
          </div>
          <div className={`${controls.field} ${controls.wide}`}>
            <label htmlFor="f-title">Name</label>
            <input
              id="f-title"
              name="title"
              type="text"
              autoComplete="off"
              placeholder="read off the URL if you leave it empty"
            />
          </div>
        </div>
        <p className={styles.fieldHelp}>
          A channel URL (<code>/@handle</code>, <code>/channel/UC…</code>) or a playlist. A single
          video is not a follow — <DashLink href={`${ROOT}/index`}>Add videos</DashLink> indexes one
          of those.
        </p>

        <RuleFields ns="f" values={ruleValues()} />

        <div data-write="">
          <div className={`${controls.field} ${controls.actions}`}>
            <button
              className={controls.ghostlink}
              type="submit"
              aria-disabled={sending || undefined}
              title={indexable ? undefined : "This instance's database refuses writes."}
            >
              {sending ? "following…" : "Follow"}
            </button>
            <DashLink className={controls.ghostlink} href={`${ROOT}/index`}>
              Add videos
            </DashLink>
          </div>
          {write.status === "done" ? <MadeIt outcome={write.outcome} /> : null}
          {write.status === "failed" ? (
            <RefusalNotice error={write.error} variant="receipt" />
          ) : null}
        </div>
      </RuleForm>
    </Panel>
  );
}

/** What the tool did; `already_following` made nothing. */
function MadeIt({ outcome }: { outcome: FollowCreated }) {
  return (
    <div className={notice.receipt} role="status" tabIndex={-1} ref={focusOnArrival}>
      {outcome.follow ? (
        <>
          <p className={notice.receiptLine}>
            <span>{outcome.already_following ? "Already following" : "Now following"}</span>
            <DashLink href={`${ROOT}/following/${encodeURIComponent(outcome.follow.slug)}`}>
              {outcome.follow.title}
            </DashLink>
          </p>
          <p className={notice.receiptNext}>
            {outcome.already_following
              ? "Nothing was made: the tool returns the follow that was already there, unchanged."
              : "Its own page has the rule the check will obey, and everything it passes over."}
          </p>
        </>
      ) : (
        <p className={notice.receiptLine}>The follow was accepted.</p>
      )}
    </div>
  );
}
