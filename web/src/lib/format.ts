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
