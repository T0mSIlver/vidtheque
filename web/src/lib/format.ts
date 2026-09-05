// Machine strings, formatted once. Everything here renders in the mono face.

/** Seconds -> `m:ss` or `h:mm:ss`, the timecode a receipt prints. */
export function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/** `youtu.be/ID?t=705`: the receipt, as the page prints it. */
export function receipt(link: string): string {
  return link.replace(/^https?:\/\//, "");
}

// ---------------------------------------------------------------------------
// The dashboard's half. Python sends typed values and React formats them
// (frontend-migration.md §1 decision 5), so every one of these was a Jinja
// filter until 2026-09-05 and is named after the one it replaces
// (`dashboard/render.py`, `vidtheque_mcp/text.py`). They are here rather than
// in `lib/dashboard/` because a duration is a duration on every surface.

/** The one rendering for "this is not recorded" (`render.dash`). */
export const DASH = "—";

/**
 * A number of seconds as a span a human reads: `12s`, `4m 12s`, `1h 05m`.
 *
 * One formatter for every duration on the management surface — how long a
 * video runs, how long a stage took, how much of a backoff is left. They are
 * the same unit and must not read as three different ones (`render.span`).
 */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  const total = Math.floor(seconds);
  if (total < 0) return DASH;
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${pad(total % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${pad(minutes % 60)}m`;
}

/** Seconds as the ledger's hours figure: one decimal, the unit set beside it. */
export function hours(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  return (seconds / 3600).toFixed(1);
}

/** A count, grouped: `1,204`. `render.count`'s `{:,}`, and its `0` for nothing. */
export function count(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "0";
  return Math.round(value).toLocaleString("en-US");
}

/** `1.4 GB`. Base-10, because that is what a disk reports (`render.bytes_human`). */
export function bytes(value: number | null | undefined): string {
  if (!value || !Number.isFinite(value)) return "0 B";
  let size = Math.abs(value);
  for (const unit of ["B", "kB", "MB", "GB"]) {
    if (size < 1000) return unit === "B" ? `${Math.round(size)} B` : `${size.toFixed(1)} ${unit}`;
    size /= 1000;
  }
  return `${size.toFixed(1)} TB`;
}

// Every clock on this surface is UTC and is printed as one, which is what the
// Jinja pages did (`text.iso_day`, `text.iso_minute`): an operator reading a
// tunnelled dashboard at 03:00 is comparing it to a log line, and a
// browser-local rendering of a server-side stamp is a subtraction they have to
// do in their head. `0` and `null` are both "the corpus has none" — an empty
// corpus has no oldest video and no last index — and both print the dash.

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
