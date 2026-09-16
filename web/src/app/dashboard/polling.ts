"use client";

import { useState } from "react";

// What changed since the first reading of a polled list. "New" means new to
// this reader, so both hooks measure against the first reading, never the last.

/**
 * The rows a tick patches: the ones on the page when it loaded, in that order.
 * A row that arrived since is not invented (the count line would stop being
 * true) but reported through `arrived`; a row that dropped out keeps its last
 * reading rather than vanishing mid-triage. Remount the caller to start a new
 * baseline.
 */
export function usePatchedRows<Row>(
  rows: Row[],
  idOf: (row: Row) => string,
): { rows: Row[]; arrived: boolean } {
  const [order] = useState(() => rows.map(idOf));
  const [seen, setSeen] = useState<Row[] | null>(null);
  const [held, setHeld] = useState<Row[]>(rows);
  const [arrived, setArrived] = useState(false);

  // Adjusted during render, not in an effect, so the previous reading's rows
  // never paint (each reading is a freshly parsed array).
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

/** Which of these were not in the first reading. Derived, never accumulated. */
export function useArrivals<Item, Id>(items: Item[], idOf: (item: Item) => Id): Set<Id> {
  const [known] = useState(() => new Set(items.map(idOf)));
  const arrived = new Set<Id>();
  for (const item of items) {
    const id = idOf(item);
    if (!known.has(id)) arrived.add(id);
  }
  return arrived;
}
