"use client";

import Link from "next/link";
import { useState } from "react";
import { Score } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { dashboard, FEED } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { OutsideFollow, OutsidePick, OutsideWeek, Speaker } from "@/lib/dashboard/schemas";
import { asked, clock, day } from "@/lib/format";

// Discovery outside the follows (companion.md §6.2): a small labelled dose,
// apart from the fitted week and outside its budget.

/** The week's band, read on its own so the fitted week never waits for it. */
export function OutsideBand({ week }: { week: string }) {
  const outside = useResource<OutsideWeek>(`outside:${week}`, (signal) =>
    dashboard.outside(week, signal),
  );
  if (!outside.data) return null;
  return <OutsideList data={outside.data} />;
}

export function OutsideList({ data }: { data: OutsideWeek }) {
  if (data.picks.length === 0 && data.speaker === null) return null;
  return (
    <section aria-labelledby="outside" className={styles.outsideBand}>
      <h2 className={styles.label} id="outside">
        From outside your follows
      </h2>
      <ol className={styles.rows}>
        {data.picks.map((pick) => (
          <PickRow key={pick.id} pick={pick} />
        ))}
      </ol>
      {data.speaker ? <SpeakerCard speaker={data.speaker} /> : null}
    </section>
  );
}

export function Because({ text }: { text: string }) {
  return <span className={styles.because}>from outside, because of: {text}</span>;
}

function PickRow({ pick }: { pick: OutsidePick }) {
  const momentsS = pick.moments.reduce(
    (sum, m) => sum + Math.max(0, (m.end_s ?? m.offset_s) - m.offset_s),
    0,
  );
  return (
    <li>
      <Link className={styles.row} href={`${FEED}/outside/${pick.id}`}>
        <Because text={pick.because} />
        {pick.channel ? <span className={styles.channel}>{pick.channel}</span> : null}
        <span className={styles.title}>{pick.title}</span>
        <span className={styles.meta}>
          {pick.score !== null ? <Score score={pick.score} /> : null}
          <span className={styles.duration}>
            {momentsS > 0 ? asked(momentsS, pick.duration_s) : clock(pick.duration_s)}
          </span>
        </span>
        {pick.reason ? <span className={styles.reason}>{pick.reason}</span> : null}
      </Link>
    </li>
  );
}

/** "Following until …", "Followed", or the trial that ended. */
export function FollowLine({ follow }: { follow: OutsideFollow }) {
  if (follow.state === "trial" && follow.until !== null)
    return <p className={styles.quiet}>On a 14-day follow until {day(follow.until)}.</p>;
  if (follow.state === "lasting") return <p className={styles.quiet}>You follow this channel.</p>;
  if (follow.state === "ended")
    return <p className={styles.quiet}>The 14-day follow ended: nothing from it was liked.</p>;
  return null;
}

function SpeakerCard({ speaker }: { speaker: Speaker }) {
  const [follow, setFollow] = useState(speaker.follow);
  const [state, setState] = useState<"idle" | "sending" | "failed" | "dismissed">("idle");
  if (state === "dismissed") return null;

  async function trial() {
    setState("sending");
    try {
      const started = await dashboard.trialFollow({ speaker: speaker.id });
      setFollow({ state: "trial", until: started.trial_until });
      setState("idle");
    } catch {
      setState("failed");
    }
  }
  async function dismiss() {
    setState("sending");
    try {
      await dashboard.dismissSpeaker(speaker.id);
      setState("dismissed");
    } catch {
      setState("failed");
    }
  }

  return (
    <article className={styles.speaker} aria-label={`Speaker suggestion: ${speaker.name}`}>
      <span className={styles.because}>a speaker from a talk you liked</span>
      <h3 className={styles.title}>{speaker.name}</h3>
      <p className={styles.reason}>{speaker.reason}</p>
      {speaker.talks.length > 0 ? (
        <ul className={styles.speakerTalks}>
          {speaker.talks.map((talk) => (
            <li key={talk.video_id}>
              <a href={talk.url} target="_blank" rel="noopener noreferrer">
                {talk.title}
              </a>
              {talk.channel ? <span className={styles.quiet}> · {talk.channel}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      <FollowLine follow={follow} />
      <div className={styles.speakerActions}>
        {speaker.channel && follow.state === "none" ? (
          <button
            className={styles.action}
            type="button"
            disabled={state === "sending"}
            onClick={() => void trial()}
          >
            Follow {speaker.channel.name ?? "their channel"} for 14 days
          </button>
        ) : null}
        <button
          className={styles.action}
          type="button"
          disabled={state === "sending"}
          onClick={() => void dismiss()}
        >
          Not interested
        </button>
      </div>
      {state === "failed" ? (
        <p className={styles.quiet} role="status">
          Not saved. Try again.
        </p>
      ) : null}
    </article>
  );
}
