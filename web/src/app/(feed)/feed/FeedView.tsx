"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { FeedFailure, Matches, Outside, Score } from "@/components/feed/parts";
import { SunkBy, sunkBy, WouldWatch } from "@/components/feed/SkipFix";
import styles from "@/components/feed/feed.module.css";
import { dashboard, FEED } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Feed, FeedFacets, FeedItem } from "@/lib/dashboard/schemas";
import { asked, clock, count } from "@/lib/format";

// Every judged video, newest first, one tap from the end of the week's fitted
// list (companion.md §6): never mixed into it. The search, channel, order and
// profile entry live in the URL, so the back button from a video returns to
// the same narrowed list (§25.2). Pages append; `has_more` decides the button.

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
  const first = useBandPage(0, narrowed);

  if (first.error && !first.data) {
    return <FeedFailure error={first.error} onRetry={first.reload} />;
  }

  return (
    <>
      <Link className={styles.action} href={FEED}>
        ‹ This week
      </Link>
      <Controls filters={filters} />

      <section aria-labelledby="all-videos">
        <h1 className={styles.label} id="all-videos">
          All videos
        </h1>
        <Band key={narrowed} narrowed={narrowed} />
      </section>
    </>
  );
}

function useBandPage(offset: number, narrowed: string) {
  return useResource<Feed>(`feed:all:${offset}:${narrowed}`, (signal) => {
    const query = new URLSearchParams(narrowed);
    query.set("band", "all");
    query.set("offset", String(offset));
    return dashboard.feed(query, signal);
  });
}

const readFacets = (signal: AbortSignal) =>
  dashboard.feedFacets(new URLSearchParams({ band: "all" }), signal);

/** Search, channel, order and the profile entries, over the list below. */
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
  // The server matches a channel in any ASCII case; the select shows the facet's own spelling.
  const listed = channels.find((c) => c.name.toLowerCase() === channel.toLowerCase())?.name;
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
          value={listed ?? channel}
          onChange={(event) => set("channel", event.target.value || null)}
        >
          <option value="">All channels</option>
          {/* A channel from the URL that the facets do not list still shows. */}
          {channel && !listed ? <option value={channel}>{channel}</option> : null}
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
  const chip = useRef<HTMLButtonElement>(null);
  // A chip pressed from the URL can sit past the row's edge, "Other" last of all.
  useEffect(() => {
    if (pressed) chip.current?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [pressed]);
  return (
    <li>
      <button
        ref={chip}
        className={styles.entryChip}
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

/** The list as the pages read so far. */
function Band({ narrowed }: { narrowed: string }) {
  const [offsets, setOffsets] = useState([0]);
  return (
    <ol className={styles.rows}>
      {offsets.map((offset, index) => (
        <BandPage
          key={offset}
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
  offset,
  narrowed,
  first,
  onMore,
}: {
  offset: number;
  narrowed: string;
  first: boolean;
  onMore: ((next: number) => void) | null;
}) {
  const page = useBandPage(offset, narrowed);
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
            : "Nothing judged yet. A verdict is written once a new video from the channels you follow is indexed."}
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

/** One video and what it asks of you: the whole video for the week's 3, else its moments. */
export function Row({ item }: { item: FeedItem }) {
  // A skipped verdict says what sank it and takes an "I'd watch this" (§6.1).
  const skipped = item.score <= 1;
  return (
    <li>
      <Link className={styles.row} href={`${FEED}/${encodeURIComponent(item.video_id)}`}>
        {item.channel ? <span className={styles.channel}>{item.channel}</span> : null}
        <span className={styles.title}>{item.title || item.video_id}</span>
        <span className={styles.meta}>
          <Score score={item.tier ?? item.score} />
          {item.explored ? <Outside /> : null}
          <span className={styles.duration}>
            {item.tier === 3 ? clock(item.duration_s) : asked(item.moments_s, item.duration_s)}
          </span>
        </span>
        {item.reason ? <span className={styles.reason}>{item.reason}</span> : null}
        <Matches matches={item.matches} />
        {skipped ? <SunkBy match={sunkBy(item.matches)} /> : null}
      </Link>
      {skipped ? <WouldWatch videoId={item.video_id} /> : null}
    </li>
  );
}
