"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { ASSETS, GRID } from "@/landing/corpus";
import { QUERIES, WALL_ORDER, type CannedQuery } from "@/landing/show";
import styles from "./landing.module.css";

// BEAT 1 — the projection room. Ported from `landing.js`, which DESIGN.md
// names the reference implementation of the visual system.
//
// Everything below the JSX is imperative on purpose and holds no React state.
// The hero is a canned cinematic sequence, not application state: it measures
// the wall against the light table and the hero's real text line boxes, then
// flies one composited transform between two 16:9 rects. Re-rendering it would
// re-lay-out mid-flight, which is the one thing the lift may not do. So React
// renders the chassis once, and the effect owns the wall, the chips, the bench
// and the lift — the same division the original had between markup and script.
//
// MOTION LAW (Tom, locked): movement only where the machine is working. The
// inventory here is the wall's slow drift, the self-typing query and its caret,
// the search dim and the hit tile lighting, the lift, and the OCR boxes
// acquiring. Everything paints its end state under `prefers-reduced-motion:
// reduce`, and `?still=1` does the same for screenshots.
export function HeroStage() {
  const hero = useRef<HTMLElement>(null);
  const wallWrap = useRef<HTMLDivElement>(null);
  const wall = useRef<HTMLDivElement>(null);
  const heroin = useRef<HTMLDivElement>(null);
  const kick = useRef<HTMLDivElement>(null);
  const headline = useRef<HTMLHeadingElement>(null);
  const lede = useRef<HTMLParagraphElement>(null);
  const slug = useRef<HTMLDivElement>(null);
  const qtext = useRef<HTMLSpanElement>(null);
  const status = useRef<HTMLSpanElement>(null);
  const cta = useRef<HTMLAnchorElement>(null);
  const chips = useRef<HTMLDivElement>(null);
  const bench = useRef<HTMLDivElement>(null);
  const benchhead = useRef<HTMLDivElement>(null);
  const benchsay = useRef<HTMLDivElement>(null);
  const benchfoot = useRef<HTMLDivElement>(null);
  const herofoot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const heroEl = hero.current;
    const wallEl = wall.current;
    const heroinEl = heroin.current;
    const benchEl = bench.current;
    const slugEl = slug.current;
    const chipsEl = chips.current;
    if (!heroEl || !wallEl || !heroinEl || !benchEl || !slugEl || !chipsEl) return;

    // `?still=1` is the lab's stills switch: the page paints its end state at
    // once, which is how it is judged from screenshots. Same path as reduced
    // motion — the motion law's off switch.
    const still = location.search.includes("still");
    const reduce = still || matchMedia("(prefers-reduced-motion: reduce)").matches;

    let token = 0;
    let currentQ = -1;
    let disposed = false;
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        const id = setTimeout(() => {
          timers.delete(id);
          resolve();
        }, ms);
        timers.add(id);
      });

    const el = (tag: string, className?: string, html?: string) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (html != null) node.innerHTML = html;
      return node;
    };
    const ENTITIES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;" };
    const esc = (s: string) => s.replace(/[&<>]/g, (c) => ENTITIES[c]);
    const arrow =
      '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" aria-hidden="true"><path d="M3.4 8.6 8.6 3.4M4.6 3.4h4v4"/></svg>';
    const receipt = (vid: string, t: number) =>
      `<a class="${styles.receipt}" href="https://youtu.be/${vid}?t=${Math.floor(t)}" rel="noopener">` +
      `<span>youtu.be/</span><span class="${styles.vid} ${styles.id}">${vid}</span>` +
      `<b>?t=${Math.floor(t)}${arrow}</b></a>`;

    /* the frame with its detections, boxes exactly where ocr_lines put them */
    function frame(img: string, alt: string, q: CannedQuery, acq: boolean) {
      const f = el("figure", `${styles.frame}${acq ? " " + styles.acq : ""}`);
      const im = el("img") as HTMLImageElement;
      im.src = img;
      im.loading = "eager";
      im.decoding = "async";
      im.alt = alt;
      f.append(im);
      q.boxes.forEach((b, i) => {
        const d = el("div", `${styles.det}${b.on ? " " + styles.on : ""}`);
        d.style.cssText =
          `--x:${(b.b[0] * 100).toFixed(2)}%;--y:${(b.b[1] * 100).toFixed(2)}%;` +
          `--w:${((b.b[2] - b.b[0]) * 100).toFixed(2)}%;--h:${((b.b[3] - b.b[1]) * 100).toFixed(2)}%;--i:${i}`;
        if (b.tab) {
          d.append(el("span", styles.tab, esc(b.tab) + (b.conf ? `<i>${esc(b.conf)}</i>` : "")));
        }
        f.append(d);
      });
      return f;
    }

    /* ═══ the wall, built to fill the room ═══ */
    function buildWall() {
      if (disposed) return;
      const w = innerWidth;
      // fewer, bigger frames — v1's wall pitch rather than v3's mosaic
      // (Tom, 2026-08-10). One tile is ~145 CSS px of viewport.
      const cols = Math.max(4, Math.min(12, Math.round(w / 145)));
      const tw = (w * 1.1) / cols;
      const th = (tw * 9) / 16;
      // stacked layouts get a clear lane of exactly one tile plus air between
      // the copy and the light table — measured here, spent by the CSS
      heroinEl!.style.setProperty("--lane", Math.round(th + 58) + "px");
      const h = heroEl!.offsetHeight || innerHeight; // read after the lane is set
      const rows = Math.max(4, Math.ceil((h * 1.1) / th) + 1);
      wallEl!.style.gridTemplateColumns = `repeat(${cols},1fr)`;
      // rows are pinned to the tile height rather than stretched to 1fr: the
      // frames stay 16:9 (they are frames), and the clearance arithmetic that
      // places the found tile is then exact. The wrap clips the remainder.
      wallEl!.style.gridAutoRows = th.toFixed(2) + "px";
      wallEl!.innerHTML = "";
      const n = cols * rows;
      for (let i = 0; i < n; i++) {
        const g = WALL_ORDER[i % WALL_ORDER.length];
        const d = el("div", styles.wt);
        d.dataset.vid = g.vid;
        d.innerHTML = `<img src="${ASSETS}${g.img}" alt="" loading="eager" decoding="async">`;
        wallEl!.append(d);
      }
      if (currentQ >= 0) {
        wallEl!.classList.add(styles.searching);
        const t = pickTile(QUERIES[currentQ].vid);
        if (t) t.classList.add(styles.hit);
      }
    }

    /* ─── where the found frame is allowed to light up ───────────────────
       Tom's note, 2026-08-10: "the frame should be near the component on the
       right, but not overlapping it — 'the most expensive typo' looks the
       best." So the pick is geometry, not luck: the lit tile must clear the
       light table AND every actual line of hero text (line boxes, not the
       copy column's bounding box, which is mostly empty air on its right),
       and among the tiles that clear, the one nearest the table wins. All
       three queries therefore land in the same place-relative-to-the-table.
       Clearance is measured with a margin that also covers the whole drift
       cycle, so nothing creeps into the copy while you watch. */
    function obstacles(): DOMRect[] {
      const out: DOMRect[] = [];
      // real line boxes for running text — a block's own rect is mostly empty
      // air on its right, and that air is where the found frame belongs
      const push = (node: Element | null) => {
        if (!node) return;
        const rg = document.createRange();
        rg.selectNodeContents(node);
        for (const r of rg.getClientRects()) if (r.width > 1 && r.height > 1) out.push(r);
      };
      [kick.current, headline.current, lede.current].forEach(push);
      const railEl = document.querySelector(`.${styles.rail}`);
      [slugEl, cta.current, chipsEl, benchEl, railEl, herofoot.current].forEach((node) => {
        if (node) out.push(node.getBoundingClientRect());
      });
      return out;
    }
    const gapTo = (a: DOMRect, b: DOMRect) => {
      // 0 when the rects touch/overlap
      const dx = Math.max(b.left - a.right, a.left - b.right, 0);
      const dy = Math.max(b.top - a.bottom, a.top - b.bottom, 0);
      return Math.hypot(dx, dy);
    };
    const over = (a: DOMRect, b: DOMRect, m: number) =>
      a.left < b.right + m && a.right > b.left - m && a.top < b.bottom + m && a.bottom > b.top - m;

    function pickTile(vid: string): HTMLElement | null {
      const tiles = [...wallEl!.querySelectorAll<HTMLElement>(`.${styles.wt}`)];
      if (!tiles.length) return null;
      const hr = heroEl!.getBoundingClientRect();
      const benchR = benchEl!.getBoundingClientRect();
      const obs = obstacles();
      // a tile that hangs off the room, or off the viewport, is half a frame:
      // the found frame has to be whole before it can lift
      const inHero = (r: DOMRect) =>
        r.top >= hr.top - 1 &&
        r.bottom <= hr.bottom + 1 &&
        r.left >= -1 &&
        r.right <= innerWidth + 1;
      // the wall drifts: budget for the worst position in the cycle so the
      // clearance Tom sees in a screenshot is the clearance at every moment
      const DRIFT = 14;
      const AIR = 10;
      const air = (r: DOMRect) => Math.min(...obs.map((o) => gapTo(r, o)));
      let pool = tiles.filter((t) => {
        const r = t.getBoundingClientRect();
        return inHero(r) && !obs.some((o) => over(r, o, AIR + DRIFT));
      });
      // nothing is fully clear (a very short viewport): never touch the table,
      // and take the tile that keeps the most air
      if (!pool.length) {
        let rest = tiles.filter((t) => {
          const r = t.getBoundingClientRect();
          return inHero(r) && !over(r, benchR, DRIFT);
        });
        if (!rest.length) rest = tiles;
        pool = [
          rest.reduce((a, b) =>
            air(b.getBoundingClientRect()) > air(a.getBoundingClientRect()) ? b : a,
          ),
        ];
      }
      // nearest to the table wins — that is the whole point of the note — and
      // near-ties go to whichever of them keeps the most air around it
      const dist = (t: HTMLElement) => gapTo(t.getBoundingClientRect(), benchR);
      pool.sort(
        (a, b) =>
          dist(a) - dist(b) || air(b.getBoundingClientRect()) - air(a.getBoundingClientRect()),
      );
      const bd = dist(pool[0]);
      const near = pool.filter((t) => dist(t) <= bd + 40);
      const best = near.reduce((a, b) =>
        air(b.getBoundingClientRect()) > air(a.getBoundingClientRect()) ? b : a,
      );
      // a tile that already shows this talk wins ties generously, so the wall
      // lights one of its own frames when it can
      const own = pool.find((t) => t.dataset.vid === vid && dist(t) <= dist(best) * 1.3 + 40);
      if (own) return own;
      // every copy of that talk sits under the copy or the table: re-slot its
      // cover onto the chosen tile so the find is visible on the wall
      const g = GRID.find((x) => x.vid === vid);
      if (g) {
        best.dataset.vid = vid;
        best.querySelector("img")!.src = ASSETS + g.img;
      }
      return best;
    }

    /* the light table */
    function populateBench(q: CannedQuery, animate: boolean) {
      benchhead.current!.innerHTML =
        `<span class="${styles.label} ${styles.gold}">the light table</span>` +
        `<span class="${styles.label} ${styles.seen}">seen — ${esc(q.seen)}</span>` +
        `<span class="${styles.label} ${styles.r} ${styles.hideS}">src <span class="${styles.id}">${q.vid}</span> · tc ${q.tc}</span>`;
      const nf = frame(
        ASSETS + q.img,
        `Matched keyframe at ${q.at} of ${q.talk} — ${q.who}`,
        q,
        animate,
      );
      benchEl!.querySelector(`.${styles.frame}`)!.replaceWith(nf);
      nf.style.opacity = "1";
      const say = benchsay.current!;
      say.hidden = false;
      say.innerHTML =
        `<span class="${styles.label} ${styles.gold}">heard — spoken at ${q.saidTc}</span>` +
        `<p class="${styles.said}">${esc(q.said)}</p>` +
        `<div class="${styles.who}"><strong>${esc(q.who)}</strong><em>${esc(q.talk)}</em></div>`;
      const foot = benchfoot.current!;
      foot.hidden = false;
      foot.innerHTML =
        receipt(q.vid, q.t) +
        `<span class="${styles.label} ${styles.r} ${styles.hideS}">${esc(q.mode)} · ${esc(q.counts)}</span>`;
      benchEl!.classList.remove(styles.idle);
    }

    /* the ask cycle: type → scan → lift → land */
    const setStatus = (s: string, label: string) => {
      status.current!.dataset.s = s;
      status.current!.textContent = label;
    };
    async function typeIn(text: string, tk: number) {
      slugEl!.classList.add(styles.typing);
      qtext.current!.textContent = "";
      for (let i = 0; i < text.length; i++) {
        if (tk !== token) return false;
        qtext.current!.textContent = text.slice(0, i + 1);
        await sleep(30 + (text[i] === " " ? 16 : 0));
      }
      slugEl!.classList.remove(styles.typing);
      return true;
    }
    function paintChips(active: number) {
      chipsEl!.innerHTML = QUERIES.map(
        (q, j) =>
          `<button type="button" data-i="${j}" class="${j === active ? styles.on : ""}">${esc(q.label)}</button>`,
      ).join("");
    }
    function paintFinal(i: number) {
      const q = QUERIES[i];
      qtext.current!.textContent = q.q;
      slugEl!.classList.remove(styles.typing);
      wallEl!.classList.add(styles.searching);
      wallEl!.querySelectorAll(`.${styles.hit}`).forEach((t) => t.classList.remove(styles.hit));
      populateBench(q, false); // final geometry first, then pick
      const t = pickTile(q.vid);
      if (t) t.classList.add(styles.hit);
      setStatus("lifted", "lifted · " + q.at);
      paintChips(i);
      currentQ = i;
    }
    async function run(i: number) {
      const q = QUERIES[i];
      const tk = ++token;
      paintChips(i);
      currentQ = i;
      if (reduce) {
        paintFinal(i);
        return;
      }
      /* reset */
      wallEl!.classList.remove(styles.searching);
      wallEl!.querySelectorAll(`.${styles.hit}`).forEach((t) => t.classList.remove(styles.hit));
      benchEl!.classList.add(styles.idle);
      new Image().src = ASSETS + q.img; // warm the still
      setStatus("reading", "reading");
      if (!(await typeIn(q.q, tk))) return;
      if (tk !== token) return;
      setStatus("scanning", "scanning the wall");
      wallEl!.classList.add(styles.searching);
      // lay the table out at its final size (veiled) so the pick and the
      // lift's landing rect are measured against where things will be
      populateBench(q, false);
      benchEl!.classList.add(styles.veil);
      benchEl!.classList.add(styles.idle);
      await sleep(460);
      if (tk !== token) return;
      const tile = pickTile(q.vid);
      if (tile) tile.classList.add(styles.hit);
      await sleep(480);
      if (tk !== token) return;
      // the lift: the matched keyframe comes off the talk's tile and lands
      // on the table
      if (tile) {
        const from = tile.getBoundingClientRect();
        const to = benchEl!.querySelector(`.${styles.frame}`)!.getBoundingClientRect();
        // both rects are 16:9, so the flight is one composited transform:
        // the element is laid out at the landing size and scaled down onto
        // the tile. Nothing re-lays-out mid-flight, so nothing jitters.
        const s = from.width / to.width;
        const dx = from.left - to.left;
        const dy = from.top - to.top;
        const lift = el("div", styles.lift, `<img src="${ASSETS}${q.img}" alt="">`);
        lift.style.cssText =
          `left:${to.left}px;top:${to.top}px;width:${to.width}px;height:${to.height}px;` +
          `outline-width:${(2 / s).toFixed(2)}px;` +
          `transform:translate3d(${dx}px,${dy}px,0) scale(${s})`;
        document.body.append(lift);
        // beat one: it comes off the wall — a short, visible detach before the
        // travel, so the lift reads as a lift and not as a cut
        await sleep(40);
        if (tk !== token) {
          lift.remove();
          return;
        }
        lift.style.transition = "transform .24s ease-out,outline-width .24s ease-out";
        lift.style.transform = `translate3d(${dx - from.width * 0.04}px,${dy - from.height * 0.04}px,0) scale(${s * 1.08})`;
        await sleep(270);
        if (tk !== token) {
          lift.remove();
          return;
        }
        // beat two: the travel, on the stylesheet's .82s ease
        lift.style.transition = "";
        lift.style.transform = "translate3d(0,0,0) scale(1)";
        lift.style.outlineWidth = "2px";
        await sleep(860);
        if (tk !== token) {
          lift.remove();
          return;
        }
        populateBench(q, true);
        benchEl!.classList.remove(styles.veil);
        await sleep(60);
        lift.remove();
      } else {
        populateBench(q, true);
        benchEl!.classList.remove(styles.veil);
      }
      setStatus("lifted", "lifted · " + q.at);
    }

    buildWall();
    // ?still=1 freezes the gate too
    if (reduce && wallWrap.current) wallWrap.current.style.animation = "none";

    let resizeTimer: ReturnType<typeof setTimeout>;
    const onResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(buildWall, 200);
    };
    addEventListener("resize", onResize, { passive: true });

    paintChips(-1);
    const onChipClick = (e: MouseEvent) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("button[data-i]");
      if (b) void run(Number(b.dataset.i));
    };
    chipsEl.addEventListener("click", onChipClick);

    // the room is already working when you walk in
    if (reduce) paintFinal(0);
    else {
      const id = setTimeout(() => {
        timers.delete(id);
        if (currentQ < 0) void run(0);
      }, 700);
      timers.add(id);
    }

    return () => {
      disposed = true;
      token++;
      timers.forEach(clearTimeout);
      timers.clear();
      clearTimeout(resizeTimer);
      removeEventListener("resize", onResize);
      chipsEl.removeEventListener("click", onChipClick);
      document.querySelectorAll(`.${styles.lift}`).forEach((n) => n.remove());
    };
  }, []);

  return (
    <section className={styles.hero} id="top" ref={hero}>
      <div className={styles.wallwrap} aria-hidden="true" ref={wallWrap}>
        <div className={styles.wall} ref={wall} />
      </div>
      <div className={styles.scrim} aria-hidden="true" />

      <div className={styles.heroin} ref={heroin}>
        <div className={styles.herocopy}>
          <div className={styles.kick} ref={kick}>
            <s />
            <span className={`${styles.label} ${styles.gold}`}>
              the knowledge of the builders, on tap
            </span>
          </div>
          <h1 className={styles.h1} ref={headline}>
            Builders talk.
            <br />
            Your agent <em>listens.</em>
          </h1>
          <p className={styles.lede} ref={lede}>
            Behind this page is every talk AI Engineer published in 2026, more conference than
            anyone has time for. Ask any AI engineering question, your agent answers from what was
            spoken and what was shown, <b>down to the second.</b>
          </p>
          <div className={styles.slug} ref={slug}>
            <span className={styles.ic}>
              <svg viewBox="0 0 12 12" aria-hidden="true">
                <path
                  d="M3.6 1.8 L8.2 6 L3.6 10.2"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                />
              </svg>
            </span>
            <span className={styles.q}>
              <span ref={qtext} />
              <span className={styles.caret} />
            </span>
            <span className={styles.st} data-s="ready" ref={status}>
              ready
            </span>
          </div>
          <div className={styles.ctarow}>
            {/* The exit into the live product. The hero's canned cycle is this
                page's own show and runs on its own; this is the one control
                that leaves it, so it is a real link — middle-click works, and
                it is the same affordance whether or not the script ran. */}
            <Link className={styles.cta} href="/demo" ref={cta}>
              Open the demo
              <svg
                viewBox="0 0 12 12"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="square"
                aria-hidden="true"
              >
                <path d="M2.6 6h6.6M6.4 3.2 9.2 6l-2.8 2.8" />
              </svg>
            </Link>
            <div className={styles.chips} ref={chips} role="group" aria-label="Example questions" />
          </div>
        </div>

        <div className={`${styles.bench} ${styles.idle}`} ref={bench} aria-live="polite">
          <div className={styles.benchhead} ref={benchhead}>
            <span className={`${styles.label} ${styles.gold}`}>the light table</span>
            <span className={`${styles.label} ${styles.r}`}>nothing lifted yet</span>
          </div>
          <figure className={`${styles.frame} ${styles.benchSlot}`} />
          <div className={styles.benchsay} ref={benchsay} hidden />
          <div className={styles.benchfoot} ref={benchfoot} hidden />
        </div>
      </div>

      <div className={styles.herofoot} ref={herofoot}>
        <div className={`${styles.wrap} ${styles.in}`}>
          <span className={styles.label}>the videos belong to the people who made them</span>
        </div>
      </div>
    </section>
  );
}
