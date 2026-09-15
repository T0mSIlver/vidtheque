// The two following payloads and the six write outcomes, as
// `mcp/tests/test_dashboard_following.py`'s seeded corpus produces them.
// Dumped from the Python suite rather than written by hand, so a field that
// changes shape on the other side of the boundary changes here too and the
// page tests notice.
//
// The seeding is the shape the surface exists for: one active follow with an
// 8:00 floor, a rate limit behind it and a ledger of six candidates it turned
// away — two of them within twelve seconds of that floor, which is the whole
// argument for the near-miss line — and one paused follow that has never run a
// check, which is every empty state on the detail page at once.
//
// One title and one `reason` carry markup, exactly as the Python suite's do. A
// candidate's title is whatever was on somebody's channel page and a reason is
// a sentence a check composed around it; both are corpus strings, and a page
// that interpolates one is a page with a hole in it.
//
// There is **no projection here, and there cannot be one**: both routes are
// registered with the write routes, so the only deployment that answers them
// is the one whose reader is the owner (dashboard.md §22). What stands in for
// the demo case is a `404`, which is what a deployment with no write side
// answers on the page and on the JSON alike.

/** One clock for the whole fixture, so every span in it is a round number.
 *  The jobs fixtures use the same one: two pages of one surface read at the
 *  same moment. */
const NOW = 1788626080;

/** The hostile pair the Python suite seeds, kept verbatim. */
const HOSTILE = "<script>alert(document.cookie)</script> <img src=x onerror=alert(1)>";

export const KARPATHY = {
  slug: "andrej-karpathy",
  title: "Andrej Karpathy",
  kind: "channel",
  source_url: "https://www.youtube.com/@karpathy",
  state: "active",
  // A healthy follow's count is zero and its derived half is false: the
  // retry fields are on every row, not only on a failing one's. Nothing will
  // refuse its check, so the refusal sentence is null.
  fail_count: 0,
  retrying: false,
  max_tries: 7,
  not_schedulable_reason: null,
  mode: "auto",
  tabs: ["videos"],
  channels: "all",
  tags: ["topic:llm"],
  min_duration_s: 480,
  max_duration_s: null,
  title_include: [],
  title_exclude: [],
  backfill: 0,
  max_per_check: 5,
  check_interval_s: 21600,
  next_check_at: NOW + 14400,
  last_check_at: NOW - 7200,
  last_new_at: NOW - 3600,
  last_error_code: "E_RATE_LIMIT",
};

/** Followed, never checked: every clock but the next one is missing, which is
 *  the row an operator meets in the first minute after making a follow. */
export const PAUSED = {
  slug: "paused-channel",
  title: "Paused Channel",
  kind: "channel",
  source_url: "https://www.youtube.com/@paused",
  state: "paused",
  fail_count: 0,
  retrying: false,
  max_tries: 7,
  // Policy text, Python's: the sentence the check write would be refused
  // with, carried on the row rather than re-composed here.
  not_schedulable_reason:
    "Not scheduled: Paused Channel is paused, and a paused follow is never " +
    "checked. Nothing was queued.",
  mode: "review",
  tabs: ["videos", "shorts"],
  channels: "all",
  tags: [],
  min_duration_s: null,
  max_duration_s: null,
  title_include: [],
  title_exclude: [],
  backfill: 0,
  max_per_check: 5,
  check_interval_s: 21600,
  next_check_at: NOW + 21600,
  last_check_at: null,
  last_new_at: null,
  last_error_code: null,
};

/** The two rows one word covers since migration 0008: a follow retrying once
 *  a day needs nothing from anyone, and one that gave up is waiting on a
 *  human. The count is the receipt — not a fourth state word. The retry clock
 *  is a day out, never sooner than the follow's own interval. */
