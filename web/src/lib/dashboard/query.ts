/**
 * The page's URL, filtered down to the parameters a read takes.
 *
 * A whitelist, because this string becomes a request. Values go as typed and
 * trimmed: every clamp is Python's, and one applied here would be a bound the
 * reader is never told about. An empty value is dropped unless its key is in
 * `keepEmpty` (search's `q`, whose emptiness is the handler's to refuse).
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
