"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useRef, type FormEvent } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { IndexOutcome } from "@/lib/dashboard/schemas";
import dash from "../dashboard.module.css";
import {
  CHANNEL_BOXES,
  DashLink,
  Fact,
  formFields,
  PageHead,
  ReadFailure,
  Reading,
  refusalOf,
  useWrite,
  useWriteSide,
} from "../parts";
import { useSessionRead } from "../session";
import styles from "./index.module.css";

// Add to the index — `templates/index.html`, posting to `POST /dashboard/index`
// (dashboard.md §5.5, §21).
//
// **The one page on this surface that reads nothing.** Every other ported page
// fetches a payload and renders it; this one is a form, and the only thing it
// needs to know before drawing is what the deployment is — which the chassis
// has already asked `/dashboard/api/session` for. So it paints immediately and
// the only request it ever makes is the write.
//
// The `GET` is Next's from here on; the `POST` stays Python's, like every other
// write on this surface (frontend-migration.md §1d). They share a path and are
// split by method, which is the second path under `/dashboard` where that is
// true — so, like `POST /dashboard/following`, the write is pulled back in
// front of the router in development by `next.config.ts`'s header shim, and
// routed by method by the reverse proxy in production.
//
// The receipt is rendered **above the form and not instead of it**, exactly as
// Jinja does: the next thing an operator does after queueing a batch is queue
// another one. And the outcome is read rather than followed — the Jinja handler
// redirects straight to the new job when there is exactly one and nothing to
// explain, and a client that always reads `jobs` is a client with no special
// case (§21).

/** The vocabularies and ceilings this form draws, copied from the modules that
 *  own them: `tools/indexing.EXPANSIONS`, and `dashboard/writes.py`'s
 *  `URLS_PER_JOB` and `MAX_FORM_URLS`. The three channel boxes are shared with
 *  the follow form and live in `parts.tsx`.
 *
 *  They are options and numbers printed on a control, never a bound. Nothing
 *  here validates and nothing here clamps: `writes._submitted` clamps
 *  `max_items`, `index_submit` caps the URL count and splits the batch, and a
 *  value corrected on this side would be a limit the operator is never told
 *  about. */
const EXPANSIONS = ["none", "playlist", "channel_recent"];
const URLS_PER_JOB = 10;
const MAX_FORM_URLS = 200;
const MAX_ITEMS = 200;

/** What the controls start out holding — `writes._index_form_values`. */
const DEFAULTS = { expand: "playlist", maxItems: 25, priority: "normal" };

/** The prefill's own bounds, which are the Jinja handler's
 *  (`MAX_PREFILL_URLS_CHARS`, `MAX_PREFILL_TAGS_CHARS`). A `GET` parameter is
 *  an input like any other, and these cap what a deep link may pour into a
 *  control. They bound the *prefill* and nothing else: what the form then posts
 *  is bounded by the handler, at `MAX_FORM_URLS` URLs. */
const MAX_PREFILL_URLS_CHARS = 16_384;
const MAX_PREFILL_TAGS_CHARS = 800;

/** The two ids the "already indexed" line prints before it says "and more". */
const ALREADY_SHOWN = 10;

export function IndexView() {
  const session = useSessionRead();
  const { rendered, indexable } = useWriteSide();

  // **The form waits for the session, and every other page on this surface does
  // not.** Elsewhere the deployment's two flags decorate a payload and a page
  // renders without them; here they are the difference between a control that
  // works, a control that is disabled with a reason, and no page at all. Drawn
  // before the answer lands, this page would tell the reader indexing is
  // refused on the strength of not yet having asked.
  if (session.status === "loading") {
    return (
      <>
        <PageHead title="Add to the index" note="A video, a playlist or a channel." />
        <Reading />
      </>
    );
  }

  // The chassis could not ask what this deployment allows, so neither can this
  // page. A document reload rather than a re-render: the read that failed is the
  // chassis's own, made once on mount, and there is no route to refresh.
  if (session.status === "failed") {
    return (
      <>
        <PageHead title="Add to the index" note="A video, a playlist or a channel." />
        <ReadFailure error={session.error} onRetry={reload} />
      </>
    );
  }

  // `GET /dashboard/index` is registered with the write routes, so on a
  // deployment that registers none this page is not a disabled form — it is not
  // there at all (dashboard.md §2.3, §3.2 rule 3). The rail leaves the link out
  // under the same predicate, so a reader normally never arrives here.
  if (!rendered) return <Absent />;

  return (
    <>
      <PageHead title="Add to the index" note="A video, a playlist or a channel.">
        {indexable ? (
          <Fact label="queued in jobs of" value={String(URLS_PER_JOB)} />
        ) : (
          <Pill state="indexing refused" tone="bad" />
        )}
      </PageHead>

      <Form indexable={indexable} />
    </>
  );
}

