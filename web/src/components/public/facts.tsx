// The chrome's facts from `/api/meta`, each a small async leaf the shell
// streams in under its own <Suspense>.
import { RailMeta } from "@/components/public/Rail";
import { browsePath, corpusCount } from "@/lib/api/meta";
import { readMeta } from "@/lib/api/search";
import { CopyRows } from "./CopyRows";
import styles from "./footer.module.css";

export const REPO = "https://github.com/T0mSIlver/vidtheque";

export async function RailFacts({ showCount }: { showCount: boolean }) {
  const outcome = await readMeta();
  const meta = outcome.kind === "ok" ? outcome.meta : null;
  return (
    <RailMeta
      count={showCount ? corpusCount(meta?.videos) : null}
      browse={browsePath(meta?.browse)}
    />
  );
}

// The endpoint is the server's to state; an unreachable server gets no guess,
// and a spent bucket says it is over in a minute.
export async function ConnectRows() {
  const outcome = await readMeta();
  const meta = outcome.kind === "ok" ? outcome.meta : null;
  return (
    <CopyRows
      mcpUrl={meta?.mcp_url ?? null}
      unavailable={
        outcome.kind === "rate_limited"
          ? "unavailable while rate limited — reload in a minute"
          : "unavailable — reload the page"
      }
    />
  );
}

export function SourceLink({ href }: { href: string }) {
  return (
    <a className={styles.lnk} href={href} rel="noopener">
      Source on GitHub
    </a>
  );
}

// The instance names its repo; only http(s) reaches the href.
export async function RepoLink() {
  const outcome = await readMeta();
  const repo = outcome.kind === "ok" ? outcome.meta.repo : undefined;
  return <SourceLink href={repo && /^https?:\/\//i.test(repo) ? repo : REPO} />;
}
