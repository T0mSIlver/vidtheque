"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import styles from "./connect.module.css";

const RESET_MS = 1600;

const ROWS = [
  { label: "mcp endpoint", template: "<mcp_url>", prompt: false },
  {
    label: "claude code",
    template: "claude mcp add --transport http vidtheque <mcp_url>",
    prompt: true,
  },
  { label: "codex", template: "codex mcp add vidtheque --url <mcp_url>", prompt: true },
  {
    label: "mistral vibe",
    template: "vibe mcp add vidtheque --url <mcp_url>",
    prompt: true,
  },
] as const;

/**
 * The endpoint and client commands, each with a copy button. A
 * clipboard that refuses selects the text instead; one live region announces
 * what was copied.
 */
export function CopyRows({
  mcpUrl,
  unavailable,
}: {
  mcpUrl: string | null;
  /** Printed in place of the endpoint when the boot call did not land. */
  unavailable: string;
}) {
  const [said, setSaid] = useState("");
  return (
    <>
      <div className={styles.box}>
        {ROWS.map(({ label, template, prompt }) => (
          <Fragment key={label}>
            <p className={styles.phead}>{label}</p>
            <CopyRow
              text={mcpUrl ? template.replace("<mcp_url>", mcpUrl) : null}
              fallback={unavailable}
              prompt={prompt}
              said={`${label} copied to the clipboard.`}
              onSay={setSaid}
            />
          </Fragment>
        ))}
      </div>
      <p className={styles.srOnly} role="status">
        {said}
      </p>
    </>
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
  fallback: string;
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
        <span ref={source}>{text ?? fallback}</span>
      </code>
      <button type="button" className={styles.copy} onClick={copy} disabled={!text}>
        {label}
      </button>
    </div>
  );
}
