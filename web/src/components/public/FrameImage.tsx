"use client";

import Image from "next/image";
import { useState } from "react";
import styles from "./Frame.module.css";

// `unoptimized`: the API already serves sized, cached variants (`?w=320`).
export function FrameImage({
  src,
  alt,
  label,
  width,
  priority,
}: {
  src: string;
  alt: string;
  label?: string;
  width: 320 | 960;
  priority: boolean;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className={styles.fallback} aria-hidden="true">
        {label ?? "video"}
      </span>
    );
  }
  return (
    <Image
      src={src}
      alt={alt}
      width={width}
      height={Math.round((width * 9) / 16)}
      unoptimized
      priority={priority}
      onError={() => setFailed(true)}
      className={styles.img}
    />
  );
}
