"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { FeedFailure } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { dashboard, FEED } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Week } from "@/lib/dashboard/schemas";
import { count, minutes } from "@/lib/format";
import { Row } from "./FeedView";

// What should I watch this week? The week's ranked verdicts fitted to the
// owner's minutes, then a stop: the feed ends where the budget does
// (companion.md §6). Every other video is one tap away, under "Show all".

export function WeekView() {
  const params = useSearchParams();
  const asked = params.get("week");
  const week = useResource<Week>(`week:${asked ?? "current"}`, (signal) =>
    dashboard.week(asked, signal),
  );
  if (!week.data) {
    if (week.error) return <FeedFailure error={week.error} onRetry={week.reload} />;
    return <p className={styles.pending} aria-busy="true" />;
  }
  return <Loaded week={week.data} onBudget={week.reload} />;
}

function Loaded({ week, onBudget }: { week: Week; onBudget: () => void }) {
  const current = week.next === null;
  return (
    <>
      <section aria-labelledby="week" className={styles.weekHead}>
        <div className={styles.weekNav}>
          <Link
            className={styles.action}
            href={`${FEED}?week=${week.previous}`}
            aria-label="The week before"
          >
            ‹
          </Link>
          <h1 className={styles.label} id="week">
            {current
              ? "This week"
              : `Week of ${dayName(week.week, { day: "numeric", month: "short" })}`}
          </h1>
          {week.next ? (
            <Link
              className={styles.action}
              href={`${FEED}?week=${week.next}`}
              aria-label="The week after"
            >
              ›
            </Link>
          ) : (
            <span />
          )}
        </div>
        <Budget week={week} onSaved={onBudget} />
        <Days week={week} />
      </section>

      <ol className={styles.rows} aria-label="Worth your time">
        {week.items.map((item) => (
          <Row key={item.video_id} item={item} />
        ))}
      </ol>

      <section className={styles.end} aria-label="End of the feed">
        <p className={styles.endLine}>
          {week.items.length === 0
            ? current
              ? "Nothing worth your time yet this week."
              : "Nothing was worth your time that week."
            : current
              ? "That is everything worth your time this week."
              : "That was everything worth your time that week."}
        </p>
        {week.rest.count > 0 ? (
          <p className={styles.quiet}>
            {count(week.rest.count)} more {week.rest.count === 1 ? "verdict asks" : "verdicts ask"}{" "}
            for {minutes(week.rest.asks_s)} past your budget.
          </p>
        ) : null}
        <Link className={styles.action} href={`${FEED}/all`}>
          Show all videos
        </Link>
      </section>
    </>
  );
}

/** The budget is weekly; it reads, and is set, as minutes a day. */
function Budget({ week, onSaved }: { week: Week; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [daily, setDaily] = useState(String(Math.round(week.budget_min / 7)));
  const [failed, setFailed] = useState(false);
  const save = async () => {
    const perDay = Number(daily);
    if (!Number.isInteger(perDay) || perDay < 0 || perDay > 1440) {
      setFailed(true);
      return;
    }
    try {
      await dashboard.budget(perDay * 7);
      setFailed(false);
      setEditing(false);
      onSaved();
    } catch {
      setFailed(true);
    }
  };
  return (
    <div className={styles.budget}>
      <p>
        <b className={styles.budgetFigure}>{minutes(week.asks_s)}</b> of{" "}
        {minutes(week.budget_min * 60)}
        <span className={styles.quiet}> · {Math.round(week.budget_min / 7)} min a day</span>
      </p>
      {editing ? (
        <form
          className={styles.budgetForm}
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <label>
            Minutes a day{" "}
            <input
              className={styles.budgetInput}
              type="number"
              inputMode="numeric"
              min={0}
              max={1440}
              value={daily}
              onChange={(event) => setDaily(event.target.value)}
            />
          </label>
          <button className={styles.action} type="submit">
            Save
          </button>
          {failed ? (
            <span className={styles.quiet}>Not saved. A whole number, 0 to 1,440.</span>
          ) : null}
        </form>
      ) : (
        <button className={styles.action} type="button" onClick={() => setEditing(true)}>
          Change
        </button>
      )}
    </div>
  );
}

/** What each day of the week asks: uploads come in bursts. */
function Days({ week }: { week: Week }) {
  const daily = (week.budget_min * 60) / 7;
  const most = Math.max(daily, ...week.days.map((d) => d.asks_s), 1);
  return (
    <ol className={styles.days} aria-label="What each day asks">
      {week.days.map((d) => (
        <li
          key={d.day}
          className={styles.day}
          aria-label={`${dayName(d.day, { weekday: "long" })}: ${minutes(d.asks_s)}, ${d.fitted} of ${d.candidates} worth your time`}
        >
          <span className={styles.dayBar}>
            <span className={styles.dayFill} style={{ height: `${(d.asks_s / most) * 100}%` }} />
            <span className={styles.dayLine} style={{ bottom: `${(daily / most) * 100}%` }} />
          </span>
          <span className={styles.dayName} aria-hidden="true">
            {dayName(d.day, { weekday: "narrow" })}
          </span>
          <span className={styles.dayMinutes} aria-hidden="true">
            {d.asks_s > 0 ? Math.round(d.asks_s / 60) : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

function dayName(day: string, options: Intl.DateTimeFormatOptions): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString("en-GB", options);
}
