"use client";

import type { FormEvent, ReactNode } from "react";
import type { FollowDetail, FollowRow } from "@/lib/dashboard/schemas";
import { at, DASH, duration, hms } from "@/lib/format";
import dash from "../dashboard.module.css";
import { CHANNEL_BOXES, formFields } from "../parts";
import styles from "./following.module.css";

// What the two following pages are built from.
//
// Everything below composes a *value* the payload already carries. §22 sends
// no rendered rule, no near-miss sentence and no budget line, because each is
// derivable from the columns beside it — and it sends `reason` verbatim,
// because that one is not derivable at all: it is the receipt the check wrote,
// with the number that made the decision inside it.
//
// The one thing this module deliberately does **not** carry is a second
// `follows.rules.describe`. That function renders a policy as English and it
// is still the only thing that does; what the pages show instead is the rule
// as facts, at one density, from one formatter (§18.3).

/** The check's own vocabularies, from the modules that own them —
 *  `follows/rules.py`'s `TABS`, `MODES`, `MAX_BACKFILL`, `MAX_PER_CHECK` and
 *  `MIN_CHECK_INTERVAL_S`. The three channel boxes are `parts.tsx`'s, for the
 *  reason `dashboard/writes.py` gives for owning them there: the index form
 *  ticks the same three for the same parameter, and a second copy of the
 *  labels is how one thing gets described two ways.
 *
 *  These are the one thing on this page that is **copied** rather than read:
 *  the Jinja form takes them from a `choices` context these two payloads do
 *  not carry, so a vocabulary Python grows has to be added here as well. They
 *  are options and ceilings on a control, never a bound — nothing here
 *  validates, every value goes to the server as typed, and `follows/params.py`
 *  is the only thing that clamps. */
export const TABS = ["videos", "streams", "shorts"] as const;
export const MODES = ["auto", "review"] as const;
export const MAX_BACKFILL = 25;
export const MAX_PER_CHECK = 25;
export const MIN_CHECK_INTERVAL_S = 900;
export const DEFAULT_CHECK_INTERVAL_S = 21_600;

/**
 * The rule compressed to the facts that fit in a table cell — `views._rule_facts`.
 *
 * Facts and not a sentence, and the argument is the one §18.3 makes: sixty
 * follows scanned at 03:00 are a column to compare, not sixty sentences to
 * read. One formatter, used at the top of a follow's own page as well, so the
 * rule a reader compares in the table is spelled the same way as the rule they
 * then open.
 *
 * The lengths are `hms` — always `h:mm:ss` — because a floor and a ceiling on
 * one line have to read on one scale.
 */
export function ruleFacts(follow: FollowRow): string[] {
  const facts = [follow.tabs.map((tab) => `/${tab}`).join(", ")];
  const low = follow.min_duration_s;
  const high = follow.max_duration_s;
  if (low !== null && high !== null) facts.push(`${hms(low)}–${hms(high)}`);
  else if (low !== null) facts.push(`${hms(low)} floor`);
  else if (high !== null) facts.push(`${hms(high)} ceiling`);
  const terms = follow.title_include.length + follow.title_exclude.length;
  if (terms) facts.push(`${terms} title term(s)`);
  // `all` is the tool's own word for every stage, so a follow that builds
  // everything says nothing here rather than listing three channels.
  if (follow.channels !== "all") facts.push(`${follow.channels} only`);
  facts.push(`${follow.max_per_check}/check`);
  facts.push(`every ${duration(follow.check_interval_s)}`);
  if (follow.mode === "review") facts.push("held for review");
  return facts;
}

/** The rule's facts, drawn. The middot is glued to the fact before it, so a
 *  cell that runs out of room breaks between two facts and never through one —
 *  the same rule every strip on this surface follows.
 *
 *  `wide` is the follow's own page, where the rule is the thing the reader came
 *  to check rather than a column to compare. Same facts, one size up. */
