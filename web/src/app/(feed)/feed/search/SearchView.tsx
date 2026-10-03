"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { receiptOf } from "@/app/(dashboard)/dashboard/search/parts";
import { FeedFailure } from "@/components/feed/parts";
import feed from "@/components/feed/feed.module.css";
import { dashboard, FEED } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import { badges, type Hit, type SearchResponse } from "@/lib/dashboard/schemas";
import { clock, day } from "@/lib/format";
import styles from "./search.module.css";

// The search the MCP `search` tool runs, over every channel and with no filter
// (dashboard.md §25.9). The query is the URL; a submitted one is a signal.

const QUERY_CHARS = 512;
// A phone row, not a reading pane: the server cuts the snippet around the match.
const TEXT_CHARS = "300";

export function SearchView() {
  const params = useSearchParams();
  const router = useRouter();
  const q = (params.get("q") ?? "").trim();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const entry = new FormData(event.currentTarget).get("q");
    const next = typeof entry === "string" ? entry.trim() : "";
    if (!next) return;
    // Once per query submitted: paging and reloads send nothing (§25.4).
    void dashboard.searched(next).catch(() => {});
    router.push(`${FEED}/search?${new URLSearchParams({ q: next })}`);
  }

  return (
    <>
      <form className={styles.form} role="search" onSubmit={submit}>
        <label className={feed.label} htmlFor="feed-search-q">
          Search what was said or shown
        </label>
        <div className={styles.bar}>
          <input
            key={q}
            autoComplete="off"
            autoFocus={!q}
            className={styles.input}
            defaultValue={q}
            enterKeyHint="search"
            id="feed-search-q"
            maxLength={QUERY_CHARS}
            name="q"
            placeholder="a phrase, a tool, a topic"
            required
            type="search"
          />
          <button className={feed.action} type="submit">
            Search
          </button>
        </div>
      </form>
      {q ? (
        <Results key={q} q={q} />
      ) : (
        <p className={feed.quiet}>
          Every transcript, slide and frame in your corpus, the way your agent searches it.
        </p>
      )}
    </>
  );
}

function Results({ q }: { q: string }) {
  const [offsets, setOffsets] = useState([0]);
  // The tool repeats a query's notes on every page; they print once, above the hits.
  const [notes, setNotes] = useState<string[]>([]);
  const onNotes = useCallback(
    (more: string[]) =>
      setNotes((now) => {
        const fresh = more.filter((note) => !now.includes(note));
        return fresh.length ? [...now, ...fresh] : now;
      }),
    [],
  );
  return (
    <ol className={feed.rows} aria-label="Results">
      {notes.map((note) => (
        <li key={note} className={feed.rowNote}>
          {note}
        </li>
      ))}
      {offsets.map((offset, index) => (
        <ResultPage
          key={offset}
          q={q}
          offset={offset}
          first={index === 0}
          onNotes={onNotes}
          onMore={index === offsets.length - 1 ? (next) => setOffsets([...offsets, next]) : null}
        />
      ))}
    </ol>
  );
}

const readPage = (q: string, offset: number) => (signal: AbortSignal) =>
  dashboard.search(
    new URLSearchParams({ q, offset: String(offset), max_text_chars: TEXT_CHARS }),
    signal,
  );

function ResultPage({
  q,
  offset,
  first,
  onNotes,
  onMore,
}: {
  q: string;
  offset: number;
  first: boolean;
  onNotes: (notes: string[]) => void;
  onMore: ((next: number) => void) | null;
}) {
  const page = useResource<SearchResponse>(`feed-search:${offset}:${q}`, readPage(q, offset));
  const notes = page.data?.notes;
  useEffect(() => {
    if (notes) onNotes(notes);
  }, [notes, onNotes]);
  if (!page.data) {
    if (page.error) {
      return (
        <li className={feed.rowNote}>
          <FeedFailure error={page.error} onRetry={page.reload} />
        </li>
      );
    }
    return <li className={feed.pending} aria-busy="true" />;
  }
  const { results, pagination, data_status } = page.data;
  return (
    <>
      {first && results.length === 0 ? (
        <li className={feed.rowNote}>
          {/* Set on every empty page; only `empty` means nothing is indexed. */}
          {data_status === "empty"
            ? "Nothing is indexed yet."
            : "Nothing matched. Try other words."}
        </li>
      ) : null}
      {results.map((hit, index) => (
        <HitRow key={`${hit.video_id}:${hit.match_start ?? hit.start}:${index}`} hit={hit} />
      ))}
      {onMore && pagination.has_more ? (
        <li className={feed.rowNote}>
          <button
            className={feed.action}
            type="button"
            onClick={() => onMore(pagination.offset + pagination.limit)}
          >
            More
          </button>
        </li>
      ) : null}
    </>
  );
}

/** The video, then the moment that matched: the receipt opens YouTube there. */
function HitRow({ hit }: { hit: Hit }) {
  const at = hit.match_start ?? hit.start;
  const receipt = receiptOf(hit.link);
  const where = badges(hit.source).join(" · ");
  const byline = [hit.channel, hit.published_at ? day(hit.published_at) : null]
    .filter(Boolean)
    .join(" · ");
  const moment = (
    <>
      <span className={feed.timecode}>{clock(at)}</span>
      <span className={styles.snippet}>
        {hit.text ?? <i className={styles.visual}>visual match, no text hit</i>}
        {where ? <span className={styles.where}>{where}</span> : null}
      </span>
    </>
  );
  return (
    <li className={styles.hit}>
      <Link className={styles.video} href={`${FEED}/${encodeURIComponent(hit.video_id)}`}>
        {byline ? <span className={feed.channel}>{byline}</span> : null}
        <span className={feed.title}>{hit.title || hit.video_id}</span>
      </Link>
      {receipt ? (
        <a
          className={feed.moment}
          href={receipt.href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => void dashboard.signal("watch", hit.video_id, at).catch(() => {})}
        >
          {moment}
          <span className={feed.arrow} aria-hidden="true">
            ↗
          </span>
        </a>
      ) : (
        <p className={feed.moment}>{moment}</p>
      )}
    </li>
  );
}
