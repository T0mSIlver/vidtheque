// The three corpus write outcomes, in the shapes
// `mcp/tests/test_dashboard_writes_json.py` asserts against the seeded corpus
// (dashboard.md §21). Read off that suite rather than invented here, so a field
// that changes shape on the other side of the boundary changes here too and the
// page tests notice.
//
// There is **no projection here, and there cannot be one**: all three routes are
// registered with the write routes, so the only deployment that answers them is
// the one whose reader is the owner. What stands in for the demo case is the
// page not being drawn at all.

/** The plain case: one URL, one job, nothing to explain. The Jinja page takes
 *  the redirect shortcut here and this branch deliberately does not — a client
 *  that always reads `jobs` is a client with no special case. */
export const ONE_JOB = {
  jobs: [{ job_id: "job_02e028870c97", items: 1, urls: ["https://youtu.be/solo0000002"] }],
  already_indexed: [],
  errors: [],
  batches: 1,
  urls: 1,
};

/** Twenty-three ids split into three jobs of at most ten — the split the
 *  operator cannot otherwise see, which is what makes the job count
 *  explicable — with two videos already in the corpus and one batch the tool
 *  refused. Every branch of the receipt at once. */
export const SPLIT_RECEIPT = {
  jobs: [
    {
      job_id: "job_aaaaaaaaaaaa",
      items: 10,
      urls: Array.from({ length: 10 }, (_, n) => `vid0000000${n}`),
    },
    {
      job_id: "job_bbbbbbbbbbbb",
      items: 10,
      urls: Array.from({ length: 10 }, (_, n) => `vid0000001${n}`),
    },
  ],
  already_indexed: ["kCc8FmEb1nY", "eMlx5fFNoYc"],
  errors: [
    {
      error: "E_BAD_PARAM",
      message: "Tags must be namespace:value, lowercase.",
      next: "fix the tag and submit that batch again.",
      urls: ["vid00000020", "vid00000021", "vid00000022"],
    },
  ],
  batches: 3,
  urls: 23,
};

/** `409`: nothing was accepted, and the reason is on the receipt rather than
 *  in a refusal envelope — which is why this status is read and not thrown. */
export const NOTHING_ACCEPTED = {
  jobs: [],
  already_indexed: [],
  errors: [
    {
      error: "E_BAD_PARAM",
      message: "Tags must be namespace:value, lowercase.",
      next: "fix the tag and submit that batch again.",
      urls: ["vid00000042"],
    },
  ],
  batches: 1,
  urls: 1,
};

// ------------------------------------------------------- the form's refusals

/** The form's own two bounds, in the envelope every other refusal uses. Both
 *  re-render the form with what was typed still in it, which is why they are
 *  inline rather than an error page.
 *
 *  Both are raised after `_submitted` resolved the three values, so both carry
 *  the receipt's `accepted` block beside the envelope (dashboard.md §21): the
 *  9000 somebody typed comes back as the tool's 200, on the refusal too. */
export const NO_URLS = {
  error: "E_BAD_PARAM",
  message: "Paste at least one video, playlist or channel URL.",
  next: "a bare 11-character YouTube id works too.",
  accepted: { expand: "playlist", max_items: 200, priority: "normal" },
};

export const TOO_MANY_URLS = {
  error: "E_TOO_LARGE",
  message: "201 URLs is past this form's cap of 200.",
  next: "submit it in parts, or point one job at the playlist.",
  accepted: { expand: "playlist", max_items: 200, priority: "normal" },
};

// ------------------------------------------------------------ the two rows

/** `POST /dashboard/videos/{video_id}/reindex`. */
export const REINDEXED = { video_id: "zduSFxRajkE", job_id: "job_02e028870c97" };

/** The tool's refusal for a video the pipeline is already inside. The button
 *  does not get to override a running job. */
export const REINDEX_REFUSED = {
  error: "E_INDEXING",
  message: "kCc8FmEb1nY is already being indexed.",
  next: "watch the job, or cancel it first.",
};

/** `POST /dashboard/videos/{video_id}/tags` — the row's tags *after* the write,
 *  read back, which is the question the panel is showing. */
export const TAGGED = {
  video_id: "kCc8FmEb1nY",
  tags: ["series:writes", "topic:attention", "topic:json"],
};

/** `tag_video`'s own refusal text, verbatim: a tag this refuses here is a tag
 *  it would refuse from the model, and the panel says so in the same words. */
export const TAG_REFUSED = {
  error: "E_BAD_PARAM",
  message: "Tags must be namespace:value, lowercase.",
  next: "try topic:attention, or series:zero-to-hero.",
};
