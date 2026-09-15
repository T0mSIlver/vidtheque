"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, use, useEffect, useRef, useState, useTransition } from "react";
import { ContentType } from "@/lib/api/schemas";
import styles from "./SearchBox.module.css";

// The one interactive boundary on the demo page in search mode. The URL is the
// state: submitting navigates to `/demo?q=…&type=…`, and the Server Component
// that owns the results re-renders for the new URL. No fetch here, no results
// state, no sequence guard against stale responses — the router serialises
// navigations, which is the job app.js did by hand with `state.seq`.
//
// Enter is the only thing that spends a request: no search-as-you-type
// against a shared rate limit.
const TYPES: { value: ContentType; label: string }[] = [
  { value: "all", label: "all" },
  { value: "transcript", label: "transcript" },
  { value: "ocr", label: "on-screen text" },
  { value: "frame", label: "frames" },
];

/** The machine's own word for what it is doing (demo-site.md §6 item 2). */
export type MachineState =
  "ready" | "scanning" | "reading" | "no hits" | "refused" | "no reply" | "rate limited";

// The word is the message and the colour only reinforces it (DESIGN.md, the
// Word-and-Colour Rule), which is why both come from one place.
const TONE: Record<MachineState, string> = {
  ready: "ready",
  scanning: "working",
  reading: "working",
  "no hits": "ready",
  refused: "refused",
  "no reply": "refused",
  "rate limited": "refused",
};

/**
 * The bar's own mark: a prompt's chevron, in gold, in its own cell.
 *
 * It is what makes the box read as a console line rather than as a text field,
 * and it is the same mark in both modes — the class is the caller's, because
 * each mode's bar is styled in its own module.
 */
export function QueryMark({ className }: { className: string }) {
  return (
    <span className={className} aria-hidden="true">
      <svg viewBox="0 0 12 12">
        <path d="M3.6 1.8 L8.2 6 L3.6 10.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
      </svg>
    </span>
  );
}

export function StateCell({ state }: { state: MachineState }) {
  return (
    <span className={styles.state} data-s={TONE[state]}>
      {state}
    </span>
  );
}

/**
 * The cell, when the word is still on its way.
 *
 * The bar is in the markup at first paint and the read that says how it ended
 * is not, so the word is the one thing here that suspends — a leaf, inside the
 * cell's own place, so the input beside it is never a thing React replaces.
 */
function PendingStateCell({
  state,
  whileWaiting,
}: {
  state: Promise<MachineState>;
  whileWaiting: MachineState;
}) {
  return (
    <Suspense fallback={<StateCell state={whileWaiting} />}>
      <ResolvedStateCell state={state} />
    </Suspense>
  );
}

function ResolvedStateCell({ state }: { state: Promise<MachineState> }) {
  return <StateCell state={use(state)} />;
}

/**
 * The mode switch, once the boot call has said whether ask exists at all.
 *
 * `ask_enabled: false` is a deployment with no key, and it is **the one load
 * that swaps the mode on screen** — which is why the swap happens here rather
 * than before the box is drawn. Waiting on `/api/meta` to decide which box to
 * draw costs every visitor a round trip with nothing to type into, to spare a
 * misconfigured deployment one correction (demo-site.md §6.1).
 *
 * The correction is a `replace` and not a re-render, so the URL says `ask=0`
 * afterwards — the same thing `setAskMode(false)` wrote with `replaceState`.
 */
export function AskSwitch({
  ask,
  q,
  enabled,
  onLeave,
}: {
  ask: boolean;
  q: string;
  enabled: boolean | Promise<boolean>;
  onLeave?: () => void;
}) {
  if (typeof enabled === "boolean") {
    return <ModeSwitch ask={ask} q={q} enabled={enabled} onLeave={onLeave} />;
  }
  return (
    <Suspense fallback={null}>
      <ResolvedAskSwitch ask={ask} q={q} enabled={enabled} onLeave={onLeave} />
    </Suspense>
  );
}

function ResolvedAskSwitch({
  ask,
  q,
  enabled,
  onLeave,
}: {
  ask: boolean;
  q: string;
  enabled: Promise<boolean>;
  onLeave?: () => void;
}) {
  const available = use(enabled);
  const router = useRouter();
  // Once, and only in the deployment that has no ask to offer. `q` is in the
  // deps because the correction carries the typed question with it, and the
  // latch is what stops a keystroke from firing a second navigation.
  const sent = useRef(false);
  useEffect(() => {
    if (available || !ask || sent.current) return;
    sent.current = true;
    const typed = q.trim();
    router.replace(typed ? `/demo?ask=0&q=${encodeURIComponent(typed)}` : "/demo?ask=0");
  }, [available, ask, q, router]);

  if (!available) return null;
  return <ModeSwitch ask={ask} q={q} enabled onLeave={onLeave} />;
}

/**
 * The mode switch, as a pressed chip pair (demo-site.md §6 item 3).
 *
 * Hidden entirely when `ask_enabled` is false: a switch into a mode that 503s
 * is worse than no switch, and the deployment with no key is the one load that
 * is allowed to swap the mode on screen.
 *
 * The URL is three-valued and each value is written explicitly, because ask is
 * the default: absent means the default, `?ask=1` loads a question without
 * firing it, and `?ask=0` is search, so a link copied out of search reopens in
 * search (§6.2).
 */
