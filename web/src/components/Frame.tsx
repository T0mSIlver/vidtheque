import Image from "next/image";
import styles from "./Frame.module.css";

// A keyframe: a fixed 16:9 box on `--black` with explicit dimensions, so the
// page never shifts when the image lands (CLS 0 is a shipped property).
//
// `unoptimized`: the API already serves sized, cached variants (`?w=320`),
// so routing them through Next's image optimizer would re-encode a JPEG that
// is already the right size and put a second cache in front of the first.
// The size set is the server's; the page asks for one of its widths.
export function Frame({
  src,
  alt,
  width = 320,
  priority = false,
}: {
  src: string | null;
  alt: string;
  width?: 320 | 960;
  priority?: boolean;
}) {
  const height = Math.round((width * 9) / 16);
  return (
    <span className={styles.box} style={{ aspectRatio: "16 / 9" }}>
      {src ? (
        <Image
          src={src}
          alt={alt}
          width={width}
          height={height}
          unoptimized
          priority={priority}
          className={styles.img}
        />
      ) : null}
    </span>
  );
}
