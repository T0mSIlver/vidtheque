"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { pickTile, type Reslot } from "./heroGeometry";
import styles from "./landing.module.css";

/** What the controller needs to know about one canned question. */
export type HeroCue = { q: string; vid: string; at: string; still: string; cover: string };

// The hero's show runs imperatively and holds no React state: a re-render
// mid-lift would re-lay-out the flight. Motion law (DESIGN.md): reduced motion
// and `?still=1` paint the end state at once.
export function HeroController({
  cues,
  className,
  id,
  children,
}: {
  cues: HeroCue[];
  className: string;
  id: string;
  children: ReactNode;
}) {
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    const hero = root.current;
    if (!hero) return;
    const part = <T extends HTMLElement = HTMLElement>(name: string) =>
      hero.querySelector<T>(`[data-hero="${name}"]`)!;
    const wall = part("wall");
    const slug = part("slug");
    const qtext = part("qtext");
    const status = part("status");
    const chips = part("chips");
    const bench = part("bench");
    const panels = [...bench.querySelectorAll<HTMLElement>("[data-panel]")];
    const tiles = [...wall.querySelectorAll<HTMLElement>(`.${styles.wt}`)];
    const chipButtons = [...chips.querySelectorAll<HTMLButtonElement>("button[data-i]")];

    const reduce =
      location.search.includes("still") || matchMedia("(prefers-reduced-motion: reduce)").matches;

    let token = 0;
    let currentQ = -1;
    let reslot: Reslot | null = null;
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        const t = setTimeout(() => {
          timers.delete(t);
          resolve();
        }, ms);
        timers.add(t);
      });

    // The server renders more tiles than most rooms need; hide the rows below.
    function fitWall() {
      const cols = getComputedStyle(wall).gridTemplateColumns.split(" ").length;
      const th = parseFloat(getComputedStyle(wall).gridAutoRows) || 1;
      const h = hero!.offsetHeight || innerHeight;
      const rows = Math.max(4, Math.ceil((h * 1.1) / th) + 1);
      tiles.forEach((t, i) => (t.hidden = i >= cols * rows));
    }

    function clearHit() {
      wall.querySelectorAll(`.${styles.hit}`).forEach((t) => t.classList.remove(styles.hit));
      if (reslot) {
        reslot.tile.dataset.vid = reslot.vid;
        reslot.tile.querySelector("img")!.src = reslot.src;
        reslot = null;
      }
    }

    function lightTile(cue: HeroCue) {
      clearHit();
      const picked = pickTile(
        hero!,
        bench,
        tiles.filter((t) => !t.hidden),
        cue,
      );
      if (!picked) return null;
      reslot = picked.reslot;
      picked.tile.classList.add(styles.hit);
      return picked.tile;
    }

    function showPanel(i: number, acquire: boolean) {
      panels.forEach((p, j) => {
        p.hidden = j !== i;
        if (j === i) p.querySelector(`.${styles.frame}`)!.classList.toggle(styles.acq, acquire);
      });
      bench.classList.remove(styles.blank, styles.idle);
    }

    const setStatus = (s: string, label: string) => {
      status.dataset.s = s;
      status.textContent = label;
    };

    function markChip(active: number) {
      currentQ = active;
      chipButtons.forEach((b, j) => b.classList.toggle(styles.on, j === active));
    }

    function paintFinal(i: number) {
      qtext.textContent = cues[i].q;
      slug.classList.remove(styles.typing);
      wall.classList.add(styles.searching);
      showPanel(i, false);
      lightTile(cues[i]);
      setStatus("lifted", "lifted · " + cues[i].at);
      markChip(i);
    }

    async function typeIn(text: string, tk: number) {
      slug.classList.add(styles.typing);
      qtext.textContent = "";
      for (let i = 0; i < text.length; i++) {
        if (tk !== token) return false;
        qtext.textContent = text.slice(0, i + 1);
        await sleep(30 + (text[i] === " " ? 16 : 0));
      }
      slug.classList.remove(styles.typing);
      return true;
    }

    async function run(i: number) {
      const cue = cues[i];
      const tk = ++token;
      markChip(i);
      if (reduce) return paintFinal(i);

      wall.classList.remove(styles.searching);
      clearHit();
      bench.classList.add(styles.idle);
      new Image().src = cue.still;
      setStatus("reading", "reading");
      if (!(await typeIn(cue.q, tk)) || tk !== token) return;

      setStatus("scanning", "scanning the wall");
      wall.classList.add(styles.searching);
      // Lay the table out at its final size, veiled, so the pick and the
      // landing rect are measured against where things will be.
      showPanel(i, false);
      bench.classList.add(styles.veil, styles.idle);
      await sleep(460);
      if (tk !== token) return;
      const tile = lightTile(cue);
      await sleep(480);
      if (tk !== token) return;

      if (tile) {
        if (!(await lift(tile, i, tk))) return;
      } else {
        showPanel(i, true);
        bench.classList.remove(styles.veil);
      }
      setStatus("lifted", "lifted · " + cue.at);
    }

    // Both rects are 16:9, so the flight is one composited transform: laid out
    // at the landing size and scaled down onto the tile.
    async function lift(tile: HTMLElement, i: number, tk: number) {
      const from = tile.getBoundingClientRect();
      const to = panels[i].querySelector(`.${styles.frame}`)!.getBoundingClientRect();
      const s = from.width / to.width;
      const dx = from.left - to.left;
      const dy = from.top - to.top;
      const node = document.createElement("div");
      node.className = styles.lift;
      const img = document.createElement("img");
      img.src = cues[i].still;
      img.alt = "";
      node.append(img);
      Object.assign(node.style, {
        left: `${to.left}px`,
        top: `${to.top}px`,
        width: `${to.width}px`,
        height: `${to.height}px`,
        outlineWidth: `${(2 / s).toFixed(2)}px`,
        transform: `translate3d(${dx}px,${dy}px,0) scale(${s})`,
      });
      document.body.append(node);
      const alive = () => {
        if (tk === token) return true;
        node.remove();
        return false;
      };

      // Detach first, so it reads as a lift and not a cut; then travel on the
      // stylesheet's transition.
      await sleep(40);
      if (!alive()) return false;
      node.style.transition = "transform .24s ease-out,outline-width .24s ease-out";
      node.style.transform = `translate3d(${dx - from.width * 0.04}px,${dy - from.height * 0.04}px,0) scale(${s * 1.08})`;
      await sleep(270);
      if (!alive()) return false;
      node.style.transition = "";
      node.style.transform = "translate3d(0,0,0) scale(1)";
      node.style.outlineWidth = "2px";
      await sleep(860);
      if (!alive()) return false;
      showPanel(i, true);
      bench.classList.remove(styles.veil);
      await sleep(60);
      node.remove();
      return true;
    }

    fitWall();
    if (reduce) part("wallwrap").style.animation = "none";

    let resizeTimer: ReturnType<typeof setTimeout> | undefined;
    const onResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        fitWall();
        if (currentQ >= 0 && wall.classList.contains(styles.searching)) lightTile(cues[currentQ]);
      }, 200);
    };
    addEventListener("resize", onResize, { passive: true });

    const onChip = (e: MouseEvent) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("button[data-i]");
      if (b) void run(Number(b.dataset.i));
    };
    chips.addEventListener("click", onChip);

    if (reduce) paintFinal(0);
    else {
      const t = setTimeout(() => {
        timers.delete(t);
        if (currentQ < 0) void run(0);
      }, 700);
      timers.add(t);
    }

    return () => {
      token++;
      timers.forEach(clearTimeout);
      clearTimeout(resizeTimer);
      removeEventListener("resize", onResize);
      chips.removeEventListener("click", onChip);
      document.querySelectorAll(`.${styles.lift}`).forEach((n) => n.remove());
    };
  }, [cues]);

  return (
    <section className={className} id={id} ref={root}>
      {children}
    </section>
  );
}
