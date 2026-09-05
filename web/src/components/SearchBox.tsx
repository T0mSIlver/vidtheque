"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState, useTransition } from "react";
import { ContentType } from "@/lib/api/schemas";
import styles from "./SearchBox.module.css";

// The one interactive boundary on the demo page. The URL is the state:
// submitting navigates to `/demo?q=…&type=…`, and the Server Component that
// owns the results re-renders for the new URL. No fetch here, no results state,
// no sequence guard against stale responses — the router serialises
// navigations, which is the job app.js did by hand with `state.seq`.
//
// Enter is the only thing that spends a request: no search-as-you-type
// against a shared rate limit.
const TYPES: { value: ContentType; label: string }[] = [
  { value: "all", label: "all" },
  { value: "transcript", label: "spoken" },
  { value: "ocr", label: "on-screen" },
  { value: "frame", label: "frames" },
];

export function SearchBox() {
  const router = useRouter();
  const params = useSearchParams();
  const urlQ = params.get("q") ?? "";
  const urlType = ContentType.catch("all").parse(params.get("type") ?? "all");

  // The box is the URL's, except while it is being typed into. So: keep the
  // draft across renders, and take the URL back the moment it genuinely
  // changes under us — an example link, the back button, a link from another
  // page. Seeding from the URL once left the box showing a stale query after
  // every navigation this form did not make.
  const [draft, setDraft] = useState({ q: urlQ, type: urlType });
  const [seen, setSeen] = useState({ q: urlQ, type: urlType });
  if (seen.q !== urlQ || seen.type !== urlType) {
    setSeen({ q: urlQ, type: urlType });
    setDraft({ q: urlQ, type: urlType });
  }
  const { q, type } = draft;

  // A transition keeps the current page interactive while the next one
  // renders on the server, and `pending` is the honest signal that the
  // machine is working.
  const [pending, startTransition] = useTransition();

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = new URLSearchParams();
    if (q.trim()) next.set("q", q.trim());
    if (type !== "all") next.set("type", type);
    startTransition(() => router.push(`/demo?${next}`));
  }

  return (
    <form role="search" onSubmit={submit} className={styles.form} aria-busy={pending}>
      <div className={styles.bar}>
        <input
          type="search"
          name="q"
          value={q}
          onChange={(e) => setDraft((d) => ({ ...d, q: e.target.value }))}
          placeholder="what did they say about…"
          aria-label="Search the corpus"
          autoComplete="off"
          className={styles.input}
        />
        <button type="submit" className={styles.go} disabled={pending}>
          {pending ? "scanning…" : "search"}
        </button>
      </div>
      <div className={styles.chips} role="radiogroup" aria-label="Kind of evidence">
        {TYPES.map((t) => (
          <button
            key={t.value}
            type="button"
            role="radio"
            aria-checked={type === t.value}
            onClick={() => setDraft((d) => ({ ...d, type: t.value }))}
            className={styles.chip}
          >
            {t.label}
          </button>
        ))}
      </div>
    </form>
  );
}
