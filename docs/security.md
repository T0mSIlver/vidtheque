# Security — where everything is

A map, not a document. vidtheque's security material is split between what is
public on purpose and what is held privately on purpose, and this file says
which is which so nobody has to guess.

## Public, and meant to be

| | |
|---|---|
| [`SECURITY.md`](../SECURITY.md) | The policy: how to report a vulnerability, what is in scope, what to expect. GitHub private vulnerability reporting is the channel |
| [`docs/deploy-public.md`](deploy-public.md) §1 | The gate — a security audit is required before the first public request, and §1.1 says what it must cover |
| [`docs/deploy-public.md`](deploy-public.md) §7.5 | The sharing checklist: the pre-exposure checks that no test can make, because they are facts about a box and a network rather than about this source tree |
| [`docs/takedown.md`](takedown.md) | The removal path, and the promise it implements |

Those four are commitments to other people. They stay in the public repo, and a
finding that says one of them is *wrong* is a good report — `SECURITY.md` says
so explicitly.

## Private, and why

**Audit reports and their fix records are not in this repository.** They live in
the private sibling repo `vidtheque-security`.

An audit's value is that it is candid about what is *not* fixed yet: deferred
findings, accepted risks, structural weaknesses recorded for the next pass. On a
public repo attached to a live public instance, that candour is a roadmap. The
choice is between an audit that is safe to publish and an audit that is worth
writing, and this project would rather have the second one.

What lives there:

- `audit-2026-08-10.md` — the pre-exposure audit that `deploy-public.md` §1
  gates the first public request on. Ten passes; three blockers, all fixed
  before merge
- `fixes-2026-08-11.md` — the fix record for that audit: what landed, what was
  deliberately deferred and on whose decision
- `audit-2026-08-11.md` — the on-box verification audit the launch required
  before the URL was shared

`.gitignore` refuses `research/security-audit-*.md` and
`HANDOFF-security-fixes.md`, so an agent working in this tree cannot commit one
here by reflex. That guard is the mechanism; this paragraph is only the reason.

## What the git history does say

The fixes themselves are ordinary public commits, and their messages describe
what they fixed — `mcp: the route with no limit had the GPU behind it` and its
fourteen siblings, merged on 2026-08-11. That is deliberate. A project that
hardens something and then hides the commit message is harder to trust and no
harder to attack; the diff is right there either way.

The line is between **a fix, which is a finished thing**, and **an audit, which
names the unfinished ones**.

## For the next audit

Write it into the private repo, not here. `deploy-public.md` §1.1 says what a
pre-exposure audit must cover; the 2026-08-10 report's method section is the
worked example of how the last one was run.
