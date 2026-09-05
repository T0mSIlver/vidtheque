// `GET /dashboard/api/search`, as `mcp/tests/test_dashboard.py`'s fixture corpus
// answers it. Dumped from the Python test fixtures rather than written by hand,
// so a field that changes shape on the other side of the boundary changes here
// too and the page tests notice.
//
// It is the facade's own handler under the dashboard prefix (dashboard.md
// §14.2), so what arrives is `search_payload`'s payload whole: the hits, the
// leg counts, the notes with their `note:` marker stripped by `humanize.notes`,
// and `data_status` on the empty path only.
//
// **The clamp is the caller's, not the prefix's** (demo-site.md §2, amended for
// phase 5). A bearer, a session or a trusted peer gets `OWNER_CLAMPS` — a
// default page of 20 and a ceiling of 50; everyone else gets `PUBLIC_CLAMPS`,
// which is a default of 10 and a ceiling of 20, on this prefix as well. That is
// the whole of the difference between the two listings below, and it is why the
// page reads its page size off `pagination` rather than knowing one.
//
// The two frame URLs on a hit are the demo's widths (320 and 960) against
// `PUBLIC_URL`, signed here because the fixture deployment is in `token` mode.
// The page uses neither: it builds `/frames/<id>.jpg?w=192&q=70` itself, at the
// dashboard's own widths and on its own origin (§14.2, "the frame widths").

const KARPATHY_OCR = {
  source: "ocr",
  video_id: "kCc8FmEb1nY",
  title: "Let's build GPT: from scratch",
  channel: "Andrej Karpathy",
  start: 5.0,
  end: null,
  match_start: 5.0,
  match_cue_id: null,
  text: "kv cache size = 2 * n_layers * n_heads",
  link: "https://youtu.be/kCc8FmEb1nY?t=3",
  cue_ids: [],
  frame_id: "kCc8FmEb1nY-00000",
  score: 0.0164,
  timestamp: "0:05",
  thumb: "http://localhost:8080/frames/kCc8FmEb1nY-00000.jpg?w=320&q=70&exp=1788725441&sig=8K5",
  thumb_large:
    "http://localhost:8080/frames/kCc8FmEb1nY-00000.jpg?w=960&q=70&exp=1788725441&sig=kOk",
};

const BRRR_SPOKEN_LATE = {
  source: "transcript",
  video_id: "zduSFxRajkE",
  title: "Making LLMs go brrr",
  channel: "GPU MODE",
  start: 200.0,
  end: 203.0,
  match_start: 200.0,
  match_cue_id: 9,
  text: "caching is the whole trick here",
  link: "https://youtu.be/zduSFxRajkE?t=198",
  cue_ids: [9],
  frame_id: null,
  score: 0.0164,
  timestamp: "3:20",
  // A transcript hit in a video with no keyframe for that second: both frame
  // URLs are `null` and the row shows its channel where the still would be.
  thumb: null,
  thumb_large: null,
};

const BRRR_SPOKEN_EARLY = {
  source: "transcript",
  video_id: "zduSFxRajkE",
  title: "Making LLMs go brrr",
  channel: "GPU MODE",
  start: 10.0,
  end: 13.0,
  match_start: 10.0,
  match_cue_id: 7,
  text: "paged attention keeps a block table for the kv cache",
  link: "https://youtu.be/zduSFxRajkE?t=8",
  cue_ids: [7],
  frame_id: null,
  score: 0.0161,
  timestamp: "0:10",
  thumb: null,
  thumb_large: null,
};

const BRRR_OCR = {
  source: "ocr",
  video_id: "zduSFxRajkE",
  title: "Making LLMs go brrr",
  channel: "GPU MODE",
  start: 12.0,
  end: null,
  match_start: 12.0,
  match_cue_id: null,
  text: "paged kv cache | block table | 4% fragmentation",
  link: "https://youtu.be/zduSFxRajkE?t=10",
  cue_ids: [],
  frame_id: "zduSFxRajkE-00000",
  score: 0.0161,
  timestamp: "0:12",
  thumb: "http://localhost:8080/frames/zduSFxRajkE-00000.jpg?w=320&q=70&exp=1788725441&sig=o4s",
  thumb_large:
    "http://localhost:8080/frames/zduSFxRajkE-00000.jpg?w=960&q=70&exp=1788725441&sig=xYa",
};

