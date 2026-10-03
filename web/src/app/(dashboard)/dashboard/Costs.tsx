"use client";

import { dashboard, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { CostWindow, Costs } from "@/lib/dashboard/schemas";
import { at, count, iso, usd } from "@/lib/format";
import { table } from "@/components/dashboard/kit/table";
import { DashLink, Figure, Panel, ui } from "@/components/dashboard/kit/ui";

// What the model calls cost (dashboard.md §25.8), at list price: on a monthly
// plan it is what they would cost pay-as-you-go (companion.md §4.1).

const read = (signal: AbortSignal) => dashboard.costs(signal);

const WINDOWS: [keyof Costs["windows"], string][] = [
  ["today", "today"],
  ["7d", "7 days"],
  ["30d", "30 days"],
  ["month", "this month"],
];

export function CostsPanel() {
  const costs = useResource("costs", read);
  const data = costs.data;
  // No write side answers 404, and a box that never called a model has
  // nothing to price: neither is a panel.
  if (!data || data.windows["30d"].calls === 0) return null;
  return <Loaded data={data} />;
}

function calls(window: CostWindow) {
  const notes = [<>{count(window.calls)} call(s)</>];
  if (window.unpriced_calls) {
    notes.push(<span className={ui.warn}>{count(window.unpriced_calls)} with no known cost</span>);
  }
  return notes;
}

function Loaded({ data }: { data: Costs }) {
  const month = data.windows["30d"];
  return (
    <Panel id="costs" title="Model cost" aside={<span className={ui.note}>list price, USD</span>}>
      <dl className={`${ui.figures} ${ui.figuresTight}`}>
        {WINDOWS.map(([key, label]) => (
          <Figure label={label} key={key} notes={calls(data.windows[key])}>
            {usd(data.windows[key].cost_micro_usd)}
          </Figure>
        ))}
        <Figure
          label="per verdict"
          notes={[<>over {count(month.verdicts)} verdict(s) in 30 days</>]}
        >
          {usd(month.per_verdict_micro_usd)}
        </Figure>
      </dl>

      <div className={table.tablewrap}>
        <table className={table.grid}>
          <caption className={ui.srOnly}>Cost by purpose, last 30 days</caption>
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
                <th scope="row">
                  <code>{row.purpose}</code>
                </th>
                <td className={table.num}>{count(row.calls)}</td>
                <td className={table.num}>
                  {count(row.prompt_tokens)} / {count(row.completion_tokens)}
                </td>
                <td className={table.num}>{usd(row.cost_micro_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.top.items.length ? (
        <div className={table.tablewrap}>
          <table className={table.grid}>
            <caption className={ui.srOnly}>The most expensive calls, last 30 days</caption>
            <thead>
              <tr>
                <th scope="col">most expensive</th>
                <th scope="col">video</th>
                <th scope="col" className={table.num}>
                  tokens in / out
                </th>
                <th scope="col" className={table.num}>
                  cost
                </th>
              </tr>
            </thead>
            <tbody>
              {data.top.items.map((call, index) => (
                <tr key={`${call.at}-${index}`}>
                  <th scope="row">
                    <time dateTime={iso(call.at)}>{at(call.at)}</time> <code>{call.purpose}</code>
                    {call.outcome !== "ok" ? (
                      <>
                        {" "}
                        <span className={ui.warn}>{call.outcome}</span>
                      </>
                    ) : null}
                  </th>
                  <td>
                    {call.video_id ? (
                      <DashLink href={`${ROOT}/videos/${encodeURIComponent(call.video_id)}`}>
                        {call.title ?? call.video_id}
                      </DashLink>
                    ) : null}
                  </td>
                  <td className={table.num}>
                    {count(call.prompt_tokens)} / {count(call.completion_tokens)}
                  </td>
                  <td className={table.num}>{usd(call.cost_micro_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Panel>
  );
}