export const RETRYING = {
  ...KARPATHY,
  state: "failing",
  fail_count: 2,
  retrying: true,
  next_check_at: NOW + 86400,
  not_schedulable_reason: null,
  last_error_code: "E_UNSUPPORTED_SOURCE",
};

export const GAVE_UP = {
  ...KARPATHY,
  state: "failing",
  fail_count: 7,
  retrying: false,
  next_check_at: NOW + 86400,
  not_schedulable_reason:
    "Not scheduled: Andrej Karpathy is failing and has stopped retrying " +
    "after 7 consecutive failures. Nothing was queued.",
  last_error_code: "E_UNSUPPORTED_SOURCE",
};

export const FOLLOWING = {
  counted_at: NOW,
  order: "failing_first",
  totals: {
    follows: 2,
    active: 1,
    paused: 1,
    failing: 0,
    due_soon: 1,
    brought_in: 1,
    held: 2,
  },
  // An hour of video spent against a sixteen-hour ceiling, over the rolling
  // day the window names.
  budget: { spent_s: 3600.0, ceiling_h: 16.0, window_s: 86400 },
  checks_enabled: true,
  vectors: true,
  follows: [KARPATHY, PAUSED],
  // The held band is what is waiting on a *person*: `held_budget` is waiting
  // on the window and is reconsidered without anybody looking.
  held: [
    {
      title: `Held for you ${HOSTILE}`,
      url: "https://youtu.be/heldreview1",
      slug: "andrej-karpathy",
      follow: "Andrej Karpathy",
      published_at: 1740000000,
      first_seen_at: NOW - 7200,
    },
  ],
  held_more: false,
  held_cap: 5,
  pagination: { limit: 25, offset: 0, has_more: false },
  notes: [],
};

/** Nothing followed yet: the add form is the empty state. */
export const NO_FOLLOWS = {
  ...FOLLOWING,
  totals: { follows: 0, active: 0, paused: 0, failing: 0, due_soon: 0, brought_in: 0, held: 0 },
  budget: { spent_s: 0.0, ceiling_h: 0.0, window_s: 86400 },
  follows: [],
  held: [],
};

/** Follow checks are off on this deployment, so every clock in `follows` is a
 *  time at which nothing will happen. */
export const CHECKS_OFF = { ...FOLLOWING, checks_enabled: false };

/** What the server says when it moved a bound the reader asked for. */
export const CLAMPED_FOLLOWING = {
  ...FOLLOWING,
  pagination: { limit: 100, offset: 0, has_more: false },
  notes: ["limit=100000 → 100", "offset=-3 → 0"],
};

