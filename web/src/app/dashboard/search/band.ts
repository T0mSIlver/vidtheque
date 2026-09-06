"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type RefCallback } from "react";

// The filter band's script, for the two pages that carry one — `dashboard.js`'s
// `data-autosubmit` appender (`static/dashboard.js:595-658`), as a hook.
//
// **This file belongs at `web/src/app/dashboard/band.ts`.** It is under
// `search/` only because the port split the dashboard between two agents and
// this one owns `search/**`; the videos table imports it across that boundary
// until it can be lifted.
//
// What it buys is a band you *use* rather than a form you fill in and then have
// to remember to submit: a picker submits the moment it changes, a text field
// submits when the typing pauses. The `Apply` button is real, is what a browser
// that never ran this uses, and comes off the page only once this has taken the
// job over. Nothing about the URL, the clamps or the server changes — this
// submits exactly the form the button submits.
//
// The one thing a reloading search box owes its reader is the caret back. The
// field that caused a submit is remembered for the length of that navigation
// and re-focused on the way in, with the caret at the end of what was typed.
// `sessionStorage` rather than the URL: which control had focus is not a fact
// about the result set and has no business in a link somebody sends.

export const FOCUS_KEY = "vidtheque:filters:focus";

/** How long a typed field waits for the typing to stop, in milliseconds. */
export const DEBOUNCE_MS = 450;

/** The controls that are half-typed for most of their life. Everything else in
 *  a band — a picker, a date — has no half-made state: the moment it changes,
 *  the reader has said what they want. */
const TYPED = "input[type='search'], input[type='text'], input[type='number']";

/** Which of a text field's cousins can hold a caret at all. `setSelectionRange`
 *  raises on the others (`number`, `date`), which is why the original guarded
 *  `number` by name and wrapped the lot in a `try`. */
const CARETED = new Set(["text", "search", "tel", "url", "password"]);

export interface FilterBand {
  /** The band's own `ref`. A callback and not an object: the form is re-keyed
   *  on the query string, so a navigation hands over a *new* node — which is
   *  exactly the moment the listeners and the caret are owed. */
  attach: RefCallback<HTMLFormElement>;
  /** `true` once the listeners are on: what hides `Apply`. */
  scripted: boolean;
  /** The band's `onSubmit`. Enter still submits, and must not leave a debounce
   *  armed behind it. */
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

/** `settled` is whether the read behind the band has landed. The band is
 *  re-keyed on the server's resolved filters as well as on the URL, so a
 *  navigation hands over two form nodes rather than one — the reader's URL
 *  first, the answer to it a read later — and the caret is owed to both. The
 *  remembered id is therefore released on the second, not the first. */
export function useFilterBand(submit: (form: HTMLFormElement) => void, settled = true): FilterBand {
  const [scripted, setScripted] = useState(false);
  const pending = useRef(0);
  // The caller closes over the router and the URL, so it may be a new function
  // on every render; the listeners are attached once per form node and must not
  // be torn down and rebuilt for that. Kept current in an effect rather than
  // written during render, which is a ref read one commit later than the render
  // that changed it — and what it closes over does not change inside one.
  const latest = useRef(submit);
  useEffect(() => {
    latest.current = submit;
  }, [submit]);

  const fire = useCallback((form: HTMLFormElement, source: EventTarget | null) => {
    window.clearTimeout(pending.current);
    remember(source);
    latest.current(form);
  }, []);

  const onSubmit = useCallback((event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    window.clearTimeout(pending.current);
    latest.current(event.currentTarget);
  }, []);

  const attach = useCallback<RefCallback<HTMLFormElement>>(
    (form) => {
      if (!form) return;
      const changed = (event: Event) => {
        if (!isTyped(event.target)) fire(form, event.target);
      };
      // `input` and not `change`: `change` on a text field fires on blur, which
      // is both too late and a second submit behind the one that already ran.
      const typing = (event: Event) => {
        if (!isTyped(event.target)) return;
        const source = event.target;
        window.clearTimeout(pending.current);
        pending.current = window.setTimeout(() => fire(form, source), DEBOUNCE_MS);
      };
      form.addEventListener("change", changed);
      form.addEventListener("input", typing);
      setScripted(true);
      restore(form, settled);
      return () => {
        window.clearTimeout(pending.current);
        form.removeEventListener("change", changed);
        form.removeEventListener("input", typing);
      };
    },
    // `settled` is in the dependencies rather than in a ref: when it flips, the
    // listeners are handed back and the caret is offered again, which is the
    // second of the two nodes one navigation produces.
    [fire, settled],
  );

  return { attach, scripted, onSubmit };
}

function isTyped(target: EventTarget | null): boolean {
  return target instanceof Element && target.matches(TYPED);
}

/** Which control caused this navigation, for the length of it. */
function remember(source: EventTarget | null) {
  try {
    const id = source instanceof HTMLElement ? source.id : "";
    if (id) sessionStorage.setItem(FOCUS_KEY, id);
  } catch {
    // A tab with storage refused still searches; it only loses the caret.
  }
}

/** The caret, back in the field that sent the reader here. */
function restore(form: HTMLFormElement, settled: boolean) {
  try {
    const wanted = sessionStorage.getItem(FOCUS_KEY);
    if (!wanted) return;
    if (settled) sessionStorage.removeItem(FOCUS_KEY);
    const field = form.querySelector(`#${CSS.escape(wanted)}`);
    if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement)) return;
    field.focus({ preventScroll: true });
    if (field instanceof HTMLInputElement && CARETED.has(field.type)) {
      const at = field.value.length;
      field.setSelectionRange(at, at);
    }
  } catch {
    // Same: no storage, no caret, still a working band.
  }
}
