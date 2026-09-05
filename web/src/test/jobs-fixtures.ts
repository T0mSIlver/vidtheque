// The two jobs payloads, owner and projection, as `mcp/tests/test_dashboard.py`'s
// fixture corpus produces them (`_seed_jobs`). Dumped from the Python test
// fixtures rather than written by hand, so a field that changes shape on the
// other side of the boundary changes here too and the page tests notice.
//
// Three jobs, and they are the three shapes the page exists for: one queued
// behind a `not_before` in the future — the countdown that was true and
// invisible during the overnight batch — one running mid-item with a retry
// already spent, and one finished carrying both kinds of loss, the item that
// failed loudly and the item that succeeded with a stage missing underneath.
//
// **The `text` block is dropped except for `basis`.** Everything else in it is
// a rendering of a number that is already on the payload, and these pages
// render from the numbers (frontend-migration.md §1 decision 5). `basis` is the
// sentence saying what the percentage is computed over, which is policy text —
// it survives the cut and it is read.
//
// The projection is not a copy with fields blanked: it is what
// `VIDTHEQUE_PUBLIC_READONLY=1` actually sends, which is `null` where the read
// was taken and the value withheld — the job's error message, an item's
// submitted URL, an event's text (dashboard.md §2.4).

/** One clock for the whole fixture, so every span in it is a round number. */
const NOW = 1788626080;

const BASIS = (items: number) =>
  `of ${items} item(s). An item still in the pipeline counts the stages it has finished, out of 7.`;

export const DEFERRED_JOB = {
  job_id: "job_deferred01",
  state: "queued",
  kind: "index",
  priority: 100,
  progress: 0,
  n_items: 1,
  n_done: 0,
  n_failed: 0,
  n_skipped: 0,
  n_cancelled: 0,
  cancel_requested: false,
  created_at: NOW - 900,
  started_at: null,
  finished_at: null,
  waited_s: null,
  ran_s: null,
  wall_s: 900,
  live: true,
  // `not_before` is 240s out. This is the row the page was built for.
  defer_s: 240,
  error_code: "E_RATE_LIMIT",
  error_message: "the source rate-limited this box; cookiefile /home/dev/.cookies.txt",
  degraded: 0,
  text: { basis: BASIS(1) },
};

export const RUNNING_JOB = {
  job_id: "job_running001",
  state: "running",
  kind: "index",
  priority: 50,
  progress: 10,
  n_items: 2,
  n_done: 0,
  n_failed: 0,
  n_skipped: 0,
  n_cancelled: 0,
  cancel_requested: false,
  created_at: NOW - 1200,
  started_at: NOW - 1100,
  finished_at: null,
  waited_s: 100,
  ran_s: 1100,
  wall_s: 1200,
  live: true,
  defer_s: 0,
  error_code: null,
  error_message: null,
  degraded: 0,
  text: { basis: BASIS(2) },
};

export const FINISHED_JOB = {
  job_id: "job_finished01",
  state: "failed",
  kind: "index",
  priority: 100,
  progress: 100,
  n_items: 2,
  n_done: 1,
  n_failed: 1,
  n_skipped: 0,
  n_cancelled: 0,
  cancel_requested: false,
  created_at: NOW - 8000,
  started_at: NOW - 7900,
  finished_at: NOW - 6400,
  waited_s: 100,
  ran_s: 1500,
  wall_s: 1600,
  live: false,
  defer_s: 0,
  error_code: null,
  error_message: null,
  // One `done` item whose `ocr` stage failed underneath it: `n_failed` does not
  // see it, and this is the count that does.
  degraded: 1,
  text: { basis: BASIS(2) },
};

export const OWNER_JOBS = {
  now: NOW,
  poll_ms: 2000,
  // Two of the three are `queued|running`, so the tick runs.
  live: true,
  jobs: [DEFERRED_JOB, RUNNING_JOB, FINISHED_JOB],
  pagination: { limit: 25, offset: 0, has_more: false },
};

/** Nothing is live: the page reads once and stops. */
export const SETTLED_JOBS = {
  ...OWNER_JOBS,
  live: false,
  jobs: [FINISHED_JOB],
};

export const EMPTY_JOBS = { ...OWNER_JOBS, live: false, jobs: [] };

/** The projection drops the job's error message and keeps its code. */
export const DEMO_JOBS = {
  ...OWNER_JOBS,
  jobs: [{ ...DEFERRED_JOB, error_message: null }, RUNNING_JOB, FINISHED_JOB],
};

// ------------------------------------------------------------ the details

