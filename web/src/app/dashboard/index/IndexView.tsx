"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, type FormEvent } from "react";
import { Pill } from "@/components/ui/Pill";
import { dashboard, echoOf, ROOT } from "@/lib/dashboard/client";
import { RefusedIndex } from "@/lib/dashboard/schemas";
import { DASH } from "@/lib/format";
import controls from "../kit/controls.module.css";
import { Absent, Notice, ReadFailure, RefusalNotice } from "../kit/notice";
import { DashLink, Fact, PageHead, Pending, ui } from "../kit/ui";
import { CHANNEL_BOXES, formFields, useWrite, useWriteSide } from "../kit/write";
import { useSessionResource } from "../session";
import { Receipt } from "./Receipt";
import styles from "./index.module.css";
import { useStoredReceipt } from "./useStoredReceipt";

// Add to the index: a form over `POST /dashboard/index` (dashboard.md §5.5,
// §21). It reads nothing but the chassis's session; the receipt sits above the
// form, because the next thing an operator does is queue another batch.

// Options and printed numbers from the modules that own them
// (`tools/indexing.EXPANSIONS`, `writes.URLS_PER_JOB`, `MAX_FORM_URLS`); never a
// bound here — the handler clamps and caps.
const EXPANSIONS = ["none", "playlist", "channel_recent"];
const URLS_PER_JOB = 10;
const MAX_FORM_URLS = 200;
const MAX_ITEMS = 200;

const DEFAULTS = { expand: "playlist", maxItems: 25, priority: "normal" };

/** This page's own bounds on what a deep link may pour into the prefill. */
const MAX_PREFILL_URLS_CHARS = 16_384;
const MAX_PREFILL_TAGS_CHARS = 800;

const TITLE = "Add to the index";
const NOTE = "A video, a playlist or a channel.";

export function IndexView() {
  const session = useSessionResource();
  const { rendered, indexable } = useWriteSide();

  // The form waits for the session: without it, it would claim indexing is
  // refused on the strength of not having asked.
  if (!session.data) {
    return (
      <>
        <PageHead title={TITLE} note={NOTE}>
          <Fact label="queued in jobs of" value={DASH} />
        </PageHead>
        {session.error !== undefined ? (
          <ReadFailure error={session.error} onRetry={session.reload} />
        ) : (
          <Pending height="44rem" />
        )}
      </>
    );
  }

  // Registered with the write routes, so without them there is no page (§2.3).
  if (!rendered) {
    return (
      <Absent title={TITLE} heading="This deployment does not index anything.">
        Adding to the index is the write side, and this instance registers none — it is either a
        read-only projection of somebody&rsquo;s index, or an instance with no credential configured
        to check.
      </Absent>
    );
  }

  return (
    <>
      <PageHead title={TITLE} note={NOTE}>
        {indexable ? (
          <Fact label="queued in jobs of" value={String(URLS_PER_JOB)} />
        ) : (
          <Pill state="indexing refused" tone="bad" />
        )}
      </PageHead>

      <Form indexable={indexable} reason={session.data.writes_refused_reason} />
    </>
  );
}

/** Write a resolved value into one of the form's uncontrolled controls. */
function setControl(form: HTMLFormElement, name: string, value: string): void {
  const control = form.elements.namedItem(name);
  if (
    control instanceof HTMLInputElement ||
    control instanceof HTMLSelectElement ||
    control instanceof HTMLTextAreaElement
  ) {
    control.value = value;
  }
}

/**
 * Uncontrolled and never remounted, so a refusal keeps what was typed. The
 * three values the server resolved are written back into their controls, and
 * a new prefill link re-seeds its three controls in place.
 */
