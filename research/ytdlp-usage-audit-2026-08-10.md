# yt-dlp usage audit + effective-limits research (2026-08-10)

Produced by codex gpt-5.6-sol (read-only over this repo, web search on),
commissioned on Tom's question "are we using yt-dlp properly / can we raise
our rate limits?". Local links are worktree-absolute as written.

Date: 2026-08-10

## Verdict

Vidtheque’s yt-dlp integration is fundamentally sound and unusually polite, but it is suboptimal in three important ways:

1. The observed bot-check waves are IP/guest-session blocks, not ordinary per-download throttling. The current five-minute retry cycle is much shorter than the measured 60–90-minute block, so it consumes all three item attempts while the IP is still blocked.
2. A normal item may perform three complete YouTube extractions—metadata, audio, then video—multiplying webpage/player requests.
3. The operator’s 30–60-second download sleeps and 90–180-second inter-video gaps add substantial latency but cannot prevent or clear a metadata-extraction bot block.

The integration otherwise follows good practice: it is on the latest stable release, includes Deno and `yt-dlp-ejs`, leaves client selection at yt-dlp defaults, skips translated subtitle enumeration, expands playlists flat, fetches only selected caption tracks, and recognizes bot-checks as `E_RATE_LIMIT`.

## What the repository actually does

All YouTube access is in [`YtDlpSource`](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/pipeline/sources.py:503); there is no yt-dlp invocation in `worker/`.

The common options are:

- `noplaylist`, quiet/no warnings, no progress.
- `youtube:skip=translated_subs`.
- Optional `youtube:player_client=<configured value>`; otherwise yt-dlp chooses.
- Deno EJS with `jitless=true`.
- Extractor retries plus the four sleep settings.
- Optional Netscape cookie file.
- No forced impersonation.  
  See [`_base_opts()`](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/pipeline/sources.py:518).

A full item can issue:

1. `extract_info(download=False)` for metadata, subtitle inventory, chapters, heatmap and formats.
2. A fresh `YoutubeDL` and full extraction for audio: `bestaudio[abr<=80]/bestaudio/best`.
3. Another fresh `YoutubeDL` and full extraction for the capped video format ladder.  
   See [`probe()` and media methods](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/pipeline/sources.py:585) and the [runner call sequence](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/pipeline/runner.py:392).

Captions are fetched directly with `ydl.urlopen()` after an explicit subtitle sleep; yt-dlp’s normal subtitle downloader is bypassed deliberately. See [`fetch_subtitle()`](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/pipeline/sources.py:612).

A rate-limited extraction receives three short custom retries—5, 10 and 20 seconds—before becoming `E_RATE_LIMIT`. The job runner then waits 300 seconds and permits three item attempts total. Excluding other sleeps, a sustained block exhausts the item in approximately:

`3 × (5 + 10 + 20) + 2 × 300 = 705 seconds`, or 11.75 minutes.

That is badly mismatched to today’s 60–90-minute waves. See the [inner retry loop](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/pipeline/sources.py:551), [job deferral](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/jobs/runner.py:309), and [three-attempt schema default](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/db/migrations/0001_initial.sql:297).

### Version

`pyproject.toml` sets a floor of `>=2026.7.4`, while `uv.lock` resolves exactly `2026.7.4`; see [pyproject.toml](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/pyproject.toml:36) and [uv.lock](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/uv.lock:2643). This is the latest stable release as of this memo, published July 4, 2026. Its YouTube changes were live/playlist/metadata fixes, not a bot-check fix. [Official 2026.07.04 release](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04)

