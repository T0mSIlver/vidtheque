// The three payloads, owner and projection, as `mcp/tests/test_dashboard.py`'s
// fixture corpus produces them. Dumped from the Python test fixtures rather
// than written by hand, so a field that changes shape on the other side of the
// boundary changes here too and the page tests notice.
//
// The projection is not a copy with fields blanked: it is what
// `VIDTHEQUE_PUBLIC_READONLY=1` actually sends, which is `null` where the read
// was never taken (frontend-migration.md §7).

export const OWNER_SESSION = {
  version: "0.0.6",
  auth_mode: "token",
  readonly: false,
  write_side: true,
  writes_allowed: true,
  authenticated: true,
  is_owner: true,
  signed_in: true,
  has_session_cookie: true,
  policy: "owner",
  login_url: "/dashboard/login",
  sign_in_hint: "Sign in at /dashboard/login, or send Authorization: Bearer $VIDTHEQUE_TOKEN.",
  accepts_password: true,
  accepts_token: true,
};

export const DEMO_SESSION = {
  version: "0.0.6",
  auth_mode: "none",
  readonly: true,
  write_side: false,
  writes_allowed: true,
  authenticated: true,
  is_owner: false,
  signed_in: false,
  has_session_cookie: false,
  policy: "public",
  login_url: null,
  sign_in_hint: null,
  accepts_password: false,
  accepts_token: false,
};

const READINESS = {
  mcp: "ready",
  database: "ready",
  vectors: { enabled: true, reason: null },
  worker: {
    state: "unavailable",
    detail: "The worker did not answer its status check.",
    models: [],
  },
  checked_at: 1788626080,
};

export const OWNER_OVERVIEW = {
  counted_at: 1788626080,
  redacted: false,
  corpus: {
    videos: 4,
    queryable_videos: 3,
    videos_by_index_state: { indexing: 1, ready: 3 },
    data_status: "indexing",
    cues: 10,
    keyframes: 3,
    ocr_lines: 5,
    duration_s: 17200.0,
    published: { oldest: 1673913600, newest: 1740000000 },
    last_indexed: 1750000000,
  },
  channels: [
    { channel: "3Blue1Brown", videos: 1, seconds: 1200.0 },
    { channel: "Andrej Karpathy", videos: 1, seconds: 7000.0 },
    { channel: "GPU MODE", videos: 1, seconds: 3600.0 },
  ],
  tags: [
    { tag: "topic:attention", videos: 3 },
    { tag: "series:gpu-mode", videos: 1 },
  ],
  gaps: { transcript_no_ocr: 1, indexing: 1, failed: 0 },
  embed_backlog: { text: 0, frame: 0 },
  jobs: { active: 2, running: 1, deferred: 1, failed_recent: 1, failed_window_s: 86400 },
  recent: [
    {
      video_id: "kCc8FmEb1nY",
      title: "Let's build GPT: from scratch",
      channel: "Andrej Karpathy",
      duration_s: 7000.0,
      indexed_at: 1750000000,
      thumb: "/frames/kCc8FmEb1nY-00000.jpg?w=192&q=70&exp=1788712480&sig=z2RXj",
    },
    {
      video_id: "eMlx5fFNoYc",
      title: "Visualizing transformers",
      channel: "3Blue1Brown",
      duration_s: 1200.0,
      indexed_at: 1750000000,
      thumb: null,
    },
  ],
  readiness: READINESS,
  declared_models: [
    { label: "transcription", key: "stt.model", value: "large-v3", dim: "" },
    {
      label: "transcript embeddings",
      key: "text_embed.model",
      value: "Qwen/Qwen3-VL-Embedding-2B",
      dim: "2048",
    },
  ],
  storage: { keyframe_bytes: 4306, database_bytes: 4653056 },
};

export const DEMO_OVERVIEW = {
  ...OWNER_OVERVIEW,
  redacted: true,
  recent: OWNER_OVERVIEW.recent.map((video) => ({
    ...video,
    thumb: video.thumb ? "/frames/kCc8FmEb1nY-00000.jpg?w=192&q=70" : null,
  })),
  readiness: { ...READINESS, worker: null },
  declared_models: null,
  storage: null,
};

export const OWNER_LEDGER = {
  counted_at: 1788626080,
  redacted: false,
  corpus: {
    videos: 4,
    duration_s: 17200.0,
    cues: 10,
    keyframes: 3,
    ocr_lines: 5,
    chunks: 3,
    tags: 2,
    channels: 4,
    last_indexed: 1750000000,
  },
  videos_by_state: { ready: 3, pending: 0, indexing: 1, failed: 0, stale: 0 },
  jobs_by_state: { queued: 1, running: 1, done: 0, failed: 1, cancelled: 0 },
  queue: { active: 2, running: 1, deferred: 1, failed_recent: 1, failed_window_s: 86400 },
  embed_backlog: { text: 0, frame: 0 },
  gaps: { transcript_no_ocr: 1 },
  readiness: READINESS,
  storage: { keyframe_bytes: 4306, database_bytes: 4653056 },
};

export const DEMO_LEDGER = {
  ...OWNER_LEDGER,
  redacted: true,
  readiness: { ...READINESS, worker: null },
  storage: null,
};
