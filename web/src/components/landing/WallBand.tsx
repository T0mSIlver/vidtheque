/* eslint-disable @next/next/no-img-element */
import { ASSETS, GRID_BY_VID } from "@/landing/corpus";
import { hms, num, tcToSeconds } from "@/lib/format/landing";
import { TILES } from "@/landing/show";
import { BandPacer } from "./BandPacer";
import styles from "./landing.module.css";

// Each row renders twice so the loop is seamless.
const ROWS = 4;

export function WallBand() {
  const rows: [string, string, string][][] = Array.from({ length: ROWS }, () => []);
  TILES.forEach((tile, i) => rows[i % ROWS].push(tile));

  return (
    <BandPacer>
      {rows.map((list, r) => (
        <div className={styles.wrow} key={r}>
          <div className={`${styles.wtrack} ${r % 2 ? styles.rev : ""}`}>
            {list.map((tile, i) => (
              <Tile key={`a${i}`} tile={tile} />
            ))}
            {list.map((tile, i) => (
              <Tile key={`b${i}`} tile={tile} />
            ))}
          </div>
        </div>
      ))}
    </BandPacer>
  );
}

function Tile({ tile }: { tile: [string, string, string] }) {
  const [file, id, tc] = tile;
  const g = GRID_BY_VID[id];
  const title =
    (g
      ? `${g.title} — ${g.speaker}${g.org ? ` (${g.org})` : ""} · ${hms(g.dur)} · ` +
        `${num(g.cues)} spoken lines · ${num(g.ocr)} lines read off the screen · `
      : "") + `${id} · ${tc}`;
  return (
    <figure className={styles.bwt} title={title}>
      <a href={`https://youtu.be/${id}?t=${tcToSeconds(tc)}`} target="_blank" rel="noopener">
        <img
          src={`${ASSETS}wall/${file}`}
          alt={g ? `${g.title} — ${g.speaker}` : id}
          loading="lazy"
          decoding="async"
          width={480}
          height={270}
        />
        <span className={styles.bslug}>
          <b>{id}</b>
          <span>{tc}</span>
        </span>
      </a>
    </figure>
  );
}
