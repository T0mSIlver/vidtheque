"use client";

import { useCallback, useMemo, useRef, useState, type RefObject } from "react";
import { Fold } from "@/components/dashboard/kit/Fold";
import type { Shot } from "@/lib/dashboard/schemas";
import { clock, count } from "@/lib/format";
import { DashLink, Panel, ui } from "@/components/dashboard/kit/ui";
import styles from "./detail.module.css";
import { frameLink } from "./query";

/** A segment's side padding, as the stylesheet sets it: a title is drawn
 *  only when it fits whole between the two. */
const TITLE_PAD_PX = 16;
/** Narrower than a title, a segment shows its number, as the line above and
 *  the list do; narrower than this, its shade alone. */
const NUMBER_MIN_PX = 20;

type Label = "title" | "number" | null;

/** A new shot's still waits this long, so a sweep is not one request per bar. */
const SETTLE_MS = 70;

const STEPS: Record<string, number | undefined> = {
  ArrowRight: 1,
  ArrowLeft: -1,
  ArrowDown: 1,
  ArrowUp: -1,
};

type Bar = { shot: Shot; left: number; width: number; right: number };
type Chapter = { start_s: number; title: string };
type Preview = { shot: Shot; left: number; below: boolean };

function barElement(target: EventTarget | null): Element | null {
  return target instanceof Element ? target.closest("[data-shot]") : null;
}

/** The chapter holding a second: the last one to start at or before it. */
function chapterIndex(chapters: Chapter[], second: number): number | null {
  let found: number | null = null;
  for (const [index, chapter] of chapters.entries()) {
    if (chapter.start_s <= second) found = index;
    else break;
  }
  return found;
}

/**
 * One bar per shot across the runtime, at percentages computed from the
 * payload's seconds (§20), under the chapters on the same scale (§28.7). A bar
 * has a CSS minimum width because its position is the fact, and links to the
 * strip page holding its first keyframe.
 */
export function Timeline({
  shots,
  capped,
  kept,
  chapters,
  runtime,
  framePage,
  search,
  videoId,
  onSelect,
}: {
  shots: Shot[];
  capped: boolean;
  /** The video's kept keyframes, the count under the band. */
  kept: number;
  chapters: Chapter[];
  runtime: number;
  framePage: number;
  search: string;
  videoId: string;
  /** Mark a frame already on this strip page; `false` lets the link navigate. */
  onSelect: (ord: number) => boolean;
}) {
  const band = useRef<HTMLOListElement>(null);
  const scrub = useRef<HTMLDivElement>(null);
  // A video with no recorded duration is drawn against its furthest shot.
  const span = runtime > 0 ? runtime : Math.max(...shots.map((s) => s.end_s), 1);
  const bars = useMemo<Bar[]>(() => {
    return shots.map((shot) => {
      const left = (100 * Math.min(shot.start_s, span)) / span;
      const width = (100 * Math.max(shot.end_s - shot.start_s, 0)) / span || 0.05;
      return { shot, left, width, right: left + width };
    });
  }, [shots, span]);

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
      <Panel id="timeline" title="Timeline">
        <p className={styles.emptyLead}>No keyframes.</p>
      </Panel>
    );
  }

  const page = Math.max(framePage, 1);

  return (
    <section className={styles.timeband} aria-labelledby="timeline">
      <h2 className={ui.srOnly} id="timeline">
        Timeline
      </h2>
      {chapters.length ? (
        <Chapters
          chapters={chapters}
          span={span}
          videoId={videoId}
          under={preview ? chapterIndex(chapters, preview.shot.start_s) : null}
        />
      ) : null}
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
            ? (chapters[chapterIndex(chapters, preview.shot.start_s) ?? -1]?.title ?? "")
            : ""}
        </span>
      </div>
      <p className={styles.scale} aria-hidden="true">
        {[0, 25, 50, 75, 100].map((q) => (
          <span className={styles.tick} key={q} style={{ left: `${q}%` }}>
            {clock((span * q) / 100)}
          </span>
        ))}
      </p>
      <div className={styles.timebandFoot}>
        <p className={styles.panelNote}>
          <span className={ui.mono}>{count(kept)}</span> keyframes
          {capped ? <span className={styles.capped}> · timeline capped</span> : null}
        </p>
        <p className={styles.legend}>
          <span>
            <span className={`${styles.swatch} ${styles.swatchKept}`} aria-hidden="true" />
            kept
          </span>
          <span>
            <span className={`${styles.swatch} ${styles.swatchDedup}`} aria-hidden="true" />
            duplicates only
          </span>
        </p>
      </div>
      {chapters.length ? (
        <div className={styles.chapterfold}>
          <ChapterList chapters={chapters} videoId={videoId} />
        </div>
      ) : null}
    </section>
  );
}

/** A title's drawn width, in the font the band sets; an estimate where there
 *  is no canvas to measure with (a test's DOM). */
