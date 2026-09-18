# Publishing: one deployment shape, corpus generations, light releases

Status: contract written 2026-09-18, decided with Tom the same day. Nothing
here is implemented yet. `DECISIONS.md` wins if the two disagree.

It replaces three things: the public box's git-and-systemd deployment, the
corpus refresh through GitHub release assets (`deploy/staging/install.md` §12),
and the committed talk alignment (`aie-paris-2026.md` §2.3). It adds no MCP
tool, no HTTP endpoint and no environment variable.

Both boxes are containers on one Proxmox host and will stay there. The design
still crosses the gap with ssh and rsync only, so a box that moves to another
machine changes an address and nothing else.

---

## 1. One deployment shape

The private box and the public box run the same thing: `deploy/docker-compose.yml`
plus `compose.release.example.yml`, at one pinned `IMAGE_TAG`. The public box
adds `compose.public.example.yml`. No box builds anything; the public box loses
its checkout-as-runtime, its uv environment, and the Node, pnpm and Caddy
installs `install.md` §5a and §5b asked for by hand.

| | Private box | Public box |
|---|---|---|
| stack | base + release overlay | base + release + public overlay |
| auth | `oauth` | `none` + `VIDTHEQUE_PUBLIC_READONLY=1` |
| edge bind | `0.0.0.0:8080`, behind the host Caddy | `127.0.0.1:8080`, behind the tunnel |
| tunnel | none | the existing host `cloudflared` unit, ingress `http://127.0.0.1:8080` |
| update | `vidtheque-update <tag>` | the deploy poller, below |

`cloudflared` stays a host service on the public box rather than moving into
the compose `tunnel` profile: it works, it holds the tunnel credential, and
the cutover then changes one line of its ingress.

**The deploy poller stays pull-based.** A GitHub deployment still names what
the public box should run, and nothing connects inward to deploy. What the
poller does with it changes: it reads the requested ref's `IMAGE_TAG`, fetches
that ref's compose files and `Caddyfile`, runs `docker compose pull` and
`up -d`, and checks `/healthz`, `/` and `/api/meta` through the edge. A failed
check restores the previous tag and compose files and reports the deployment
failed. It never touches the corpus (§2 owns that).

**The GPU** reaches the worker container the way it does on the private box:
the NVIDIA container toolkit with cgroup management off, over the device nodes
the Proxmox container already passes through.

