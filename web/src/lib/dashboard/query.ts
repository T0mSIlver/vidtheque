/**
 * The page's URL, whitelisted down to the parameters a read takes. Values pass
 * trimmed but unclamped: every clamp is Python's. Empty values drop unless
 * their key is in `keepEmpty`.
 */
export function pick(
  search: string | URLSearchParams,
  keys: readonly string[],
  { keepEmpty = [] }: { keepEmpty?: readonly string[] } = {},
): URLSearchParams {
  const from = typeof search === "string" ? new URLSearchParams(search) : search;
  const query = new URLSearchParams();
  for (const key of keys) {
    const value = from.get(key);
    if (value === null) continue;
    const trimmed = value.trim();
    if (trimmed || keepEmpty.includes(key)) query.set(key, trimmed);
  }
  return query;
}

/** `path` with `query` appended, never with a bare `?`. */
export function withQuery(path: string, query: URLSearchParams): string {
  const search = query.toString();
  return search ? `${path}?${search}` : path;
}