Upstream currently recommends nightly for regular users because stable can become stale as sites change. A pinned, tested nightly is reasonable when diagnosing YouTube breakage, but the production dependency should not float automatically. [Official update-channel guidance](https://pypi.org/project/yt-dlp/#update)

## What is causing the waves

yt-dlp’s pinned known-issues page is explicit: “Sign in to confirm you’re not a bot” means YouTube has blocked the IP while the client is logged out. It says a PO provider may help prevent this but will not help once the IP is already blocked. [yt-dlp known issues](https://github.com/yt-dlp/yt-dlp/issues/3766)

The official extractor guidance separately describes a guest/account video-request limit and recommends 5–10 seconds between downloads. Its estimates are roughly 300 videos or 1,000 webpage/player requests per hour for guest sessions, versus 2,000 videos or 4,000 requests for accounts. These are maintainer estimates, not contractual limits. [YouTube extractor guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#common-youtube-errors)

Today’s pattern—every video fails, metadata-only extraction fails identically, the condition disappears after 60–90 minutes—is therefore strong evidence of an IP/guest-session reputation block. It is not evidence that media bandwidth, the chosen format, or insufficient `--sleep-interval` caused each individual failure. Other processes and devices sharing the residential WAN IP should be included when auditing request volume.

PO-token enforcement is related but distinct. Tokens attest player, subtitle or Google Video Server requests for particular clients; missing required tokens can produce 403s and can contribute to account/IP blocking. [Official PO-token guide](https://github.com/yt-dlp/yt-dlp/wiki/Po-Token-Guide)

## Ranked recommendations

### 1. Align the circuit breaker with the observed block

Category: config-only. Expected effect: high reliability; fewer harmful retry requests.

Set:

```text
VIDTHEQUE_RATE_LIMIT_BACKOFF_S=5400
VIDTHEQUE_YTDLP_EXTRACTOR_RETRIES=1
```

`5400` seconds is an estimate based on the observed 60–90-minute waves, not an upstream constant. One short retry still catches a genuinely transient response; after that, the job should stop talking to YouTube until the measured wave has had time to clear.

The ideal future implementation would separate ordinary extractor retries from bot-check retries: retain yt-dlp’s normal three retries for known transient extractor errors, but treat bot-confirmation as an immediate circuit-breaker event. Officially, `--extractor-retries` means retries for known extractor errors; it is not designed as a recovery mechanism for an IP block. [yt-dlp extractor retry documentation](https://github.com/yt-dlp/yt-dlp/blob/2026.07.04/README.md#extractor-options)

### 2. Return the ordinary sleep settings to approximately the official preset

Category: config-only. Expected effect: materially faster clean batches, with little expected change in bot-wave frequency.

Use the values in the table below. The current operator settings already exceed upstream’s recommended spacing by several times. Once blocked, more 30–60-second media sleeps do not repair the IP.

For 15 videos needing both audio and video, changing the download range from 30–60 to 10–20 saves an estimated 15 minutes of pre-download sleep. Reducing the inter-video target from 90–180 to 30–60 could save up to another estimated 21 minutes when item processing has not already consumed the gap. These estimates are not additive in slow pipelines.

### 3. Move upload-date/recency probes to the YouTube Data API

Category: architectural/configuration addition. Expected effect: eliminates all yt-dlp traffic for date-only probes.

Use `videos.list(part=snippet&id=<comma-separated IDs>)` for known video IDs. It costs one quota unit per request and returns `snippet.publishedAt`; a 15-ID batch fits in one request. [Official `videos.list` documentation](https://developers.google.com/youtube/v3/docs/videos/list)

For channel discovery, cache the channel’s uploads-playlist ID and call `playlistItems.list(maxResults=50)`, also one unit per page. Do not use `search.list` for normal channel polling. [Official `playlistItems.list` documentation](https://developers.google.com/youtube/v3/docs/playlistItems/list)

This is appropriate for scheduling and recency, but not as a replacement for Vidtheque’s fetch-stage probe. The pipeline also consumes chapters, heatmap, subtitle inventory, language and description links; see [`parse_info()`](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/pipeline/sources.py:398). The Data API cannot provide the media URLs or public caption payloads needed by the remaining stages.

### 4. Pilot `bgutil-ytdlp-pot-provider` with `mweb`

Category: plugin installation plus config. Expected effect: medium for media 403s and future block prevention; uncertain for the current bot wave.

Current official guidance recommends a PO provider supplying GVS tokens to the `mweb` client. Tokens are increasingly video-bound, so manual token capture is no longer recommended. [Official PO-token recommendation](https://github.com/yt-dlp/yt-dlp/wiki/Po-Token-Guide)

After installing and verifying the provider, use:

```text
VIDTHEQUE_YTDLP_PLAYER_CLIENT=mweb
```

`bgutil-ytdlp-pot-provider` can run a local provider service and plugin without account cookies. Its own documentation cautions that a token does not guarantee bypassing either 403s or bot checks, although it may make traffic appear more legitimate. [Provider documentation and installation](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)

Risks:

- It is a third-party plugin, albeit one featured by yt-dlp and maintained by a yt-dlp maintainer.
- It adds a local JavaScript service and another versioned dependency.
- YouTube’s token rules change continuously.
- It will not clear an already-blocked IP.

The repository’s claim that the default set contains “two clients needing no PO token” is already stale. Stable 2026.07.04 uses `android_vr,web_safari`; `web_safari` requires GVS tokens for non-HLS media, and post-release master now records selective Android VR enforcement as well. [Stable client defaults](https://github.com/yt-dlp/yt-dlp/blob/2026.07.04/README.md#extractor-arguments), [current client/token policies](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/youtube/_base.py)

### 5. Consolidate repeated extractions

Category: future code change. Expected effect: potentially up to roughly 67% fewer extraction cycles on items needing metadata, audio and frames.

At present, each of those stages starts a new `YoutubeDL` and repeats webpage/player extraction. Reusing the first information result, or arranging the required media downloads under one extraction/session, would remove up to two of three extraction cycles. The exact request reduction must be measured because manifests and format refreshes may still be needed.

This is more likely to improve effective throughput than increasing concurrency.

### 6. Use account cookies only as an explicit trade-off

Category: account/cookie configuration. Expected effect: potentially higher request allowance and access during logged-out IP blocks; high consequence risk.

The maintainers warn that using an account with yt-dlp can cause temporary or permanent account bans, and say cookies are normally needed only for account-gated content. Public conference talks do not justify that risk by default. [Official cookie/account warning](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)

If Tom accepts the trade-off:

- Use a separate, disposable account rather than a valued primary account.
- Export only YouTube cookies from a fresh private/incognito session after visiting `youtube.com/robots.txt`, then close that session permanently.
- Do not subsequently open that session in a browser; YouTube rotates cookies on active tabs.
- Protect the cookie file as an account credential.
- Do not use the combined `--cookies --cookies-from-browser` export method for this isolated private session; the extractor wiki explicitly warns against it.  
  [Cookie export instructions](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)

Vidtheque already supports only a cookie-file path, which is the appropriate service-side interface.

## Sleep-knob tuning table

| Setting | Current effective value | Actual semantics | Recommendation |
|---|---:|---|---:|
| `VIDTHEQUE_YTDLP_SLEEP_REQUESTS` | 2 s | Sleeps between requests during data extraction; it does not delay media fragments or create an inter-video cooldown. [yt-dlp options](https://github.com/yt-dlp/yt-dlp/blob/2026.07.04/README.md#workarounds) | `0.75` s; up to `2` is harmless if latency is irrelevant |
| `VIDTHEQUE_YTDLP_SLEEP_INTERVAL` | 30 s | Minimum sleep before each media download, not before the metadata-only probe. | `10` s |
| `VIDTHEQUE_YTDLP_MAX_SLEEP_INTERVAL` | 60 s | Upper bound paired with the preceding minimum. | `20` s |
| `VIDTHEQUE_YTDLP_SLEEP_SUBTITLES` | 5 s if unset | Officially sleeps before subtitle downloads. Vidtheque bypasses that downloader but explicitly applies the same value before `urlopen()`. [Local implementation](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/pipeline/sources.py:621) | `5` s |
| `VIDTHEQUE_YTDLP_BETWEEN_VIDEOS_S` | 90 s → actual target 90–180 s | Custom Vidtheque gap between metadata-probe start times. Time already spent processing the preceding item counts toward the gap. [Local implementation](/home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/pipeline/runner.py:545) | `30` s → target 30–60 s |
| `VIDTHEQUE_YTDLP_EXTRACTOR_RETRIES` | 3 | Also controls Vidtheque’s short bot-check retries, producing 5+10+20 seconds and four total calls before deferral. | `1` until retry classes are separated |
| `VIDTHEQUE_RATE_LIMIT_BACKOFF_S` | 300 s | Job-level cooldown after `E_RATE_LIMIT`; this is the setting that matters once the IP is blocked. | `5400` s, an estimate based on today’s waves |

The proposed ordinary values are essentially yt-dlp’s official `-t sleep` preset—0.75-second extraction-request sleep, 10–20 seconds before downloads, and five seconds before subtitles—plus Vidtheque’s conservative 30–60-second item-start gap. [Official preset](https://github.com/yt-dlp/yt-dlp/blob/2026.07.04/README.md#preset-aliases)

## Data API quota math

Assuming three 15-video batches per day:

- Batched `videos.list`: `3 batches × 1 unit = 3 units/day`.
- Unbatched worst case: `45 videos × 1 unit = 45 units/day`.
- One followed channel polled three times through its uploads playlist: another `3 units/day`; adding one batched `videos.list` after each poll makes the discovery-plus-details total `6 units/day`.

The default allocation for non-search endpoints is 10,000 units/day. Thus the batched known-ID case uses about `0.03%` of the daily allocation; the unbatched 45-call case uses `0.45%`. Google requires an API project and API key, while OAuth is needed only for methods accessing user-authorized data. [Official API setup and quota overview](https://developers.google.com/youtube/v3/getting-started)

`snippet.publishedAt` is a full timestamp and may differ from the original upload time when a private video is later made public. That is normally the right meaning for Vidtheque’s `published_*` filters, but the mapping should be tested for unlisted and premiere cases. [Official publication-time semantics](https://developers.google.com/youtube/v3/docs/videos#snippet.publishedAt)

## Options not recommended

- Do not rotate residential IPv6 addresses per request. Current maintainer guidance lists IPv6 itself among common 403 conditions and suggests testing `--force-ipv4`. If a CAPTCHA is solved on one of several legitimate external addresses, yt-dlp says to bind the same address with `--source-address`; that is consistency, not rotation. [Maintainer 403 guidance](https://github.com/yt-dlp/yt-dlp/issues/17103), [official source-address/CAPTCHA guidance](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#http-error-429-too-many-requests-or-402-payment-required)
- Do not install the old YouTube OAuth plugin. Its repository is archived and obsolete, and YouTube OAuth login no longer works in yt-dlp under current restrictions. [Archived OAuth plugin](https://github.com/coletdjnz/yt-dlp-youtube-oauth2), [official OAuth status](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#logging-in-with-oauth)
- Do not force `player_client=all`; upstream explicitly calls that not recommended.
- Do not select `tv` merely because its token table looks attractive: cookie/DRM and format behavior differ, and arbitrary extractor-argument selection is a documented source of 403s.
- Do not use `player_skip` or hand-supplied visitor data solely to reduce calls. Upstream says those modes sacrifice robust extraction, can lose metadata/formats, and visitor-data mode can require more requests. [Extractor-argument warnings](https://github.com/yt-dlp/yt-dlp/blob/2026.07.04/README.md#extractor-arguments)
- Do not expect longer media sleeps to clear an active bot wave. Stop all YouTube extraction for the full cooldown instead.

No files or configuration were changed.