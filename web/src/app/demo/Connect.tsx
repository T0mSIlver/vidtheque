"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./connect.module.css";

// "Add this corpus to your own agent" (demo-site.md §6 item 6). The endpoint is
// the server's to state — it is the same string the OAuth `resource` uses — so
// the page never hardcodes a hostname, and a boot that could not read it prints
// why rather than a guess.
//
// Two things worth copying, one behaviour: the endpoint, and the line somebody
// actually pastes. A clipboard that refuses — no permission, no secure context
// — selects the text instead, so there is always a way to take it.

const RESET_MS = 1600;

export function Connect({
  mcpUrl,
  command,
  unavailable,
}: {
  mcpUrl: string | null;
  command: string | null;
  /** What to print in place of the endpoint when the boot call did not land. */
  unavailable?: string;
}) {
  // One live region for both rows: what was copied is the whole announcement,
  // and two of them would be two things a screen reader has to keep apart.
  const [said, setSaid] = useState("");

  return (
    <section className={styles.connect}>
      <div className={styles.inner}>
        <p className={styles.kick}>
          <s />
          <span>yours, mid-task</span>
        </p>
        <h2 className={styles.head}>Add this corpus to your own agent</h2>
        <p className={styles.lede}>
          The same corpus this page is searching, on tap for whatever you are building.
        </p>
        <div className={styles.box}>
          <p className={styles.phead}>mcp endpoint</p>
          <CopyRow
            text={mcpUrl}
            fallback={unavailable}
            said="Endpoint copied to the clipboard."
            onSay={setSaid}
          />
          <p className={styles.phead}>claude code</p>
          <CopyRow
            text={command}
            fallback={unavailable}
            prompt
            said="Command copied to the clipboard."
            onSay={setSaid}
          />
        </div>
        <p className={styles.srOnly} role="status">
          {said}
        </p>
      </div>
    </section>
  );
}

function CopyRow({
  text,
  fallback,
  prompt = false,
  said,
  onSay,
}: {
  text: string | null;
  fallback?: string;
  prompt?: boolean;
  said: string;
  onSay: (text: string) => void;
}) {
  const source = useRef<HTMLElement>(null);
  const [label, setLabel] = useState("copy");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  async function copy() {
    if (!text) return;
    let next = "copied";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // No clipboard: select the text so there is still a way to take it.
      next = "select it";
      const range = document.createRange();
      if (source.current) range.selectNodeContents(source.current);
      const selection = getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    }
    setLabel(next);
    onSay(next === "copied" ? said : "");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setLabel("copy");
      onSay("");
    }, RESET_MS);
  }

  return (
    <div className={styles.row}>
      <code className={styles.code}>
        {prompt && text ? <i className={styles.prompt}>$</i> : null}
        <span ref={source}>{text ?? fallback ?? "unavailable — reload the page"}</span>
      </code>
      {/* A copy button with nothing to copy is disabled rather than lying. */}
      <button type="button" className={styles.copy} onClick={copy} disabled={!text}>
        {label}
      </button>
    </div>
  );
}
