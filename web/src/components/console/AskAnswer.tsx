import { Fragment } from "react";
import { FrameShot } from "@/components/Frame";
import { Receipt } from "@/components/Receipt";
import type { AskAnswer, Citation, EditionTalk } from "@/lib/api/schemas";
import { labelCitation } from "@/lib/api/edition";
import { badgeWords, channelWord, presentationOf } from "@/lib/api/group";
import styles from "./console.module.css";

export function Answer({
  answer,
  talks,
  children,
}: {
  answer: AskAnswer;
  talks: EditionTalk[];
  /** The folded work log, under the prose. */
  children?: React.ReactNode;
}) {
  const citations = answer.citations.map((citation) => labelCitation(citation, talks));
  const byNumber = new Map(citations.map((c) => [c.n, c]));
  return (
    <>
      <div className={styles.prose}>
        {answer.answer.split(/\n{2,}/).map((para, i) => (
          <p key={i} className={styles.para}>
            <Cited text={para} byNumber={byNumber} />
          </p>
        ))}
      </div>
      {citations.length > 0 ? (
        <div className={styles.sources}>
          <h2 className={styles.label}>Sources</h2>
          <ol>
            {citations.map((c) => (
              <Source key={c.n} c={c} />
            ))}
          </ol>
        </div>
      ) : null}
      {children}
      {answer.model ? <p className={styles.model}>model · {answer.model}</p> : null}
    </>
  );
}

// `[n]` becomes a link into the moment it cites; a marker naming nothing stays
// text rather than becoming a dead link.
function Cited({ text, byNumber }: { text: string; byNumber: Map<number, Citation> }) {
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