/** The one retry on this page, and it is the document's: the session is read
 *  once by the chassis on mount, so nothing short of loading the page again
 *  re-asks it. Named rather than inlined so a test can watch it. */
export const reload = () => {
  if (typeof window !== "undefined") window.location.reload();
};

/** The deployment registers no write side, so there is nothing to add to.
 *
 *  Not an error state: in `VIDTHEQUE_PUBLIC_READONLY=1` and in
 *  `VIDTHEQUE_AUTH=none` this route, its `POST` and every other write are not
 *  registered at all — a route that exists and refuses is a route somebody
 *  probes. */
function Absent() {
  return (
    <>
      <PageHead title="Add to the index" />
      <section className={dash.notice} aria-labelledby="noindexing">
        <h2 className={dash.noticeTitle} id="noindexing">
          This deployment does not index anything.
        </h2>
        <p className={dash.noticeDetail}>
          Adding to the index is the write side, and this instance registers none — it is either a
          read-only projection of somebody&rsquo;s index, or an instance with no credential
          configured to check.
        </p>
        <p className={dash.noticeNext}>
          <DashLink href={ROOT}>The overview</DashLink> and{" "}
          <DashLink href={`${ROOT}/videos`}>the videos this index holds</DashLink> are what it does
          answer for.
        </p>
      </section>
    </>
  );
}

/**
 * The form, the refusal it can come back with, and the receipt it leaves.
 *
 * Every control is uncontrolled and seeded with `defaultValue`, re-keyed on the
 * page's query string: the browser owns what is being typed, and arriving on a
 * seeded link reseeds every control from the URL that arrived. A controlled
 * form here would be a second copy of the draft to keep in step with the one
 * the reader can see.
 *
 * A refusal replaces neither: `_index_refusal` re-renders the form with what
 * was typed still in it, which is the whole reason the two refusals this route
 * has are inline rather than an error page.
 */