function measurer(element: Element): (text: string) => number {
  const style = getComputedStyle(element);
  const context =
    typeof OffscreenCanvas === "undefined" ? null : new OffscreenCanvas(1, 1).getContext("2d");
  if (!context) {
    const size = parseFloat(style.fontSize) || 14;
    return (text) => text.length * size * 0.6;
  }
  context.font = style.font || `${style.fontSize} ${style.fontFamily}`;
  return (text) => context.measureText(text).width;
}

/**
 * The chapters on the shots' scale (§28.7). Every segment is drawn, shaded in
 * turn, so a chapter's extent shows without its title; a title is drawn only
 * where it fits whole, and its number where that does. The line above the band names the chapter under the
 * pointer, the keyboard, a tap, or the shot preview, with its start linked;
 * the full list folds under the band.
 */
function Chapters({
  chapters,
  span,
  videoId,
  under,
}: {
  chapters: Chapter[];
  span: number;
  videoId: string;
  /** The chapter of the shot the preview shows. */
  under: number | null;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [labels, setLabels] = useState<Label[]>([]);

  const segments = useMemo(
    () =>
      chapters.map((chapter, index) => {
        const end = chapters[index + 1]?.start_s ?? span;
        const left = (100 * Math.min(chapter.start_s, span)) / span;
        const width = (100 * Math.max(Math.min(end, span) - chapter.start_s, 0)) / span;
        return { chapter, left, width };
      }),
    [chapters, span],
  );

  // A callback ref: the band is measured as it mounts and as it resizes.
  const observer = useRef<ResizeObserver | null>(null);
  const band = useCallback(
    (element: HTMLOListElement | null) => {
      observer.current?.disconnect();
      observer.current = null;
      if (!element) return;
      const width = measurer(element);
      const fit = () => {
        const px = element.clientWidth;
        setLabels(
          segments.map(({ chapter, width: share }): Label => {
            const room = (share / 100) * px;
            if (px > 0 && width(chapter.title) + TITLE_PAD_PX <= room) return "title";
            return room >= NUMBER_MIN_PX ? "number" : null;
          }),
        );
      };
      fit();
      if (typeof ResizeObserver === "undefined") return;
      observer.current = new ResizeObserver(fit);
      observer.current.observe(element);
    },
    [segments],
  );

  const shown = hover ?? under ?? picked;
  const active = shown === null ? null : chapters[shown];

  return (
    <>
      <p className={styles.chapternow}>
        {active && shown !== null ? (
          <>
            <span className={styles.chapterord}>
              {shown + 1}/{chapters.length}
            </span>
            {/* The chapter's own start, not a quoted moment (§3.6). */}
            <a
              className={styles.at}
              href={`https://youtu.be/${encodeURIComponent(videoId)}?t=${Math.floor(active.start_s)}`}
              rel="noopener noreferrer"
              target="_blank"
            >
              {clock(active.start_s)}
            </a>
            <span className={styles.chaptertitle}>{active.title}</span>
          </>
        ) : (
          <span className={styles.muted}>{count(chapters.length)} chapters</span>
        )}
      </p>
      <ol
        aria-label="Chapters"
        className={styles.chapterband}
        onPointerLeave={() => setHover(null)}
        ref={band}
      >
        {segments.map(({ chapter, left, width }, index) => (
          <li
            className={`${styles.chapterseg} ${index === shown ? styles.isOn : ""}`}
            key={`${chapter.start_s}-${chapter.title}`}
            style={{ left: `${left}%`, width: `${width}%` }}
          >
            {/* A tap names the chapter above the band; its link is there. */}
            <button
              aria-pressed={index === picked}
              className={styles.chapterbtn}
              onBlur={() => setHover(null)}
              onClick={() => setPicked(index === picked ? null : index)}
              onFocus={() => setHover(index)}
              onPointerEnter={(event) => {
                if (event.pointerType !== "touch") setHover(index);
              }}
              type="button"
            >
              {labels[index] === "title" ? (
                chapter.title
              ) : (
                <>
                  {labels[index] === "number" ? (
                    <span aria-hidden="true" className={styles.chapternum}>
                      {index + 1}
                    </span>
                  ) : null}
                  <span className={ui.srOnly}>{chapter.title}</span>
                </>
              )}
            </button>
          </li>
        ))}
      </ol>
    </>
  );
}

/** Every chapter, its start linked: the band's index, for a phone's thumb. */
function ChapterList({ chapters, videoId }: { chapters: Chapter[]; videoId: string }) {
  return (
    <Fold label="chapters">
      <ol className={styles.chapterlist}>
        {chapters.map((chapter, index) => (
          <li className={styles.chapteritem} key={`${chapter.start_s}-${chapter.title}`}>
            <span className={styles.chapterord}>{index + 1}</span>
            <a
              className={styles.at}
              href={`https://youtu.be/${encodeURIComponent(videoId)}?t=${Math.floor(chapter.start_s)}`}
              rel="noopener noreferrer"
              target="_blank"
            >
              {clock(chapter.start_s)}
            </a>
            <span>{chapter.title}</span>
          </li>
        ))}
      </ol>
    </Fold>
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