export const FOLLOW_DETAIL = {
  fetched_at: NOW,
  follow: { ...KARPATHY, last_error_message: "the source rate-limited this box" },
  checks_enabled: true,
  brought_in: 1,
  counts: {
    already_indexed: 1,
    held_budget: 1,
    held_review: 1,
    queued: 1,
    skipped_duration: 3,
  },
  // Two of the six were within a minute of the floor. This is the line the
  // band exists for, and `null` is what it looks like when it is not true.
  near_miss: { count: 2, of: 6, within_s: 60, edge: "floor" },
  checks: [
    {
      job_id: "job_followchk1",
      state: "done",
      error_code: null,
      created_at: NOW - 7200,
      started_at: NOW - 7200,
      finished_at: NOW - 7180,
    },
  ],
  index_jobs: [
    {
      job_id: "job_followidx1",
      state: "done",
      n_items: 1,
      n_done: 1,
      n_failed: 0,
      created_at: NOW - 7100,
    },
  ],
  in_flight: null,
  order: "newest",
  seen: [
    {
      title: "Already in the corpus",
      url: "https://youtu.be/alreadyidx1",
      decision: "already_indexed",
      reason: "this one is already indexed",
      judged_from: "listing",
      duration_s: 1800.0,
      published_at: 1740000000,
      decided_at: NOW - 7200,
    },
    {
      title: `Held for you ${HOSTILE}`,
      url: "https://youtu.be/heldreview1",
      decision: "held_review",
      reason: `neither the listing nor a probe gave a duration ${HOSTILE}`,
      judged_from: "probe",
      duration_s: null,
      published_at: 1740000000,
      decided_at: NOW - 7200,
    },
    {
      title: "Held by the budget",
      url: "https://youtu.be/heldbudget1",
      decision: "held_budget",
      reason: "the day's 8h of video is already spoken for",
      judged_from: "listing",
      duration_s: 5400.0,
      published_at: 1740000000,
      decided_at: NOW - 7200,
    },
    {
      title: "Nowhere near it",
      url: "https://youtu.be/faraway0001",
      decision: "skipped_duration",
      reason: "0:30, shorter than your 8:00 floor",
      judged_from: "listing",
      duration_s: 30.0,
      published_at: 1740000000,
      decided_at: NOW - 7200,
    },
    {
      title: "Also near the floor",
      url: "https://youtu.be/nearmiss002",
      decision: "skipped_duration",
      reason: "7:30, shorter than your 8:00 floor",
      judged_from: "probe",
      duration_s: 450.0,
      published_at: 1740000000,
      decided_at: NOW - 7200,
    },
    {
      title: "Near the floor",
      url: "https://youtu.be/nearmiss001",
      decision: "skipped_duration",
      reason: "7:48, shorter than your 8:00 floor",
      judged_from: "listing",
      duration_s: 468.0,
      published_at: 1740000000,
      decided_at: NOW - 7200,
    },
  ],
  caps: { checks: 10, index_jobs: 10 },
  pagination: { limit: 25, offset: 0, has_more: false },
  notes: [],
};

/** The paused follow: no check has run, nothing has been passed over, and the
 *  length rule that would make a near miss meaningful does not exist. */
export const QUIET_DETAIL = {
  ...FOLLOW_DETAIL,
  follow: { ...PAUSED, last_error_message: null },
  brought_in: 0,
  counts: {},
  near_miss: null,
  checks: [],
  index_jobs: [],
  seen: [],
};

/** A check already on the queue, so `Check now` cannot look like it did
 *  nothing. */
export const IN_FLIGHT_DETAIL = { ...FOLLOW_DETAIL, in_flight: "job_followchk2" };

/** Follow checks are off on this deployment, and this follow has a check on the
 *  queue: the two lines the page reads off `checks_enabled` are both a schedule
 *  nothing will run. */
export const CHECKS_OFF_DETAIL = {
  ...IN_FLIGHT_DETAIL,
  checks_enabled: false,
};

/** A channel that 404'd twice and is coming back on its own: still `failing`,
 *  still schedulable, so `Check now` is a control that does what it says. */
export const RETRYING_DETAIL = {
  ...FOLLOW_DETAIL,
  follow: { ...RETRYING, last_error_message: "This channel does not exist" },
};

/** Seven strikes: the follow stops being due and waits on a human. `Check
 *  now` would be refused, so the page draws it disabled with the refusal as
 *  its help, and `Try again` is the control that clears the count. */
export const GAVE_UP_DETAIL = {
  ...FOLLOW_DETAIL,
  follow: { ...GAVE_UP, last_error_message: "This channel does not exist" },
};

// ---------------------------------------------------------- write outcomes

// Every block here is the *detail* row, because §21 builds it with the function
// §22's detail read calls: identity, every rule column, and the two failure
// columns. `KARPATHY` is the table's row, so each outcome names its own
// `last_error_message` — and a `resume` is the one that nulls both, which is
// the whole reason they travel on an outcome at all.
const FAILING = {
  ...KARPATHY,
  last_error_message: "the source rate-limited this box",
};

export const PAUSED_OUTCOME = {
  follow: {
    ...FAILING,
    state: "paused",
    // A pause puts the row on the other branch of the same sentence.
    not_schedulable_reason:
      "Not scheduled: Andrej Karpathy is paused, and a paused follow is never " +
      "checked. Nothing was queued.",
  },
};