function Form({ indexable }: { indexable: boolean }) {
  const params = useSearchParams();
  const search = params.toString();
  const seed = prefill(params);

  // A ref rather than state: the fields are read by the request this submit
  // makes, not by anything that renders, and a `setState` here would put the
  // POST a render behind the form it came from.
  const fields = useRef<Record<string, string>>({});
  const send = useCallback(() => dashboard.indexUrls(fields.current), []);
  const [write, run] = useWrite(send);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    fields.current = formFields(event.currentTarget);
    run();
  }

  const refusal = write.status === "failed" ? refusalOf(write.error) : null;

  return (
    <>
      {/* §5.5: refuse honestly. The form renders disabled with the reason,
          rather than accepting a submission that comes back
          `E_FEATURE_DISABLED` after the operator has typed sixty URLs into it.

          The Jinja page prints `vectors.reason` here, off a context this page
          has no payload for — the session says *that* writes are refused, not
          *why*. The reason is on the readiness strip, so the sentence points at
          it rather than composing a second explanation of its own. */}
      {indexable ? null : (
        <section className={dash.notice} aria-labelledby="refused">
          <h2 className={dash.noticeTitle} id="refused">
            Indexing is disabled on this instance.
          </h2>
          <p className={dash.noticeDetail}>
            The corpus config and the vector tables disagree.{" "}
            <DashLink href={ROOT}>The overview&rsquo;s readiness strip</DashLink> carries the
            mismatch in the instance&rsquo;s own words.
          </p>
          <p className={dash.noticeNext}>Fix the config or dimension mismatch and restart.</p>
        </section>
      )}

      {refusal ? (
        <section className={dash.notice} aria-labelledby="norun">
          <h2 className={dash.noticeTitle} id="norun">
            {refusal.message}
          </h2>
          <p className={dash.noticeDetail}>
            <code>{refusal.code}</code>
          </p>
          {refusal.next ? <p className={dash.noticeNext}>{refusal.next}</p> : null}
        </section>
      ) : null}

      {write.status === "done" ? <Receipt outcome={write.outcome} /> : null}

      <form className={styles.form} key={search} onSubmit={submit}>
        <div className={`${dash.field} ${styles.block}`}>
          <label htmlFor="i-urls">URLs</label>
          <textarea
            id="i-urls"
            name="urls"
            rows={6}
            spellCheck={false}
            autoComplete="off"
            placeholder={
              "https://youtu.be/kCc8FmEb1nY\nkCc8FmEb1nY\nhttps://www.youtube.com/playlist?list=…"
            }
            defaultValue={seed.urls}
            disabled={!indexable}
          />
          <p className={styles.fieldHelp}>
            One per line, or separated by spaces or commas; a bare 11-character id works. Up to{" "}
            <span className={dash.mono}>{MAX_FORM_URLS}</span> per submission, queued as jobs of{" "}
            <span className={dash.mono}>{URLS_PER_JOB}</span>.
          </p>
        </div>

        <div className={styles.formrow}>
          <div className={`${dash.field} ${dash.pickField}`}>
            <label htmlFor="i-expand">Expand</label>
            <span className={dash.pick}>
              <select id="i-expand" name="expand" defaultValue={seed.expand} disabled={!indexable}>
                {EXPANSIONS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </span>
          </div>
          <div className={`${dash.field} ${dash.narrow}`}>
            <label htmlFor="i-max">Max items</label>
            <input
              id="i-max"
              name="max_items"
              type="number"
              min={1}
              max={MAX_ITEMS}
              inputMode="numeric"
              defaultValue={DEFAULTS.maxItems}
              disabled={!indexable}
            />
          </div>
          <div className={`${dash.field} ${dash.pickField}`}>
            <label htmlFor="i-priority">Priority</label>
            <span className={dash.pick}>
              <select
                id="i-priority"
                name="priority"
                defaultValue={DEFAULTS.priority}
                disabled={!indexable}
              >
                <option value="normal">normal</option>
                <option value="high">high</option>
              </select>
            </span>
          </div>
          <div className={`${dash.field} ${dash.wide}`}>
            <label htmlFor="i-tags">Tags</label>
            <input
              id="i-tags"
              name="tags"
              type="text"
              defaultValue={seed.tags}
              placeholder="topic:attention, series:zero-to-hero"
              autoComplete="off"
              disabled={!indexable}
            />
          </div>
        </div>
        <p className={styles.fieldHelp}>
          <em>Expand</em> decides what a playlist or channel URL means: <code>none</code> indexes
          the one video, <code>playlist</code> takes the list it is in, <code>channel_recent</code>{" "}
          takes the channel&rsquo;s latest. <em>Max items</em> caps that expansion, and caps each
          job.
        </p>

        <fieldset className={styles.checks}>
          <legend className={styles.legend}>Channels to build</legend>
          {CHANNEL_BOXES.map(([name, label, note]) => (
            <label className={dash.check} key={name}>
              <input
                type="checkbox"
                name={`channel_${name}`}
                value="1"
                defaultChecked
                disabled={!indexable}
              />
              <span className={dash.checkWord}>{label}</span>
              <span className={styles.checkNote}>{note}</span>
            </label>
          ))}
        </fieldset>

        {/* Its own group: forcing a rebuild is not a channel, and a checkbox
            filed under the wrong legend is a wrong answer to a screen reader as
            well as to a reader. */}
        <fieldset className={styles.checks}>
          <legend className={styles.legend}>If it is already indexed</legend>
          <label className={dash.check}>
            <input type="checkbox" name="force_reindex" value="1" disabled={!indexable} />
            <span className={dash.checkWord}>Force re-index</span>
            <span className={styles.checkNote}>
              rebuild every stage. Without this, an incomplete video resumes at its outstanding
              stages and a finished one is left alone.
            </span>
          </label>
        </fieldset>

        <div className={`${dash.field} ${dash.actions} ${styles.actions}`}>
          <button
            className={dash.ghostlink}
            type="submit"
            disabled={!indexable || write.status === "sending"}
            // The database's own flag, said where a reader meets it: the rail's
            // foot already prints `indexing refused` for the deployment.
            title={indexable ? undefined : "This instance's database refuses writes."}
          >
            {write.status === "sending" ? "queueing…" : "Queue the job"}
          </button>
          <DashLink className={dash.ghostlink} href={`${ROOT}/jobs`}>
            Jobs
          </DashLink>
        </div>
      </form>

      {/* Why there is no delete control (`jobs.kind='delete'` has no pipeline
          behind it) is dashboard.md §5.5; a page does not owe the reader an
          argument for a button it does not have. */}
    </>
  );
}