const DONE_ITEM = {
  item_id: 4,
  seq: 0,
  state: "done",
  stage: null,
  stage_pct: 0,
  attempts: 1,
  max_attempts: 3,
  retries_left: 0,
  video_id: "eMlx5fFNoYc",
  title: "Visualizing transformers",
  channel: "3Blue1Brown",
  duration_s: 1200.0,
  source_url: "https://youtu.be/eMlx5fFNoYc",
  error_code: null,
  error_message: null,
  started_at: NOW - 7900,
  finished_at: NOW - 7000,
  took_s: 900,
};

const FAILED_ITEM = {
  item_id: 5,
  seq: 1,
  state: "failed",
  stage: null,
  stage_pct: 0,
  attempts: 3,
  max_attempts: 3,
  retries_left: 0,
  // Never resolved to a video: a bot-check on the fetch. The row has a URL and
  // no title, which is one of the three shapes the items table draws.
  video_id: null,
  title: null,
  channel: null,
  duration_s: null,
  source_url: "https://youtu.be/failedvideo",
  error_code: "E_SOURCE",
  error_message: "ERROR: [youtube] Sign in to confirm you are not a bot.",
  started_at: NOW - 7000,
  finished_at: NOW - 6400,
  took_s: 600,
};

export const OWNER_JOB_DETAIL = {
  now: NOW,
  poll_ms: 2000,
  live: false,
  job: FINISHED_JOB,
  items: [DONE_ITEM, FAILED_ITEM],
  events: [],
};

export const RUNNING_JOB_DETAIL = {
  now: NOW,
  poll_ms: 2000,
  live: true,
  job: RUNNING_JOB,
  items: [
    {
      item_id: 2,
      seq: 0,
      state: "running",
      stage: "stt",
      stage_pct: 42,
      attempts: 2,
      max_attempts: 3,
      retries_left: 0,
      video_id: "kCc8FmEb1nY",
      title: "Let's build GPT: from scratch",
      channel: "Andrej Karpathy",
      duration_s: 7000.0,
      source_url: "https://youtu.be/kCc8FmEb1nY",
      error_code: null,
      error_message: null,
      started_at: NOW - 690,
      finished_at: null,
      took_s: 690,
    },
    {
      item_id: 3,
      seq: 1,
      state: "queued",
      stage: null,
      stage_pct: 0,
      attempts: 0,
      max_attempts: 3,
      retries_left: 3,
      video_id: null,
      title: null,
      channel: null,
      duration_s: null,
      source_url: "https://youtu.be/queuedvideo",
      error_code: null,
      error_message: null,
      started_at: null,
      finished_at: null,
      took_s: null,
    },
  ],
  events: [],
};

export const DEFERRED_JOB_DETAIL = {
  now: NOW,
  poll_ms: 2000,
  live: true,
  job: DEFERRED_JOB,
  items: [
    {
      item_id: 1,
      seq: 0,
      state: "queued",
      stage: null,
      stage_pct: 0,
      attempts: 2,
      max_attempts: 3,
      retries_left: 1,
      video_id: null,
      title: null,
      channel: null,
      duration_s: null,
      source_url: "https://youtu.be/deferredvid",
      error_code: null,
      error_message: null,
      started_at: NOW - 880,
      finished_at: null,
      took_s: null,
    },
  ],
  // The one record a non-rate-limit deferral has anywhere in the system.
  events: [
    {
      id: 1,
      at: NOW - 300,
      level: "warn",
      stage: null,
      item_id: null,
      message: "retrying in 300s after E_RATE_LIMIT: HTTP 429",
    },
  ],
};

/** The projection: the job's message, the items' URLs and the events' text are
 *  all `null`, and the codes, the counts and every clock survive. */
export const DEMO_JOB_DETAIL = {
  ...DEFERRED_JOB_DETAIL,
  job: { ...DEFERRED_JOB, error_message: null },
  items: DEFERRED_JOB_DETAIL.items.map((item) => ({ ...item, source_url: null })),
  events: DEFERRED_JOB_DETAIL.events.map((event) => ({ ...event, message: null })),
};

// ------------------------------------------------------------- the writes

/** Running work does not settle: the request is recorded and the pipeline stops
 *  at its next stage boundary. */
export const CANCEL_RUNNING = {
  job_id: "job_running001",
  state: "running",
  cancel_requested: true,
};

/** Queued work has no worker to cooperate with and settles now. */
export const CANCEL_QUEUED = {
  job_id: "job_deferred01",
  state: "cancelled",
  cancel_requested: true,
};

export const RETRY_RECEIPT = {
  from_job_id: "job_finished01",
  selected: 2,
  jobs: [{ job_id: "job_4cee026cb790", items: 2 }],
  errors: [],
  preserved: { channels: "all", tags: [], priority: "normal" },
};

/** Every refusal on this surface, in one shape. */
export const REFUSAL = {
  error: "E_BAD_PARAM",
  message: 'Job "job_finished01" is already failed.',
  next: "only queued or running jobs can be cancelled.",
};