/** `set_state` nulls both error columns when it resumes, so the row that comes
 *  back is the receipt for the error going away as well as for the state. */
export const RESUMED_OUTCOME = {
  follow: { ...FAILING, state: "active", last_error_code: null, last_error_message: null },
};

/** `check_now` writes `next_check_at = 0`: due immediately, which is a value
 *  and not a missing clock — the row is the receipt for that. */
export const CHECKED_OUTCOME = { follow: { ...FAILING, next_check_at: 0 } };

/** The rule the store *kept*, read back: a nine-minute floor and four a check,
 *  which is what was posted. */
export const RULES_OUTCOME = {
  follow: { ...FAILING, min_duration_s: 540, max_per_check: 4 },
};

export const CREATED_OUTCOME = {
  follow: {
    ...KARPATHY,
    slug: "new-one",
    title: "New One",
    source_url: "https://www.youtube.com/@newone",
    tags: [],
    min_duration_s: null,
    last_check_at: null,
    last_new_at: null,
    last_error_code: null,
    last_error_message: null,
  },
  already_following: false,
};

/** The tool returns the existing follow rather than making a second one, which
 *  is what makes a retried submission safe. */
export const ALREADY_FOLLOWING = { follow: FAILING, already_following: true };

export const DELETED_OUTCOME = {
  slug: "andrej-karpathy",
  deleted: true,
  videos_kept: 1,
  spent_s: 0.0,
};

/** The follow that spent an hour of the day's budget before it was unfollowed:
 *  the hour stays spent (migration 0007), so the receipt owes the operator the
 *  line the tool prints rather than a budget that appears not to have moved. */
export const DELETED_SPENT_OUTCOME = { ...DELETED_OUTCOME, spent_s: 3600.0 };

export const QUEUED_OUTCOME = {
  slug: "andrej-karpathy",
  url: "https://youtu.be/nearmiss001",
  job_id: "job_02e028870c97",
};

// ------------------------------------------------------------- the refusals

/** A single video URL handed to the add form. The message and the `next:` line
 *  are Python's, and the page renders them rather than writing its own. */
export const NOT_A_CHANNEL = {
  error: "E_BAD_PARAM",
  message:
    "'https://youtu.be/kCc8FmEb1nY' is a single video, and a follow watches a channel or a playlist for new uploads.",
  next: 'index-video url="https://youtu.be/kCc8FmEb1nY" indexes that one video; follow-channel takes the channel or playlist URL it came from.',
};

/** The shared validator refusing a length it could not parse. */
export const BAD_DURATION = {
  error: "E_BAD_TIME_FORMAT",
  message: "Could not parse min_duration='banana'.",
  next: 'accepted: ISO 8601 (2026-03-01, 2026-03-01T12:00:00Z), relative ("7d ago", "3w ago", "6mo ago", "2y ago"), or a keyword (now, today, yesterday). Intra-video times also accept seconds (723) or a clock string (12:03, 1:12:03).',
};

/** An unknown slug, on the read as on every write under it. */
export const UNKNOWN_FOLLOW = {
  error: "E_UNKNOWN_FOLLOW",
  message: '"nope" is not a follow on this instance.',
  next: "the Following page lists every channel this index watches.",
};

/** Check-now on a follow that gave up. The message is the tool's own first
 *  line — one renderer beside `tools/follows.not_scheduled_line`, so the two
 *  media cannot describe one refusal two ways — and the way out is the
 *  control that clears the count. */
export const NOT_SCHEDULABLE = {
  error: "E_NOT_SCHEDULABLE",
  message:
    "Not scheduled: Andrej Karpathy is failing and has stopped retrying after 7 " +
    "consecutive failures. Nothing was queued.",
  next: "Try again (resume) clears the failure count and re-arms the clock.",
};