/**
 * What that submission did — the jobs it made, what it left alone, and what it
 * could not do.
 *
 * `409` lands here too, and that is the point of reading it rather than
 * throwing it: nothing was accepted, and every reason is in `errors` with the
 * batch of URLs it was refused for.
 */
function Receipt({ outcome }: { outcome: IndexOutcome }) {
  const split = outcome.batches > 1;
  const shown = outcome.already_indexed.slice(0, ALREADY_SHOWN);
  return (
    <section className={dash.panel} aria-labelledby="queued" role="status">
      <h2 className={dash.panelTitle} id="queued">
        What that submission did
      </h2>
      <p className={styles.panelNote}>
        <span className={dash.mono}>{outcome.urls}</span> URL(s){" "}
        {split ? (
          <>
            split into <span className={dash.mono}>{outcome.batches}</span> jobs of at most{" "}
            <span className={dash.mono}>{URLS_PER_JOB}</span>.
          </>
        ) : (
          <>in one job.</>
        )}
      </p>

      {outcome.jobs.length ? (
        <ul className={styles.jobs}>
          {outcome.jobs.map((job) => (
            <li key={job.job_id}>
              <DashLink
                className={dash.mono}
                href={`${ROOT}/jobs/${encodeURIComponent(job.job_id)}`}
              >
                {job.job_id}
              </DashLink>
              <span className={dash.rowMeta}>
                <span className={dash.mono}>{job.items}</span> video(s) queued
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {outcome.already_indexed.length ? (
        <>
          <p className={styles.lead}>
            <span className={dash.mono}>{outcome.already_indexed.length}</span> already indexed and
            left alone.
          </p>
          <p className={styles.note}>
            {shown.map((videoId, index) => (
              <span key={videoId}>
                <DashLink
                  className={dash.mono}
                  href={`${ROOT}/videos/${encodeURIComponent(videoId)}`}
                >
                  {videoId}
                </DashLink>
                {index < shown.length - 1 ? ", " : ""}
              </span>
            ))}
            {outcome.already_indexed.length > ALREADY_SHOWN ? " and more" : ""}. Tick{" "}
            <em>force re-index</em> to rebuild one.
          </p>
        </>
      ) : null}

      {/* Every refusal in the API's own words, with the batch it was refused
          for: a submission split into twenty jobs has twenty ways to fail
          partially, and which URLs went with which refusal is the only thing
          that makes a partial failure actionable. */}
      {outcome.errors.map((error, index) => (
        <div key={index}>
          <p className={styles.refusal}>
            <Pill state={error.error ?? "E_HTTP"} tone="bad" />
            <span>{error.message}</span>
          </p>
          <p className={styles.note}>
            {error.urls.map((url) => (
              <span key={url}>
                <code>{url}</code>{" "}
              </span>
            ))}
            {error.next ? (
              <>
                <br />
                {error.next}
              </>
            ) : null}
          </p>
        </div>
      ))}

      {outcome.jobs.length ? (
        <p className={dash.pager}>
          <DashLink className={dash.ghostlink} href={`${ROOT}/jobs?state=active`}>
            Watch the queue
          </DashLink>
        </p>
      ) : null}
    </section>
  );
}

/**
 * The seeding parameters, copied into controls and nowhere else —
 * `writes._prefilled_index_form`, which is what "Queue more from this channel"
 * on a video's detail page links to.
 *
 * This deliberately does not normalise a URL, validate a tag, or decide
 * anything: a prefill is a draft the operator may still edit, and the `POST` is
 * the only thing that interprets it. `expand` is taken only when it is one of
 * the three the tool knows, so a link carrying a fourth leaves the picker on
 * the default rather than adding an option nobody can post.
 */
export function prefill(params: URLSearchParams) {
  const expand = params.get("expand") ?? "";
  return {
    urls: (params.get("urls") ?? "").slice(0, MAX_PREFILL_URLS_CHARS),
    tags: (params.get("tags") ?? "").slice(0, MAX_PREFILL_TAGS_CHARS),
    expand: EXPANSIONS.includes(expand) ? expand : DEFAULTS.expand,
  };
}
