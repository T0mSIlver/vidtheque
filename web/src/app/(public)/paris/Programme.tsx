import type { EditionResponse, EditionTalk } from "@/lib/api/schemas";
import { speakerLine } from "@/lib/api/edition";
import { clock, receiptParts } from "@/lib/format";
import { readEdition, type EditionOutcome } from "./edition";
import styles from "./page.module.css";

const OTHER_TRACKS = [
  ["discovery-1", "Discovery Track 1"],
  ["discovery-2", "Discovery Track 2"],
  ["workshop", "Workshop"],
] as const;

/** The schedule, or the facade's own sentence for why there is none. */
export async function Programme() {
  const outcome = await readEdition();
  return outcome.kind === "ok" ? (
    <Timeline edition={outcome.page} />
  ) : (
    <EditionFailure outcome={outcome} />
  );
}

export function EditionFailure({ outcome }: { outcome: Exclude<EditionOutcome, { kind: "ok" }> }) {
  return (
    <section className={`${styles.notice} ${styles.noticeBad}`} role="status">
      <p className={styles.state}>{outcome.word}</p>
      {outcome.kind === "unreachable" ? (
        <>
          <p>Could not reach the edition facade.</p>
          <a className={styles.retry} href="/paris">
            Retry
          </a>
        </>
      ) : (
        <>
          <p>{outcome.message}</p>
          {outcome.next ? <p className={styles.next}>{outcome.next}</p> : null}
        </>
      )}
    </section>
  );
}

export function Timeline({ edition }: { edition: EditionResponse }) {
  const talks = new Map(edition.talks.map((talk) => [talk.session_id, talk]));
  const main = edition.sessions.filter((session) => session.stage === "main");
  const days = [...new Set(main.map((session) => session.day))];
  return (
    <section aria-labelledby="timeline-title">
      <div className={styles.sectionHead}>
        <div>
          <p className={styles.kick}>
            <s />
            <span>main stage</span>
          </p>
          <h2 id="timeline-title" className={styles.sectionTitle}>
            Programme
          </h2>
        </div>
        <a
          href={edition.edition.source_url}
          className={styles.scheduleLink}
          rel="noopener noreferrer"
        >
          Official schedule ↗
        </a>
      </div>
      {days.map((day) => (
        <div className={styles.day} key={day}>
          <h3>
            {new Intl.DateTimeFormat("en-GB", {
              weekday: "long",
              day: "numeric",
              month: "long",
              timeZone: "UTC",
            }).format(new Date(`${day}T12:00:00Z`))}
          </h3>
          <ol>
            {main
              .filter((session) => session.day === day)
              .map((session) => {
                // A row the character cap trimmed is skipped, not thrown.
                const talk = talks.get(session.id);
                return talk ? <TalkRow key={session.id} talk={talk} /> : null;
              })}
          </ol>
        </div>
      ))}
      <div className={styles.otherTracks}>
        {OTHER_TRACKS.map(([stage, label]) => {
          const sessions = edition.sessions.filter((session) => session.stage === stage);
          if (!sessions.length) return null;
          return (
            <details key={stage} className={styles.other}>
              <summary>
                <span>{label}</span>
                <small>not streamed; may arrive later as individual uploads</small>
              </summary>
              <ul>
                {sessions.map((session) => (
                  <li key={session.id}>
                    <time>{session.start}</time>
                    <span>
                      <b>{session.title}</b>
                      <small>{speakerLine(session)}</small>
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          );
        })}
      </div>
    </section>
  );
}

// One scheduled talk. `<time>` is the hour it was given; the offset belongs to
// the receipt, in the seconds of the video it points into.
export function TalkRow({ talk }: { talk: EditionTalk }) {
  const state =
    talk.alignment_state === "not_yet_indexed"
      ? "not yet indexed"
      : talk.alignment_state === "indexed_not_aligned"
        ? "indexed, not yet aligned"
        : talk.source_kind;
  const parts = talk.source ? receiptParts(talk.source) : null;
  return (
    <li className={styles.talk} data-state={talk.alignment_state}>
      <time>{talk.scheduled_start}</time>
      <div className={styles.talkCopy}>
        {talk.source ? (
          <a href={talk.source} target="_blank" rel="noopener noreferrer">
            <b>{talk.title}</b>
          </a>
        ) : (
          <b>{talk.title}</b>
        )}
        <p>{speakerLine(talk)}</p>
        <small>{talk.category}</small>
        {/* The quiet face, not the gold slab: a schedule full of slabs spends
            the accent on the list. */}
        {talk.source && talk.start_s !== null ? (
          <a
            className={styles.receipt}
            href={talk.source}
            target="_blank"
            rel="noopener noreferrer"
          >
            {clock(talk.start_s)} · {parts ? `${parts.host}${parts.id}${parts.query}` : ""}
          </a>
        ) : null}
      </div>
      <span className={styles.talkState}>{state}</span>
    </li>
  );
}

export function ProgrammeLoading() {
  return (
    <section className={styles.loading} aria-busy="true" aria-label="Loading edition">
      <p className={styles.state}>loading</p>
      {Array.from({ length: 6 }, (_, index) => (
        <span key={index} />
      ))}
    </section>
  );
}