export function ModeSwitch({
  ask,
  q,
  enabled,
  onLeave,
}: {
  ask: boolean;
  q: string;
  enabled: boolean;
  /** Ask mode's chance to abort whatever it had in flight before the mode it
   *  belonged to leaves the screen. */
  onLeave?: () => void;
}) {
  const router = useRouter();
  // The pin is the URL's, not this component's: in ask mode there is no chip
  // row to read it off, and the switch still has to hand it back.
  const pinned = useSearchParams().get("type");
  if (!enabled) return null;

  function go(next: boolean) {
    if (next === ask) return;
    onLeave?.();
    const params = new URLSearchParams({ ask: next ? "1" : "0" });
    if (q.trim()) params.set("q", q.trim());
    // The pinned channel survives the switch. `setAskMode` rewrote one search
    // parameter and left the rest of the URL alone; rebuilding it from scratch
    // dropped `type`, so a visitor who had pinned "on-screen text", looked at
    // an answer and came back was silently searching all four channels again.
    if (pinned && pinned !== "all") params.set("type", pinned);
    router.push(`/demo?${params}`);
  }

  return (
    <div className={styles.modes} role="group" aria-label="Result mode">
      <button type="button" aria-pressed={!ask} onClick={() => go(false)}>
        search
      </button>
      <button type="button" aria-pressed={ask} onClick={() => go(true)}>
        ask ✨
      </button>
    </div>
  );
}

export function SearchBox({
  state = "ready",
  whileWaiting = "ready",
  askEnabled = false,
  onPending,
}: {
  /** The machine's word, or the read that will say it (`Query`). */
  state?: MachineState | Promise<MachineState>;
  /** What the cell prints while a promised word is still on its way. */
  whileWaiting?: MachineState;
  askEnabled?: boolean | Promise<boolean>;
  /** A navigation is out. The caller reserves the results' space while it is
   *  (see `app/demo/Query.tsx`); the state cell says the word here. */
  onPending?: (pending: boolean) => void;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const urlQ = params.get("q") ?? "";
  const urlType = ContentType.catch("all").parse(params.get("type") ?? "all");
  const input = useRef<HTMLInputElement>(null);

  // The box is the URL's, except while it is being typed into. So: keep the
  // draft across renders, and take the URL back the moment it genuinely
  // changes under us — an example link, the back button, a link from another
  // page. Seeding from the URL once left the box showing a stale query after
  // every navigation this form did not make.
  const [draft, setDraft] = useState({ q: urlQ, type: urlType });
  const [seen, setSeen] = useState({ q: urlQ, type: urlType });
  // "Try one of the examples" clears the box, resets the channel and puts the
  // cursor back in it. The first two are the link's — it navigates to a URL
  // with neither — and this is the third: a navigation that emptied a query
  // the visitor had is the cold page arriving, and the cold page is a box
  // waiting to be typed in.
  const [emptied, setEmptied] = useState(false);
  if (seen.q !== urlQ || seen.type !== urlType) {
    setSeen({ q: urlQ, type: urlType });
    setDraft({ q: urlQ, type: urlType });
    setEmptied(Boolean(seen.q) && !urlQ);
  }
  const { q, type } = draft;

  useEffect(() => {
    if (emptied) input.current?.focus();
  }, [emptied]);

  // A transition keeps the current page interactive while the next one
  // renders on the server, and `pending` is the honest signal that the
  // machine is working.
  const [pending, startTransition] = useTransition();
  useEffect(() => onPending?.(pending), [pending, onPending]);

  function run(query: string, channel: ContentType) {
    const next = new URLSearchParams();
    if (query.trim()) next.set("q", query.trim());
    if (channel !== "all") next.set("type", channel);
    if (askEnabled) next.set("ask", "0");
    startTransition(() => router.push(`/demo?${next}`));
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Enter (or the button) is the only thing that spends a request; the blur
    // is what dismisses a phone's keyboard over the results it just asked for.
    input.current?.blur();
    run(q, type);
  }

  // A chip is the filter, so picking one re-runs the search that is on screen
  // — the pin lands in the URL and the widening tip in the empty state already
  // knows how to undo it. With no query there is nothing to re-run, and the
  // chip is just the channel the next search will use.
  function pick(channel: ContentType) {
    setDraft((d) => ({ ...d, type: channel }));
    if (q.trim()) run(q, channel);
  }

  return (
    <form role="search" onSubmit={submit} className={styles.form} aria-busy={pending}>
      <label className={styles.srOnly} htmlFor="q">
        Search this video corpus
      </label>
      <div className={styles.bar}>
        <QueryMark className={styles.ic} />
        <input
          id="q"
          ref={input}
          type="search"
          name="q"
          value={q}
          onChange={(e) => setDraft((d) => ({ ...d, q: e.target.value }))}
          placeholder="kv cache, nvidia-smi, ontology…"
          enterKeyHint="search"
          spellCheck={false}
          autoComplete="off"
          // The page is a search surface and the box is its one primary
          // action, which is why the original carried this in the markup.
          autoFocus
          className={styles.input}
        />
        {pending ? (
          <StateCell state="scanning" />
        ) : typeof state === "string" ? (
          <StateCell state={state} />
        ) : (
          <PendingStateCell state={state} whileWaiting={whileWaiting} />
        )}
        <button type="submit" className={styles.go} disabled={pending}>
          Search
        </button>
      </div>
      <div className={styles.controls}>
        {/* Toggles, not radios: a screen reader should hear the state rather
            than infer it from colour (demo-site.md §6.2). */}
        <div className={styles.chips} role="group" aria-label="Search which channel">
          {TYPES.map((t) => (
            <button
              key={t.value}
              type="button"
              aria-pressed={type === t.value}
              onClick={() => pick(t.value)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <AskSwitch ask={false} q={q} enabled={askEnabled} />
      </div>
    </form>
  );
}
