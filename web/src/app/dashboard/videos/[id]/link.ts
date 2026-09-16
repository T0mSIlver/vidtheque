// Pointing at a shot bar lights its frame cards, and a card lights its bar.

/** Which shot a pointer or focus event is about: bars and frame cards both
 *  carry `data-shot`. */
export function shotOf(target: EventTarget | null): string | null {
  return target instanceof Element
    ? (target.closest("[data-shot]")?.getAttribute("data-shot") ?? null)
    : null;
}

/**
 * Light every bar and card of one shot. Outside React state on purpose: a
 * hover must not re-render the timeline, every card and the transcript.
 */
export function linkShot(root: HTMLElement, shot: string | null): void {
  for (const element of root.querySelectorAll("[data-linked]")) {
    if (element.getAttribute("data-shot") !== shot) element.removeAttribute("data-linked");
  }
  if (shot === null || !/^\d+$/.test(shot)) return;
  for (const element of root.querySelectorAll(`[data-shot="${shot}"]`)) {
    element.setAttribute("data-linked", "");
  }
}
