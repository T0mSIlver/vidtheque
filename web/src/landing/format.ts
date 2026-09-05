// The landing's number and time formatting, ported from `landing.js`.

export const pad = (n: number) => String(n).padStart(2, "0");

/** `h:mm:ss` past an hour, `mm:ss` below it — the page's own clock. */
export function hms(seconds: number): string {
  const s = Math.floor(seconds);
  const h = (s / 3600) | 0;
  const m = ((s % 3600) / 60) | 0;
  return (h ? h + ":" + pad(m) : pad(m)) + ":" + pad(s % 60);
}

export const num = (n: number) => Math.round(n).toLocaleString("en-US");

/** `00:12:46` → seconds, for the wall band's receipts. */
export const tcToSeconds = (tc: string) => tc.split(":").reduce((a, p) => a * 60 + Number(p), 0);

export const ymd = (unixSeconds: number) => new Date(unixSeconds * 1000).toISOString().slice(0, 10);

/** The receipt's own href: the talk, at the second. */
export const youtubeAt = (videoId: string, t: number) =>
  `https://youtu.be/${videoId}?t=${Math.floor(t)}`;
