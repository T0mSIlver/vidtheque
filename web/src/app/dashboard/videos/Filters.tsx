"use client";

import { ROOT } from "@/lib/dashboard/client";
import controls from "../kit/controls.module.css";
import { FilterBand } from "../kit/FilterBand";
import { DashLink, Fact, Sep, ui, Unbroken } from "../kit/ui";
import { bandUrl, type Band, type DateKey } from "./query";
import styles from "./videos.module.css";

const INDEX_STATES = ["pending", "indexing", "ready", "failed", "stale"];
const HAS_VALUES = ["any", "transcript", "ocr", "frames", "all"];
const ORDERS = ["recency", "title", "duration", "indexed_at", "relevance"];

/** What narrows the table, as the query ran; an open end of a range is `…`.
 *  The order is not a narrowing, so the sorted head says it instead. */
export function Narrowing({ band }: { band: Band }) {
  const range = (after: DateKey, before: DateKey) =>
    band[after] || band[before] ? `${band[after] || "…"} – ${band[before] || "…"}` : "";

  const facts: [string, string][] = [];
  if (band.index_state && band.index_state !== "all") facts.push(["state", band.index_state]);
  if (band.has && band.has !== "any") facts.push(["has", band.has]);
  const published = range("published_after", "published_before");
  if (published) facts.push(["published", published]);
  const indexed = range("indexed_after", "indexed_before");
  if (indexed) facts.push(["indexed", indexed]);

  return (
    <>
      {facts.map(([label, text], index) => (
        <span key={label}>
          <Unbroken>
            <Fact label={label} value={text} />
            {index < facts.length - 1 ? <Sep /> : null}
          </Unbroken>{" "}
        </span>
      ))}
    </>
  );
}

/** A change to any control is the search: a picker at once, a text field when
 *  the typing pauses. */
export function Filters({ band }: { band: Band }) {
  return (
    <FilterBand auto values={band} toUrl={bandUrl}>
      <div className={`${controls.field} ${controls.wide}`}>
        <label htmlFor="f-q">Title, channel or description</label>
        <input
          id="f-q"
          name="q"
          type="search"
          defaultValue={band.q}
          placeholder="attention, tokenizer…"
          spellCheck={false}
          autoComplete="off"
        />
      </div>
      <div className={`${controls.field} ${controls.text}`}>
        <label htmlFor="f-channel">Channel</label>
        <input
          id="f-channel"
          name="channel"
          type="text"
          defaultValue={band.channel}
          autoComplete="off"
        />
      </div>
      <div className={`${controls.field} ${controls.text}`}>
        <label htmlFor="f-tags">Tags</label>
        <input
          id="f-tags"
          name="tags"
          type="text"
          defaultValue={band.tags}
          placeholder="topic:attention"
          autoComplete="off"
        />
      </div>
      <div className={`${controls.field} ${controls.pickField}`}>
        <label htmlFor="f-state">State</label>
        <span className={controls.pick}>
          <select id="f-state" name="index_state" defaultValue={band.index_state}>
            <option value="all">all states</option>
            {INDEX_STATES.map((state) => (
              <option key={state} value={state}>
                {state}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${controls.field} ${controls.pickField}`}>
        <label htmlFor="f-has">Coverage</label>
        <span className={controls.pick}>
          <select id="f-has" name="has" defaultValue={band.has}>
            {HAS_VALUES.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      {/* The two time axes stay two controls (AGENTS.md invariant). */}
      <DateRange legend="Published" after="published_after" before="published_before" band={band} />
      <DateRange legend="Indexed" after="indexed_after" before="indexed_before" band={band} />
      <div className={`${controls.field} ${controls.pickField}`}>
        <label htmlFor="f-order">Order</label>
        <span className={controls.pick}>
          {/* The five orders the tool takes; no "unset" option. */}
          <select id="f-order" name="order" defaultValue={band.order}>
            {ORDERS.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${controls.field} ${controls.narrow}`}>
        <label htmlFor="f-limit">Rows</label>
        {/* No `max`: the ceiling is the server's, disclosed in `notes`. */}
        <input
          id="f-limit"
          name="limit"
          type="number"
          min={1}
          defaultValue={band.limit}
          inputMode="numeric"
        />
      </div>
      <div className={`${controls.field} ${controls.actions}`}>
        <button className={controls.button} type="submit" data-apply="">
          Apply
        </button>
        <DashLink className={controls.ghostlink} href={`${ROOT}/videos`}>
          Reset
        </DashLink>
      </div>
    </FilterBand>
  );
}

function DateRange({
  legend,
  after,
  before,
  band,
}: {
  legend: string;
  after: DateKey;
  before: DateKey;
  band: Band;
}) {
  const id = legend === "Published" ? "pub" : "idx";
  return (
    <fieldset className={`${controls.field} ${styles.rangeField}`}>
      <legend>{legend}</legend>
      <div className={styles.range}>
        <label className={ui.srOnly} htmlFor={`f-${id}-after`}>
          {legend} on or after
        </label>
        <input id={`f-${id}-after`} name={after} type="date" defaultValue={band[after]} />
        <span className={styles.rangeSep} aria-hidden="true">
          –
        </span>
        <label className={ui.srOnly} htmlFor={`f-${id}-before`}>
          {legend} on or before
        </label>
        <input id={`f-${id}-before`} name={before} type="date" defaultValue={band[before]} />
      </div>
    </fieldset>
  );
}
