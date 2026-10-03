"use client";

import { useState, type FormEvent } from "react";
import { FeedFailure } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { refusalOf, useWrite } from "@/components/dashboard/kit/write";
import { dashboard } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Profile, ProfileEntry, ProfileEvent } from "@/lib/dashboard/schemas";
import { at, iso } from "@/lib/format";
import { claudeUrl, parsePasted, PROFILE_PROMPT, signed } from "@/lib/feed/words";

// The interest profile: what scores the next verdict, who changed it and why,
// and a way back from any change (companion.md §2).

const FIRST = "first";

function useProfilePage(before: number | null) {
  return useResource<Profile>(`profile:${before ?? FIRST}`, (signal) =>
    dashboard.profile(
      new URLSearchParams(before === null ? {} : { before: String(before) }),
      signal,
    ),
  );
}

export function ProfileView() {
  const profile = useProfilePage(null);
  // History pages read so far, by their `before`; a write starts over.
  const [befores, setBefores] = useState<(number | null)[]>([null]);

  function changed() {
    setBefores([null]);
    profile.reload();
  }

  if (!profile.data) {
    if (profile.error) return <FeedFailure error={profile.error} onRetry={profile.reload} />;
    return <div className={styles.pending} aria-busy="true" />;
  }
  const { entries, max_entries } = profile.data;

  return (
    <>
      <section aria-labelledby="entries">
        <h1 className={styles.label} id="entries">
          Your interests · {entries.length} of {max_entries}
        </h1>
        <Entries entries={entries} onChanged={changed} />
      </section>

      <section className={styles.build} aria-labelledby="build">
        <h2 className={styles.label} id="build">
          Build it
        </h2>
        <BuildWithClaude />
        <Paste onChanged={changed} />
      </section>

      <section aria-labelledby="history">
        <h2 className={styles.label} id="history">
          History
        </h2>
        <ol className={styles.rows}>
          {befores.map((before, index) => (
            <HistoryPage
              key={before ?? FIRST}
              before={before}
              entries={entries}
              onChanged={changed}
              onMore={
                index === befores.length - 1 ? (next) => setBefores([...befores, next]) : null
              }
            />
          ))}
        </ol>
      </section>
    </>
  );
}

function Entries({ entries, onChanged }: { entries: ProfileEntry[]; onChanged: () => void }) {
  if (entries.length === 0) {
    return (
      <p className={styles.quiet}>
        No interests yet, so every verdict is scored without them. Ask Claude to build the list, or
        paste one below.
      </p>
    );
  }
  const sorted = [...entries].sort((a, b) => b.weight - a.weight);
  return (
    <ul className={styles.rows}>
      {sorted.map((entry) => (
        <Entry key={entry.id} entry={entry} onChanged={onChanged} />
      ))}
    </ul>
  );
}

function Entry({ entry, onChanged }: { entry: ProfileEntry; onChanged: () => void }) {
  const [drop, run] = useWrite(
    () => dashboard.profileOps({ drop: [entry.id], reason: "dropped on the profile screen" }),
    onChanged,
  );
  return (
    <li className={styles.entry}>
      <span className={styles.weight} data-negative={entry.weight < 0 || undefined}>
        {signed(entry.weight)}
      </span>
      <span className={styles.entryText}>
        {entry.text}
        <span className={styles.evidence}>
          {entry.source}
          {entry.evidence ? ` · ${entry.evidence}` : ""}
        </span>
        {drop.status === "failed" ? (
          <span className={styles.refused} role="status">
            {refusalOf(drop.error).message}
          </span>
        ) : null}
      </span>
      <button
        className={styles.small}
        type="button"
        aria-label={`Drop ${entry.text}`}
        aria-disabled={drop.status === "sending"}
        onClick={() => run()}
      >
        Drop
      </button>
    </li>
  );
}

function BuildWithClaude() {
  const [copied, setCopied] = useState(false);
  return (
    <div className={styles.buildRow}>
      <a
        className={styles.askWide}
        href={claudeUrl(PROFILE_PROMPT)}
        target="_blank"
        rel="noopener noreferrer"
      >
        Ask Claude to build my profile
      </a>
      <button
        className={styles.copy}
        type="button"
        onClick={() =>
          navigator.clipboard.writeText(PROFILE_PROMPT).then(
            () => setCopied(true),
            () => setCopied(false),
          )
        }
      >
        {copied ? "Copied" : "Copy prompt"}
      </button>
      <p className={styles.quiet}>
        Claude saves the list with the <code>profile</code> tool when vidtheque is one of its
        connectors. Without one, paste its answer below.
      </p>
    </div>
  );
}

