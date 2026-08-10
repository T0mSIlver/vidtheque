# Security policy

vidtheque is a self-hosted server that people are invited to point their agents
at, and it ships a public demo mode. If you have found a way to read, write or
spend something you should not be able to, we want to hear about it before
anyone else does.

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting**, on this repository:

> **Security** tab → **Report a vulnerability**
> ([direct link](https://github.com/T0mSIlver/vidtheque/security/advisories/new))

It opens a private advisory that only the maintainer can see, it keeps the whole
exchange — and any fix — in one place, and it needs no key exchange and no email
address from either side. Please use it rather than a public issue, a pull
request or a discussion: those are visible to everyone the moment you press the
button, including to whoever else is scanning.

Useful in a report, in rough order of usefulness:

- what an attacker gets — the impact, in one sentence;
- the version or commit, and whether the instance was in public read-only mode
  (`VIDTHEQUE_PUBLIC_READONLY=1`, `VIDTHEQUE_AUTH=none`) or private;
- the smallest reproduction you have, ideally a `curl`;
- anything you already know about the fix.

Please do not run denial-of-service tests, spend someone else's GPU or API
budget, or fetch other people's data from a live instance to prove a point. A
description of the path is enough.

## What to expect

This is a one-person project. You will get an acknowledgement, an honest
assessment of whether it is a bug and how bad it is, and credit in the advisory
unless you ask not to be named. Fixes land on `main`; there are no releases and
no backports to maintain yet, so `main` is the only supported version.

If you would rather not use GitHub, say so in a public issue **without any
detail** and a private channel will be arranged. (A dedicated security address
may be published here later; until then the advisory form is the channel.)

## Scope

In scope: this repository, and the deployment guidance it ships —
`mcp/`, `worker/`, `deploy/`, `scripts/`, and `docs/deploy-public.md`.

Particularly interesting: anything that crosses the public read-only boundary
(a write tool or write route reachable at `VIDTHEQUE_PUBLIC_READONLY=1`),
anything that escapes `VIDTHEQUE_DATA_DIR` through `/frames/*` or `/static/*`,
anything that defeats the per-IP rate limiting, and anything that lets an
anonymous caller drive the GPU worker.

Out of scope: findings that require an operator to have already ignored
`docs/deploy-public.md` — a worker exposed to the internet (it answers an
unauthenticated OpenAI-compatible API by design, and the runbook says to keep it
off-box), a dashboard published without a password, or trusting
`CF-Connecting-IP` on an origin that is reachable without going through the
tunnel. Those are documented configurations, not vulnerabilities; if the
documentation is what is wrong, that is a good report and it belongs in a public
issue.

Third-party findings — yt-dlp, whisperX, SQLite, Starlette — belong upstream.
Tell us anyway if this project's use of them is what makes them exploitable.
