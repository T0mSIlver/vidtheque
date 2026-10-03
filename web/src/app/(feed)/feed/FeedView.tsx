"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { FeedFailure, Matches, Outside, Score } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { dashboard, FEED } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Feed, FeedFacets, FeedItem } from "@/lib/dashboard/schemas";
import { clock, count } from "@/lib/format";

// What should I watch? Verdicts newest first, 2–3 on top, 0–1 folded into
// "skipped (n)" (companion.md §6). Pages append; `has_more` decides the button.
// The search, channel, order and profile entry live in the URL, so the back
// button from a video returns to the same narrowed feed (§25.2).

type BandName = "top" | "skipped";

const FILTERS = ["q", "channel", "entry", "order"] as const;
const TYPING_MS = 300;

export function FeedView() {
  const params = useSearchParams();
  const filters = new URLSearchParams();
  for (const name of FILTERS) {
    const value = params.get(name);
    if (value) filters.set(name, value);
  }
  const narrowed = filters.toString();
  const [skippedOpen, setSkippedOpen] = useState(false);
  const first = useBandPage("top", 0, narrowed);

  if (first.error && !first.data) {
    return <FeedFailure error={first.error} onRetry={first.reload} />;
  }
  const skipped = first.data?.skipped;

  return (
    <>
      <Controls filters={filters} />

      <section aria-labelledby="to-watch">
        <h1 className={styles.label} id="to-watch">
          To watch
        </h1>
        <Band key={narrowed} band="top" narrowed={narrowed} />
      </section>

      {skipped && skipped.count > 0 ? (
        <section className={styles.skipped} aria-labelledby="skipped">
          <h2 id="skipped">
            <button
              className={styles.fold}
              type="button"
              aria-expanded={skippedOpen}
              onClick={() => setSkippedOpen((open) => !open)}
            >
              <span>
                Skipped ({count(skipped.count)}
                {skipped.capped ? "+" : ""})
              </span>
              <span aria-hidden="true">{skippedOpen ? "−" : "+"}</span>
            </button>
          </h2>
          {skippedOpen ? <Band key={narrowed} band="skipped" narrowed={narrowed} /> : null}
        </section>
      ) : null}
    </>
  );
}

function useBandPage(band: BandName, offset: number, narrowed: string) {
  return useResource<Feed>(`feed:${band}:${offset}:${narrowed}`, (signal) => {
    const query = new URLSearchParams(narrowed);
    query.set("band", band);
    query.set("offset", String(offset));
    return dashboard.feed(query, signal);
  });
}

const readFacets = (signal: AbortSignal) =>
  dashboard.feedFacets(new URLSearchParams({ band: "top" }), signal);

