/* eslint-disable @next/next/no-img-element */
import type { CSSProperties } from "react";
import type { Box } from "@/landing/show";
import styles from "./landing.module.css";

// The frame and its detections — boxes exactly where `ocr_lines` put them.
// A plain <img>: these are the packaged stills under /public/landing, already
// sized, and the wall's whole point is that the frames are the evidence.
export function EvidenceFrame({
  src,
  alt,
  boxes,
  acquiring = false,
}: {
  src: string;
  alt: string;
  boxes: Box[];
  /** Play the acquire animation on the boxes — the machine reading the frame. */
  acquiring?: boolean;
}) {
  return (
    <figure className={`${styles.frame} ${acquiring ? styles.acq : ""}`}>
      <img src={src} alt={alt} loading="eager" decoding="async" />
      {boxes.map((box, i) => (
        <div
          key={i}
          className={`${styles.det} ${box.on ? styles.on : ""}`}
          style={boxStyle(box, i)}
        >
          {box.tab ? (
            <span className={styles.tab}>
              {box.tab}
              {box.conf ? <i>{box.conf}</i> : null}
            </span>
          ) : null}
        </div>
      ))}
    </figure>
  );
}

/** Normalised [x0, y0, x1, y1] → the four custom properties `.det` reads. */
function boxStyle(box: Box, i: number): CSSProperties {
  const [x0, y0, x1, y1] = box.b;
  return {
    "--x": `${(x0 * 100).toFixed(2)}%`,
    "--y": `${(y0 * 100).toFixed(2)}%`,
    "--w": `${((x1 - x0) * 100).toFixed(2)}%`,
    "--h": `${((y1 - y0) * 100).toFixed(2)}%`,
    "--i": i,
  } as CSSProperties;
}
