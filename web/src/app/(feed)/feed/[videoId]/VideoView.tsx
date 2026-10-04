"use client";

import { useEffect, useRef, useState } from "react";
import { FeedFailure, Matches, Outside, Score } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { Title } from "@/components/dashboard/kit/ui";
import { z } from "zod";
import { dashboard, DashboardError, echoOf } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import { FeedVideo, type FeedbackState, type Moment, type Verdict } from "@/lib/dashboard/schemas";
import { asked, clock, day } from "@/lib/format";
import { claudeUrl, videoPrompt } from "@/lib/feed/words";

// One verdict: the summary, the moments as receipts, and what you thought of
// it. Every tap here is a signal the nightly update reads (companion.md §2.3).

export function VideoView({ videoId }: { videoId: string }) {
  const verdict = useResource<Verdict>(`verdict:${videoId}`, (signal) =>
    dashboard.verdict(videoId, signal),
  );
  // Search opens videos no verdict has scored yet; the refusal names the video (§25.3).
  const unjudged =
    verdict.error instanceof DashboardError && verdict.error.code === "E_NO_VERDICT"
      ? (echoOf(verdict.error, NoVerdict)?.video ?? null)
      : null;
  const loaded = verdict.data !== undefined || unjudged !== null;

  // `open` once per visit, when the verdict is on screen.
  const opened = useRef<string | null>(null);
  useEffect(() => {
    if (!loaded || opened.current === videoId) return;
    opened.current = videoId;
    void dashboard.signal("open", videoId).catch(() => {});
  }, [loaded, videoId]);

  if (unjudged) return <Unjudged video={unjudged} />;
  if (!verdict.data) {
    if (verdict.error) return <FeedFailure error={verdict.error} onRetry={verdict.reload} />;
    return <div className={styles.pending} aria-busy="true" />;
  }
  return <Loaded verdict={verdict.data} />;
}

const NoVerdict = z.object({ video: FeedVideo });

function Unjudged({ video }: { video: FeedVideo }) {
  const id = video.video_id;
  return (
    <>
      <Title>{video.title || id}</Title>
      <article className={styles.video}>
        <header className={styles.videoHead}>
          {video.channel ? <p className={styles.channel}>{video.channel}</p> : null}
          <h1 className={styles.videoTitle}>{video.title || id}</h1>
          <p className={styles.meta}>
            <span className={styles.duration}>
              {clock(video.duration_s)}
              {video.published_at ? ` · ${day(video.published_at)}` : ""}
            </span>
          </p>
          <a
            className={styles.play}
            href={`https://youtu.be/${encodeURIComponent(id)}`}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => void dashboard.signal("watch", id, 0).catch(() => {})}
          >
            <span aria-hidden="true">▶</span> Play from the start
          </a>
        </header>
        <p className={styles.quiet}>
          No verdict yet: this video has not been scored against your profile.
        </p>
      </article>
    </>
  );
}

function Loaded({ verdict }: { verdict: Verdict }) {
  const { video } = verdict;
  const id = video.video_id;
  return (
    <>
      <Title>{video.title || id}</Title>
      <article className={styles.video}>
        <header className={styles.videoHead}>
          {video.channel ? <p className={styles.channel}>{video.channel}</p> : null}
          <h1 className={styles.videoTitle}>{video.title || id}</h1>
          <p className={styles.meta}>
            <Score score={verdict.tier ?? verdict.score} />
            {verdict.explored ? <Outside /> : null}
            <span className={styles.duration}>
              {asked(verdict.moments_s, video.duration_s)}
              {video.published_at ? ` · ${day(video.published_at)}` : ""}
            </span>
          </p>
          {verdict.reason ? <p className={styles.reason}>{verdict.reason}</p> : null}
          <Matches matches={verdict.matches} />
          {/* From the start, like a moment at 0: the same link shape and the same signal. */}
          <a
            className={styles.play}
            href={`https://youtu.be/${encodeURIComponent(id)}`}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => void dashboard.signal("watch", id, 0).catch(() => {})}
          >
            <span aria-hidden="true">▶</span> Play from the start
          </a>
        </header>

        <p className={styles.summary}>{verdict.summary}</p>

        <section aria-labelledby="moments">
          <h2 className={styles.label} id="moments">
            Moments
          </h2>
          <Moments videoId={id} moments={verdict.moments} dropped={verdict.moments_dropped} />
        </section>
      </article>

      {/* Keyed on the stored state, so a fresher verdict (another device, another tab)
          resets the buttons instead of a toggle flipping the wrong way. */}
      <Actions key={verdict.feedback} verdict={verdict} />
    </>
  );
}

