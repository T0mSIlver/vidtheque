"use client";

import { useCallback, useRef, type FormEvent } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import dash from "../dashboard.module.css";
import { DashLink, formFields, refusalOf, useWrite, useWriteSide } from "../parts";
import styles from "./videos.module.css";

// The two writes on one video — `POST /dashboard/videos/{id}/reindex` and
// `POST /dashboard/videos/{id}/tags` (dashboard.md §5.2, §5.3, §21).
//
// They live beside the table rather than inside either page because the
// re-index is on both: one decision that fits a 34px row, offered per row where
// there are sixty of them and again on the video's own page where there is
// room for the sentence explaining what it rebuilds. One control drawn twice is
// how two pages start disagreeing about what a refusal looks like.
//
// Both are a `fetch` rather than the Jinja pages' real forms, and for the
// reason §21 gives: the answer has to be **inline**. Neither of these pages
// polls, so the job the re-index queued and the tags the row carries after the
// write are the only evidence either control leaves. The tags outcome in
// particular is not derivable from what was asked for — `tag_video` reports
// what it added and removed across a batch, and the panel is showing the row.
//
// Whether they are drawn at all is the deployment's answer and not this
// module's: `write_side` false means no control, disabled or otherwise, because
// a button that 404s is worse than a button that is not there. `writes_allowed`
// is the database's own flag and disables instead — the same split the jobs
// pages' Cancel and Retry make.

/** Re-index this one video, forced.
 *
 *  `expand=none` and `force_reindex` are the handler's, so this cannot queue a
 *  surprise playlist off a URL that happens to be in one. The button does not
 *  come back after it has fired: the job it made is the answer, and a second
 *  POST would queue a second rebuild of the same video. */
export function ReindexControl({ videoId, label }: { videoId: string; label: string }) {
  const { indexable } = useWriteSide();
  const send = useCallback(() => dashboard.reindexVideo(videoId), [videoId]);
  const [write, run] = useWrite(send);

  if (write.status === "done") {
    return (
      <span className={styles.outcome} role="status">
        {write.outcome.job_id ? (
          <DashLink href={`${ROOT}/jobs/${encodeURIComponent(write.outcome.job_id)}`}>
            <code>{write.outcome.job_id}</code>
          </DashLink>
        ) : (
          // The tool made no job. `force_reindex` always does, so this is the
          // shape saying so rather than a page rendering `undefined` on the day
          // it stops being true.
          <span>nothing was queued</span>
        )}
      </span>
    );
  }

  if (write.status === "failed") return <Refusal error={write.error} />;

  return (
    <button
      className={styles.rowbutton}
      type="button"
      onClick={run}
      disabled={!indexable || write.status === "sending"}
      // The database's own flag, said where a reader meets it: the rail's foot
      // already prints `indexing refused` for the deployment.
      title={indexable ? undefined : "This instance's database refuses writes."}
    >
      {write.status === "sending" ? "queueing…" : label}
    </button>
  );
}

/**
 * Add and remove tags, one form.
 *
 * The namespace rules, the ten-tag cap and the `<ns>:<value>` shape are
 * `tag_video`'s, verbatim, including its error text: a tag this refuses here is
 * a tag it would refuse from the model, and the panel says so in the same
 * words. Nothing on this side validates.
 *
 * Nothing asked for is nothing done on both branches, so an empty submission is
 * accepted and answers with the row unchanged — the form's policy, not a second
 * one written for a JSON caller.
 *
 * `onWritten` hands the page the tags the row carries *after* the write, which
 * is what replaces the list rather than a diff applied on this side.
 */
export function TagsForm({
  videoId,
  tags,
  onWritten,
}: {
  videoId: string;
  tags: string[];
  onWritten: (tags: string[]) => void;
}) {
  // A ref rather than state: the fields are read by the request this submit
  // makes, not by anything that renders.
  const fields = useRef<Record<string, string>>({});
  const send = useCallback(() => dashboard.setVideoTags(videoId, fields.current), [videoId]);
  const [write, run] = useWrite(send, (outcome) => onWritten(outcome.tags));

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    fields.current = formFields(event.currentTarget);
    run();
  }

  return (
    <>
      {/* Re-keyed on the tags the row carries, so both boxes empty themselves
          when a write comes back: what was applied is on the list under them,
          and a box still holding it invites the same submission twice. */}
      <form className={styles.tagform} key={tags.join(",")} onSubmit={submit}>
        <div className={`${dash.field} ${dash.wide}`}>
          <label htmlFor="tag-add">Add</label>
          <input
            id="tag-add"
            name="add"
            type="text"
            placeholder="topic:attention"
            autoComplete="off"
          />
        </div>
        <div className={`${dash.field} ${dash.wide}`}>
          <label htmlFor="tag-remove">Remove</label>
          <input
            id="tag-remove"
            name="remove"
            type="text"
            placeholder={tags[0] ?? "series:zero-to-hero"}
            autoComplete="off"
          />
        </div>
        <div className={`${dash.field} ${dash.actions}`}>
          <button className={dash.ghostlink} type="submit" disabled={write.status === "sending"}>
            {write.status === "sending" ? "applying…" : "Apply"}
          </button>
        </div>
      </form>
      {write.status === "failed" ? (
        <p className={styles.manageNote}>
          <Refusal error={write.error} />
        </p>
      ) : null}
    </>
  );
}

/** Why a write was refused, in the API's own words — the code, the message and
 *  the `next:` line are policy text and stay Python's. */
function Refusal({ error }: { error: unknown }) {
  const refusal = refusalOf(error);
  return (
    <span className={`${styles.outcome} ${styles.outcomeBad}`} role="status">
      <code>{refusal.code}</code> <span>{refusal.message}</span>
    </span>
  );
}
