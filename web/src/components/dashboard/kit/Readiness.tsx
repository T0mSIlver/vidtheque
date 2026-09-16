import type { ReactNode } from "react";
import type { Readiness as ReadinessPayload } from "@/lib/dashboard/schemas";
import { DASH, iso } from "@/lib/format";
import { Panel, StatePair, ui } from "./ui";

/**
 * The pipeline observation, identical on the overview and the ledger
 * (dashboard.md §15). The projection carries neither the worker probe nor the
 * indexing state, so both are absent there rather than redacted in place.
 */
export function Readiness({
  readiness,
  redacted,
  writesAllowed,
  drift,
  children,
}: {
  readiness: ReadinessPayload;
  redacted: boolean;
  /** The payload's own flag (§19), so the state paints with the page. */
  writesAllowed: boolean;
  drift?: boolean;
  children?: ReactNode;
}) {
  // The whole stamp, seconds and all: it is the instant of a probe.
  const checked = iso(readiness.checked_at);
  const worker = readiness.worker;
  return (
    <Panel
      id="readiness"
      title="Pipeline readiness"
      drift={drift}
      aside={
        <p className={ui.clock}>
          last health check <time dateTime={checked}>{checked ?? DASH}</time>
        </p>
      }
    >
      <p className={ui.states}>
        <StatePair label="MCP" word={readiness.mcp} tone="ok" />
        <StatePair label="Database" word={readiness.database} tone="ok" />
        {worker ? (
          <StatePair
            label="Worker"
            word={worker.state}
            tone={
              worker.state === "ready" ? "ok" : worker.state === "unavailable" ? "bad" : "neutral"
            }
            detail={worker.detail}
          />
        ) : null}
        <StatePair
          label="Vector search"
          word={readiness.vectors.enabled ? "ready" : "full-text only"}
          tone={readiness.vectors.enabled ? "ok" : "bad"}
          detail={readiness.vectors.reason}
        />
        {redacted ? null : (
          <StatePair
            label="Indexing"
            word={writesAllowed ? "allowed" : "refused"}
            tone={writesAllowed ? "ok" : "bad"}
          />
        )}
      </p>
      {children}
    </Panel>
  );
}
