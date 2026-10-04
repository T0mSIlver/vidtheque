"use client";

import { dashboard } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Costs } from "@/lib/dashboard/schemas";
import { count, DASH, usd } from "@/lib/format";
import { Fold } from "@/components/dashboard/kit/Fold";
import { table } from "@/components/dashboard/kit/table";
import { Panel, ui } from "@/components/dashboard/kit/ui";
import styles from "./health.module.css";

// What the model calls cost (dashboard.md §25.8, §28.2), at list price: on a
// monthly plan it is what they would cost pay-as-you-go (companion.md §4.1).
// One line on Health; the purposes on demand.

const read = (signal: AbortSignal) => dashboard.costs(signal);

// `llm_calls.purpose` (companion.md §4.1) in words.
const PURPOSES: Record<string, string> = {
  verdict: "Verdicts",
  verdict_explore: "Exploration rescores",
  scout_verdict: "Scout verdicts",
  week_rank: "Week ranking",
  weekly_brief: "Weekly brief",
  nightly_update: "Nightly update",
  github_projects: "GitHub projects",
  speaker_names: "Speaker names",
  unknown: "Unnamed caller",
};

const purpose = (key: string) => PURPOSES[key] ?? key.replaceAll("_", " ");

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const tokens = (n: number | null) => (n === null ? DASH : compact.format(n));

export function CostsPanel() {
  const costs = useResource("costs", read);
  const data = costs.data;
  // No write side answers 404, and a box that never called a model has
  // nothing to price: neither is a panel.
  if (!data || data.windows["30d"].calls === 0) return null;
  return <Loaded data={data} />;
}

function Loaded({ data }: { data: Costs }) {
  const { windows } = data;
  const unpriced = windows["30d"].unpriced_calls;
  return (
    <Panel
      id="costs"
      title="Model cost"
      aside={<span className={ui.panelAside}>at list price</span>}
    >
      <ul className={styles.costLine}>
        <li>
          <span className={styles.money}>{usd(windows.today.cost_micro_usd)}</span> today
        </li>
        <li>
          <span className={styles.money}>{usd(windows.month.cost_micro_usd)}</span> this month
        </li>
        <li>
          <span className={styles.money}>{usd(windows["30d"].cost_micro_usd)}</span> in 30 days
        </li>
        {windows["30d"].verdicts ? (
          <li>
            <span className={styles.money}>{usd(windows["30d"].per_verdict_micro_usd)}</span> a
            verdict
          </li>
        ) : null}
      </ul>
      {unpriced ? (
        <p className={`${styles.costNote} ${ui.warn}`}>
          {count(unpriced)} {unpriced === 1 ? "call" : "calls"} in 30 days with no known price, left
          out of these sums.
        </p>
      ) : null}
      <Fold label="the breakdown">
        <div className={table.tablewrap}>
          <table className={table.grid}>
            <caption className={ui.srOnly}>Cost by purpose over 30 days</caption>
            <thead>
              <tr>
                <th scope="col">purpose, 30 days</th>
                <th scope="col" className={table.num}>
                  calls
                </th>
                <th scope="col" className={table.num}>
                  tokens in / out
                </th>
                <th scope="col" className={table.num}>
                  cost
                </th>
              </tr>
            </thead>
            <tbody>
              {data.by_purpose.items.map((row) => (
                <tr key={row.purpose}>
                  <th scope="row">{purpose(row.purpose)}</th>
                  <td className={table.num}>{count(row.calls)}</td>
                  <td className={`${table.num} ${ui.nowrap}`}>
                    {tokens(row.prompt_tokens)} / {tokens(row.completion_tokens)}
                  </td>
                  <td className={table.num}>{usd(row.cost_micro_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Fold>
    </Panel>
  );
}