**Cutover** is one reversible step. The compose stack comes up on
`127.0.0.1:8080` beside the running systemd services and is checked there
(`frontend-migration.md` §10's through-the-edge checks). Then the tunnel
ingress moves from `:8100` to `:8080`. Rollback is moving it back; the systemd
units are disabled only after a quiet day.

## 2. The corpus moves as generations

A generation is an immutable directory:

```
generations/<id>/vidtheque.db
generations/<id>/keyframes/<video_id>/…
generations/<id>/MANIFEST.json
```

`<id>` is `YYYY-MM-DD-<slug>`. `MANIFEST.json` carries the id, the keep rules
that built it, video counts per rule, the database's sha256 and byte size, the
keyframe directory count, the schema version, and the alignment rows (§3) as
readable JSON.

### 2.1 Build, on the private box

`python -m vidtheque_mcp.corpus_snapshot`, run inside the `mcp` container. It
never writes to the live database.

- Keep rules: repeatable `--keep-channel` and `--keep-tag`; a video is kept if
  any rule matches. No rule is a refusal. Only queryable videos
  (`QUERYABLE_INDEX_STATES`) are kept.
- `VACUUM INTO` a copy, then on the copy only: delete every other video through
  the cascade `docs/takedown.md` §2 documents, and empty the operational tables
  (queue, jobs, follows, follow spend). A public snapshot carries no source URL
  it did not index and no job history.
- Verify with takedown §2.6, `integrity_check` and `foreign_key_check`. An
  orphan is a failed build.
- Refuse while the queue has a claimed item (`LESSONS.md`, cutting over);
  `--allow-busy` overrides.
- Keyframes are hard-linked or copied for kept videos only. `audio/` and
  `derived/` never enter a generation.

### 2.2 Transfer

`rsync -a --link-dest=<previous generation>` over ssh, from the private box to
the public box. Unchanged keyframes cost no bytes and no disk. The private box
holds the key; the public-facing box holds no credential for anything. On the
public side the key belongs to an unprivileged `corpus` user and is pinned with
`rrsync` to the `generations/` directory, so it can write files there and run
nothing.

The last file written is `generations/<id>/READY`.

**2026-09-18 transfer note.** The receiving `corpus` account has uid and gid
10001, matching the container user. Its restricted key may read the
generations directory because `--link-dest` must read the previous generation.

### 2.3 Activate, on the public box

A root-owned systemd path unit watches for `READY`. Its service:

1. verifies `MANIFEST.json` against the database's sha256 and the keyframe
   count, and runs `integrity_check` on the database;
2. prints the alignment change against the current generation to the journal;
3. moves the `current` symlink to the new generation in one rename;
4. restarts `mcp` and checks `/healthz` and `/api/meta` through the edge;
5. on any failure, moves `current` back, restarts, and leaves a `FAILED` file
   in the generation naming the step.

`mcp` reads `VIDTHEQUE_DATA_DIR=/data/current`, with the parent directory
mounted, so a restart follows the symlink. The resize cache `derived/` is per
generation and starts empty.

**2026-09-18 activation note.** The running service writes `secret.key` inside
the current generation. Activation copies that file into the new generation
before moving `current`; it creates no key when the outgoing generation has
none. Every attempted `READY` is consumed. Success renames it to `ACTIVATED`,
and a pre-switch verification failure renames it to `REJECTED` and writes the
reason into the file.

Rollback is the same service pointed at the previous id. Two generations are
kept, current and previous; older ones are pruned after a successful
activation.

### 2.4 One command

`scripts/publish_corpus.sh --generation <id> --keep-channel … --keep-tag …`
runs build, transfer and the wait for activation from the dev sandbox, over
ssh to the private box, and ends by printing the public `/api/meta` video
count and the activation journal. `--dry-run` prints every command.
`--rollback` reactivates the previous generation.

`deploy/staging/corpus-manifest.json`, the release-asset parts and the
poller's corpus stage are deleted when this lands.

## 3. Talk alignment is data

The schedule stays the committed, reviewed fixture. Which video and which
seconds a main-stage session maps to is operational data: it is unknowable
until a VOD exists, it changes on the day, and committing it made every
correction a release.

- Migration `0009`: table `edition_alignment(edition_slug, session_id,
  talk_video_id, stream_video_id, start_s, end_s, updated_at)`, primary key
  `(edition_slug, session_id)`.
- The fixture's four alignment fields are removed (`schema_version: 2`); a
  main-stage session keeps `"alignment": {}` as the marker that it was
  streamed, and an unstreamed stage keeps `null`.
- `python -m vidtheque_mcp.editions align <slug> <session_id> …` writes one
  row on the private box, under the rules `aie-paris-2026.md` §2.3 already
  sets: the video exists, is queryable and carries the right tags; offsets are
  inside the video; `end_s > start_s`; stream spans of one video do not
  overlap. `… align --list` prints the table; `--clear` removes a row.
- `… editions propose <slug> <stream_video_id>` prints candidate rows from
  the stream's YouTube chapters matched to session titles and speakers. It
  writes nothing: chapters help an operator, they do not become alignment by
  themselves, as §2.3 already says.
- The facade reads the table where it read the fixture. The three alignment
  states and the payload are unchanged.
- The snapshot carries the table, `MANIFEST.json` prints it, and activation
  logs its diff, so the review a commit gave is kept as a receipt per
  generation.

No MCP tool and no endpoint writes alignment. It is an operator command on the
box that owns the corpus.

## 4. Releases stop moving gigabytes

Measured on `vidtheque-worker:0.0.8`: about 12 GB unpacked, of which 8.4 GB is
one dependency layer (torch, the NVIDIA wheels, triton) and 3.2 GB is the CUDA
base. The layer order in `worker/Dockerfile` is already right. Three things
defeat it:

1. `uv.lock` is copied before the dependency install, and every version bump
   edits `uv.lock`, because the workspace's own package versions live in it.
   Each release therefore rebuilds, re-uploads and re-pulls the 8.4 GB layer.
2. The build cache is `type=gha`, capped at 10 GB per repository.
3. A `v*` tag builds all three images whatever changed.

The fix:

- **A base image per Python image.** `vidtheque-worker-base` and
  `vidtheque-mcp-base` hold the OS packages, Python and the locked third-party
  dependencies, installed from `uv export --no-emit-workspace` so a version
  bump does not change the input. The base tag is the sha256 of that export
  plus the base Dockerfile. It is built only when that hash has no image.
- **The release image** is `FROM` the base at that tag, plus the package's own
  source. Building it takes seconds and pushing or pulling it moves megabytes.
- **Retag, do not rebuild.** When an image's inputs match the previous
  release, the new version tag is pointed at the existing digest.
- Build cache moves to `type=registry`.

Dropping the CUDA base image (its libraries largely duplicate the NVIDIA
wheels) would cut about a quarter of the worker. It needs a GPU validation run
of whisperX's ctranslate2 on Tom's box and is deferred until after the Paris
edition.

## 5. Order of work

1. Public box: Docker and the NVIDIA toolkit, the compose stack at `0.0.8`
   beside the running services, the §1 checks, then the tunnel cutover with
   Tom's go.
2. The image build (§4) and release `0.0.9`, which also ships the `/paris`
   connect panel.
3. Corpus generations (§2), first exercised by republishing the current 310
   videos unchanged.
4. Alignment as data (§3), before 2026-09-23.
5. A full rehearsal: index one recent video on the private box, tag it,
   publish, align, check `/paris`, roll back.

## 6. Open, for Tom

1. `audio/` on the public box is 2.1 GB the read-only instance never reads. The
   12 GB data volume has 3.8 GB free. Deleting it, or growing the volume, is
   needed before two generations fit comfortably.
2. The `corpus` user and its `rrsync` key on the public box need the private
   box's public key placed once, by root.
