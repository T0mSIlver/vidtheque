"use client";

import { ROOT } from "@/lib/dashboard/client";
import { withQuery } from "@/lib/dashboard/query";
import type { Jobs } from "@/lib/dashboard/schemas";
import controls from "@/components/dashboard/kit/controls.module.css";
import { FilterBand } from "@/components/dashboard/kit/FilterBand";
import { Fold } from "@/components/dashboard/kit/Fold";
import { DashLink } from "@/components/dashboard/kit/ui";
import { KINDS as KIND_WORDS } from "./parts";

// The jobs band: its pickers, read off what the listing ran with. The state
// is the tabs above it; the rest is rarely touched, so it is folded unless
// one of them is set.

export const FILTERS = ["state", "kind", "error_code", "order", "degraded", "limit"] as const;

// The pickers' words (`views._JOB_STATES` and friends): options, not bounds.
// `videos` is every kind but `follow_check` (dashboard.md §24.2).
const KINDS = ["videos", "all", "index", "reindex", "delete", "follow_check", "verdict"];
const ORDERS = ["newest", "priority", "wall_clock"];
const KIND_LABELS: Record<string, string> = { videos: "Videos", all: "All", ...KIND_WORDS };
const ORDER_LABELS: Record<string, string> = {
  newest: "Newest",
  priority: "Priority",
  wall_clock: "Longest",
};

/** Values the API would use anyway, left off a link. */
export const DEFAULTS: Record<string, string> = { state: "all", kind: "videos", order: "newest" };

/** The band, seeded from what the listing ran with (the URL until it answers). */
export function Filters({ params, data }: { params: URLSearchParams; data?: Jobs }) {
  const asked = (key: string, fallback = "") => params.get(key) ?? fallback;
  const filters = data?.filters;
  const values = {
    state: filters?.state ?? asked("state", "all"),
    kind: filters?.kind ?? asked("kind", DEFAULTS.kind),
    order: filters?.order ?? asked("order", "newest"),
    error_code: filters ? (filters.error_code ?? "") : asked("error_code"),
    degraded: filters ? filters.degraded : asked("degraded") === "1",
    limit: data ? String(data.pagination.limit) : asked("limit"),
  };

  function toUrl(form: FormData) {
    const next = new URLSearchParams();
    for (const key of FILTERS) {
      const entry = form.get(key);
      if (typeof entry !== "string") continue;
      const chosen = entry.trim();
      if (chosen && chosen !== DEFAULTS[key]) next.set(key, chosen);
    }
    return withQuery(`${ROOT}/jobs`, next);
  }

  const narrowed =
    values.kind !== DEFAULTS.kind ||
    values.order !== DEFAULTS.order ||
    values.error_code !== "" ||
    values.degraded ||
    (values.limit !== "" && values.limit !== "25");

  return (
    <Fold label="more filters" open={narrowed}>
      <FilterBand values={values} toUrl={toUrl}>
        <input type="hidden" name="state" value={values.state} />
        <div className={`${controls.field} ${controls.pickField}`}>
          <label htmlFor="f-jobkind">Kind</label>
          <span className={controls.pick}>
            <select id="f-jobkind" name="kind" defaultValue={values.kind}>
              {KINDS.map((entry) => (
                <option key={entry} value={entry}>
                  {KIND_LABELS[entry] ?? entry}
                </option>
              ))}
            </select>
          </span>
        </div>
        <div className={`${controls.field} ${controls.text}`}>
          <label htmlFor="f-joberror">Error code</label>
          <input
            id="f-joberror"
            name="error_code"
            type="text"
            defaultValue={values.error_code}
            placeholder="E_RATE_LIMIT"
            autoComplete="off"
          />
        </div>
        <div className={`${controls.field} ${controls.pickField}`}>
          <label htmlFor="f-joborder">Order</label>
          <span className={controls.pick}>
            <select id="f-joborder" name="order" defaultValue={values.order}>
              {ORDERS.map((entry) => (
                <option key={entry} value={entry}>
                  {ORDER_LABELS[entry] ?? entry}
                </option>
              ))}
            </select>
          </span>
        </div>
        <label className={controls.check}>
          <input type="checkbox" name="degraded" value="1" defaultChecked={values.degraded} />
          <span className={controls.checkWord}>Degraded only</span>
        </label>
        <div className={`${controls.field} ${controls.narrow}`}>
          <label htmlFor="f-joblimit">Rows</label>
          {/* No `max`: the ceiling is the server's, disclosed in `notes`. */}
          <input
            id="f-joblimit"
            name="limit"
            type="number"
            min={1}
            defaultValue={values.limit}
            inputMode="numeric"
          />
        </div>
        <div className={`${controls.field} ${controls.actions}`}>
          <button className={controls.button} type="submit">
            Apply
          </button>
          <DashLink className={controls.ghostlink} href={`${ROOT}/jobs`}>
            Reset
          </DashLink>
        </div>
      </FilterBand>
    </Fold>
  );
}
