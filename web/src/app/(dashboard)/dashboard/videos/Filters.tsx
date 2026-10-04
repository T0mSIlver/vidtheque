"use client";

import { ROOT } from "@/lib/dashboard/client";
import { ChannelPick } from "@/components/dashboard/ChannelPick";
import controls from "@/components/dashboard/kit/controls.module.css";
import { FilterBand } from "@/components/dashboard/kit/FilterBand";
import { Fold } from "@/components/dashboard/kit/Fold";
import { DashLink, ui } from "@/components/dashboard/kit/ui";
import { bandUrl, type Band, type DateKey } from "./query";
import styles from "./videos.module.css";

const INDEX_STATES = ["pending", "indexing", "ready", "failed", "stale"];
const HAS_VALUES = ["any", "transcript", "ocr", "frames", "all"];
const ORDERS = ["recency", "title", "duration", "indexed_at", "relevance"];

/** A filter the fold holds is on, so the fold opens to show it. */
function folded(band: Band): boolean {
  return Boolean(
    band.tags ||
    (band.has && band.has !== "any") ||
    band.published_after ||
    band.published_before ||
    band.indexed_after ||
    band.indexed_before,
  );
}

/** A change to any control is the search: a picker at once, a text field when
 *  the typing pauses. The three used most are out; the rest are folded. */
export function Filters({ band }: { band: Band }) {
  const narrowed = Boolean(
    band.q || band.channel || (band.index_state && band.index_state !== "all") || folded(band),
  );
  return (
    <div className={styles.band}>
      <FilterBand auto values={band} toUrl={bandUrl}>
        <div className={`${controls.field} ${controls.wide}`}>
          <label htmlFor="f-q">Title, channel or description</label>
          <input
            id="f-q"
            name="q"
            type="search"
            defaultValue={band.q}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <ChannelPick id="f-channel" value={band.channel} />
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
        <div className={`${controls.field} ${controls.actions}`}>
          <button className={controls.button} type="submit" data-apply="">
            Apply
          </button>
          {narrowed ? (
            <DashLink className={controls.ghostlink} href={`${ROOT}/videos`}>
              Reset
            </DashLink>
          ) : null}
        </div>
        <div className={styles.more}>
          <Fold label="more filters" open={folded(band)}>
            <div className={styles.moreFields}>
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
              <DateRange
                legend="Published"
                after="published_after"
                before="published_before"
                band={band}
              />
              <DateRange
                legend="Indexed"
                after="indexed_after"
                before="indexed_before"
                band={band}
              />
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
            </div>
          </Fold>
        </div>
      </FilterBand>
    </div>
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
