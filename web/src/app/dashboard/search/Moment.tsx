"use client";

import type { Hit } from "@/lib/dashboard/schemas";
import { clock, DASH } from "@/lib/format";
import type { Shot } from "../FrameOverlay";
import { DashLink, Sep } from "../kit/ui";
import { evidenceOf, highlight, insideLink, receiptOf } from "./parts";
import styles from "./search.module.css";

// The dashboard's own widths from `variants.py`, built on this origin rather
// than from the payload's `PUBLIC_URL` URLs, which break behind a tunnel (§14.2).
const STRIP_WIDTH = 192;
const LIGHTBOX_WIDTH = 1280;
const THUMB_QUALITY = 70;

function frameUrl(frameId: string, width: number): string {
  return `/frames/${encodeURIComponent(frameId)}.jpg?w=${width}&q=${THUMB_QUALITY}`;
}

const BADGE_CLASS: Record<string, string> = {
  spoken: styles.badgeSpoken,
  screen: styles.badgeScreen,
  frame: styles.badgeFrame,
  other: styles.badgeOther,
};

const SNIPPET_CLASS: Record<string, string> = {
  spoken: styles.isSpoken,
  screen: styles.isScreen,
  frame: styles.isFrame,
  mixed: "",
  other: "",
};

/** A key for a moment: its leg, the second it matched, and its frame. */
export function momentKey(hit: Hit): string {
  return `${hit.source}-${hit.match_start ?? hit.start}-${hit.frame_id ?? ""}`;
}

/** One ranked hit: the frame (which opens in place), the moment, the receipt. */
export function Moment({
  hit,
  onOpen,
  query,
}: {
  hit: Hit;
  onOpen: (shot: Shot) => void;
  query: string;
}) {
  const evidence = evidenceOf(hit.source);
  const inside = insideLink(hit);
  const receipt = receiptOf(hit.link);
  // `match_start`, not `timestamp`: the cue that matched, not the segment's
  // opening (§14.2).
  const at = hit.match_start === null ? DASH : clock(hit.match_start);
  const runs = highlight(hit.text, query);

  return (
    <li className={styles.hit}>
      {hit.frame_id ? (
        <button
          aria-label={`Enlarge the frame at ${at}`}
          className={styles.shot}
          onClick={() =>
            onOpen({
              alt: `Keyframe at ${at}`,
              caption: `${hit.frame_id} · ${at} · ${hit.title}`,
              frameId: hit.frame_id as string,
              large: frameUrl(hit.frame_id as string, LIGHTBOX_WIDTH),
              link: receipt?.href ?? null,
            })
          }
          type="button"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            alt=""
            decoding="async"
            height={72}
            loading="lazy"
            src={frameUrl(hit.frame_id, STRIP_WIDTH)}
            width={128}
          />
        </button>
      ) : (
        <span aria-hidden="true" className={`${styles.shot} ${styles.shotEmpty}`}>
          {evidence.pills[0]?.label ?? "video"}
        </span>
      )}

      <div className={styles.body}>
        <p className={styles.title}>
          {inside ? <DashLink href={inside}>{hit.title}</DashLink> : hit.title}
        </p>
        <p className={styles.meta}>
          <span className={styles.badges} title={`source=${evidence.key}`}>
            {evidence.pills.map((pill) => (
              <span className={`${styles.badge} ${BADGE_CLASS[pill.kind]}`} key={pill.label}>
                {pill.label}
              </span>
            ))}
          </span>
          {/* Every row's own channel: two hits of one video may disagree. */}
          <span className={styles.where}>{hit.channel || "unknown"}</span>
          <Sep />
          {inside ? (
            <DashLink className={styles.at} href={inside}>
              {at}
            </DashLink>
          ) : (
            <span className={styles.at}>{at}</span>
          )}
        </p>

        {runs.length ? (
          <p className={`${styles.snippet} ${SNIPPET_CLASS[evidence.kind]}`}>
            {runs.map((run, index) =>
              run.hit ? <mark key={index}>{run.text}</mark> : <span key={index}>{run.text}</span>,
            )}
          </p>
        ) : evidence.kind === "frame" ? (
          // `text` is `null` on an imagery-only frame hit (§14.2).
          <p className={`${styles.snippet} ${styles.isFrame}`}>visual match, no text hit</p>
        ) : null}
      </div>

      {receipt ? (
        <a className={styles.receipt} href={receipt.href} rel="noopener noreferrer" target="_blank">
          {receipt.label} ↗
        </a>
      ) : null}
    </li>
  );
}
