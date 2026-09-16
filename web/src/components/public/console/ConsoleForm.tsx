import type { Ref } from "react";
import type { ContentType } from "@/lib/api/schemas";
import type { Mode } from "./url";
import styles from "./console.module.css";

/** The machine's own word for what it is doing (demo-site.md §6 item 2). */
export type MachineState =
  "ready" | "scanning" | "reading" | "no hits" | "refused" | "no reply" | "rate limited";

// The word is the message; the tone only reinforces it (DESIGN.md).
const TONE: Record<MachineState, string> = {
  ready: "ready",
  scanning: "working",
  reading: "working",
  "no hits": "ready",
  refused: "refused",
  "no reply": "refused",
  "rate limited": "refused",
};

const CHIPS: { value: ContentType; label: string }[] = [
  { value: "all", label: "all" },
  { value: "transcript", label: "transcript" },
  { value: "ocr", label: "on-screen text" },
  { value: "frame", label: "frames" },
];

export function StateCell({ state }: { state: MachineState }) {
  return (
    <span className={styles.state} data-s={TONE[state]}>
      {state}
    </span>
  );
}

/**
 * One form for both modes. The input is the same element whichever mode is on
 * screen, so switching keeps its value, caret and focus.
 */
export function ConsoleForm({
  mode,
  askEnabled,
  draft,
  channel,
  word,
  busy,
  inputRef,
  autoFocus,
  onDraft,
  onSubmit,
  onChannel,
  onMode,
}: {
  mode: Mode;
  askEnabled: boolean;
  draft: string;
  channel: ContentType;
  word: MachineState;
  busy: boolean;
  inputRef: Ref<HTMLInputElement>;
  autoFocus?: boolean;
  onDraft: (value: string) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  onChannel: (value: ContentType) => void;
  onMode: (mode: Mode) => void;
}) {
  const ask = mode === "ask";
  return (
    <form role="search" onSubmit={onSubmit} className={styles.form} aria-busy={busy}>
      {/* Tabs on the bar's top edge: the same place in both modes. */}
      {askEnabled ? (
        <div className={styles.modes} role="group" aria-label="Result mode">
          <button type="button" aria-pressed={!ask} onClick={() => onMode("search")}>
            search
          </button>
          <button type="button" aria-pressed={ask} onClick={() => onMode("ask")}>
            ask ✨
          </button>
        </div>
      ) : null}
      <label className={styles.srOnly} htmlFor="q">
        {ask ? "Your question" : "Search this video corpus"}
      </label>
      <div className={styles.bar}>
        <span className={styles.ic} aria-hidden="true">
          <svg viewBox="0 0 12 12">
            <path
              d="M3.6 1.8 L8.2 6 L3.6 10.2"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
            />
          </svg>
        </span>
        <input
          id="q"
          ref={inputRef}
          type="search"
          name="q"
          value={draft}
          onChange={(event) => onDraft(event.target.value)}
          placeholder={
            ask ? "ask a question about AI engineering…" : "kv cache, nvidia-smi, ontology…"
          }
          enterKeyHint="search"
          spellCheck={false}
          autoComplete="off"
          autoFocus={autoFocus}
          className={styles.input}
        />
        <StateCell state={word} />
        {/* Both labels share one cell, so the button is as wide in either mode. */}
        <button type="submit" className={styles.go} disabled={busy}>
          <span className={ask ? styles.off : undefined} aria-hidden={ask}>
            Search
          </span>
          <span className={ask ? undefined : styles.off} aria-hidden={!ask}>
            Ask ✨
          </span>
        </button>
      </div>
      {/* The chips and the ask note share one cell, so the row keeps its height. */}
      <div className={styles.controls}>
        <div
          className={ask ? `${styles.chips} ${styles.off}` : styles.chips}
          role="group"
          aria-label="Search which channel"
          inert={ask}
        >
          {CHIPS.map((chip) => (
            <button
              key={chip.value}
              type="button"
              aria-pressed={channel === chip.value}
              onClick={() => onChannel(chip.value)}
            >
              {chip.label}
            </button>
          ))}
        </div>
        <p className={ask ? styles.askNote : `${styles.askNote} ${styles.off}`} aria-hidden={!ask}>
          The model picks the channels, and every answer cites the second it read.
        </p>
      </div>
    </form>
  );
}
