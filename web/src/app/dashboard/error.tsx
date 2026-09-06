"use client";

import styles from "./dashboard.module.css";
import { Refusal } from "./parts";

// The management surface's own error boundary, and the reason it exists is the
// rail: without one, a throw under `/dashboard` fell through to the root
// boundary — the landing's page, in the landing's voice, with none of this
// surface's chrome and one door out, to the demo. This boundary sits *inside*
// `layout.tsx`, so the rail, the deployment's facts and the signature stay
// where they were and only the column is replaced.
//
// What replaces it is `templates/error.html`, which is what every other
// refusal on this surface is: the message as the title, the code as a state
// beside it in its tone, and a panel with somewhere to click. A thrown render
// is `E_INTERNAL` — the instance's own word for a 500, not a new one invented
// for the front end — and `retry` re-renders the tree that threw, which is the
// one thing here a reload would also do.
//
// In production the server strips the message off anything thrown during
// render and sends a `digest` instead, so that string is the only one worth
// quoting into a report and it is printed as the root boundary prints it.
export default function DashboardError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <>
      <Refusal
        code="E_INTERNAL"
        message={error.message || "This page could not be drawn."}
        onRetry={retry}
      />
      {error.digest ? <p className={styles.refusalCode}>ref {error.digest}</p> : null}
    </>
  );
}