export function RuleFacts({ follow, wide }: { follow: FollowRow; wide?: boolean }) {
  const facts = ruleFacts(follow);
  return (
    <span className={wide ? styles.rule : styles.facts}>
      {facts.map((fact, index) => (
        <span className={wide ? undefined : styles.fact} key={fact}>
          {fact}
          {index < facts.length - 1 ? <span className={dash.sep}>·</span> : null}
        </span>
      ))}
    </span>
  );
}

/**
 * The one derived line above the ledger, or nothing at all.
 *
 * The arithmetic and the omission are both the contract's: `near_miss` is
 * `null` when the count is zero or the follow has no length rule, and `null`
 * means print nothing. A "0 of the last 25" line is a fact about nothing
 * dressed as a finding, and this is the one band on the surface that has to
 * stay believable. `within_s` comes off the payload rather than out of a
 * constant here, so the count and the number in this sentence cannot disagree.
 */
export function nearMissLine(near: NonNullable<FollowDetail["near_miss"]>): string {
  const verb = near.count === 1 ? "was" : "were";
  return `${near.count} of the last ${near.of} passed over ${verb} within ${near.within_s} seconds of your ${near.edge}.`;
}

/**
 * Whether the scheduler would enqueue this follow's check — the store's own
 * `_SCHEDULABLE`, arrived as a field: `active`, or `failing` with retries
 * left (migration 0008). Everything that promises a clock, and the one
 * control that moves one, asks this first, because `state` alone cannot say
 * which kind of `failing` this is.
 */
export function schedulable(follow: FollowRow): boolean {
  return follow.state === "active" || follow.retrying;
}

/**
 * The table's spelling of the retry state: `retry 2 of 7` while the check is
 * still coming back on its own, `gave up` once it has stopped. Not a fourth
 * state word — the count is the receipt, and the sentence is composed here
 * off the row's own numbers, the same split the near-miss line takes
 * (following.md §11.4).
 */
export function retryFact(follow: FollowRow): string {
  return follow.retrying ? `retry ${follow.fail_count} of ${follow.max_tries}` : "gave up";
}

/**
 * The detail page's spelling, one size up: the cadence while it is still
 * trying, the tally once it has stopped.
 */
export function retryWords(follow: FollowRow): string {
  return follow.retrying
    ? `retry ${follow.fail_count} of ${follow.max_tries}, once a day`
    : `gave up after ${follow.fail_count} tries`;
}

/**
 * When this follow is next looked at — or why that is not a time.
 *
 * Three answers, and the third is Tom's (2026-09-05): with
 * `checks_enabled: false` every `next_check_at` on the payload is a moment
 * at which nothing will happen, so the clock is replaced by the fact rather than
 * printed beside it. A page that showed the time anyway would be confidently
 * wrong sixty rows at a time.
 *
 * The fourth is migration 0008's: a follow nothing will enqueue keeps a
 * `next_check_at` and it means nothing, so the cell prints the dash —
 * `follow.html`'s minirow rule, which the table shares because both print
 * through this one function.
 *
 * `0` is what `Check now` leaves behind — due immediately, which is a value
 * and not a missing clock, and only ever on a follow that will be enqueued.
 */
export function nextCheckWords(follow: FollowRow, checksEnabled?: boolean): string {
  if (checksEnabled === false) return "checks off";
  if (!schedulable(follow)) return DASH;
  if (!follow.next_check_at) return "due now";
  return at(follow.next_check_at);
}

/** The budget's rolling window, as the band says it: `24h` rather than
 *  `24h 00m`, because it is a setting and not a measurement. */
export function windowWords(seconds: number): string {
  return seconds % 3600 === 0 ? `${seconds / 3600}h` : duration(seconds);
}

/** What a form's controls start out holding: empty for the add form, and the
 *  follow's own columns for the edit. `Rules.from_row` parsed them, so this is
 *  the rule the check obeys rather than the string somebody typed. */
