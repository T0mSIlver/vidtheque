"use client";

import { ROOT } from "@/lib/dashboard/client";
import { withQuery } from "@/lib/dashboard/query";
import type { Jobs } from "@/lib/dashboard/schemas";
import { DASH } from "@/lib/format";
import controls from "@/components/dashboard/kit/controls.module.css";
import { FilterBand } from "@/components/dashboard/kit/FilterBand";
import { DashLink } from "@/components/dashboard/kit/ui";

// The jobs band: its pickers and the head's facts, both read off what the
// listing ran with.

export const FILTERS = ["state", "kind", "error_code", "order", "degraded", "limit"] as const;

// The pickers' words (`views._JOB_STATES` and friends): options, not bounds.
const STATES = ["all", "active", "failed", "done"];
const KINDS = ["all", "index", "reindex", "delete", "follow_check"];
const ORDERS = ["newest", "priority", "wall_clock"];

/** Values the API would use anyway, left off a link. */
export const DEFAULTS: Record<string, string> = { state: "all", kind: "all", order: "newest" };

/** `state` and `order` always (the table is read in an order); the rest only
 *  when they narrow. Dashes until the listing answers. */
export function factsOf(filters?: Jobs["filters"]): [string, string][] {
  if (!filters) {
    return [
      ["state", DASH],
      ["order", DASH],
    ];
  }
  const facts: [string, string][] = [["state", filters.state]];
  if (filters.kind !== DEFAULTS.kind) facts.push(["kind", filters.kind]);
  if (filters.error_code) facts.push(["error code", filters.error_code]);
  if (filters.degraded) facts.push(["degraded", "only"]);
  facts.push(["order", filters.order]);
  return facts;
}

/** The band, seeded from what the listing ran with (the URL until it answers). */
export function Filters({ params, data }: { params: URLSearchParams; data?: Jobs }) {
  const asked = (key: string, fallback = "") => params.get(key) ?? fallback;
  const filters = data?.filters;
  const values = {
    state: filters?.state ?? asked("state", "all"),
    kind: filters?.kind ?? asked("kind", "all"),
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

  return (
    <FilterBand values={values} toUrl={toUrl}>
      <div className={`${controls.field} ${controls.pickField}`}>
        <label htmlFor="f-jobstate">State</label>
        <span className={controls.pick}>
          <select id="f-jobstate" name="state" defaultValue={values.state}>
            {STATES.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${controls.field} ${controls.pickField}`}>
        <label htmlFor="f-jobkind">Kind</label>
        <span className={controls.pick}>
          <select id="f-jobkind" name="kind" defaultValue={values.kind}>
            {KINDS.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
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
                {entry.replace("_", " ")}
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
  );
}
