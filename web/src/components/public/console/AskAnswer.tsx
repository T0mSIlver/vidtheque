import { Fragment, useEffect, useState } from "react";
import { Frame, FrameShot } from "@/components/public/Frame";
import { Receipt } from "@/components/public/Receipt";
import type { AskAnswer, Citation, EditionTalk } from "@/lib/api/schemas";
import { labelCitation } from "@/lib/api/edition";
import { badgeWords, channelWord, presentationOf } from "@/lib/api/group";
import styles from "./console.module.css";

type Card = { c: Citation; x: number; y: number; above: boolean };

/** The answer, then the moments it cites. A marker shows its moment on hover. */
export function Answer({ answer, talks }: { answer: AskAnswer; talks: EditionTalk[] }) {
  const citations = answer.citations.map((citation) => labelCitation(citation, talks));
  const byNumber = new Map(citations.map((c) => [c.n, c]));
  const [card, setCard] = useState<Card | null>(null);
  // Fixed to the viewport, so the panel's clip cannot cut it: under the marker,
  // or over it when the room below is short. A scroll would strand it, and a
  // touch screen has no hover to end it, so a tap just follows the link.
  const point = (c: Citation, el: HTMLElement | null) => {
    if (window.matchMedia?.("(hover: none)").matches) return setCard(null);
    // Leaving one marker must not drop the card another one just raised.
    if (!el) return setCard((shown) => (shown?.c.n === c.n ? null : shown));
    const r = el.getBoundingClientRect();
    const x = Math.min(Math.max(12, r.left + r.width / 2 - 160), window.innerWidth - 332);
    const below = window.innerHeight - r.bottom;
    const above = below < 360 && r.top > below;
    setCard({ c, x, y: above ? window.innerHeight - r.top + 8 : r.bottom + 8, above });
  };
  useEffect(() => {
    if (!card) return;
    const drop = () => setCard(null);
    // Captured, so a scroll inside any scrolling ancestor drops it too.
    const opts = { capture: true, passive: true, once: true };
    window.addEventListener("scroll", drop, opts);
    return () => window.removeEventListener("scroll", drop, opts);
  }, [card]);
  return (
    <>
      <div className={`${styles.prose} ${styles.landed}`}>
        {answer.answer.split(/\n{2,}/).map((para, i) => (
          <p key={i} className={styles.para}>
            <Cited text={para} byNumber={byNumber} point={point} />
          </p>
        ))}
      </div>
      {card ? (
        <span
          className={styles.citeCard}
          style={card.above ? { left: card.x, bottom: card.y } : { left: card.x, top: card.y }}
          aria-hidden
        >
          <Frame src={card.c.thumb} alt="" label={channelWord(card.c.source ?? "")} />
          <span className={styles.citeCardTitle}>{card.c.title}</span>
          <span className={styles.citeCardMeta}>
            {card.c.channel} · <span className={styles.mono}>{card.c.timestamp}</span>
          </span>
          {card.c.text ? <span className={styles.citeCardText}>{card.c.text}</span> : null}
        </span>
      ) : null}
      {citations.length > 0 ? (
        <div className={`${styles.sources} ${styles.landedLate}`}>
          <h2 className={styles.label}>Sources</h2>
          <ol>
            {citations.map((c) => (
              <Source key={c.n} c={c} />
            ))}
          </ol>
        </div>
      ) : null}
      {answer.model ? (
        <p className={`${styles.model} ${styles.landedLate}`}>model · {answer.model}</p>
      ) : null}
    </>
  );
}

// `[n]` becomes a link into the moment it cites; a marker naming nothing stays
// text rather than becoming a dead link.
function Cited({
  text,
  byNumber,
  point,
}: {
  text: string;
  byNumber: Map<number, Citation>;
  point: (c: Citation, el: HTMLElement | null) => void;
}) {
  const out: React.ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(/\[(\d{1,2})\]/g)) {
    if (match.index > cursor) out.push(text.slice(cursor, match.index));
    const n = Number(match[1]);
    const cited = byNumber.get(n);
    out.push(
      cited?.link ? (
        <a
          key={`${n}-${match.index}`}
          className={styles.cite}
          href={cited.link}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Source ${n}: ${cited.title} at ${cited.timestamp}`}
          onMouseEnter={(e) => point(cited, e.currentTarget)}
          onMouseLeave={() => point(cited, null)}
          onFocus={(e) => point(cited, e.currentTarget)}
          onBlur={() => point(cited, null)}
        >
          [{n}]
        </a>
      ) : (
        match[0]
      ),
    );
    cursor = match.index + match[0].length;
  }
  out.push(text.slice(cursor));
  return out.map((node, i) => <Fragment key={i}>{node}</Fragment>);
}

// A citation is a result row: the same badges, snippet presentation and
// receipt (demo-site.md §6.3, §6.5).
const SNIPPET: Record<string, string> = {
  spoken: styles.snipSpoken,
  screen: styles.snipScreen,
  frame: styles.snipFrame,
  mixed: styles.snipMixed,
};

function Source({ c }: { c: Citation }) {
  const kinds = badgeWords(c.source ?? "");
  return (
    <li className={styles.source}>
      <span className={styles.n}>[{c.n}]</span>
      <div className={styles.sourceShot}>
        <FrameShot shot={c} alt="" label={channelWord(c.source ?? "")} />
      </div>
      <div className={styles.sourceText}>
        <a
          href={c.link ?? `https://youtu.be/${encodeURIComponent(c.video_id)}`}
          target="_blank"
          rel="noopener noreferrer"
          className={styles.sourceTitle}
        >
          {c.title}
        </a>
        <span className={styles.sourceMeta}>
          {kinds.map((kind) => (
            <span
              key={kind}
              className={`${styles.badge} ${kind === "on-screen" ? styles.seen : ""}`}
            >
              {kind}
            </span>
          ))}
          {c.channel} · <span className={styles.mono}>{c.timestamp}</span>
        </span>
        {c.text ? (
          <span className={`${styles.snippet} ${SNIPPET[presentationOf(c.source ?? "")]}`}>
            {c.text}
          </span>
        ) : null}
        {c.link ? <Receipt href={c.link} size="lg" className={styles.receipt} /> : null}
      </div>
    </li>
  );
}
