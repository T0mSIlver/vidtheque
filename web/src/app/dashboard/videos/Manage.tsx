"use client";

import { useRef, type FormEvent } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import controls from "../kit/controls.module.css";
import { notice, RefusalNotice } from "../kit/notice";
import { DashLink } from "../kit/ui";
import { focusOnArrival, formFields, useWrite, useWriteSide } from "../kit/write";
import styles from "./videos.module.css";

// The two writes on one video, shared by the table and the detail page
// (dashboard.md §21). Neither page polls, so the inline answer is the only
// evidence a write leaves; `tag_video` and `index_video` decide everything.

/** Re-index one video, forced. The button does not come back: a second POST
 *  would queue a second rebuild. */
export function ReindexControl({
  videoId,
  label,
  primary,
}: {
  videoId: string;
  label: string;
  /** The detail page's own action, at the chassis height. */
  primary?: boolean;
}) {
  const { indexable } = useWriteSide();
  const [write, run] = useWrite(() => dashboard.reindexVideo(videoId));
  const sending = write.status === "sending";

  if (write.status === "done") {
    const jobId = write.outcome.job_id;
    return (
      <span data-write="">
        <span className={notice.outcome} role="status" tabIndex={-1} ref={focusOnArrival}>
          {jobId ? (
            <DashLink href={`${ROOT}/jobs/${encodeURIComponent(jobId)}`}>
              <code>{jobId}</code>
            </DashLink>
          ) : (
            <span>nothing was queued</span>
          )}
        </span>
      </span>
    );
  }

  if (write.status === "failed") {
    return (
      <span data-write="">
        <RefusalNotice error={write.error} variant="inline" />
      </span>
    );
  }

  return (
    <span data-write="">
      <button
        className={primary ? controls.button : notice.rowbutton}
        type="button"
        onClick={() => run()}
        disabled={!indexable}
        aria-disabled={sending || undefined}
        title={indexable ? undefined : "This instance's database refuses writes."}
      >
        {sending ? "queueing…" : label}
      </button>
    </span>
  );
}

/**
 * Add and remove tags in one form. The rules and their refusals are
 * `tag_video`'s; `onWritten` hands the page the row's tags after the write.
 * The boxes are emptied in place on success, so focus stays in the form.
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
  const form = useRef<HTMLFormElement>(null);
  const [write, run] = useWrite(
    (fields: Record<string, string>) => dashboard.setVideoTags(videoId, fields),
    (outcome) => {
      form.current?.reset();
      onWritten(outcome.tags);
    },
  );
  const sending = write.status === "sending";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    run(formFields(event.currentTarget));
  }

  return (
    <div data-write="">
      <form className={styles.tagform} onSubmit={submit} ref={form}>
        <div className={`${controls.field} ${controls.wide}`}>
          <label htmlFor="tag-add">Add</label>
          <input
            id="tag-add"
            name="add"
            type="text"
            placeholder="topic:attention"
            autoComplete="off"
          />
        </div>
        <div className={`${controls.field} ${controls.wide}`}>
          <label htmlFor="tag-remove">Remove</label>
          <input
            id="tag-remove"
            name="remove"
            type="text"
            placeholder={tags[0] ?? "series:zero-to-hero"}
            autoComplete="off"
          />
        </div>
        <div className={`${controls.field} ${controls.actions}`}>
          <button className={controls.button} type="submit" aria-disabled={sending || undefined}>
            {sending ? "applying…" : "Apply"}
          </button>
        </div>
      </form>
      {write.status === "failed" ? <RefusalNotice error={write.error} variant="receipt" /> : null}
    </div>
  );
}
