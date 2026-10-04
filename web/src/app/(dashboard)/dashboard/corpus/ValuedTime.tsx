"use client";

import { dashboard } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { ValuedTime, ValuedWeek } from "@/lib/dashboard/schemas";
import { count, day, DASH } from "@/lib/format";
import { Fold } from "@/components/dashboard/kit/Fold";
import { table } from "@/components/dashboard/kit/table";
import { Panel, ui } from "@/components/dashboard/kit/ui";
import styles from "./corpus.module.css";

// The feed against YouTube (companion.md §3.3): this week in a line, the
// weeks before on demand. Opens alone never count as a hit; the server
// decides what does.

const read = (signal: AbortSignal) => dashboard.valuedTime(signal);

export function ValuedTimePanel() {
  const ledger = useResource("valued-time", read);
  // No write side answers 404: no feed, nothing to measure.
  if (!ledger.data) return null;
  return <Loaded data={ledger.data} />;
}

const percent = (rate: number | null) => (rate === null ? DASH : `${Math.round(rate * 100)}%`);

// Noon on the box's Monday, so the UTC date printed is that Monday's.
const weekOf = (week: ValuedWeek) => `from ${day(week.start + 43_200)}`;

function Loaded({ data }: { data: ValuedTime }) {
  const current = data.weeks.find((week) => week.current);
  const earlier = data.weeks.filter((week) => !week.current);
  return (
    <Panel
      id="valued-time"
      title="Against YouTube"
      aside={
        <span className={ui.panelAside}>regret target under {percent(data.regret_target)}</span>
      }
    >
      {current ? <ThisWeek week={current} target={data.regret_target} /> : null}
      {earlier.length ? (
        <Fold
          label={earlier.length === 1 ? "the week before" : `the ${earlier.length} weeks before`}
        >
          <Weeks weeks={earlier} target={data.regret_target} />
        </Fold>
      ) : null}
    </Panel>
  );
}

/** This week as a sentence: a figure with nothing under it is left out. */
function ThisWeek({ week, target }: { week: ValuedWeek; target: number }) {
  if (!week.hits.offered && !week.regret.watched && !week.misses.count) {
    return <p className={styles.weekLine}>Nothing offered yet this week.</p>;
  }
  return (
    <ul className={styles.weekLine}>
      <li>
        <span className={styles.figure}>{percent(week.hits.rate)}</span> hit rate,{" "}
        {count(week.hits.kept)} of {count(week.hits.offered)}
        {week.hits.capped ? "+" : ""} kept
      </li>
      {week.regret.watched ? (
        <li>
          <span className={`${styles.figure} ${(week.regret.rate ?? 0) > target ? ui.warn : ""}`}>
            {percent(week.regret.rate)}
          </span>{" "}
          regret, {count(week.regret.down)} of {count(week.regret.watched)} watched
        </li>
      ) : null}
      {week.misses.count ? (
        <li>
          <span className={styles.figure}>{count(week.misses.count)}</span>{" "}
          {week.misses.count === 1 ? "miss" : "misses"}
          {week.misses.pending ? `, ${count(week.misses.pending)} not judged` : ""}
        </li>
      ) : null}
      {week.outside?.shown ? (
        <li>
          <span className={styles.figure}>
            {count(week.outside.kept)} of {count(week.outside.shown)}
          </span>{" "}
          kept from outside your follows
        </li>
      ) : null}
    </ul>
  );
}

function Weeks({ weeks, target }: { weeks: ValuedWeek[]; target: number }) {
  return (
    <div className={table.tablewrap}>
      <table className={table.grid}>
        <thead>
          <tr>
            <th scope="col">week</th>
            <th scope="col" className={table.num}>
              hit rate
            </th>
            <th scope="col" className={table.num}>
              kept / scored 2–3
            </th>
            <th scope="col" className={table.num}>
              regret
            </th>
            <th scope="col" className={table.num}>
              down / watched
            </th>
            <th scope="col" className={table.num}>
              misses
            </th>
            <th scope="col" className={table.num}>
              kept / shown from outside
            </th>
          </tr>
        </thead>
        <tbody>
          {weeks.map((week) => (
            <tr key={week.start}>
              <th scope="row">{weekOf(week)}</th>
              <td className={table.num}>{percent(week.hits.rate)}</td>
              <td className={table.num}>
                {count(week.hits.kept)} / {count(week.hits.offered)}
                {week.hits.capped ? "+" : ""}
              </td>
              <td className={`${table.num} ${(week.regret.rate ?? 0) > target ? ui.warn : ""}`}>
                {percent(week.regret.rate)}
              </td>
              <td className={table.num}>
                {count(week.regret.down)} / {count(week.regret.watched)}
                {week.regret.capped ? "+" : ""}
              </td>
              <td className={table.num}>
                {count(week.misses.count)}
                {week.misses.pending ? ` (+${count(week.misses.pending)} not judged)` : ""}
              </td>
              <td className={table.num}>
                {week.outside && week.outside.shown > 0
                  ? `${count(week.outside.kept)} / ${count(week.outside.shown)}`
                  : DASH}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