/** The fused transcript hit: four cues ranked as one segment. */
const KARPATHY_FUSED = {
  source: "transcript",
  video_id: "kCc8FmEb1nY",
  title: "Let's build GPT: from scratch",
  channel: "Andrej Karpathy",
  start: 0.0,
  end: 11.8,
  match_start: 0.0,
  match_cue_id: 1,
  text:
    "we cache the keys and the values at every new token otherwise you would recompute " +
    "attention over the entire prefix which is quadratic in the sequence length the cache " +
    "makes it linear in the number of new tokens",
  link: "https://youtu.be/kCc8FmEb1nY?t=0",
  cue_ids: [1, 2, 3, 4],
  frame_id: null,
  score: 0.0159,
  timestamp: "0:00",
  thumb: null,
  thumb_large: null,
};

/** Every leg the planner ran for `q=cache`, including the two it did not use.
 *  A `0` is a reading — `fts 0` is how you learn the corpus does not contain
 *  your phrasing — so the line is drawn for every key that arrives. */
const CACHE_LEGS = {
  transcript: 3,
  ocr: 2,
  frame: 0,
  transcript_fts: 4,
  transcript_vec: 0,
  transcript_vec_knn: 3,
  frame_vec: 0,
  frame_knn: 0,
};

/** `?q=cache`, owner clamps: a page of 20, five hits over two videos. */
export const OWNER_SEARCH = {
  query: "cache",
  content_type: "all",
  results: [KARPATHY_OCR, BRRR_SPOKEN_LATE, BRRR_SPOKEN_EARLY, BRRR_OCR, KARPATHY_FUSED],
  pagination: { limit: 20, offset: 0, has_more: false, approx_total: 5, pool_exhausted: false },
  leg_counts: CACHE_LEGS,
  notes: [],
  data_status: null,
};

/** The same query with no credential behind it — the demo, and the owner's own
 *  `AUTH=none` box. Nothing on a hit is redacted (a corpus is what §2.4 gives
 *  the demo whole); the page is ten instead of twenty because the caller is
 *  anonymous, and that is the only thing the page can see. */
export const DEMO_SEARCH = {
  ...OWNER_SEARCH,
  pagination: { limit: 10, offset: 0, has_more: false, approx_total: 5, pool_exhausted: false },
  results: OWNER_SEARCH.results.map((hit) => ({
    ...hit,
    // No signer in `AUTH=none`: the URLs arrive bare, because signing a link to
    // a file the server hands to anyone who asks buys nothing.
    thumb: hit.thumb?.replace(/&exp=.*$/, "") ?? null,
    thumb_large: hit.thumb_large?.replace(/&exp=.*$/, "") ?? null,
  })),
};

/** `?q=cache&limit=1&offset=1`: the middle of the ranking, with more behind it. */
export const PAGED_SEARCH = {
  ...OWNER_SEARCH,
  results: [BRRR_SPOKEN_LATE],
  pagination: { limit: 1, offset: 1, has_more: true, approx_total: 5, pool_exhausted: false },
};

/** The pool ran out before the count did, so the total is an estimate and the
 *  page prints the `~` that says so — `has_more` over exact totals. */
export const PROBED_SEARCH = {
  ...OWNER_SEARCH,
  pagination: { limit: 20, offset: 0, has_more: true, approx_total: 200, pool_exhausted: true },
};

/** Nothing matched, in a corpus that is still being built: `data_status` is the
 *  difference between "nothing matched" and "nothing is indexed", and the note
 *  is the query planner saying which legs it did not bother to run. */
export const NO_MATCH_SEARCH = {
  query: "zzzznothingmatchesthis",
  content_type: "all",
  results: [],
  pagination: { limit: 20, offset: 0, has_more: false, approx_total: 0 },
  leg_counts: {},
  notes: [
    "No word of this query occurs anywhere in the corpus, so the semantic " +
      "(nearest-neighbour) legs were not queried — they would have returned their k " +
      "nearest vectors regardless.",
  ],
  data_status: "indexing",
};

