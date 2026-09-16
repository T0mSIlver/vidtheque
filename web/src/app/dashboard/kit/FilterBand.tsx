"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import controls from "./controls.module.css";

/** How long a typed field waits for the typing to stop. */
export const DEBOUNCE_MS = 450;

const TYPED = "input[type='search'], input[type='text'], input[type='number']";

/**
 * A filter band: a real form over the URL, always mounted, that navigates
 * without scrolling and leaves focus on the control that was used.
 *
 * `values` are what the listing ran with (the server's resolved filters, or
 * the URL until they land). A control is re-seeded from them imperatively,
 * never by a remount — except the focused control while it still holds what
 * this band last sent, so a reply to an earlier keystroke never overwrites the
 * typing that followed it.
 *
 * `auto` submits a picker the moment it changes and a text field when the
 * typing pauses; its `Apply` button (rendered by the caller with
 * `data-apply`) is hidden once this has hydrated.
 */
export function FilterBand({
  values,
  toUrl,
  auto = false,
  role,
  children,
}: {
  values: Record<string, string | boolean>;
  toUrl: (form: FormData) => string;
  auto?: boolean;
  role?: "search";
  children: ReactNode;
}) {
  const router = useRouter();
  const form = useRef<HTMLFormElement>(null);
  const sent = useRef<Record<string, string>>({});
  const pending = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const hydrated = useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );

  function submit(element: HTMLFormElement) {
    clearTimeout(pending.current);
    const data = new FormData(element);
    const url = toUrl(data);
    sent.current = {};
    for (const [name, value] of data.entries()) {
      if (typeof value === "string") sent.current[name] = value.trim();
    }
    router.push(url, { scroll: false });
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit(event.currentTarget);
  }

  function onChange(event: FormEvent<HTMLFormElement>) {
    if (!auto) return;
    const element = event.currentTarget;
    const target = event.target;
    if (target instanceof Element && target.matches(TYPED)) {
      clearTimeout(pending.current);
      pending.current = setTimeout(() => submit(element), DEBOUNCE_MS);
    } else {
      submit(element);
    }
  }

  useEffect(() => () => clearTimeout(pending.current), []);

  const seed = JSON.stringify(values);
  useEffect(() => {
    const element = form.current;
    if (!element) return;
    const wanted = JSON.parse(seed) as Record<string, string | boolean>;
    for (const [name, value] of Object.entries(wanted)) {
      const control = element.elements.namedItem(name);
      if (control instanceof HTMLInputElement && control.type === "checkbox") {
        control.checked = value === true || value === "1";
        continue;
      }
      if (!(
        control instanceof HTMLInputElement ||
        control instanceof HTMLSelectElement ||
        control instanceof HTMLTextAreaElement
      )) {
        continue;
      }
      const next = String(value);
      if (control.value === next) continue;
      if (control === document.activeElement && sent.current[name] === next) continue;
      control.value = next;
    }
  }, [seed]);

  return (
    <form
      className={controls.filters}
      data-scripted={auto && hydrated ? "" : undefined}
      onChange={onChange}
      onSubmit={onSubmit}
      ref={form}
      role={role}
    >
      {children}
    </form>
  );
}

const noop = () => () => {};
