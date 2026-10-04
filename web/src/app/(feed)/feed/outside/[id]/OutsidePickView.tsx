"use client";

import { useState } from "react";
import { Title } from "@/components/dashboard/kit/ui";
import { Because, FollowLine } from "@/components/feed/OutsidePicks";
import { FeedFailure, Score } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { dashboard } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { OutsideFeedback, OutsideFeedbackStored, OutsidePick } from "@/lib/dashboard/schemas";
import { clock, day } from "@/lib/format";

// A pick from outside the follows (companion.md §6.2): judged on its captions,
// never indexed. A thumbs up offers a 14-day follow of its channel.

export function OutsidePickView({ id }: { id: number }) {
  const pick = useResource<OutsidePick>(`outside-pick:${id}`, (signal) =>
    dashboard.outsidePick(id, signal),
  );
  if (!pick.data) {
    if (pick.error) return <FeedFailure error={pick.error} onRetry={pick.reload} />;
    return <div className={styles.pending} aria-busy="true" />;
  }
  return <Loaded key={pick.data.feedback} pick={pick.data} />;
}

function Loaded({ pick }: { pick: OutsidePick }) {
  return (
    <>
      <Title>{pick.title}</Title>
      <article className={styles.video}>
        <header className={styles.videoHead}>
          <Because text={pick.because} />
          {pick.channel ? <p className={styles.channel}>{pick.channel}</p> : null}
          <h1 className={styles.videoTitle}>{pick.title}</h1>
          <p className={styles.meta}>
            {pick.score !== null ? <Score score={pick.score} /> : null}
            <span className={styles.duration}>
              {clock(pick.duration_s)}
              {pick.published_at ? ` · ${day(pick.published_at)}` : ""}
            </span>
          </p>
          {pick.reason ? <p className={styles.reason}>{pick.reason}</p> : null}
          <a className={styles.play} href={pick.url} target="_blank" rel="noopener noreferrer">
            <span aria-hidden="true">▶</span> Play from the start
          </a>
        </header>

        {pick.summary ? <p className={styles.summary}>{pick.summary}</p> : null}

        <section aria-labelledby="moments">
          <h2 className={styles.label} id="moments">
            Moments
          </h2>
          <ol className={styles.moments}>
            {pick.moments.map((moment) => (
              <li key={moment.offset_s}>
                <a
                  className={styles.moment}
                  href={moment.url}
                  target="_blank"
                  rel="noopener noreferrer"
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
          {pick.moments.length === 0 ? (
            <p className={styles.quiet}>This verdict names no moment; the summary is all it has.</p>
          ) : null}
          <p className={styles.quiet}>
            Judged on YouTube&apos;s captions only. It is not in your library.
          </p>
        </section>
      </article>
      <Actions pick={pick} />
    </>
  );
}

function Actions({ pick }: { pick: OutsidePick }) {
  const [feedback, setFeedback] = useState<OutsideFeedback>(pick.feedback);
  const [offer, setOffer] = useState<OutsideFeedbackStored["offer"]>(null);
  const [follow, setFollow] = useState(pick.follow);
  const [saving, setSaving] = useState<"sending" | "failed" | undefined>();

  function set(state: OutsideFeedback) {
    if (saving === "sending") return;
    const before = feedback;
    const target = feedback === state ? "none" : state;
    setFeedback(target);
    setSaving("sending");
    dashboard.outsideFeedback(pick.id, target).then(
      (stored) => {
        setOffer(stored.offer);
        setSaving(undefined);
      },
      () => {
        setFeedback(before);
        setSaving("failed");
      },
    );
  }

  async function trial() {
    setSaving("sending");
    try {
      const started = await dashboard.trialFollow({ pick: pick.id });
      setFollow({ state: "trial", until: started.trial_until });
      setOffer(null);
      setSaving(undefined);
    } catch {
      setSaving("failed");
    }
  }

  const busy = saving === "sending";
  return (
    <div className={styles.bar}>
      {offer ? (
        <div className={styles.offer}>
          <p className={styles.quiet}>
            Try {offer.channel ?? "this channel"} for {offer.days} days? It ends by itself unless
            you like something from it.
          </p>
          <button
            className={styles.action}
            type="button"
            disabled={busy}
            onClick={() => void trial()}
          >
            Follow for {offer.days} days
          </button>
        </div>
      ) : (
        <FollowLine follow={follow} />
      )}
      <div className={styles.barInner}>
        {(["up", "down"] as const).map((state) => (
          <button
            key={state}
            className={styles.signal}
            type="button"
            aria-label={state === "up" ? "Thumbs up" : "Thumbs down"}
            aria-pressed={feedback === state}
            aria-busy={busy}
            onClick={() => set(state)}
          >
            {state === "up" ? "▲" : "▼"}
          </button>
        ))}
      </div>
      {saving === "failed" ? (
        <p className={styles.barNote} role="status">
          Not saved. Tap again to retry.
        </p>
      ) : null}
    </div>
  );
}