export function ruleValues(follow?: FollowRow | null) {
  if (!follow) {
    return {
      tabs: ["videos"],
      minDuration: "",
      maxDuration: "",
      titleInclude: "",
      titleExclude: "",
      channels: CHANNEL_BOXES.map(([name]) => name),
      tags: "",
      backfill: 0,
      maxPerCheck: 5,
      mode: "auto",
      interval: DEFAULT_CHECK_INTERVAL_S,
    };
  }
  return {
    tabs: follow.tabs,
    minDuration: follow.min_duration_s === null ? "" : hms(follow.min_duration_s),
    maxDuration: follow.max_duration_s === null ? "" : hms(follow.max_duration_s),
    titleInclude: follow.title_include.join(", "),
    titleExclude: follow.title_exclude.join(", "),
    // `all` is a word, not a list, and it means every box is ticked.
    channels:
      follow.channels === "all"
        ? CHANNEL_BOXES.map(([name]) => name)
        : follow.channels.split(",").map((name) => name.trim()),
    tags: follow.tags.join(", "),
    backfill: follow.backfill,
    maxPerCheck: follow.max_per_check,
    mode: follow.mode,
    interval: follow.check_interval_s,
  };
}

export type RuleValues = ReturnType<typeof ruleValues>;

/**
 * The eleven controls a rule is made of — `templates/_follow_rules.html`, once.
 *
 * The add form and the edit form differ in exactly two fields, a URL and a
 * name, which identify a follow rather than condition it. So everything a rule
 * *is* lives here and neither page owns a second copy of it, which is the
 * macro's own argument.
 *
 * `ns` prefixes every control id, because the detail page carries this twice
 * over its life and a duplicated `id` is a label pointing at the wrong field.
 * Every control is uncontrolled and seeded with `defaultValue`: the browser
 * owns what is being typed, and a form re-keyed on the row it was prefilled
 * from reseeds itself when a write returns a new one.
 */
