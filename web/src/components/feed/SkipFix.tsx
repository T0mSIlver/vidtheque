"use client";

import { useState } from "react";
import { refusalOf, useWrite } from "@/components/dashboard/kit/write";
import { dashboard } from "@/lib/dashboard/client";
import type { Match, Proposal, SkipAnswer, SkipAnswered } from "@/lib/dashboard/schemas";
import { signed } from "@/lib/feed/words";
import styles from "./feed.module.css";

// "Why skipped" with a fix (companion.md §6.1): the entry that sank a skipped
// video, and the owner's word on it. "I'd watch this" sets the thumb up on the
// server and proposes easing that entry; nothing changes the profile until the
// owner applies the proposal.

/** The strongest "less of this" match, which is what sank the video. */
export function sunkBy(matches: Match[]): Match | null {
  const down = matches.filter((m) => m.direction === "down");
  return down.reduce<Match | null>(
    (best, m) => (best && best.strength >= m.strength ? best : m),
    null,
  );
}

export function SunkBy({ match }: { match: Match | null }) {
  if (!match) return null;
  return <span className={styles.sunk}>Sunk by “{match.text}”</span>;
}

/** The feed's skipped row: one button, "I'd watch this". */
export function WouldWatch({ videoId }: { videoId: string }) {
  const [write, run] = useWrite((answer: SkipAnswer) => dashboard.skip(videoId, answer, "row"));
  if (write.status === "done") return <Answered outcome={write.outcome} />;
  return (
    <div className={styles.skipFix} data-write="">
      <button
        className={styles.small}
        type="button"
        aria-disabled={write.status === "sending"}
        onClick={() => run("wrong")}
      >
        I’d watch this
      </button>
      {write.status === "failed" ? <Refused error={write.error} /> : null}
    </div>
  );
}

/** The brief's skip audit: "would you have watched it?", yes or no. */
export function AuditAnswer({ videoId, answer }: { videoId: string; answer: SkipAnswer | null }) {
  const [shown, setShown] = useState(answer);
  const [write, run] = useWrite(
    (next: SkipAnswer) => dashboard.skip(videoId, next, "audit"),
    (outcome: SkipAnswered) => setShown(outcome.answer),
  );
  return (
    <div
      className={styles.skipFix}
      data-write=""
      role="group"
      aria-label="Would you have watched it?"
    >
      {(["wrong", "right"] as const).map((value) => (
        <button
          key={value}
          className={styles.small}
          type="button"
          aria-pressed={shown === value}
          aria-disabled={write.status === "sending"}
          onClick={() => run(value)}
        >
          {value === "wrong" ? "Yes" : "No"}
        </button>
      ))}
      {write.status === "done" && write.outcome.proposal ? (
        <Propose proposal={write.outcome.proposal} />
      ) : null}
      {write.status === "failed" ? <Refused error={write.error} /> : null}
    </div>
  );
}

function Answered({ outcome }: { outcome: SkipAnswered }) {
  if (outcome.proposal) return <Propose proposal={outcome.proposal} />;
  return (
    <p className={styles.quiet} role="status">
      Noted, as a thumb up.
    </p>
  );
}

function Propose({ proposal }: { proposal: Proposal }) {
  const [write, run] = useWrite(() =>
    dashboard.profileOps({
      reweight: [{ id: proposal.entry_id, weight: proposal.to }],
      reason: "eased after a skip you would have watched",
    }),
  );
  if (write.status === "done") {
    return (
      <p className={styles.quiet} role="status">
        “{proposal.text}” is now {signed(proposal.to)}.
      </p>
    );
  }
  return (
    <div className={styles.proposal} role="status">
      <span>
        Ease “{proposal.text}” from {signed(proposal.weight)} to {signed(proposal.to)}?
      </span>
      <button
        className={styles.small}
        type="button"
        aria-disabled={write.status === "sending"}
        onClick={() => run()}
      >
        Ease it
      </button>
      {write.status === "failed" ? <Refused error={write.error} /> : null}
    </div>
  );
}

function Refused({ error }: { error: unknown }) {
  return (
    <span className={styles.refused} role="status">
      {refusalOf(error).message}
    </span>
  );
}
