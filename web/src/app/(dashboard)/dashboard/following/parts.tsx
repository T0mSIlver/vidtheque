"use client";

import type { FormEvent, ReactNode } from "react";
import type { FollowDetail, FollowRow } from "@/lib/dashboard/schemas";
import { at, DASH, duration, hms } from "@/lib/format";
import controls from "@/components/dashboard/kit/controls.module.css";
import { ui } from "@/components/dashboard/kit/ui";
import { CHANNEL_BOXES, formFields } from "@/components/dashboard/kit/write";
import styles from "./following.module.css";

// What the two following pages are built from: values composed from the
// payload's columns (§22). The rule as English stays Python's only renderer.

/** `follows/rules.py`'s vocabularies and ceilings: options on a control, never
 *  a bound — `follows/params.py` is the only thing that clamps. */
export const TABS = ["videos", "streams", "shorts"] as const;
export const MODES = ["auto", "review"] as const;
export const MAX_BACKFILL = 25;
export const MAX_PER_CHECK = 25;
export const MIN_CHECK_INTERVAL_S = 900;
export const DEFAULT_CHECK_INTERVAL_S = 21_600;

/** The rule as facts that fit a table cell (§18.3). Lengths are `hms` so a
 *  floor and a ceiling read on one scale. */
export function ruleFacts(follow: FollowRow): string[] {
  const facts = [follow.tabs.map((tab) => `/${tab}`).join(", ")];
  const low = follow.min_duration_s;
  const high = follow.max_duration_s;
  if (low !== null && high !== null) facts.push(`${hms(low)}–${hms(high)}`);
  else if (low !== null) facts.push(`${hms(low)} floor`);
  else if (high !== null) facts.push(`${hms(high)} ceiling`);
  const terms = follow.title_include.length + follow.title_exclude.length;
  if (terms) facts.push(`${terms} title term(s)`);
  if (follow.channels !== "all") facts.push(`${follow.channels} only`);
  facts.push(`${follow.max_per_check}/check`);
  facts.push(`every ${duration(follow.check_interval_s)}`);
  if (follow.mode === "review") facts.push("held for review");
  return facts;
}

/** The facts drawn; `wide` is the follow's own page, one size up. */
export function RuleFacts({ follow, wide }: { follow: FollowRow; wide?: boolean }) {
  const facts = ruleFacts(follow);
  return (
    <span className={wide ? styles.rule : styles.facts}>
      {facts.map((fact, index) => (
        <span className={wide ? undefined : styles.fact} key={fact}>
          {fact}
          {index < facts.length - 1 ? <span className={ui.sep}>·</span> : null}
        </span>
      ))}
    </span>
  );
}

/** The near-miss sentence; the payload sends `null` when there is nothing to
 *  say, and nothing is printed then. */
export function nearMissLine(near: NonNullable<FollowDetail["near_miss"]>): string {
  const verb = near.count === 1 ? "was" : "were";
  return `${near.count} of the last ${near.of} passed over ${verb} within ${near.within_s} seconds of your ${near.edge}.`;
}

/** Would the scheduler enqueue this follow's check (the store's `_SCHEDULABLE`)? */
export function schedulable(follow: FollowRow): boolean {
  return follow.state === "active" || follow.retrying;
}

/** The retry count beside a `failing` pill, in its tone (following.md §11.4). */
export function RetryFact({ follow, wide }: { follow: FollowRow; wide?: boolean }) {
  const text = wide
    ? follow.retrying
      ? `retry ${follow.fail_count} of ${follow.max_tries}, once a day`
      : `gave up after ${follow.fail_count} tries`
    : follow.retrying
      ? `retry ${follow.fail_count} of ${follow.max_tries}`
      : "gave up";
  return (
    <span className={styles.fact} data-tone={follow.retrying ? "warn" : "bad"}>
      {text}
    </span>
  );
}

/**
 * When this follow is next looked at, or why that is not a time: checks off
 * (§22), the dash for a follow nothing will enqueue (0008), `due now` for the
 * `0` Check now leaves behind.
 */
export function nextCheckWords(follow: FollowRow, checksEnabled?: boolean): string {
  if (checksEnabled === false) return "checks off";
  if (!schedulable(follow)) return DASH;
  if (!follow.next_check_at) return "due now";
  return at(follow.next_check_at);
}

/** The budget window as a setting: `24h`, not `24h 00m`. */
export function windowWords(seconds: number): string {
  return seconds % 3600 === 0 ? `${seconds / 3600}h` : duration(seconds);
}

/** A form's starting values: empty for the add form, the parsed row for the
 *  edit. */
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

/** The eleven controls a rule is made of, shared by the add and edit forms.
 *  `ns` prefixes ids, because the detail page can carry both. */
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
        <legend className={styles.legend}>Listings to watch</legend>
        {TABS.map((tab) => (
          <label className={controls.check} key={tab}>
            <input
              type="checkbox"
              name={`tab_${tab}`}
              value="1"
              defaultChecked={values.tabs.includes(tab)}
              disabled={disabled}
            />
            <span className={controls.checkWord}>/{tab}</span>
          </label>
        ))}
      </fieldset>
      <p className={styles.fieldHelp}>One request per listing per check.</p>

      <div className={styles.formrow}>
        <div className={`${controls.field} ${controls.text}`}>
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
        <div className={`${controls.field} ${controls.text}`}>
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
        <div className={`${controls.field} ${controls.narrow}`}>
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
        <div className={`${controls.field} ${controls.narrow}`}>
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
        <div className={`${controls.field} ${controls.text}`}>
          <label htmlFor={`${ns}-every`}>Check every (s)</label>
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
        <div className={`${controls.field} ${controls.pickField}`}>
          <label htmlFor={`${ns}-mode`}>On match</label>
          <span className={controls.pick}>
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
        Lengths as <code>480</code> or <code>8:00</code>. <em>Backfill</em> reaches back once;{" "}
        <code>review</code> holds matches for you.
      </p>

      <div className={styles.formrow}>
        <div className={`${controls.field} ${controls.wide}`}>
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
        <div className={`${controls.field} ${controls.wide}`}>
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
        <div className={`${controls.field} ${controls.wide}`}>
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
        Case-insensitive substrings; <em>never contains</em> wins. Tags are applied to every video
        brought in.
      </p>

      <fieldset className={styles.checks}>
        <legend className={styles.legend}>Channels to build</legend>
        {CHANNEL_BOXES.map(([name, label]) => (
          <label className={controls.check} key={name}>
            <input
              type="checkbox"
              name={`channel_${name}`}
              value="1"
              defaultChecked={values.channels.includes(name)}
              disabled={disabled}
            />
            <span className={controls.checkWord}>{label}</span>
          </label>
        ))}
      </fieldset>
    </>
  );
}

/** A form that hands its fields to a write instead of navigating. */
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