/** §2.2's fallback: one entry per line, a weight at either end if it has one. */
function Paste({ onChanged }: { onChanged: () => void }) {
  const [text, setText] = useState("");
  const parsed = parsePasted(text);
  const [write, run] = useWrite(
    (add: { text: string; weight: number }[]) =>
      dashboard.profileOps({ add, reason: "pasted on the profile screen" }),
    () => {
      setText("");
      onChanged();
    },
  );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (parsed.length) run(parsed);
  }

  const duplicates = write.status === "done" ? write.outcome.applied.duplicates : [];
  return (
    <form className={styles.paste} onSubmit={submit} data-write="">
      <label className={styles.pasteLabel} htmlFor="paste">
        Paste a list, one interest per line. A weight from −1 to 1 at the start or the end of a line
        sets it; without one it is +0.5.
      </label>
      <textarea
        id="paste"
        className={styles.textarea}
        rows={5}
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={"Eval harnesses for coding agents (+0.9)\nModel launch hype (−0.8)"}
      />
      <button
        className={styles.action}
        type="submit"
        aria-disabled={parsed.length === 0 || write.status === "sending"}
      >
        {parsed.length === 0
          ? "Add interests"
          : parsed.length === 1
            ? "Add 1 interest"
            : `Add ${parsed.length} interests`}
      </button>
      {write.status === "failed" ? (
        <p className={styles.refused} role="status">
          <code>{refusalOf(write.error).code}</code> {refusalOf(write.error).message}
        </p>
      ) : null}
      {duplicates.length ? (
        <p className={styles.quiet} role="status">
          Already there, so not added: {duplicates.join(", ")}.
        </p>
      ) : null}
    </form>
  );
}

function HistoryPage({
  before,
  entries,
  onChanged,
  onMore,
}: {
  before: number | null;
  entries: ProfileEntry[];
  onChanged: () => void;
  onMore: ((next: number) => void) | null;
}) {
  const page = useProfilePage(before);
  if (!page.data) {
    if (page.error) {
      return (
        <li className={styles.rowNote}>
          <FeedFailure error={page.error} onRetry={page.reload} />
        </li>
      );
    }
    return <li className={styles.pending} aria-busy="true" />;
  }
  const { events, has_more, next_before } = page.data.history;
  return (
    <>
      {before === null && events.length === 0 ? (
        <li className={styles.rowNote}>No change yet.</li>
      ) : null}
      {events.map((event) => (
        <HistoryRow key={event.id} event={event} entries={entries} onChanged={onChanged} />
      ))}
      {onMore && has_more && next_before !== null ? (
        <li className={styles.rowNote}>
          <button className={styles.action} type="button" onClick={() => onMore(next_before)}>
            Older
          </button>
        </li>
      ) : null}
    </>
  );
}

function HistoryRow({
  event,
  entries,
  onChanged,
}: {
  event: ProfileEvent;
  entries: ProfileEntry[];
  onChanged: () => void;
}) {
  const [revert, run] = useWrite(() => dashboard.profileRevert({ event_id: event.id }), onChanged);
  const text =
    event.after?.text ??
    event.before?.text ??
    entries.find((entry) => entry.id === event.entry_id)?.text ??
    `entry ${event.entry_id}`;
  return (
    <li className={styles.event}>
      <span className={styles.entryText}>
        <span className={styles.eventHead}>
          <span className={styles.op}>{event.op}</span>
          <span className={styles.change}>{change(event)}</span>
        </span>
        {text}
        <span className={styles.evidence}>
          {event.actor} · <time dateTime={iso(event.at)}>{at(event.at)}</time>
          {event.reason ? ` · ${event.reason}` : ""}
        </span>
        {revert.status === "failed" ? (
          <span className={styles.refused} role="status">
            {refusalOf(revert.error).message}
          </span>
        ) : null}
      </span>
      <button
        className={styles.small}
        type="button"
        aria-label={`Revert: ${event.op} ${text}`}
        aria-disabled={revert.status === "sending"}
        onClick={() => run()}
      >
        Revert
      </button>
    </li>
  );
}

/** What the event did to the entry, in weights and liveness. */
function change(event: ProfileEvent): string {
  const before = event.before;
  const after = event.after;
  const parts: string[] = [];
  if (!before && after?.weight !== undefined) parts.push(signed(after.weight));
  if (
    before?.weight !== undefined &&
    after?.weight !== undefined &&
    before.weight !== after.weight
  ) {
    parts.push(`${signed(before.weight)} → ${signed(after.weight)}`);
  }
  if (before?.live === true && after?.live === false) parts.push("retired");
  if (before?.live === false && after?.live === true) parts.push("back");
  return parts.join(" · ");
}
