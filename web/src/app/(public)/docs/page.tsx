import Link from "next/link";
import { REPO } from "@/components/public/facts";
import { readMeta } from "@/lib/api/search";
import styles from "@/components/public/page/page.module.css";

// Run your own (demo-site.md §8.4): the README's quickstart at this
// deployment's version, then recommendations, the agent, and the app.

const ENV = `${REPO}/blob/main/deploy/.env.example`;

function quickstart(version: string | null): string {
  const tag = version ?? "<version>";
  return [
    "mkdir vidtheque && cd vidtheque",
    `REL=https://raw.githubusercontent.com/T0mSIlver/vidtheque/v${tag}/deploy`,
    'curl -fsSLO "$REL/docker-compose.yml" -O "$REL/compose.release.example.yml" -O "$REL/Caddyfile"',
    'curl -fsSL -o .env "$REL/.env.example"',
    `echo "IMAGE_TAG=${tag}" >> .env`,
    "docker compose -f docker-compose.yml -f compose.release.example.yml up -d",
  ].join("\n");
}

export default async function DocsPage() {
  const meta = await readMeta();
  const version = meta.kind === "ok" ? meta.meta.version : null;
  return (
    <main className={styles.main}>
      <header>
        <p className={styles.kick}>
          <s />
          <span>docs</span>
        </p>
        <h1 className={styles.big}>Run your own.</h1>
        <p className={styles.lede}>
          The sample feed is scored for someone else. Your own instance follows your channels and
          learns what you care about from what you watch, skip and ask.
        </p>
      </header>

      <section className={styles.section} aria-labelledby="where">
        <h2 className={styles.h2} id="where">
          1. Pick a machine at home
        </h2>
        <p className={styles.p}>
          A NAS, a mini PC or a Raspberry Pi 5 runs it. A home connection matters more than the
          hardware: YouTube blocks many datacenter addresses, so a rented server may fail to fetch
          videos where a home machine works.
        </p>
        <p className={styles.p}>
          Without a GPU, set{" "}
          <span className={styles.inline}>VIDTHEQUE_STT_POLICY=captions_only</span> and leave the
          worker out: vidtheque reads YouTube&apos;s captions instead of transcribing, and search
          falls back to keywords.
        </p>
      </section>

      <section className={styles.section} aria-labelledby="run">
        <h2 className={styles.h2} id="run">
          2. Start it
        </h2>
        <pre className={styles.code}>{quickstart(version)}</pre>
        <p className={styles.p}>
          Every setting is documented in <a href={ENV}>.env.example</a>. Updates are one command,{" "}
          <span className={styles.inline}>deploy/vidtheque-update.sh</span>.
        </p>
      </section>

      <section className={styles.section} aria-labelledby="recs">
        <h2 className={styles.h2} id="recs">
          3. Turn on recommendations
        </h2>
        <p className={styles.p}>
          Give it a model: <span className={styles.inline}>VIDTHEQUE_LLM_BASE_URL</span>,{" "}
          <span className={styles.inline}>VIDTHEQUE_LLM_MODEL</span> and{" "}
          <span className={styles.inline}>VIDTHEQUE_LLM_API_KEY</span>, any OpenAI-compatible
          endpoint. Follow a few channels from the dashboard. Each new video then gets a verdict:
          whether it is worth your time, why, and which minutes. Your interest profile starts empty
          and fills in from what you do in the feed.
        </p>
      </section>

      <section className={styles.section} aria-labelledby="agent">
        <h2 className={styles.h2} id="agent">
          4. Connect your agent
        </h2>
        <p className={styles.p}>
          Your instance serves MCP at <span className={styles.inline}>/mcp</span>. Claude Code,
          Codex, Mistral Vibe or any MCP client can search what it watched and cite the second. Try
          it now on the public corpus with the commands below.
        </p>
      </section>

      <section className={styles.section} aria-labelledby="phone">
        <h2 className={styles.h2} id="phone">
          5. Get the feed on your phone
        </h2>
        <p className={styles.p}>
          The Android app signs in to your instance and shows the same feed.{" "}
          <Link href="/app">Install the app</Link>.
        </p>
      </section>
    </main>
  );
}
