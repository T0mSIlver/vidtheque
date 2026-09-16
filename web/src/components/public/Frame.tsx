import { FrameImage } from "./FrameImage";
import { FrameLightbox } from "./FrameLightbox";
import styles from "./Frame.module.css";

// A keyframe in a fixed 16:9 box, so nothing shifts when the bytes land.
// Server-safe: only the load-failure fallback and the lightbox are client.
export function Frame({
  src,
  alt,
  label,
  width = 320,
  priority = false,
}: {
  src: string | null;
  alt: string;
  /** What the placeholder prints instead of a frame (`lib/api/group`'s `channelWord`). */
  label?: string;
  width?: 320 | 960;
  priority?: boolean;
}) {
  if (!src) {
    return (
      <span className={`${styles.box} ${styles.placeholder}`} aria-hidden="true">
        {label ?? "video"}
      </span>
    );
  }
  return (
    <span className={styles.box}>
      {/* Keyed on the source, so a different frame gets a fresh attempt. */}
      <FrameImage key={src} src={src} alt={alt} label={label} width={width} priority={priority} />
    </span>
  );
}

/** What the lightbox needs to know about the moment it is enlarging. */
export interface Shot {
  thumb: string | null;
  thumb_large: string | null;
  title: string;
  channel: string;
  video_id: string;
  timestamp: string;
  link: string | null;
}

/** A thumbnail that opens the frame at `thumb_large` (demo-site.md §6.4). */
export function FrameShot({ shot, alt, label }: { shot: Shot; alt: string; label?: string }) {
  const large = shot.thumb_large ?? shot.thumb;
  if (!large) return <Frame src={shot.thumb} alt={alt} label={label} />;
  return (
    <FrameLightbox shot={shot} large={large}>
      {/* The button owns the accessible name, so the image is decorative. */}
      <Frame src={shot.thumb} alt="" label={label} />
    </FrameLightbox>
  );
}