function Moments({
  videoId,
  moments,
  dropped,
}: {
  videoId: string;
  moments: Moment[];
  dropped: number;
}) {
  return (
    <>
      <ol className={styles.moments}>
        {moments.map((moment) => (
          <li key={moment.cue_id}>
            <a
              className={styles.moment}
              href={moment.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() =>
                void dashboard.signal("watch", videoId, moment.offset_s).catch(() => {})
              }
            >
              <span className={styles.timecode}>
                {clock(moment.offset_s)}
                {moment.end_s != null ? `–${clock(moment.end_s)}` : ""}
              </span>
              <span className={styles.why}>{moment.why}</span>
              <span className={styles.arrow} aria-hidden="true">
                ↗
              </span>
            </a>
          </li>
        ))}
      </ol>
      {moments.length === 0 && dropped === 0 ? (
        <p className={styles.quiet}>This verdict names no moment; the summary is all it has.</p>
      ) : null}
      {dropped > 0 ? (
        <p className={styles.quiet}>
          {dropped === 1 ? "1 moment is" : `${dropped} moments are`} left out: a reindex removed the
          transcript line it cited, and the verdict will be rewritten.
        </p>
      ) : null}
    </>
  );
}

/**
 * Thumbs, "less like this" and Ask Claude, in reach of a thumb at the bottom.
 * The thumbs and mute are one stored state (companion.md §2.3): a tap sets it,
 * a tap on the one already set takes it back, and a refusal puts it back.
 */
function Actions({ verdict }: { verdict: Verdict }) {
  const id = verdict.video.video_id;
  const [feedback, setFeedback] = useState<FeedbackState>(verdict.feedback);
  const [saving, setSaving] = useState<"sending" | "failed" | undefined>();
  const [copied, setCopied] = useState(false);
  const prompt = videoPrompt(verdict.video);

  function set(state: FeedbackState) {
    if (saving === "sending") return;
    const before = feedback;
    const target = feedback === state ? "none" : state;
    setFeedback(target);
    setSaving("sending");
    dashboard.feedback(id, target).then(
      () => setSaving(undefined),
      () => {
        setFeedback(before);
        setSaving("failed");
      },
    );
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const busy = saving === "sending";
  return (
    <div className={styles.bar}>
      <div className={styles.barInner}>
        <Signal state="up" label="Thumbs up" glyph="▲" now={feedback} busy={busy} onSet={set} />
        <Signal state="down" label="Thumbs down" glyph="▼" now={feedback} busy={busy} onSet={set} />
        <Signal
          state="muted"
          label="Less like this"
          glyph="Less"
          now={feedback}
          busy={busy}
          onSet={set}
        />
        <button
          className={styles.copy}
          type="button"
          aria-label="Copy the Ask Claude prompt"
          onClick={copy}
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <a
          className={styles.ask}
          href={claudeUrl(prompt)}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => void dashboard.signal("ask_claude", id).catch(() => {})}
        >
          Ask Claude
        </a>
      </div>
      {saving === "failed" ? (
        <p className={styles.barNote} role="status">
          Not saved. Tap again to retry.
        </p>
      ) : null}
    </div>
  );
}

function Signal({
  state,
  label,
  glyph,
  now,
  busy,
  onSet,
}: {
  state: FeedbackState;
  label: string;
  glyph: string;
  now: FeedbackState;
  busy: boolean;
  onSet: (state: FeedbackState) => void;
}) {
  return (
    <button
      className={styles.signal}
      type="button"
      aria-label={label}
      aria-pressed={now === state}
      aria-busy={busy}
      onClick={() => onSet(state)}
    >
      {glyph}
    </button>
  );
}
