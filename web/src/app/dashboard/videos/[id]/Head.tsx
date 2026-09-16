"use client";

import { Pill } from "@/components/ui/Pill";
import { ROOT } from "@/lib/dashboard/client";
import type { VideoDetail } from "@/lib/dashboard/schemas";
import { at, day, hms } from "@/lib/format";
import { Notice } from "../../kit/notice";
import { Notes } from "../../kit/table";
import { DashLink, Fact, PageHead, Sep, StatePair, ui, Unbroken } from "../../kit/ui";
import { useWriteSide } from "../../kit/write";
import styles from "./detail.module.css";

/** The video's name, states and source facts, and what the pipeline said
 *  about it: notes, the summary's refusal, and any stage that failed. */
export function Head({ data, tags }: { data: VideoDetail; tags: string[] }) {
  const { video } = data;
  const failed = data.stages.filter((stage) => stage.state === "failed");
  return (
    <>
      {/* Separators glue to the fact before them, with a real space after, so
          a strip keeps its break opportunities. */}
      <PageHead title={video.title}>
        <Unbroken>
          <StatePair label="index_state" word={video.index_state} />
        </Unbroken>{" "}
        {/* Both words, named, only when they differ (§4.5). */}
        {data.data_status && data.data_status !== video.index_state ? (
          <Unbroken>
            <StatePair label="data_status" word={data.data_status} />
          </Unbroken>
        ) : null}
      </PageHead>

      <p className={styles.facts}>
        {video.channel}
        <Sep /> <Fact label="published" value={day(video.published_at)} />
        <Sep /> {/* `h:mm:ss`: every other clock on the page is an offset into it. */}
        <Unbroken>
          <span className={ui.mono}>{hms(video.duration_s)}</span>
        </Unbroken>
        {video.language ? (
          <>
            <Sep />{" "}
            <Unbroken>
              <span className={ui.mono}>{video.language}</span>
            </Unbroken>
          </>
        ) : null}
        <Sep />{" "}
        {video.indexed_at ? (
          <Fact label="indexed" value={at(video.indexed_at)} />
        ) : (
          <Unbroken>never finished indexing</Unbroken>
        )}
      </p>

      <p className={styles.facts}>
        <a href={video.url} rel="noopener noreferrer" target="_blank">
          Open on YouTube
        </a>
        {tags.length ? (
          <>
            <Sep />{" "}
            <span className={styles.taglist}>
              {tags.map((tag) => (
                <DashLink
                  key={tag}
                  className={ui.chip}
                  href={`${ROOT}/videos?tags=${encodeURIComponent(tag)}&index_state=all`}
                >
                  {tag}
                </DashLink>
              ))}
            </span>
          </>
        ) : null}
        <QueueChannel url={video.url} />
      </p>

      <Notes notes={data.notes} />

      {/* `video-summary`'s refusal: a fact about the video, not a failed read. */}
      {data.summary_error ? (
        <Notice
          id="summary-refused"
          title={data.summary_error.message}
          detail={<code>{data.summary_error.code}</code>}
          next={data.summary_error.next}
        />
      ) : null}

      {failed.length ? (
        <p className={styles.alarm}>
          <Pill state="failed" />{" "}
          {failed.map((stage, index) => (
            <span key={stage.stage}>
              <code>{stage.stage}</code>
              {index < failed.length - 1 ? ", " : ""}
            </span>
          ))}{" "}
          did not finish. <a href="#provenance">Provenance</a>.
        </p>
      ) : null}
    </>
  );
}

/** A `GET` prefill into the index form, never a write. */
function QueueChannel({ url }: { url: string }) {
  const { rendered } = useWriteSide();
  if (!rendered) return null;
  const query = new URLSearchParams({ urls: url, expand: "channel_recent" });
  return (
    <>
      <Sep /> <DashLink href={`${ROOT}/index?${query}`}>Queue more from this channel</DashLink>
    </>
  );
}
