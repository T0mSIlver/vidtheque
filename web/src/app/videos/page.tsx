import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { VideoCard } from "@/components/VideoCard";
import { listVideos, PAGE } from "@/lib/library";
import { LibrarySkeleton } from "./loading";
import styles from "./page.module.css";

export const metadata: Metadata = { title: "Library" };

// The page is static: heading and shell prerender at build. Only the part
// that reads the query string is dynamic, and it sits inside <Suspense> so
// the shell streams first and the list follows.
export default function LibraryPage(props: PageProps<"/videos">) {
  return (
    <main className={styles.main}>
      <h1 className={styles.headline}>The library</h1>
      <Suspense fallback={<LibrarySkeleton />}>
        <Library searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}

async function Library({ searchParams }: Pick<PageProps<"/videos">, "searchParams">) {
  // `searchParams` is a Promise in Next 16: awaiting it is what marks this
  // subtree dynamic, and doing so here rather than in the page keeps the
  // shell static.
  const { offset: raw } = await searchParams;
  const offset = Math.max(0, Number.parseInt(String(raw ?? "0"), 10) || 0);
  const { videos, pagination } = await listVideos(offset);

  if (videos.length === 0) {
    return (
      <p className={styles.empty}>
        {offset === 0 ? "Nothing is indexed yet." : "No more videos past this point."}
      </p>
    );
  }

  return (
    <>
      <p className={styles.count}>
        {offset + 1}–{offset + videos.length}
        {pagination.approx_total != null ? ` of about ${pagination.approx_total}` : ""}
      </p>
      <ul className={styles.grid}>
        {videos.map((video, i) => (
          <VideoCard key={video.video_id} video={video} priority={i < 4} />
        ))}
      </ul>
      <nav className={styles.pager} aria-label="Pages">
        {offset > 0 ? (
          <Link href={`/videos?offset=${Math.max(0, offset - PAGE)}`}>← newer</Link>
        ) : (
          <span />
        )}
        {pagination.has_more ? <Link href={`/videos?offset=${offset + PAGE}`}>older →</Link> : null}
      </nav>
    </>
  );
}
