"use client";

import { useEffect, useRef, useState } from "react";
import { FeedFailure, Outside, Score } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { Title } from "@/components/dashboard/kit/ui";
import { dashboard } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Moment, SignalKind, Verdict } from "@/lib/dashboard/schemas";
import { clock, day } from "@/lib/format";
import { claudeUrl, videoPrompt } from "@/lib/feed/words";

// One verdict: the summary, the moments as receipts, and what you thought of
// it. Every tap here is a signal the nightly update reads (companion.md §2.3).

export function VideoView({ videoId }: { videoId: string }) {
  const verdict = useResource<Verdict>(`verdict:${videoId}`, (signal) =>
    dashboard.verdict(videoId, signal),
  );
  const loaded = verdict.data !== undefined;

  // `open` once per visit, when the verdict is on screen.
  const opened = useRef<string | null>(null);
  useEffect(() => {
    if (!loaded || opened.current === videoId) return;
    opened.current = videoId;
    void dashboard.signal("open", videoId).catch(() => {});
  }, [loaded, videoId]);

  if (!verdict.data) {
    if (verdict.error) return <FeedFailure error={verdict.error} onRetry={verdict.reload} />;
    return <div className={styles.pending} aria-busy="true" />;
  }
  return <Loaded verdict={verdict.data} />;
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
            <Score score={verdict.score} />
            {verdict.explored ? <Outside /> : null}
            <span className={styles.duration}>
              {clock(video.duration_s)}
              {video.published_at ? ` · ${day(video.published_at)}` : ""}
            </span>
          </p>
          {verdict.reason ? <p className={styles.reason}>{verdict.reason}</p> : null}
        </header>

        <p className={styles.summary}>{verdict.summary}</p>

        <section aria-labelledby="moments">
          <h2 className={styles.label} id="moments">
            Moments
          </h2>
          <Moments videoId={id} moments={verdict.moments} dropped={verdict.moments_dropped} />
        </section>
      </article>

      <Actions verdict={verdict} />
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
              <span className={styles.timecode}>{clock(moment.offset_s)}</span>
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

type Sent = Partial<Record<SignalKind, "sending" | "done" | "failed">>;

/** Thumbs, mute and Ask Claude, in reach of a thumb at the bottom. */
function Actions({ verdict }: { verdict: Verdict }) {
  const id = verdict.video.video_id;
  const [sent, setSent] = useState<Sent>({});
  const [copied, setCopied] = useState(false);
  const prompt = videoPrompt(verdict.video);

  function send(kind: SignalKind) {
    if (sent[kind] === "sending" || sent[kind] === "done") return;
    setSent((s) => ({ ...s, [kind]: "sending" }));
    dashboard.signal(kind, id).then(
      () => setSent((s) => ({ ...s, [kind]: "done" })),
      () => setSent((s) => ({ ...s, [kind]: "failed" })),
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

  const failed = Object.values(sent).includes("failed");
  return (
    <div className={styles.bar}>
      <div className={styles.barInner}>
        <Signal kind="thumb_up" label="Thumbs up" glyph="▲" state={sent.thumb_up} onSend={send} />
        <Signal
          kind="thumb_down"
          label="Thumbs down"
          glyph="▼"
          state={sent.thumb_down}
          onSend={send}
        />
        <Signal kind="mute" label="Mute" glyph="Mute" state={sent.mute} onSend={send} />
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
      {failed ? (
        <p className={styles.barNote} role="status">
          Not recorded. Tap again to retry.
        </p>
      ) : null}
    </div>
  );
}

function Signal({
  kind,
  label,
  glyph,
  state,
  onSend,
}: {
  kind: SignalKind;
  label: string;
  glyph: string;
  state: Sent[SignalKind];
  onSend: (kind: SignalKind) => void;
}) {
  return (
    <button
      className={styles.signal}
      type="button"
      aria-label={label}
      aria-pressed={state === "done"}
      aria-busy={state === "sending"}
      data-failed={state === "failed" || undefined}
      onClick={() => onSend(kind)}
    >
      {glyph}
    </button>
  );
}
