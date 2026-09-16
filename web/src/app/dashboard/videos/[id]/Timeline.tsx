"use client";

import { useMemo, useRef, useState, type RefObject } from "react";
import type { Shot } from "@/lib/dashboard/schemas";
import { clock, count } from "@/lib/format";
import { DashLink, Panel, ui } from "@/components/dashboard/kit/ui";
import styles from "./detail.module.css";
import { frameLink } from "./query";

/** A new shot's still waits this long, so a sweep is not one request per bar. */
const SETTLE_MS = 70;

const STEPS: Record<string, number | undefined> = {
  ArrowRight: 1,
  ArrowLeft: -1,
  ArrowDown: 1,
  ArrowUp: -1,
};

type Bar = { shot: Shot; left: number; width: number; right: number };
type Preview = { shot: Shot; left: number; below: boolean };

function barElement(target: EventTarget | null): Element | null {
  return target instanceof Element ? target.closest("[data-shot]") : null;
}

/**
 * One bar per shot across the runtime, at percentages computed from the
 * payload's seconds (§20). A bar has a CSS minimum width because its position
 * is the fact, and links to the strip page holding its first keyframe.
 */
export function Timeline({
  shots,
  capped,
  runtime,
  framePage,
  search,
  videoId,
  onSelect,
}: {
  shots: Shot[];
  capped: boolean;
  runtime: number;
  framePage: number;
  search: string;
  videoId: string;
  /** Mark a frame already on this strip page; `false` lets the link navigate. */
  onSelect: (ord: number) => boolean;
}) {
  const band = useRef<HTMLOListElement>(null);
  const scrub = useRef<HTMLDivElement>(null);

  const bars = useMemo<Bar[]>(() => {
    // A video with no recorded duration is drawn against its furthest shot.
    const span = runtime > 0 ? runtime : Math.max(...shots.map((s) => s.end_s), 1);
    return shots.map((shot) => {
      const left = (100 * Math.min(shot.start_s, span)) / span;
      const width = (100 * Math.max(shot.end_s - shot.start_s, 0)) / span || 0.05;
      return { shot, left, width, right: left + width };
    });
  }, [shots, runtime]);

  const { preview, still, show, hide } = useShotPreview(band, scrub);

  /** The bar under the pointer; in a gap between bars (the CSS minimum width
   *  makes rendered bars wider than their share), the nearest one. */
  function barAt(target: EventTarget | null, fraction: number): Shot | null {
    const hit = barElement(target);
    const id = hit ? Number(hit.getAttribute("data-shot")) : NaN;
    const named = bars.find((entry) => entry.shot.shot_id === id);
    if (named) return named.shot;

    const at = fraction * 100;
    let nearest: Shot | null = null;
    let distance = Infinity;
    for (const entry of bars) {
      if (at >= entry.left && at <= entry.right) return entry.shot;
      const gap = at < entry.left ? entry.left - at : at - entry.right;
      if (gap < distance) {
        distance = gap;
        nearest = entry.shot;
      }
    }
    return nearest;
  }

  if (!shots.length) {
    return (
      <Panel id="timeline" title="Scene timeline">
        <div className={styles.empty}>
          <p className={styles.emptyLead}>
            No keyframes were captured, so this video has no shots.
          </p>
          <p className={ui.emptyNote}>
            The <code>keyframe</code> row in Provenance says why.
          </p>
        </div>
      </Panel>
    );
  }

  const page = Math.max(framePage, 1);

  return (
    <section className={styles.timeband} aria-labelledby="timeline">
      <h2 className={ui.srOnly} id="timeline">
        Scene timeline
      </h2>
      {/* Arrows move focus between the bars' own links, so Enter always
          follows one. */}
      <ol
        aria-label="Shots across the runtime"
        className={styles.timeline}
        onBlur={(event) => {
          if (!band.current?.contains(event.relatedTarget)) hide();
        }}
        onFocus={(event) => {
          const bar = barElement(event.target);
          const bandBox = band.current?.getBoundingClientRect();
          if (!bar || !bandBox) return;
          const box = bar.getBoundingClientRect();
          show(barAt(bar, 0), box.left - bandBox.left + box.width / 2);
        }}
        onKeyDown={(event) => {
          if (event.altKey || event.ctrlKey || event.metaKey) return;
          if (event.key === "Escape") return hide();
          const bar = barElement(event.target);
          const items = Array.from(band.current?.children ?? []);
          const index = bar ? items.indexOf(bar) : -1;
          if (index < 0) return;
          const step = STEPS[event.key];
          const next =
            event.key === "Home"
              ? items[0]
              : event.key === "End"
                ? items[items.length - 1]
                : step
                  ? items[Math.min(Math.max(index + step, 0), items.length - 1)]
                  : undefined;
          if (!next) return;
          event.preventDefault();
          next.querySelector("a")?.focus();
        }}
        onPointerLeave={hide}
        onPointerMove={(event) => {
          // On touch the bar's link is the whole interaction.
          if (event.pointerType === "touch") return;
          const bandBox = band.current?.getBoundingClientRect();
          if (!bandBox?.width) return;
          const x = event.clientX - bandBox.left;
          show(barAt(event.target, x / bandBox.width), x);
        }}
        ref={band}
      >
        {bars.map(({ shot, left, width }) => (
          <li
            className={`${styles.shotbar} ${shot.kept === 0 ? styles.dedup : ""}`}
            data-shot={shot.shot_id}
            key={shot.shot_id}
            style={{ left: `${left}%`, width: `${width}%` }}
          >
            <DashLink
              href={frameLink(
                search,
                videoId,
                Math.floor(shot.first_ord / page) * page,
                shot.first_ord,
              )}
              onClick={(event) => {
                // A modified click asks for a new tab.
                if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                if (onSelect(shot.first_ord)) event.preventDefault();
              }}
            >
              <span className={ui.srOnly}>
                {`Shot ${shot.shot_id}, ${clock(shot.start_s)} to ${clock(shot.end_s)}, ${shot.kept} of ${shot.frames} keyframes kept`}
              </span>
            </DashLink>
          </li>
        ))}
      </ol>
      {/* Out of the a11y tree: every string is already in the bar's label. */}
      <div
        aria-hidden="true"
        className={`${styles.scrubpreview} ${preview ? "" : styles.isOff} ${preview?.below ? styles.isBelow : ""}`}
        ref={scrub}
        style={{ left: `${preview?.left ?? 0}px` }}
      >
        <span className={styles.scrubshot}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {still ? <img alt="" decoding="async" height={108} src={still} width={192} /> : null}
        </span>
        <span className={styles.scrubspan}>
          {preview ? `${clock(preview.shot.start_s)}–${clock(preview.shot.end_s)}` : ""}
        </span>
        <span className={styles.scrubmeta}>
          {preview
            ? `shot ${preview.shot.shot_id} · ${preview.shot.kept}/${preview.shot.frames} kept`
            : ""}
        </span>
      </div>
      <p className={styles.scale} aria-hidden="true">
        {[0, 25, 50, 75, 100].map((q) => (
          <span className={styles.tick} key={q} style={{ left: `${q}%` }}>
            {clock((runtime * q) / 100)}
          </span>
        ))}
      </p>
      <div className={styles.timebandFoot}>
        <p className={styles.panelNote}>
          {count(shots.length)} shot(s){capped ? ", capped: the video has more" : ""}.
        </p>
        <p className={styles.legend}>
          <span>
            <span className={`${styles.swatch} ${styles.swatchKept}`} aria-hidden="true" />
            keyframes kept
          </span>
          <span>
            <span className={`${styles.swatch} ${styles.swatchDedup}`} aria-hidden="true" />
            every frame deduplicated
          </span>
        </p>
      </div>
    </section>
  );
}

