import styles from "./landing.module.css";

/** A tile whose cover was swapped for the found talk's, to put back later. */
export type Reslot = { tile: HTMLElement; vid: string; src: string };

// Clearance budget: the wall's drift amplitude, plus air around every obstacle.
const DRIFT = 14;
const AIR = 10;

/** Line boxes for running text (a block's rect is mostly empty air on its
 *  right, which is where the found frame belongs), plain rects for the rest. */
function obstacles(hero: HTMLElement): DOMRect[] {
  const out: DOMRect[] = [];
  hero.querySelectorAll('[data-hero="obstacle-text"]').forEach((node) => {
    const range = document.createRange();
    range.selectNodeContents(node);
    for (const r of range.getClientRects()) if (r.width > 1 && r.height > 1) out.push(r);
  });
  const boxes = [
    ...hero.querySelectorAll(
      '[data-hero="obstacle"], [data-hero="slug"], [data-hero="chips"], [data-hero="bench"]',
    ),
    document.querySelector(`.${styles.rail}`),
  ];
  for (const node of boxes) if (node) out.push(node.getBoundingClientRect());
  return out;
}

/** 0 when the rects touch or overlap. */
function gapTo(a: DOMRect, b: DOMRect) {
  const dx = Math.max(b.left - a.right, a.left - b.right, 0);
  const dy = Math.max(b.top - a.bottom, a.top - b.bottom, 0);
  return Math.hypot(dx, dy);
}

const over = (a: DOMRect, b: DOMRect, m: number) =>
  a.left < b.right + m && a.right > b.left - m && a.top < b.bottom + m && a.bottom > b.top - m;

/** The tile that lights up for a find: whole inside the room, clear of every
 *  obstacle through the drift cycle, and nearest the light table (Tom's call,
 *  2026-08-10). If no copy of the talk qualifies, the chosen tile takes its cover. */
export function pickTile(
  hero: HTMLElement,
  bench: HTMLElement,
  tiles: HTMLElement[],
  cue: { vid: string; cover: string },
): { tile: HTMLElement; reslot: Reslot | null } | null {
  if (!tiles.length) return null;
  const hr = hero.getBoundingClientRect();
  const benchR = bench.getBoundingClientRect();
  const obs = obstacles(hero);
  const rects = new Map(tiles.map((t) => [t, t.getBoundingClientRect()]));
  const rect = (t: HTMLElement) => rects.get(t)!;
  const inHero = (r: DOMRect) =>
    r.top >= hr.top - 1 && r.bottom <= hr.bottom + 1 && r.left >= -1 && r.right <= innerWidth + 1;
  const air = (t: HTMLElement) => Math.min(...obs.map((o) => gapTo(rect(t), o)));
  const airiest = (list: HTMLElement[]) => list.reduce((a, b) => (air(b) > air(a) ? b : a));

  let pool = tiles.filter(
    (t) => inHero(rect(t)) && !obs.some((o) => over(rect(t), o, AIR + DRIFT)),
  );
  if (!pool.length) {
    // A very short viewport: never touch the table, keep the most air.
    let rest = tiles.filter((t) => inHero(rect(t)) && !over(rect(t), benchR, DRIFT));
    if (!rest.length) rest = tiles;
    pool = [airiest(rest)];
  }

  const dist = (t: HTMLElement) => gapTo(rect(t), benchR);
  pool.sort((a, b) => dist(a) - dist(b) || air(b) - air(a));
  const nearest = dist(pool[0]);
  const best = airiest(pool.filter((t) => dist(t) <= nearest + 40));

  const own = pool.find((t) => t.dataset.vid === cue.vid && dist(t) <= dist(best) * 1.3 + 40);
  if (own) return { tile: own, reslot: null };

  const img = best.querySelector("img")!;
  const reslot = { tile: best, vid: best.dataset.vid ?? "", src: img.src };
  best.dataset.vid = cue.vid;
  img.src = cue.cover;
  return { tile: best, reslot };
}