/** The same query against an instance with nothing in it at all. */
export const EMPTY_CORPUS_SEARCH = { ...NO_MATCH_SEARCH, data_status: "empty" };

// ----------------------------------------------------------------- the traps
//
// Constructed rather than dumped: the seeded corpus has no frame-leg hit and no
// fused segment whose matching cue opens later than the segment does, and both
// are shapes §14.2 names as the ways this payload allows a plausible wrong
// answer. Every field below is the shape the dumped hits above already have.

/** A frame hit that matched on imagery alone. `text` is `null` because the
 *  humanising layer drops the tool's stand-in sentence, so the page prints
 *  *visual match, no text hit* itself rather than styling a sentence as a
 *  quotation. */
export const FRAME_HIT = {
  source: "frame",
  video_id: "zduSFxRajkE",
  title: "Making LLMs go brrr",
  channel: "GPU MODE",
  start: 612.0,
  end: null,
  match_start: 612.0,
  match_cue_id: null,
  text: null,
  link: "https://youtu.be/zduSFxRajkE?t=610",
  cue_ids: [],
  frame_id: "zduSFxRajkE-00025",
  score: 0.0143,
  timestamp: "10:12",
  thumb: "http://localhost:8080/frames/zduSFxRajkE-00025.jpg?w=320&q=70&exp=1788725441&sig=aaa",
  thumb_large:
    "http://localhost:8080/frames/zduSFxRajkE-00025.jpg?w=960&q=70&exp=1788725441&sig=bbb",
};

/** A fused transcript hit whose matching cue is 1:12:03, in a segment that
 *  opens at 1:11:40. `timestamp` is `clock(start)` and names the opening; the
 *  timecode the page prints is `clock(match_start)`. */
export const FUSED_HIT = {
  ...KARPATHY_FUSED,
  start: 4300.0,
  end: 4340.0,
  match_start: 4323.0,
  timestamp: "1:11:40",
  link: "https://youtu.be/kCc8FmEb1nY?t=4321",
};

/** A hit whose source the badge table does not know, and whose link is not a
 *  receipt: `http`, not `https`, so §14's admission rule refuses it and the row
 *  prints no receipt at all rather than an unchecked link. */
export const ODD_HIT = {
  ...BRRR_SPOKEN_EARLY,
  source: "caption_track",
  match_start: 10.0,
  link: "http://youtu.be/zduSFxRajkE?t=8",
};

export const TRAP_SEARCH = {
  ...OWNER_SEARCH,
  results: [FRAME_HIT, FUSED_HIT, ODD_HIT],
  pagination: { limit: 20, offset: 0, has_more: false, approx_total: 3, pool_exhausted: false },
};

// -------------------------------------------------------------- the refusals

/** `?q=` with no filter beside it. The tool's own code, and the page keeps the
 *  band on screen because it is a query the reader can fix. */
export const EMPTY_QUERY_REFUSAL = {
  error: "E_EMPTY_QUERY",
  message: "search needs either a query or at least one filter.",
  next: "pass q, or use list-videos to browse the library.",
};

/** `?content_type=nonsense`. The facade's, before the tool is called at all. */
export const BAD_PARAM_REFUSAL = {
  error: "E_BAD_PARAM",
  message: "content_type must be one of all, transcript, ocr, frame.",
  next: "omit it for all three channels.",
};

/** The read gate, on a browser with no session. */
export const AUTH_REFUSAL = {
  error: "E_AUTH_REQUIRED",
  message: "The dashboard needs the owner's token or session.",
  next: "Sign in at /dashboard/login, or send Authorization: Bearer $VIDTHEQUE_TOKEN.",
};

/** The per-IP `/dashboard/*` bucket, in the limiter's own envelope — it has no
 *  `next:` line and carries its delay twice, in the body and in `Retry-After`. */
export const RATE_REFUSAL = {
  error: "E_RATE_LIMIT",
  message: "Too many requests — 120 per minute.",
  retry_after_s: 24,
  bucket: "dashboard",
};
