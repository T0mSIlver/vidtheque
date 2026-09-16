"use client";

import { useRef, useState } from "react";
import { ResultGroup } from "@/components/ResultGroup";
import { RetryIn } from "@/components/RetryIn";
import {
  type ContentType,
  type EditionTalk,
  type Hit,
  type Pagination,
  PartialErrorEnvelope,
  SearchResponse,
} from "@/lib/api/schemas";
import { groupByVideo } from "@/lib/group";
import { labelHit } from "@/lib/edition";
import styles from "./page.module.css";

// The rows, the count over them, and the one control under them.
//
// Page one is the server's — it is rendered before this component's JavaScript
// exists, and a visitor with none still gets results. "More results" is this
// side's, because `append` is the whole difference between a hiccup and a
// wipe: a failed second page must not cost the visitor the ten rows they
// already have. The notice goes into the foot, under the list, where the
// button that asked for page two was, and the count line goes on counting
// what is on screen (demo-site.md §6.1).
//
// Grouping is per *screen*, not per page: `groupByVideo` over everything that
// has arrived merges page two into the card its video already has, so a repeated
// title cannot read as the list restarting (§6.5). It never re-ranks — the
// order is still first appearance, which is the server's ordering.

const PAGE = 10;

type Foot =
  | { kind: "none" }
  | { kind: "loading" }
  | { kind: "rate_limited"; seconds: number }
  | { kind: "failed"; message: string; next?: string };

export function Results({
  q,
  type,
  hits: first,
  pagination: firstPage,
  notes: firstNotes,
  tags,
  talks = [],
}: {
  q: string;
  type: ContentType;
  hits: Hit[];
  pagination: Pagination;
  notes: string[];
  tags?: string;
  talks?: EditionTalk[];
}) {
  const [pages, setPages] = useState<{ hits: Hit[]; page: Pagination; notes: string[] }[]>([]);
  const [foot, setFoot] = useState<Foot>({ kind: "none" });
  const abort = useRef<AbortController | null>(null);

  const hits = [...first, ...pages.flatMap((p) => p.hits)].map((hit) => labelHit(hit, talks));
  const latest = pages.at(-1);
  const page = latest?.page ?? firstPage;
  const notes = latest?.notes ?? firstNotes;
  const shown = firstPage.offset + hits.length;
  // `has_more` over exact totals: the count probe is bounded, so an "of ~N"
  // that equals what is on screen would be noise rather than information.
  const total =
    page.has_more && (page.approx_total ?? 0) > shown ? ` of ~${page.approx_total}` : "";

  async function more() {
    // The next attempt clears the foot before it starts, so a retry can never
    // layer fresh rows under a stale error box.
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setFoot({ kind: "loading" });

    const params = new URLSearchParams({
      q,
      content_type: type,
      limit: String(PAGE),
      offset: String(shown),
    });
    if (tags) params.set("tags", tags);
    let response: Response;
    try {
      response = await fetch(`/api/search?${params}`, { signal: controller.signal });
    } catch {
      if (controller.signal.aborted) return;
      setFoot({ kind: "failed", message: "Could not reach the server." });
      return;
    }
    const body: unknown = await response.json().catch(() => null);
    if (controller.signal.aborted) return;

    if (response.status === 429) {
      const envelope = PartialErrorEnvelope.safeParse(body);
      const seconds =
        (envelope.success ? envelope.data.retry_after_s : null) ??
        Number(response.headers.get("Retry-After")) ??
        0;
      setFoot({ kind: "rate_limited", seconds: seconds || 60 });
      return;
    }
    if (!response.ok) {
      const envelope = PartialErrorEnvelope.safeParse(body);
      setFoot({
        kind: "failed",
        message: (envelope.success && envelope.data.message) || "Search failed.",
        next: (envelope.success && envelope.data.next) || undefined,
      });
      return;
    }
    const parsed = SearchResponse.safeParse(body);
    if (!parsed.success) {
      setFoot({ kind: "failed", message: "The corpus answered in a shape this page cannot read." });
      return;
    }
    setPages((seen) => [
      ...seen,
      { hits: parsed.data.results, page: parsed.data.pagination, notes: parsed.data.notes },
    ]);
    setFoot({ kind: "none" });
  }

  const groups = groupByVideo(hits);
  return (
    <>
      {/* What the count line said before "loading more…" replaced it is what
          it says again if that page fails: the count of what is still on
          screen is the truth, not a "loading more…" frozen forever. */}
      <p className={styles.status} role="status">
        {foot.kind === "loading"
          ? "loading more…"
          : `${shown} result${shown === 1 ? "" : "s"}${total}`}
        {notes.map((note) => (
          <span key={note} className={styles.note}>
            {note}
          </span>
        ))}
      </p>
      <section className={styles.results} aria-label="Results">
        {groups.map((group) => (
          <ResultGroup key={group.video_id} group={group} query={q} />
        ))}
      </section>
      <div className={styles.foot}>
        {foot.kind === "rate_limited" ? (
          <RetryIn seconds={foot.seconds} variant="notice" onRetry={() => void more()} />
        ) : foot.kind === "failed" ? (
          <div className={`${styles.notice} ${styles.noticeBad}`}>
            <p className={styles.noticeTitle}>{foot.message}</p>
            {foot.next ? <p className={styles.noticeDetail}>{foot.next}</p> : null}
            <button type="button" className={styles.ghost} onClick={() => void more()}>
              Try again
            </button>
          </div>
        ) : page.has_more ? (
          <button
            type="button"
            className={styles.ghost}
            disabled={foot.kind === "loading"}
            onClick={() => void more()}
          >
            More results
          </button>
        ) : null}
      </div>
    </>
  );
}
