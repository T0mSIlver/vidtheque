// This browser's random id, sent with each ask so the answer to a dropped
// stream can be picked up again (demo-site.md §3.6). No cookie: it lives in
// localStorage, and where storage is refused it lasts as long as the page.
const KEY = "vidtheque.visitor";
const SHAPE = /^[A-Za-z0-9_-]{16,64}$/;

let memo: string | null = null;

function fresh(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return "v-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function visitorId(): string {
  if (memo) return memo;
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(KEY);
  } catch {
    // Private mode or blocked storage: an id for this page only.
  }
  memo = stored && SHAPE.test(stored) ? stored : fresh();
  if (memo !== stored) {
    try {
      window.localStorage.setItem(KEY, memo);
    } catch {
      // Same as above.
    }
  }
  return memo;
}
