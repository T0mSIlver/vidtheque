"use client";

import { useRef, type FormEvent } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import controls from "@/components/dashboard/kit/controls.module.css";
import { notice, RefusalNotice } from "@/components/dashboard/kit/notice";
import { DashLink, ui } from "@/components/dashboard/kit/ui";
import {
  focusOnArrival,
  formFields,
  useWrite,
  useWriteSide,
} from "@/components/dashboard/kit/write";
import styles from "./videos.module.css";

// The two writes on one video, on its detail page (dashboard.md §21, §29.3).
// The page does not poll, so the inline answer is the only evidence a write
// leaves; `tag_video` and `index_video` decide everything.

/** The detail page's one rare write: re-index, the button alone (§29.3).
 *  No delete: that job kind has no pipeline (§5.2). */
export function ManagePanel({ videoId }: { videoId: string }) {
  const { rendered } = useWriteSide();
  if (!rendered) return null;
  return (
    <section className={styles.manage} id="manage" aria-label="Manage this video">
      <ReindexControl label="Re-index this video" videoId={videoId} />
    </section>
  );
}

/** Re-index one video, forced. The button does not come back: a second POST
 *  would queue a second rebuild. */
export function ReindexControl({ videoId, label }: { videoId: string; label: string }) {
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
        className={controls.ghostlink}
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
 * The video's tags as chips, each with its own remove, and one box that adds
 * (§29.3). The rules and their refusals are `tag_video`'s; `onWritten` hands
 * the page the row's tags after the write. Without a write side the chips are
 * plain links to the table filtered by the tag.
 */
export function Tags({
  videoId,
  tags,
  onWritten,
}: {
  videoId: string;
  tags: string[];
  onWritten: (tags: string[]) => void;
}) {
  const { rendered } = useWriteSide();
  const form = useRef<HTMLFormElement>(null);
  const [write, run] = useWrite(
    (fields: Record<string, string>) => dashboard.setVideoTags(videoId, fields),
    (outcome) => {
      form.current?.reset();
      onWritten(outcome.tags);
    },
  );
  const sending = write.status === "sending";

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = formFields(event.currentTarget);
    if (fields.add?.trim()) run(fields);
  }

  return (
    <div className={styles.tags} data-write="">
      <ul className={styles.taglist} aria-label="Tags">
        {tags.map((tag) => (
          <li className={styles.tag} key={tag}>
            <DashLink href={`${ROOT}/videos?tags=${encodeURIComponent(tag)}&index_state=all`}>
              {tag}
            </DashLink>
            {rendered ? (
              <button
                aria-disabled={sending || undefined}
                aria-label={`Remove ${tag}`}
                className={styles.untag}
                onClick={() => run({ remove: tag })}
                type="button"
              >
                <svg aria-hidden="true" height="10" viewBox="0 0 10 10" width="10">
                  <path d="M2 2l6 6M8 2 2 8" fill="none" stroke="currentColor" strokeWidth="1.25" />
                </svg>
              </button>
            ) : null}
          </li>
        ))}
        {rendered ? (
          <li>
            <form onSubmit={add} ref={form}>
              <label className={ui.srOnly} htmlFor="tag-add">
                Add a tag
              </label>
              <input
                autoComplete="off"
                className={styles.taginput}
                id="tag-add"
                name="add"
                placeholder="add tag"
                spellCheck={false}
                type="text"
              />
            </form>
          </li>
        ) : null}
      </ul>
      {write.status === "failed" ? <RefusalNotice error={write.error} variant="receipt" /> : null}
    </div>
  );
}
