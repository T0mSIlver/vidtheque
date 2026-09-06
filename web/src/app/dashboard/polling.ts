"use client";

import { useState } from "react";

// What changed since the first reading, for a list a tick keeps re-reading.
//
// A polled page has two of them, and they are the same question asked of two
// shapes: the jobs table patches the rows it started with and says so when a
// row it cannot patch has appeared, and the event log marks the entries that
// landed while the reader was looking at it. Both are read against the *first*
// reading, not the previous one — "new" means new to this reader, and a mark
// that moved every two seconds would be a mark nobody could follow.
//
// Neither hook fetches: `useJobsPoll` does that, `useRead` holds the state
// machine, and these two are what a page does with a payload it has been
// handed again.

/**
 * The rows the tick patches — the ones that were on the page when it loaded.
 *
 * `jobs.js` patched the rows it could find and revealed a note for the ones it
 * could not: a row that appeared *since* this page rendered has nothing to
 * patch, and a table that silently grows a row under the reader's cursor is a
 * table whose count line has stopped being true. React would happily re-render
 * the whole body every two seconds instead; this keeps the original's
 * contract, which is also the one that keeps a row's identity — the focus
 * inside a cell, a selection, an open hint — across a reading.
 *
 * A row that drops out of the listing keeps its last reading rather than
 * vanishing mid-triage, which is what patching in place meant.
 */
export function usePatchedRows<Row>(
  rows: Row[],
  idOf: (row: Row) => string,
): { rows: Row[]; arrived: boolean } {
  // The baseline is fixed at the first reading; the rows are patched by every
  // one after it. A reading is a new array — the payload is parsed fresh — so
  // the array's own identity is what says one has landed. Adjusted during
  // render rather than in an effect, which is React's own answer to
  // "recompute when a prop changes": an effect would paint the previous
  // payload's rows first.
  const [order] = useState(() => rows.map(idOf));
  const [seen, setSeen] = useState<Row[] | null>(null);
  const [held, setHeld] = useState<Row[]>(rows);
  const [arrived, setArrived] = useState(false);

  if (seen !== rows) {
    setSeen(rows);
    const reading = new Map(rows.map((row) => [idOf(row), row]));
    const standing = new Map(held.map((row) => [idOf(row), row]));
    setHeld(
      order
        .map((id) => reading.get(id) ?? standing.get(id))
        .filter((row): row is Row => row !== undefined),
    );
    const known = new Set(order);
    if (rows.some((row) => !known.has(idOf(row)))) setArrived(true);
  }

  return { rows: held, arrived };
}

/**
 * Which of these arrived while the page was open.
 *
 * Watching a deferral get logged is the point of the jobs page being live at
 * all — the event log is the only record a non-rate-limit one has — so an
 * entry that lands under the reader is marked. A left rule rather than a
 * flash, because it has to survive being scrolled past, which an animation
 * does not.
 */
export function useArrivals<Item, Id>(items: Item[], idOf: (item: Item) => Id): Set<Id> {
  const [known] = useState(() => new Set(items.map(idOf)));
  const [arrived] = useState(() => new Set<Id>());
  for (const item of items) {
    const id = idOf(item);
    if (!known.has(id)) {
      known.add(id);
      arrived.add(id);
    }
  }
  return arrived;
}