function Form({ indexable, reason }: { indexable: boolean; reason: string | null }) {
  const params = useSearchParams();
  const search = params.toString();
  const seed = prefill(params);
  const form = useRef<HTMLFormElement>(null);
  const [receipt, keep] = useStoredReceipt();
  const [write, run] = useWrite(
    (fields: Record<string, string>) => dashboard.indexUrls(fields),
    keep,
  );
  const sending = write.status === "sending";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!indexable || sending) return;
    // The previous batch's receipt goes with the submission that replaces it.
    keep(null);
    run(formFields(event.currentTarget));
  }

  // What `_submitted` resolved, on the receipt or on the refusal (§21).
  const accepted = useMemo(
    () =>
      write.status === "done"
        ? write.outcome.accepted
        : write.status === "failed"
          ? (echoOf(write.error, RefusedIndex)?.accepted ?? undefined)
          : undefined,
    [write],
  );
  useEffect(() => {
    if (!form.current || !accepted) return;
    setControl(form.current, "max_items", String(accepted.max_items));
    setControl(form.current, "expand", accepted.expand);
    setControl(form.current, "priority", accepted.priority);
  }, [accepted]);

  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (!form.current) return;
    const next = prefill(new URLSearchParams(search));
    setControl(form.current, "urls", next.urls);
    setControl(form.current, "tags", next.tags);
    setControl(form.current, "expand", next.expand);
  }, [search]);

  return (
    <>
      {indexable ? null : (
        <Notice
          id="refused"
          tone="bad"
          title="Indexing is disabled on this instance."
          detail={
            <>
              The corpus config and the vector tables disagree
              {reason ? <>: {reason}</> : <>.</>}
            </>
          }
          next="Fix the config or dimension mismatch and restart."
        />
      )}

      {receipt ? <Receipt outcome={receipt} perJob={URLS_PER_JOB} /> : null}

      <form className={styles.form} ref={form} onSubmit={submit}>
        <div className={`${controls.field} ${styles.block}`}>
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
            <span className={ui.mono}>{MAX_FORM_URLS}</span> per submission, queued as jobs of{" "}
            <span className={ui.mono}>{URLS_PER_JOB}</span>.
          </p>
        </div>

        <div className={styles.formrow}>
          <div className={`${controls.field} ${controls.pickField}`}>
            <label htmlFor="i-expand">Expand</label>
            <span className={controls.pick}>
              <select id="i-expand" name="expand" defaultValue={seed.expand} disabled={!indexable}>
                {EXPANSIONS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </span>
          </div>
          <div className={`${controls.field} ${controls.narrow}`}>
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
          <div className={`${controls.field} ${controls.pickField}`}>
            <label htmlFor="i-priority">Priority</label>
            <span className={controls.pick}>
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
          <div className={`${controls.field} ${controls.wide}`}>
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

        <fieldset className={controls.checkList}>
          <legend className={controls.checkLegend}>Channels to build</legend>
          {CHANNEL_BOXES.map(([name, label, note]) => (
            <label className={controls.checkNoted} key={name}>
              <input
                type="checkbox"
                name={`channel_${name}`}
                value="1"
                defaultChecked
                disabled={!indexable}
              />
              <span className={controls.checkNotedWord}>{label}</span>
              <span className={controls.checkNote}>{note}</span>
            </label>
          ))}
        </fieldset>

        <fieldset className={controls.checkList}>
          <legend className={controls.checkLegend}>If it is already indexed</legend>
          <label className={controls.checkNoted}>
            <input type="checkbox" name="force_reindex" value="1" disabled={!indexable} />
            <span className={controls.checkNotedWord}>Force re-index</span>
            <span className={controls.checkNote}>
              rebuild every stage. Without this, an incomplete video resumes at its outstanding
              stages and a finished one is left alone.
            </span>
          </label>
        </fieldset>

        {/* The refusal lands under the actions and takes focus, so nothing the
            reader was aiming at moves. No delete control: dashboard.md §5.5. */}
        <div data-write="">
          <div className={`${controls.field} ${controls.actions} ${styles.actions}`}>
            <button
              className={controls.button}
              type="submit"
              disabled={!indexable}
              aria-disabled={sending || undefined}
              title={indexable ? undefined : "This instance's database refuses writes."}
            >
              {sending ? "queueing…" : "Queue the job"}
            </button>
            <DashLink className={controls.ghostlink} href={`${ROOT}/jobs`}>
              Jobs
            </DashLink>
          </div>
          {write.status === "failed" ? (
            <RefusalNotice error={write.error} variant="receipt" />
          ) : null}
        </div>
      </form>
    </>
  );
}

/**
 * The prefill a link may carry ("Queue more from this channel"): copied into
 * controls, never normalised. An unknown `expand` leaves the default.
 */
export function prefill(params: URLSearchParams) {
  const expand = params.get("expand") ?? "";
  return {
    urls: (params.get("urls") ?? "").slice(0, MAX_PREFILL_URLS_CHARS),
    tags: (params.get("tags") ?? "").slice(0, MAX_PREFILL_TAGS_CHARS),
    expand: EXPANSIONS.includes(expand) ? expand : DEFAULTS.expand,
  };
}
