# Run your own vidtheque, captions only

This setup needs no GPU and keeps no video. It reads the YouTube captions of
the channels you follow, and a language model you pay for scores each new
video against your interests. The scores show up in the feed, in the Android
app and in your agent over MCP.

## What you give up

Without the GPU worker:

- **Search is keyword-only.** Nothing is embedded, so a search finds the words
  that were said, not what they mean. Searching "vector database" will not
  find a talk that only says "embedding store".
- **No novelty check.** A verdict cannot tell that you already watched three
  talks making the same point, because that comparison uses embeddings.
- **Auto-captions have no punctuation or casing**, and they get proper nouns
  wrong. Quotes read as one long lowercase line, and a search for a product
  name can miss the misspelled version. Timestamps stay exact to the word.
- **No slides or on-screen text.** The video is never downloaded, so there are
  no frames to search or show.

Verdicts, the feed, following, the profile and every MCP tool still work.

## Hardware

Any always-on amd64 machine with Docker and about 2 GB of free RAM: a mini PC,
an old laptop, or an x86 NAS that runs containers (Synology Container
Manager, Unraid, TrueNAS SCALE). Disk use is small: three videos totalling
52 minutes took 5.5 MB, database and logs included.

**Not a Raspberry Pi 5 yet.** The server image is built for arm64, but the web
front end (`vidtheque-web`) is published for amd64 only, so the stack does not
start on a Pi. An ARM NAS has the same problem.

