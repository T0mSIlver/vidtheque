"use client";

import { notice, Refusal } from "./kit/notice";

// Inside the layout, so a throw keeps the rail and replaces only the column.
// A thrown render is `E_INTERNAL`; production strips the message and sends a
// `digest`, which is the string worth quoting into a report.
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
      {error.digest ? <p className={notice.digest}>ref {error.digest}</p> : null}
    </>
  );
}
