"use client";

import { dashboard } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { ValuedTime, ValuedWeek } from "@/lib/dashboard/schemas";
import { count, day, DASH } from "@/lib/format";
import { table } from "@/components/dashboard/kit/table";
import { Panel, ui } from "@/components/dashboard/kit/ui";

// The feed against YouTube, one row a week (companion.md §3.3). Opens alone
// never count as a hit; the server decides what does.

const read = (signal: AbortSignal) => dashboard.valuedTime(signal);

export function ValuedTimePanel() {
  const ledger = useResource("valued-time", read);
  // No write side answers 404: no feed, nothing to measure.
  if (!ledger.data) return null;
  return <Loaded data={ledger.data} />;
}

const percent = (rate: number | null) => (rate === null ? DASH : `${Math.round(rate * 100)}%`);

// Noon on the box's Monday, so the UTC date printed is that Monday's.
const weekOf = (week: ValuedWeek) =>
  week.current ? "this week" : `from ${day(week.start + 43_200)}`;

function Loaded({ data }: { data: ValuedTime }) {
  return (
    <Panel
      id="valued-time"
      title="Against YouTube"
      aside={<span className={ui.note}>regret target under {percent(data.regret_target)}</span>}
    >
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
            </tr>
          </thead>
          <tbody>
            {data.weeks.map((week) => (
              <tr key={week.start}>
                <th scope="row">{weekOf(week)}</th>
                <td className={table.num}>{percent(week.hits.rate)}</td>
                <td className={table.num}>
                  {count(week.hits.kept)} / {count(week.hits.offered)}
                  {week.hits.capped ? "+" : ""}
                </td>
                <td
                  className={`${table.num} ${(week.regret.rate ?? 0) > data.regret_target ? ui.warn : ""}`}
                >
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