/** The floating preview: where it sits, and the still it shows once the
 *  pointer has settled on a shot. */
function useShotPreview(
  band: RefObject<HTMLOListElement | null>,
  scrub: RefObject<HTMLDivElement | null>,
) {
  const showing = useRef<number | null>(null);
  const settle = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const fetched = useRef(new Set<string>());
  const [preview, setPreview] = useState<Preview | null>(null);
  const [still, setStill] = useState<string | null>(null);

  function hide() {
    clearTimeout(settle.current);
    showing.current = null;
    setPreview(null);
    setStill(null);
  }

  /** Clamp the preview inside the band, above the bars unless there is no room
   *  above them in the viewport. */
  function show(shot: Shot | null, x: number) {
    const bandBox = band.current?.getBoundingClientRect();
    const box = scrub.current;
    if (!shot || !bandBox || !box) return hide();

    const half = box.offsetWidth / 2;
    const left = Math.round(
      bandBox.width <= box.offsetWidth
        ? bandBox.width / 2
        : Math.min(Math.max(x, half), bandBox.width - half),
    );
    const below = bandBox.top < box.offsetHeight + 16;
    // Most pointer moves stay on one shot at one clamped spot: no render.
    setPreview((last) =>
      last?.shot === shot && last.left === left && last.below === below
        ? last
        : { shot, left, below },
    );

    if (showing.current === shot.shot_id) return;
    showing.current = shot.shot_id;
    clearTimeout(settle.current);
    // A stale still under a new caption is worse than an empty box.
    if (!shot.preview) return setStill(null);
    if (fetched.current.has(shot.preview)) return setStill(shot.preview);
    setStill(null);
    const url = shot.preview;
    settle.current = setTimeout(() => {
      fetched.current.add(url);
      setStill(url);
    }, SETTLE_MS);
  }

  return { preview, still, show, hide };
}
