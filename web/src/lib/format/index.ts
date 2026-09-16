// Machine strings, formatted once. Everything here renders in the mono face.
// Both halves read this module; the landing's own pair is `./landing`.

/** Seconds -> `m:ss` or `h:mm:ss`, the timecode a receipt prints. */
export function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/** The three parts a receipt is printed in (`components/Receipt`). */
export interface ReceiptParts {
  /** `youtu.be/` — which surface, trailing slash included. */
  host: string;
  /** The path with its leading slash off: which talk. */
  id: string;
  /** `?t=705` — which second, or empty when the link names none. */
  query: string;
}

/** `https://youtu.be/ID?t=705` as `youtu.be/` · `ID` · `?t=705`; `null` for a
 *  link that is not http(s), which gets no receipt. */
export function receiptParts(link: string): ReceiptParts | null {
  try {
    const url = new URL(link);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    return { host: `${url.host}/`, id: url.pathname.replace(/^\//, ""), query: url.search };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Display formatting for typed values (frontend-migration.md §1 decision 5).

/** The one rendering for "this is not recorded". */
export const DASH = "—";

/** Seconds as a span a human reads: `12s`, `4m 12s`, `1h 05m`. One formatter
 *  for every duration, so they never read as different units. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  const total = Math.floor(seconds);
  if (total < 0) return DASH;
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${pad(total % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${pad(minutes % 60)}m`;
}

/** Seconds always as `h:mm:ss` (`0:08:00`): unlike `clock`, it keeps the hour,
 *  so a floor and a ceiling on one line read on one scale. */
export function hms(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  return `${h}:${pad(m)}:${pad(total % 60)}`;
}

/** Seconds as the ledger's hours figure: one decimal, the unit set beside it. */
export function hours(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  return (seconds / 3600).toFixed(1);
}

/** A count, grouped: `1,204`; `0` for nothing. */
export function count(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "0";
  return Math.round(value).toLocaleString("en-US");
}

/** `1.4 GB`. Base-10, as a disk reports. */
export function bytes(value: number | null | undefined): string {
  if (!value || !Number.isFinite(value)) return "0 B";
  let size = Math.abs(value);
  for (const unit of ["B", "kB", "MB", "GB"]) {
    if (size < 1000) return unit === "B" ? `${Math.round(size)} B` : `${size.toFixed(1)} ${unit}`;
    size /= 1000;
  }
  return `${size.toFixed(1)} TB`;
}

// Clocks print in UTC, to compare against log lines. `0` and `null` both mean
// "none" and print the dash.

/** Epoch seconds -> `2026-08-13 04:12` (UTC). */
export function at(epochSeconds: number | null | undefined): string {
  const iso = isoOf(epochSeconds);
  return iso ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : DASH;
}

/** Epoch seconds -> `2026-08-13` (UTC). */
export function day(epochSeconds: number | null | undefined): string {
  const iso = isoOf(epochSeconds);
  return iso ? iso.slice(0, 10) : DASH;
}

/** Epoch seconds -> the `<time datetime=…>` attribute, or `undefined`. */
export function iso(epochSeconds: number | null | undefined): string | undefined {
  return isoOf(epochSeconds)?.replace(/\.\d+Z$/, "Z");
}

function isoOf(epochSeconds: number | null | undefined): string | undefined {
  if (!epochSeconds || !Number.isFinite(epochSeconds)) return undefined;
  const date = new Date(Math.floor(epochSeconds) * 1000);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

const pad = (n: number) => String(n).padStart(2, "0");
