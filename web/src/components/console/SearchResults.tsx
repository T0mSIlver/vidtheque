import { ResultGroup } from "@/components/ResultGroup";
import { RetryIn } from "@/components/RetryIn";
import type { SearchOutcome } from "@/lib/api/outcome";
import type { ContentType, EditionTalk, SearchResponse } from "@/lib/api/schemas";
import { labelHit } from "@/lib/api/edition";
import { groupByVideo } from "@/lib/api/group";
import type { SearchView } from "./state";
import styles from "./console.module.css";

const CHANNEL_NAME: Record<string, string> = {
  transcript: "the transcript",
  ocr: "on-screen text",
  frame: "frames",
};

/** A link that works without JavaScript and runs in place with it. */
export function InPlaceLink({
  href,
  onRun,
  className,
  children,
}: {
  href: string;
  onRun: () => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      className={className}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button)
          return;
        event.preventDefault();
        onRun();
      }}
    >
      {children}
    </a>
  );
}

export interface SearchActions {
  href: (q: string, type: ContentType) => string;
  retry: () => void;
  more: () => void;
  searchAll: () => void;
  examples: (() => void) | null;
}

/** Page one's outcome and the pages appended under it. */
export function SearchResults({
  view,
  talks,
  noMatch,
  actions,
  pending,
}: {
  view: SearchView & { outcome: SearchOutcome };
  talks: EditionTalk[];
  noMatch: string;
  actions: SearchActions;
  /** Page one of a newer search is out; this view's controls are inert. */
  pending: boolean;
}) {
  const { outcome } = view;
  if (outcome.kind === "rate_limited") {
    return (
      <div className={styles.foot}>
        <RetryIn
          key={view.id}
          seconds={outcome.retryAfter}
          variant="notice"
          onRetry={actions.retry}
        />
      </div>
    );
  }
  if (outcome.kind !== "ok") {
    return (
      <div className={styles.foot}>
        <div className={`${styles.notice} ${styles.noticeBad}`}>
          <p className={styles.noticeTitle}>
            {outcome.kind === "refused" ? outcome.message : "Could not reach the server."}
          </p>
          {outcome.kind === "refused" && outcome.next ? (
            <p className={styles.noticeDetail}>{outcome.next}</p>
          ) : null}
          <button type="button" className={styles.ghost} disabled={pending} onClick={actions.retry}>
            Try again
          </button>
        </div>
      </div>
    );
  }
  if (!outcome.page.results.length) {
    return <NoResults view={view} page={outcome.page} noMatch={noMatch} actions={actions} />;
  }
  return (
    <Rows view={view} first={outcome.page} talks={talks} actions={actions} pending={pending} />
  );
}

function Notes({ notes }: { notes: string[] }) {
  return notes.map((note) => (
    <span key={note} className={styles.note}>
      {note}
    </span>
  ));
}

function Rows({
  view,
  first,
  talks,
  actions,
  pending,
}: {
  view: SearchView;
  first: SearchResponse;
  talks: EditionTalk[];
  actions: SearchActions;
  pending: boolean;
}) {
  const pages = [first, ...view.more];
  const hits = pages.flatMap((page) => page.results).map((hit) => labelHit(hit, talks));
  const latest = pages[pages.length - 1];
  const shown = first.pagination.offset + hits.length;
  // `has_more` over exact totals: an estimate equal to what is shown is noise.
  const total =
    latest.pagination.has_more && (latest.pagination.approx_total ?? 0) > shown
      ? ` of ~${latest.pagination.approx_total}`
      : "";
  const { foot } = view;
  return (
    <>
      <p className={styles.status} role="status">
        {foot.kind === "loading"
          ? "loading more…"
          : `${shown} result${shown === 1 ? "" : "s"}${total}`}
        <Notes notes={latest.notes} />
      </p>
      {/* Grouped per screen, so page two joins the card its video already has. */}
      <section className={styles.results} aria-label="Results">
        {groupByVideo(hits).map((group) => (
          <ResultGroup key={group.video_id} group={group} query={view.q} />
        ))}
      </section>
      <div className={styles.foot}>
        {foot.kind === "rate_limited" ? (
          <RetryIn seconds={foot.seconds} variant="notice" onRetry={actions.more} />
        ) : foot.kind === "failed" ? (
          <div className={`${styles.notice} ${styles.noticeBad}`}>
            <p className={styles.noticeTitle}>{foot.message}</p>
            {foot.next ? <p className={styles.noticeDetail}>{foot.next}</p> : null}
            <button
              type="button"
              className={styles.ghost}
              disabled={pending}
              onClick={actions.more}
            >
              Try again
            </button>
          </div>
        ) : latest.pagination.has_more ? (
          <button
            type="button"
            className={styles.ghost}
            disabled={foot.kind === "loading" || pending}
            onClick={actions.more}
          >
            More results
          </button>
        ) : null}
      </div>
    </>
  );
}

function NoResults({
  view,
  page,
  noMatch,
  actions,
}: {
  view: SearchView;
  page: SearchResponse;
  noMatch: string;
  actions: SearchActions;
}) {
  return (
    <>
      {/* A leg that could not run says so even when nothing matched. */}
      {page.notes.length ? (
        <p className={styles.status} role="status">
          <Notes notes={page.notes} />
        </p>
      ) : null}
      {page.dropped ? null : page.data_status === "empty" ? (
        <div className={styles.notice}>
          <p className={styles.noticeTitle}>Nothing is indexed yet.</p>
        </div>
      ) : (
        <div className={styles.notice}>
          <p className={styles.noticeTitle}>{noMatch}</p>
          {view.type === "all" && !actions.examples ? null : (
            <ul className={styles.tips}>
              {view.type !== "all" ? (
                <li>
                  {CHANNEL_NAME[view.type]} only ·{" "}
                  <InPlaceLink
                    className={styles.linky}
                    href={actions.href(view.q, "all")}
                    onRun={actions.searchAll}
                  >
                    Search all
                  </InPlaceLink>
                </li>
              ) : null}
              {actions.examples ? (
                <li>
                  Try{" "}
                  <InPlaceLink
                    className={styles.linky}
                    href={actions.href("", "all")}
                    onRun={actions.examples}
                  >
                    one of the examples
                  </InPlaceLink>
                  .
                </li>
              ) : null}
            </ul>
          )}
        </div>
      )}
    </>
  );
}