Run it at home rather than on a VPS. YouTube asks datacenter IPs to sign in
much more often than home connections, and vidtheque does not sign in. If you
try a VPS anyway, see [Testing a VPS](#testing-a-vps).

## Install

You need Docker with the Compose plugin and an API key for an
OpenAI-compatible model (Mistral, OpenRouter, OpenAI, or a local llama.cpp).

```bash
mkdir vidtheque && cd vidtheque
TAG=0.0.41   # the latest release; the preset needs 0.0.41 or later
REL=https://raw.githubusercontent.com/T0mSIlver/vidtheque/v$TAG/deploy
curl -fsSL -o docker-compose.yml "$REL/docker-compose.yml"
curl -fsSL -o compose.release.yml "$REL/compose.release.example.yml"
curl -fsSL -o compose.captions.yml "$REL/compose.captions.example.yml"
curl -fsSL -o compose.local.yml "$REL/compose.local.example.yml"
curl -fsSL -o Caddyfile "$REL/Caddyfile"
curl -fsSL -o vidtheque-update.sh "$REL/vidtheque-update.sh" && chmod +x vidtheque-update.sh
```

`compose.local.yml` puts the data on a host path, which makes backups plain
file copies. Edit the path in it, then create the directory and give it to
the container's user (uid 10001 in the image):

```bash
sudo mkdir -p /srv/vidtheque-data && sudo chown 10001:10001 /srv/vidtheque-data
```

Then write `.env`. Every line below is documented in
[`deploy/.env.example`](../deploy/.env.example):

```bash
IMAGE_TAG=0.0.41
EDGE_PORT=8080

# The preset
VIDTHEQUE_STT_POLICY=captions_only
VIDTHEQUE_INDEX_CHANNELS=transcript
VIDTHEQUE_KEEP_SOURCE=none

# Your model, for verdicts and the nightly profile update
VIDTHEQUE_LLM_BASE_URL=https://api.mistral.ai/v1
VIDTHEQUE_LLM_API_KEY=your-key
VIDTHEQUE_LLM_MODEL=mistral-small-latest

# Sign-in, needed for the phone app and for any agent outside your network
VIDTHEQUE_AUTH=oauth
VIDTHEQUE_PASSWORD=a-long-password
PUBLIC_URL=https://vid.example.com
VIDTHEQUE_PUBLIC_HOSTNAME=vid.example.com
VIDTHEQUE_TIMEZONE=Europe/Paris
```

`compose.captions.yml` sets `WORKER_URL=none` and leaves the GPU worker out,
so its 28 GB image is never pulled. Start the stack:

```bash
docker compose -f docker-compose.yml -f compose.release.yml \
  -f compose.captions.yml -f compose.local.yml up -d
curl localhost:8080/healthz
```

`/healthz` should answer `"vector_legs":false`. That is the captions-only mode,
not an error.

`VIDTHEQUE_AUTH=oauth` refuses to boot until `PUBLIC_URL` is an `https://`
address, so set up the next section first. To try it on your own network
before that, use `VIDTHEQUE_AUTH=token` with `VIDTHEQUE_TOKEN=` set to a long
random string, and `PUBLIC_URL=http://<box>:8080`.

### Choosing a model

One verdict is one call that reads up to 40,000 characters of transcript. In
our test, three verdicts with `mistral-small-latest` averaged 15,000 input
tokens and 300 output tokens, in 2.4 seconds each. Multiply by your model's
price per token. The console's Health page shows what your calls cost once
`VIDTHEQUE_LLM_PRICE` is set or the model is in the built-in price table.

A small model is cheap but judges loosely: in the same test it matched a
review of a Qwen model to "Rust" and "array languages". A stronger model
gives verdicts you can trust more.

Instead of an API key, `VIDTHEQUE_LLM_BACKEND=claude-code` or `codex` runs the
`claude -p` or `codex exec` you are signed in to. The binary must be on the
server's PATH, which means building your own image.

## Reach it from your phone

The app and claude.ai connect over HTTPS to a public hostname. The server
then asks for your password before it lets them in.

**Cloudflare Tunnel** needs a domain on Cloudflare (the free plan is enough)
and opens no port on your router.

1. In the Cloudflare dashboard, open Zero Trust, then Networks, then Tunnels,
   and create a tunnel. Copy its token into `.env` as `TUNNEL_TOKEN=`.
2. Add a public hostname, for example `vid.example.com`, with service
   `http://caddy:80`.
3. In `compose.local.yml`, publish the edge on loopback only, so the tunnel is
   the only way in:

   ```yaml
   services:
     caddy:
       ports: !override
         - "127.0.0.1:8080:80"
   ```

4. Start the stack with the tunnel profile:

   ```bash
   docker compose -f docker-compose.yml -f compose.release.yml \
     -f compose.captions.yml -f compose.local.yml --profile tunnel up -d
   ```

Do not put Cloudflare Access in front of the hostname. Clients must reach
`/.well-known/*` and `/mcp` without it to start signing in.

**Tailscale Funnel** gives you an `https://<machine>.<tailnet>.ts.net` address
without owning a domain: `tailscale funnel 8080`, then use that name for
`PUBLIC_URL` and `VIDTHEQUE_PUBLIC_HOSTNAME`. We have not tested this path.

[`docs/deploy-public.md`](deploy-public.md) covers the tunnel in more depth.

## Use it

Open `https://vid.example.com/dashboard` and sign in with your password. The
console's Following page adds channels. The feed is at `/feed`, and
`/feed/profile` holds the interests verdicts are scored against. A short list
of topics is enough to start; the profile learns from what you open, thumb
and mute.

### Connect an agent over MCP

The endpoint is `https://vid.example.com/mcp`. Each client opens your sign-in
page the first time.

- Claude Code: `claude mcp add --transport http vidtheque https://vid.example.com/mcp`,
  then `/mcp` in a session to sign in.
- claude.ai, Claude Desktop and the Claude mobile app: Settings, Connectors,
  Add custom connector, with the same URL.
- Any other client that speaks streamable HTTP and OAuth uses the same URL.

Ask your agent to follow a channel ("follow https://youtube.com/@handle") or
to add interests to your profile. It can also search the corpus and read
transcripts with timestamps.

### Install the Android app

1. On the phone, download `vidtheque-0.2.0.apk` from the
   [Android 0.2.0 release](https://github.com/T0mSIlver/vidtheque/releases/tag/android-v0.2.0)
   and open it. Android asks once to allow installs from your browser.
2. Open vidtheque, enter `https://vid.example.com` and sign in with your
   password.

Push notifications work only on the developer's own instance, because only
that server holds the key to the app's Firebase project. On yours the feed
works, but nothing is pushed.

## Updates

When YouTube changes something, yt-dlp breaks for everyone at once, and a new
release follows. Update with one command:

```bash
VIDTHEQUE_DEPLOY_DIR=$PWD ./vidtheque-update.sh 0.0.42
```

It backs up the databases, fetches the compose files of that release
(the captions overlay included, because `compose.captions.yml` is present),
pulls the images and restarts. It prints the rollback command if anything
fails.

## Testing a VPS

A VPS is cheaper to keep on than a home machine, but YouTube may block it.
To find out before you move:

1. On the VPS, install Docker and run the install above with
   `VIDTHEQUE_AUTH=token`.
2. Follow one channel with `backfill=3` from the console or your agent.
3. Watch the jobs page for an hour. A job that fails with `E_RATE_LIMIT` and
   "Sign in to confirm you're not a bot" means YouTube has blocked that IP for
   logged-out access. vidtheque then waits 90 minutes
   (`VIDTHEQUE_RATE_LIMIT_BACKOFF_S`) and tries again.
4. Run it for a few days. Blocks come in waves, so one clean hour proves
   little.

A quicker check, without vidtheque:

```bash
docker run --rm ghcr.io/t0msilver/vidtheque-mcp:0.0.41 \
  yt-dlp --skip-download --list-subs https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

A list of subtitle tracks means the IP works today. "Sign in to confirm
you're not a bot" means it does not.