/** Search, channel, order and the profile entries, over the band below. */
function Controls({ filters }: { filters: URLSearchParams }) {
  const router = useRouter();
  const path = usePathname();
  const facets = useResource<FeedFacets>("feed:facets", readFacets).data;
  const q = filters.get("q") ?? "";
  const [typed, setTyped] = useState(q);
  const [urlQ, setUrlQ] = useState(q);

  const set = (name: (typeof FILTERS)[number], value: string | null) => {
    const next = new URLSearchParams(filters);
    if (value) next.set(name, value);
    else next.delete(name);
    const query = next.toString();
    router.replace(query ? `${path}?${query}` : path, { scroll: false });
  };

  // The URL follows the box once typing pauses; a URL that moves on its own
  // (back, a cleared filter) puts its text back in the box.
  if (urlQ !== q) {
    setUrlQ(q);
    setTyped(q);
  }
  useEffect(() => {
    if (typed.trim() === q) return;
    const timer = setTimeout(() => set("q", typed.trim() || null), TYPING_MS);
    return () => clearTimeout(timer);
  });

  const channel = filters.get("channel") ?? "";
  const entry = filters.get("entry") ?? "";
  const channels = facets?.channels ?? [];
  const entries = facets?.entries ?? [];

  return (
    <div className={styles.controls} role="search">
      <input
        className={styles.searchBox}
        type="search"
        aria-label="Search titles and channels"
        placeholder="Search titles and channels"
        value={typed}
        onChange={(event) => setTyped(event.target.value)}
      />
      <div className={styles.selects}>
        <select
          className={styles.select}
          aria-label="Channel"
          value={channel}
          onChange={(event) => set("channel", event.target.value || null)}
        >
          <option value="">All channels</option>
          {/* A channel from the URL that the facets do not list still shows. */}
          {channel && !channels.some((c) => c.name.toLowerCase() === channel.toLowerCase()) ? (
            <option value={channel}>{channel}</option>
          ) : null}
          {channels.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name} ({count(c.count)})
            </option>
          ))}
        </select>
        <select
          className={styles.select}
          aria-label="Order"
          value={filters.get("order") ?? "newest"}
          onChange={(event) => set("order", event.target.value === "oldest" ? "oldest" : null)}
        >
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
        </select>
      </div>
      {entries.length ? (
        <ul className={styles.entries} aria-label="Profile entries">
          <EntryChip label="All" pressed={!entry} onPress={() => set("entry", null)} />
          {entries.map((e) => (
            <EntryChip
              key={e.entry_id}
              label={e.text}
              direction={e.direction}
              n={e.count}
              pressed={entry === String(e.entry_id)}
              onPress={() => set("entry", String(e.entry_id))}
            />
          ))}
          {facets && facets.other > 0 ? (
            <EntryChip
              label="Other"
              n={facets.other}
              pressed={entry === "other"}
              onPress={() => set("entry", "other")}
            />
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

function EntryChip({
  label,
  direction,
  n,
  pressed,
  onPress,
}: {
  label: string;
  direction?: "up" | "down";
  n?: number;
  pressed: boolean;
  onPress: () => void;
}) {
  return (
    <li>
      <button
        className={styles.entry}
        type="button"
        data-direction={direction}
        aria-pressed={pressed}
        onClick={onPress}
      >
        {direction ? <span aria-hidden="true">{direction === "up" ? "↑" : "↓"}</span> : null}
        {label}
        {n !== undefined ? <span className={styles.entryCount}>{count(n)}</span> : null}
      </button>
    </li>
  );
}

/** One band as the pages read so far. */
function Band({ band, narrowed }: { band: BandName; narrowed: string }) {
  const [offsets, setOffsets] = useState([0]);
  return (
    <ol className={styles.rows}>
      {offsets.map((offset, index) => (
        <BandPage
          key={offset}
          band={band}
          offset={offset}
          narrowed={narrowed}
          first={index === 0}
          onMore={index === offsets.length - 1 ? (next) => setOffsets([...offsets, next]) : null}
        />
      ))}
    </ol>
  );
}

function BandPage({
  band,
  offset,
  narrowed,
  first,
  onMore,
}: {
  band: BandName;
  offset: number;
  narrowed: string;
  first: boolean;
  onMore: ((next: number) => void) | null;
}) {
  const page = useBandPage(band, offset, narrowed);
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
  const { items, pagination } = page.data;
  const next = pagination.next_offset;
  return (
    <>
      {first && items.length === 0 ? (
        <li className={styles.rowNote}>
          {narrowed
            ? "Nothing here matches."
            : band === "top"
              ? "Nothing to watch yet. A verdict is written once a new video from the channels you follow is indexed."
              : "Nothing skipped."}
        </li>
      ) : null}
      {items.map((item) => (
        <Row key={item.video_id} item={item} />
      ))}
      {onMore && pagination.has_more && next !== null ? (
        <li className={styles.rowNote}>
          <button className={styles.action} type="button" onClick={() => onMore(next)}>
            More
          </button>
        </li>
      ) : null}
    </>
  );
}

function Row({ item }: { item: FeedItem }) {
  return (
    <li>
      <Link className={styles.row} href={`${FEED}/${encodeURIComponent(item.video_id)}`}>
        {item.channel ? <span className={styles.channel}>{item.channel}</span> : null}
        <span className={styles.title}>{item.title || item.video_id}</span>
        <span className={styles.meta}>
          <Score score={item.score} />
          {item.explored ? <Outside /> : null}
          <span className={styles.duration}>{clock(item.duration_s)}</span>
        </span>
        {item.reason ? <span className={styles.reason}>{item.reason}</span> : null}
        <Matches matches={item.matches} />
      </Link>
    </li>
  );
}
