"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { refusalOf, useWrite } from "@/components/dashboard/kit/write";
import { OutsideList } from "@/components/feed/OutsidePicks";
import { FeedFailure, Score } from "@/components/feed/parts";
import { AuditAnswer, SunkBy } from "@/components/feed/SkipFix";
import styles from "@/components/feed/feed.module.css";
import { dashboard, FEED } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Brief } from "@/lib/dashboard/schemas";
import { clock, day } from "@/lib/format";
import { signed } from "@/lib/feed/words";

// The weekly brief (companion.md §6.1): about one phone screen. The three
// picks and the check-in are open; everything longer opens on a tap.

type Change = Brief["profile_changes"][number];
type Channel = Brief["channels"][number];

export function BriefView({ week }: { week: string | null }) {
  const brief = useResource<Brief>(`brief:${week ?? "latest"}`, (signal) =>
    dashboard.brief(week, signal),
  );
  if (!brief.data) {
    if (brief.error) return <FeedFailure error={brief.error} onRetry={brief.reload} />;
    return <div className={styles.pending} aria-busy="true" />;
  }
  const b = brief.data;
  const flagged = b.channels.filter((c) => c.suggest_pause);
  return (
    <div className={styles.brief}>
      <header className={styles.briefHead}>
        <h1 className={styles.label}>Week of {day(b.since)}</h1>
        {b.previous_week ? (
          <Link className={styles.navlink} href={`${FEED}/brief?week=${b.previous_week}`}>
            Earlier
          </Link>
        ) : null}
      </header>

      <section aria-labelledby="picks">
        <h2 className={styles.label} id="picks">
          Worth your time
        </h2>
        {b.picks.length === 0 ? (
          <p className={styles.quiet}>Nothing scored worth your time this week.</p>
        ) : (
          <ol className={styles.rows}>
            {b.picks.map((pick) => (
              <li key={pick.video_id} className={styles.pick}>
                <Link
                  className={styles.title}
                  href={`${FEED}/${encodeURIComponent(pick.video_id)}`}
                >
                  {pick.title || pick.video_id}
                </Link>
                <span className={styles.meta}>
                  {pick.channel ? <span className={styles.channel}>{pick.channel}</span> : null}
                  <Score score={pick.score} />
                </span>
                {pick.moments.length ? (
                  <ul className={styles.moments}>
                    {pick.moments.map((m) => (
                      <li key={m.cue_id}>
                        <a
                          className={styles.momentLink}
                          href={m.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {clock(m.offset_s)} {m.why}
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </section>

      {b.outside ? <OutsideList data={b.outside} /> : null}

      {b.ledger ? <Ledger ledger={b.ledger} /> : null}

      {/* Keyed: another week starts from its own answer. */}
      <Checkin key={b.week} week={b.week} stored={b.checkin} />

      <details className={styles.more}>
        <summary className={styles.moreSummary}>What speakers said · {b.said.length}</summary>
        {b.said.length === 0 ? <p className={styles.quiet}>{saidNote(b.said_note)}</p> : null}
        {b.said.map((topic) => (
          <div key={topic.entry_id}>
            <h3 className={styles.label}>{topic.text}</h3>
            <ul className={styles.rows}>
              {topic.points.map((p) => (
                <li key={p.cue_id} className={styles.rowNote}>
                  <span className={styles.said}>{p.said}</span>{" "}
                  <Receipt url={p.url} label={`${p.channel || p.title} ${clock(p.offset_s)}`} />
                </li>
              ))}
              {topic.disagreement ? (
                <li className={styles.rowNote}>
                  <span className={styles.said}>Disagree: {topic.disagreement.about}</span>{" "}
                  {topic.disagreement.sides.map((side) => (
                    <Receipt
                      key={side.cue_id}
                      url={side.url}
                      label={`${side.channel || side.title} ${clock(side.offset_s)}`}
                    />
                  ))}
                </li>
              ) : null}
            </ul>
          </div>
        ))}
      </details>

      <details className={styles.more}>
        <summary className={styles.moreSummary}>
          Channels ·{" "}
          {flagged.length ? `${flagged.length} to review` : `${b.channels.length} followed`}
        </summary>
        <ul className={styles.rows}>
          {b.channels.map((channel) => (
            <ChannelRow key={channel.slug} channel={channel} />
          ))}
        </ul>
      </details>

      <details className={styles.more}>
        <summary className={styles.moreSummary}>
          Profile changes · {b.profile_changes.length}
        </summary>
        {b.profile_changes.length === 0 ? (
          <p className={styles.quiet}>The nightly update changed nothing this week.</p>
        ) : (
          <ul className={styles.rows}>
            {b.profile_changes.map((change) => (
              <ChangeRow key={change.event_id} change={change} />
            ))}
          </ul>
        )}
      </details>

      <details className={styles.more}>
        <summary className={styles.moreSummary}>Skip audit · {b.audit.length}</summary>
        <p className={styles.quiet}>Would you have watched these skipped videos?</p>
        <ul className={styles.rows}>
          {b.audit.map((video) => (
            <li key={video.video_id} className={styles.pick}>
              <Link className={styles.title} href={`${FEED}/${encodeURIComponent(video.video_id)}`}>
                {video.title || video.video_id}
              </Link>
              <span className={styles.reason}>{video.reason}</span>
              <SunkBy match={video.sunk_by} />
              <AuditAnswer videoId={video.video_id} answer={video.answer} />
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

/** The week against YouTube (§3.3), in one line. */
function Ledger({ ledger }: { ledger: NonNullable<Brief["ledger"]> }) {
  const week = ledger.weeks[0];
  if (!week) return null;
  const percent = (rate: number | null) => (rate === null ? "–" : `${Math.round(rate * 100)}%`);
  return (
    <p className={styles.quiet}>
      Against YouTube: {percent(week.hits.rate)} hit rate · {percent(week.regret.rate)} regret ·{" "}
      {week.misses.count} {week.misses.count === 1 ? "miss" : "misses"}
      {week.outside && week.outside.shown > 0
        ? ` · ${percent(week.outside.rate)} of the picks from outside kept`
        : ""}
    </p>
  );
}

function saidNote(note: string | null): string {
  return note ? `Nothing this week: ${note}.` : "Nothing this week.";
}

function Receipt({ url, label }: { url: string | null; label: string }) {
  if (!url) return <span className={styles.momentLink}>{label}</span>;
  return (
    <a className={styles.momentLink} href={url} target="_blank" rel="noopener noreferrer">
      {label}
    </a>
  );
}

function Checkin({ week, stored }: { week: string; stored: Brief["checkin"] }) {
  const [rating, setRating] = useState(stored?.rating ?? null);
  const [missing, setMissing] = useState(stored?.missing ?? "");
  const [write, run] = useWrite((next: { rating: number; missing: string }) =>
    dashboard.checkin(week, next.rating, next.missing),
  );

  function rate(value: number) {
    setRating(value);
    run({ rating: value, missing });
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (rating !== null) run({ rating, missing });
  }

  return (
    <form className={styles.paste} onSubmit={save} data-write="" aria-labelledby="checkin">
      <h2 className={styles.label} id="checkin">
        Was last week’s feed worth the time?
      </h2>
      <div className={styles.rating} role="group" aria-label="1 not at all, 5 fully">
        {[1, 2, 3, 4, 5].map((value) => (
          <button
            key={value}
            className={styles.small}
            type="button"
            aria-pressed={rating === value}
            onClick={() => rate(value)}
          >
            {value}
          </button>
        ))}
      </div>
      {rating !== null ? (
        <>
          <input
            className={styles.missing}
            aria-label="What was missing (optional)"
            placeholder="What was missing? (optional)"
            maxLength={500}
            value={missing}
            onChange={(event) => setMissing(event.target.value)}
          />
          <button className={styles.small} type="submit" aria-disabled={write.status === "sending"}>
            Save
          </button>
        </>
      ) : null}
      {write.status === "done" ? (
        <p className={styles.quiet} role="status">
          Saved.
        </p>
      ) : null}
      {write.status === "failed" ? (
        <p className={styles.refused} role="status">
          {refusalOf(write.error).message}
        </p>
      ) : null}
    </form>
  );
}

function ChannelRow({ channel }: { channel: Channel }) {
  const [write, run] = useWrite(() => dashboard.setFollowState(channel.slug, "pause"));
  const paused = channel.state === "paused" || write.status === "done";
  const share = (value: number | null) => (value === null ? "–" : `${Math.round(value * 100)}%`);
  return (
    <li className={styles.event}>
      <span className={styles.entryText}>
        {channel.title}
        <span className={styles.evidence}>
          {channel.videos} videos in 30 days · {share(channel.worth_share)} worth it ·{" "}
          {share(channel.engaged_share)} watched or liked{paused ? " · paused" : ""}
        </span>
        {write.status === "failed" ? (
          <span className={styles.refused} role="status">
            {refusalOf(write.error).message}
          </span>
        ) : null}
      </span>
      {channel.suggest_pause && !paused ? (
        <button
          className={styles.small}
          type="button"
          aria-label={`Pause ${channel.title}`}
          aria-disabled={write.status === "sending"}
          onClick={() => run()}
        >
          Pause
        </button>
      ) : null}
    </li>
  );
}

function ChangeRow({ change }: { change: Change }) {
  const [write, run] = useWrite(() => dashboard.profileRevert({ event_id: change.event_id }));
  const reverted = change.reverted || write.status === "done";
  const text = change.after?.text ?? change.before?.text ?? `entry ${change.entry_id}`;
  const weights =
    change.before?.weight !== undefined && change.after?.weight !== undefined
      ? `${signed(change.before.weight)} → ${signed(change.after.weight)}`
      : change.after?.weight !== undefined
        ? signed(change.after.weight)
        : "";
  return (
    <li className={styles.event}>
      <span className={styles.entryText}>
        <span className={styles.eventHead}>
          <span className={styles.op}>{change.op}</span>
          <span className={styles.change}>{weights}</span>
        </span>
        {text}
        {change.reason ? <span className={styles.evidence}>{change.reason}</span> : null}
        {write.status === "failed" ? (
          <span className={styles.refused} role="status">
            {refusalOf(write.error).message}
          </span>
        ) : null}
      </span>
      {reverted ? (
        <span className={styles.evidence}>reverted</span>
      ) : (
        <button
          className={styles.small}
          type="button"
          aria-label={`Revert: ${change.op} ${text}`}
          aria-disabled={write.status === "sending"}
          onClick={() => run()}
        >
          Revert
        </button>
      )}
    </li>
  );
}
