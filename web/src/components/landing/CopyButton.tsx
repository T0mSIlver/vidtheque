"use client";

import { useEffect, useRef, useState } from "react";

// The quickstart's copy control. No clipboard, no drama: the command is
// already on screen and selectable, so a refusal costs nothing.
export function CopyButton({ value, label = "copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // no clipboard, no drama
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1400);
  }

  return (
    <button type="button" onClick={copy}>
      {copied ? "copied" : label}
    </button>
  );
}
