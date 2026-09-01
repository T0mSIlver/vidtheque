import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Frame } from "@/components/Frame";
import { Pill } from "@/components/Pill";
import { clock, receipt } from "@/lib/format";
import { getVideo } from "@/lib/library";
import { DetailSkeleton } from "./loading";
import styles from "./page.module.css";

// The <title> is data too, so it is fetched through the same cached function
// the page uses: one request, memoised for both.
export async function generateMetadata({ params }: PageProps<"/videos/[id]">): Promise<Metadata> {
  const { id } = await params;
  const video = await getVideo(id);
  return { title: video?.title ?? "Not found" };
}

export default function VideoPage(props: PageProps<"/videos/[id]">) {
  return (
    <main className={styles.main}>
      <Suspense fallback={<DetailSkeleton />}>
        <Detail params={props.params} />
      </Suspense>
    </main>
  );
}

async function Detail({ params }: Pick<PageProps<"/videos/[id]">, "params">) {
  const { id } = await params;
  const video = await getVideo(id);
  // Throws a special error the framework turns into the not-found boundary;
  // it must run in the render path, never inside a try/catch.
  if (!video) notFound();

  const chapters = video.chapters ?? [];
  const keyTexts = (video.key_texts ?? []).filter((k) => k.text);
  const highlights = (video.ocr_highlights ?? []).filter((h) => h.screen_text);

  return (
    <article className={styles.article}>
      <header className={styles.head}>
        <div className={styles.cover}>
          <a href={video.link} rel="noreferrer">
            <Frame src={video.thumb} alt="" width={960} priority />
          </a>
        </div>
        <div>
          <p className={styles.kicker}>{video.channel}</p>
          <h1 className={styles.title}>{video.title}</h1>
          <p className={styles.facts}>
            {video.duration} · published {video.published} · indexed {video.indexed_at} ·{" "}
            {video.keyframes} keyframes <Pill state={video.data_status} />
          </p>
          <a className={styles.receipt} href={video.link} rel="noreferrer">
            {receipt(video.link)}
          </a>
        </div>
      </header>

      {chapters.length > 0 ? (
        <section className={styles.section}>
          <h2 className={styles.label}>Chapters</h2>
          <ol className={styles.chapters}>
            {chapters.map((c) => (
              <li key={c.start}>
                <a href={c.link} rel="noreferrer" className={styles.chapter}>
                  <span className={styles.time}>{clock(c.start)}</span>
                  <span>{c.title}</span>
                </a>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {keyTexts.length > 0 ? (
        <section className={styles.section}>
          <h2 className={styles.label}>Key texts</h2>
          <ul className={styles.quotes}>
            {keyTexts.map((k) => (
              <li key={k.start}>
                <blockquote className={styles.quote}>{k.text}</blockquote>
                <a className={styles.receipt} href={k.link} rel="noreferrer">
                  {receipt(k.link)}
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {highlights.length > 0 ? (
        <section className={styles.section}>
          <h2 className={styles.label}>On-screen text</h2>
          <ul className={styles.frames}>
            {highlights.map((h) => (
              <li key={h.frame_id} className={styles.frameItem}>
                <a href={h.link} rel="noreferrer">
                  <Frame src={h.thumb} alt="" />
                </a>
                {/* Lime marks exactly one thing: text the machine read off the
                    screen. This is that thing. */}
                <p className={styles.seen}>
                  <span className={styles.seenTag}>seen</span>
                  <span className={styles.time}>{clock(h.t)}</span>
                  {h.screen_text}
                </p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}