export function RuleFields({
  ns,
  values,
  disabled,
}: {
  ns: string;
  values: RuleValues;
  disabled?: boolean;
}) {
  return (
    <>
      <fieldset className={styles.checks}>
        <legend className={styles.legend}>Which listings to watch</legend>
        {TABS.map((tab) => (
          <label className={dash.check} key={tab}>
            <input
              type="checkbox"
              name={`tab_${tab}`}
              value="1"
              defaultChecked={values.tabs.includes(tab)}
              disabled={disabled}
            />
            <span className={dash.checkWord}>/{tab}</span>
          </label>
        ))}
      </fieldset>
      <p className={styles.fieldHelp}>
        A channel&rsquo;s <code>/videos</code>, <code>/streams</code> and <code>/shorts</code> are
        three listings, and each one this follow watches is one more request per check.
      </p>

      <div className={styles.formrow}>
        <div className={`${dash.field} ${dash.text}`}>
          <label htmlFor={`${ns}-min`}>Longer than</label>
          <input
            id={`${ns}-min`}
            name="min_duration"
            type="text"
            defaultValue={values.minDuration}
            placeholder="8:00"
            autoComplete="off"
            spellCheck={false}
            disabled={disabled}
          />
        </div>
        <div className={`${dash.field} ${dash.text}`}>
          <label htmlFor={`${ns}-max`}>Shorter than</label>
          <input
            id={`${ns}-max`}
            name="max_duration"
            type="text"
            defaultValue={values.maxDuration}
            placeholder="1:30:00"
            autoComplete="off"
            spellCheck={false}
            disabled={disabled}
          />
        </div>
        <div className={`${dash.field} ${dash.narrow}`}>
          <label htmlFor={`${ns}-per`}>Per check</label>
          <input
            id={`${ns}-per`}
            name="max_per_check"
            type="number"
            min={1}
            max={MAX_PER_CHECK}
            inputMode="numeric"
            defaultValue={values.maxPerCheck}
            disabled={disabled}
          />
        </div>
        <div className={`${dash.field} ${dash.narrow}`}>
          <label htmlFor={`${ns}-back`}>Backfill</label>
          <input
            id={`${ns}-back`}
            name="backfill"
            type="number"
            min={0}
            max={MAX_BACKFILL}
            inputMode="numeric"
            defaultValue={values.backfill}
            disabled={disabled}
          />
        </div>
        <div className={`${dash.field} ${dash.text}`}>
          <label htmlFor={`${ns}-every`}>Check every</label>
          <input
            id={`${ns}-every`}
            name="check_interval_s"
            type="number"
            min={MIN_CHECK_INTERVAL_S}
            step={900}
            inputMode="numeric"
            defaultValue={values.interval}
            disabled={disabled}
          />
        </div>
        <div className={`${dash.field} ${dash.pickField}`}>
          <label htmlFor={`${ns}-mode`}>When something matches</label>
          <span className={dash.pick}>
            <select id={`${ns}-mode`} name="mode" defaultValue={values.mode} disabled={disabled}>
              {MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </select>
          </span>
        </div>
      </div>
      <p className={styles.fieldHelp}>
        Lengths read <code>480</code>, <code>8:00</code> or <code>1:30:00</code>; leave one empty
        for no bound. <em>Check every</em> is seconds, floor{" "}
        <span className={dash.mono}>{MIN_CHECK_INTERVAL_S}</span>, and a week is as far as it goes.{" "}
        <em>Backfill</em> reaches back that many uploads once, at the moment you follow.{" "}
        <code>review</code> holds every match for you instead of queueing it.
      </p>

      <div className={styles.formrow}>
        <div className={`${dash.field} ${dash.wide}`}>
          <label htmlFor={`${ns}-inc`}>Title contains</label>
          <input
            id={`${ns}-inc`}
            name="title_include"
            type="text"
            defaultValue={values.titleInclude}
            placeholder="lecture, workshop"
            autoComplete="off"
            disabled={disabled}
          />
        </div>
        <div className={`${dash.field} ${dash.wide}`}>
          <label htmlFor={`${ns}-exc`}>Title never contains</label>
          <input
            id={`${ns}-exc`}
            name="title_exclude"
            type="text"
            defaultValue={values.titleExclude}
            placeholder="shorts, trailer"
            autoComplete="off"
            disabled={disabled}
          />
        </div>
        <div className={`${dash.field} ${dash.wide}`}>
          <label htmlFor={`${ns}-tags`}>Tags</label>
          <input
            id={`${ns}-tags`}
            name="tags"
            type="text"
            defaultValue={values.tags}
            placeholder="topic:attention, series:zero-to-hero"
            autoComplete="off"
            disabled={disabled}
          />
        </div>
      </div>
      <p className={styles.fieldHelp}>
        Comma-separated plain substrings, matched case-insensitively — not patterns. Exclude wins
        over include. Tags are applied to every video this follow brings in, under the same
        namespace rules <code>tag-video</code> uses.
      </p>

      {/* The band's chip above holds a word — `/videos` — and these three hold
          a word and the sentence under it, so they take the other shape the
          index form takes: at 390 a sentence in a 34px chip wraps its word and
          runs its note out of the band. */}
      <fieldset className={dash.checkList}>
        <legend className={dash.checkLegend}>Channels to build</legend>
        {CHANNEL_BOXES.map(([name, label, note]) => (
          <label className={dash.checkNoted} key={name}>
            <input
              type="checkbox"
              name={`channel_${name}`}
              value="1"
              defaultChecked={values.channels.includes(name)}
              disabled={disabled}
            />
            <span className={dash.checkNotedWord}>{label}</span>
            <span className={dash.checkNote}>{note}</span>
          </label>
        ))}
      </fieldset>
    </>
  );
}

/** A form that posts through `postForm` rather than navigating. One `onSubmit`
 *  shape for the add form and the edit disclosure, so the fields that reach
 *  Python are collected the same way from both. */
export function RuleForm({
  className,
  onFields,
  children,
}: {
  className?: string;
  onFields: (fields: Record<string, string>) => void;
  children: ReactNode;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onFields(formFields(event.currentTarget));
  }
  return (
    <form className={className ?? styles.form} onSubmit={submit}>
      {children}
    </form>
  );
}
