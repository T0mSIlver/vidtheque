"""The write side, answering JSON — dashboard.md §21, settled 2026-09-05.

Thirteen POSTs keep one URL each and answer in the medium the caller asked
for: the 303 the Jinja pages have always had, or the typed outcome the React
pages read. Same route, same guard, same Origin rule, same rate bucket — the
only thing that branches is the `Accept` header.

So the three things this file is most interested in are the ones that would
make that claim untrue:

* the **negotiation is strict**, and a browser's `*/*` or its
  `text/html,…,*/*;q=0.8` is still the page it was yesterday;
* a refusal is the **same refusal** on both branches — same code, same status,
  same Origin rule, same 404 in the deployments that register no write side;
* the outcome carries **values, not renderings**: ints, epoch seconds,
  booleans and lists, with the policy text (`message`, `next`) still Python's.

Client builders and fixture corpora come from `test_dashboard.py` (jobs,
videos, the index form) and `test_dashboard_following.py` (the six follow
writes), because a second seed of the same corpus is a second corpus.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from vidtheque_mcp.dashboard import ROOT
from vidtheque_mcp.dashboard.writes import MAX_FORM_URLS, _accepts_json

from .test_dashboard import BEARER, SAME_ORIGIN, make_client, owner_client, sign_in
from .test_dashboard_following import _empty_corpus as empty_follows
from .test_dashboard_following import make_client as follows_make_client
from .test_dashboard_following import owner_client as follows_client
from .test_dashboard_following import sign_in as follows_sign_in

# What the React client sends, and what a form navigation sends. Chrome's own
# string, verbatim, because the point of the second one is that it is not a
# thing this suite invented.
JSON = {"Accept": "application/json"}
FORM = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
CROSS_ORIGIN = {"Origin": "https://evil.example"}

# Every write route, as a client would reach it. The follow half lives in the
# following fixture and is listed in `FOLLOW_WRITES`.
CORE_WRITES = (
    f"{ROOT}/logout",
    f"{ROOT}/index",
    f"{ROOT}/jobs/job_running001/cancel",
    f"{ROOT}/jobs/job_finished01/retry",
    f"{ROOT}/videos/kCc8FmEb1nY/reindex",
    f"{ROOT}/videos/kCc8FmEb1nY/tags",
)
FOLLOW_WRITES = (
    f"{ROOT}/following",
    f"{ROOT}/following/andrej-karpathy/state",
    f"{ROOT}/following/andrej-karpathy/check",
    f"{ROOT}/following/andrej-karpathy/rules",
    f"{ROOT}/following/andrej-karpathy/delete",
    f"{ROOT}/following/andrej-karpathy/queue",
)

# A rendered clock or a spoken duration, in any field of an outcome payload.
# The whole decision is that these are React's now (DECISIONS.md, 2026-09-05).
RENDERED = re.compile(r"\d{4}-\d{2}-\d{2}T|\b\d+:\d{2}\b|\b\d+m \d+s\b")


def post(client: TestClient, path: str, **kwargs) -> object:
    headers = {**SAME_ORIGIN, **JSON, **kwargs.pop("headers", {})}
    return client.post(path, headers=headers, follow_redirects=False, **kwargs)


def scan(value: object) -> list[str]:
    """Every string anywhere in a payload that looks like a rendering."""
    if isinstance(value, dict):
        return [hit for v in value.values() for hit in scan(v)]
    if isinstance(value, list):
        return [hit for v in value for hit in scan(v)]
    if isinstance(value, str) and RENDERED.search(value):
        return [value]
    return []


# ------------------------------------------------ 1. what decides the branch


@pytest.mark.parametrize(
    "accept, wants_json",
    [
        ("application/json", True),
        ("application/json, text/plain", True),
        # The React client's own shape: JSON first, the page as a fallback.
        ("application/json, text/html;q=0.9", True),
        ("APPLICATION/JSON", True),
        # Chrome's navigation header. This is the case the strictness is for.
        ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", False),
        ("text/html", False),
        # `fetch` with no `Accept` set, and a client that will take anything:
        # a request for anything is not a request for a typed outcome.
        ("*/*", False),
        ("", False),
        # Named but not preferred, and named but refused.
        ("application/json;q=0.5, text/html;q=0.9", False),
        ("application/json;q=0, text/html", False),
        # A tie goes to the page: the redirect is the older contract.
        ("application/json, text/html", False),
    ],
)
def test_the_accept_header_is_the_whole_switch(accept: str, wants_json: bool) -> None:
    """§21's rule, and nothing else — no parameter, no header of our own."""
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"{ROOT}/logout",
            "headers": [(b"accept", accept.encode())],
        }
    )
    assert _accepts_json(request) is wants_json


def test_the_form_branch_is_untouched(tmp_path: Path) -> None:
    """Every write still answers a navigating browser with its 303.

    The redirect targets are the pages' contract (`_see`: POST → 303 → GET),
    and the JSON branch was added beside them rather than over them.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        cancelled = client.post(
            f"{ROOT}/jobs/job_running001/cancel",
            headers={**SAME_ORIGIN, **FORM},
            follow_redirects=False,
        )
        assert cancelled.status_code == 303
        assert cancelled.headers["location"] == f"{ROOT}/jobs/job_running001"

        solo = client.post(
            f"{ROOT}/index",
            data={"urls": "https://youtu.be/solo0000001"},
            headers={**SAME_ORIGIN, **FORM},
            follow_redirects=False,
        )
        assert solo.status_code == 303
        assert solo.headers["location"].startswith(f"{ROOT}/jobs/job_")

        tagged = client.post(
            f"{ROOT}/videos/kCc8FmEb1nY/tags",
            data={"add": "topic:forms"},
            headers={**SAME_ORIGIN, **FORM},
            follow_redirects=False,
        )
        assert tagged.status_code == 303
        assert tagged.headers["location"].endswith("#manage")

        out = client.post(
            f"{ROOT}/logout", headers={**SAME_ORIGIN, **FORM}, follow_redirects=False
        )
        assert out.status_code == 303
        assert out.headers["location"] == f"{ROOT}/login"


# ------------------------------------------------------------- 2. the jobs writes


def test_cancel_answers_the_state_the_job_is_actually_in(tmp_path: Path) -> None:
    """The reason this route answers inline at all (§5.4, §21).

    Queued work settles now and running work does not, and a 2 s poll cannot
    tell the operator which of the two just happened to the button they
    pressed. The payload says the store's own word for the state.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        running = post(client, f"{ROOT}/jobs/job_running001/cancel")
        assert running.status_code == 200
        assert running.json() == {
            "job_id": "job_running001",
            "state": "running",
            "cancel_requested": True,
        }
        assert running.headers["cache-control"] == "no-store"

        deferred = post(client, f"{ROOT}/jobs/job_deferred01/cancel")
        assert deferred.json()["state"] == "cancelled"


def test_cancel_refuses_in_the_envelope_at_the_codes_own_status(
    tmp_path: Path,
) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        unknown = post(client, f"{ROOT}/jobs/job_nosuchjob/cancel")
        assert unknown.status_code == 404
        assert unknown.json()["error"] == "E_UNKNOWN_JOB"
        assert "not a job on this instance" in unknown.json()["message"]
        assert unknown.json()["next"]

        # A state that cannot be cancelled — the same refusal the page renders.
        settled = post(client, f"{ROOT}/jobs/job_finished01/cancel")
        assert settled.status_code == 400
        assert settled.json()["error"] == "E_BAD_PARAM"
        assert "already failed" in settled.json()["message"]

        html = client.post(
            f"{ROOT}/jobs/job_finished01/cancel", headers={**SAME_ORIGIN, **FORM}
        )
        assert html.status_code == settled.status_code
        assert "E_BAD_PARAM" in html.text


def test_retry_answers_the_jobs_it_made_and_what_it_preserved(
    tmp_path: Path,
) -> None:
    """The retry receipt page, typed — and no one-job redirect shortcut.

    The page goes straight to the new job when there is exactly one; a client
    that always reads `jobs` is a client with no special case.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        receipt = post(client, f"{ROOT}/jobs/job_finished01/retry")
        assert receipt.status_code == 200
        payload = receipt.json()
        assert payload["from_job_id"] == "job_finished01"
        assert payload["selected"] == 2
        assert payload["errors"] == []
        assert [job["job_id"].startswith("job_") for job in payload["jobs"]] == [True]
        assert payload["jobs"][0]["items"] == 2
        assert payload["preserved"]["priority"] in ("normal", "high")
        assert isinstance(payload["preserved"]["tags"], list)

        still_running = post(client, f"{ROOT}/jobs/job_running001/retry")
        assert still_running.status_code == 400
        assert still_running.json()["error"] == "E_BAD_PARAM"
        assert "still running" in still_running.json()["message"]


# ------------------------------------- 3. the index form, re-index, tags, logout


def test_the_index_form_answers_its_receipt_rather_than_rendering_it(
    tmp_path: Path,
) -> None:
    ids = [f"vid{n:08d}" for n in range(23)]
    with owner_client(tmp_path) as client:
        sign_in(client)
        batched = post(client, f"{ROOT}/index", data={"urls": "\n".join(ids)})
        assert batched.status_code == 200
        payload = batched.json()
        assert payload["urls"] == 23
        assert payload["batches"] == 3
        assert sorted(job["items"] for job in payload["jobs"]) == [3, 10, 10]
        assert payload["already_indexed"] == [] and payload["errors"] == []
        # The split the operator cannot see is a job count they cannot explain:
        # the batch each job took is on the entry that made it.
        assert sum(len(job["urls"]) for job in payload["jobs"]) == 23

        solo = post(
            client, f"{ROOT}/index", data={"urls": "https://youtu.be/solo0000002"}
        )
        assert solo.status_code == 200
        assert len(solo.json()["jobs"]) == 1


def test_the_index_forms_own_bounds_refuse_in_the_envelope(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        empty = post(client, f"{ROOT}/index", data={"urls": "  \n "})
        assert empty.status_code == 400
        assert empty.json()["error"] == "E_BAD_PARAM"

        before = len(client.get(f"{ROOT}/api/jobs").json()["jobs"])
        flood = "\n".join(f"vid{n:08d}" for n in range(MAX_FORM_URLS + 1))
        refused = post(client, f"{ROOT}/index", data={"urls": flood})
        assert refused.status_code == 413
        assert refused.json()["error"] == "E_TOO_LARGE"
        assert str(MAX_FORM_URLS) in refused.json()["message"]
        assert len(client.get(f"{ROOT}/api/jobs").json()["jobs"]) == before

        # The tool's own refusal, per batch, and the 409 the page answers with
        # when nothing was accepted at all.
        bad_tag = post(
            client, f"{ROOT}/index", data={"urls": "vid00000042", "tags": "NotATag"}
        )
        assert bad_tag.status_code == 409
        assert bad_tag.json()["jobs"] == []
        assert bad_tag.json()["errors"][0]["error"] == "E_BAD_PARAM"
        assert bad_tag.json()["errors"][0]["urls"] == ["vid00000042"]


def test_reindex_answers_the_job_it_queued(tmp_path: Path) -> None:
    with owner_client(tmp_path) as client:
        sign_in(client)
        queued = post(client, f"{ROOT}/videos/zduSFxRajkE/reindex")
        assert queued.status_code == 200
        assert queued.json()["video_id"] == "zduSFxRajkE"
        assert queued.json()["job_id"].startswith("job_")

        unknown = post(client, f"{ROOT}/videos/nosuchvideo/reindex")
        assert unknown.status_code == 404
        assert unknown.json()["error"] == "E_UNKNOWN_VIDEO"

        # `kCc8FmEb1nY` is mid-`stt` in the fixture's running job. The button
        # does not get to override that on this branch either: `index-video`'s
        # own refusal, at the status the code maps to.
        held = post(client, f"{ROOT}/videos/kCc8FmEb1nY/reindex")
        assert held.status_code == 409
        assert held.json()["error"] == "E_INDEXING"


def test_tags_answer_the_rows_tags_after_the_write(tmp_path: Path) -> None:
    """`tag_video` reports what it changed across a batch; this reports the row.

    The panel that made the call is showing one video's tags, so the outcome is
    that list, read back rather than derived from what was asked for.
    """
    path = f"{ROOT}/videos/kCc8FmEb1nY/tags"
    with owner_client(tmp_path) as client:
        sign_in(client)
        added = post(client, path, data={"add": "topic:json, series:writes"})
        assert added.status_code == 200
        assert added.json()["video_id"] == "kCc8FmEb1nY"
        assert {"topic:json", "series:writes"} <= set(added.json()["tags"])

        removed = post(client, path, data={"remove": "series:writes"})
        assert "series:writes" not in removed.json()["tags"]
        assert "topic:json" in removed.json()["tags"]

        # Nothing asked for is nothing done, on both branches — the form's
        # policy, not a second one written for the JSON caller.
        idle = post(client, path, data={})
        assert idle.status_code == 200
        assert idle.json()["tags"] == removed.json()["tags"]

        # The tool's rules, verbatim, in the tool's own words.
        refused = post(client, path, data={"add": "NotATag"})
        assert refused.status_code == 400
        assert refused.json()["error"] == "E_BAD_PARAM"
        assert "NotATag" in refused.json()["message"]
        assert "namespace" in refused.json()["next"]


def test_signing_out_answers_typed_and_still_clears_the_cookie(
    tmp_path: Path,
) -> None:
    """The `Set-Cookie` is on both branches: a React shell cannot clear an
    `HttpOnly` cookie itself, so the response has to."""
    with owner_client(tmp_path) as client:
        sign_in(client)
        out = post(client, f"{ROOT}/logout")
        assert out.status_code == 200
        assert out.json() == {"signed_out": True}
        assert "vidtheque_session=" in out.headers["set-cookie"]
        # The row went with it, so the next write is refused, not merely
        # cookie-less.
        assert post(client, f"{ROOT}/jobs/job_running001/cancel").status_code == 401


# --------------------------------------------- 4. the guard, on both branches


def test_the_origin_rule_is_the_same_rule_for_a_fetch(tmp_path: Path) -> None:
    """§3.3 is about the *credential*, not about the medium.

    The session cookie is ambient whether a form or a `fetch` sends it, so the
    JSON branch needs the same positive same-origin evidence and is refused at
    the same status with the same code.
    """
    for path, body in (
        (f"{ROOT}/jobs/job_running001/cancel", {}),
        (f"{ROOT}/videos/kCc8FmEb1nY/tags", {"add": "topic:origin"}),
        (f"{ROOT}/index", {"urls": "vid00000044"}),
    ):
        with owner_client(tmp_path) as client:
            sign_in(client)
            cross = client.post(path, data=body, headers={**CROSS_ORIGIN, **JSON})
            assert cross.status_code == 403, path
            assert cross.json()["error"] == "E_BAD_ORIGIN"

            # Neither header, with the cookie: the shape a cross-site form POST
            # would have. Refused on this branch exactly as on the other.
            absent = client.post(path, data=body, headers=JSON)
            assert absent.status_code == 403, path
            assert absent.json()["error"] == "E_BAD_ORIGIN"

            form = client.post(path, data=body, headers={**CROSS_ORIGIN, **FORM})
            assert form.status_code == absent.status_code
            assert form.json()["error"] == absent.json()["error"]

            # A bearer is not ambient, so absent headers still pass — curl and
            # the React page are not the same caller.
            allowed = client.post(
                path, data=body, headers={**BEARER, **JSON}, follow_redirects=False
            )
            assert allowed.status_code == 200, path


def test_a_signed_out_fetch_is_told_so_rather_than_redirected(
    tmp_path: Path,
) -> None:
    """The 401 is what sends the browser to the login page (DECISIONS.md).

    A navigating browser still gets the 303 to `/dashboard/login?next=…` it has
    always got — the refusal is the same one, rendered in the medium that
    asked.
    """
    with owner_client(tmp_path) as client:
        for path in CORE_WRITES:
            refused = post(client, path)
            assert refused.status_code == 401, path
            assert refused.json()["error"] == "E_AUTH_REQUIRED"
            assert refused.json()["next"]

            navigating = client.post(
                path, headers={**SAME_ORIGIN, **FORM}, follow_redirects=False
            )
            assert navigating.status_code == 303, path
            assert navigating.headers["location"].startswith(f"{ROOT}/login?next=")


def test_the_projection_has_no_write_side_on_either_branch(tmp_path: Path) -> None:
    """§2.3: absent, not refused — and absent to a `fetch` too.

    A route that exists and refuses is a route somebody probes, and content
    negotiation must not turn one of those into a route that exists for a
    client that asks nicely.
    """
    with owner_client(tmp_path, readonly=True) as demo:
        for path in CORE_WRITES:
            assert demo.post(path, headers={**BEARER, **JSON}).status_code == 404, path
            assert demo.post(path, headers={**BEARER, **FORM}).status_code == 404, path

    with make_client(tmp_path) as none_mode:  # VIDTHEQUE_AUTH=none
        for path in CORE_WRITES:
            assert none_mode.post(path, headers=JSON).status_code == 404, path


# ------------------------------------------------------------ 5. the six follows


def test_creating_a_follow_answers_the_row_and_whether_it_is_new(
    tmp_path: Path,
) -> None:
    """`already_following` is the difference a redirect cannot express.

    The tool deliberately returns the existing follow rather than making a
    second one, which is what makes a retried request safe — and what a client
    has to be told, because the page it would be sent to looks identical.
    """
    with empty_follows(tmp_path) as client:
        follows_sign_in(client)
        body = {
            "url": "https://www.youtube.com/@karpathy",
            "title": "Andrej Karpathy",
            "tab_videos": "1",
            "min_duration": "8:00",
            "max_per_check": "5",
            "mode": "auto",
        }
        created = post(client, f"{ROOT}/following", data=body)
        assert created.status_code == 200
        follow = created.json()["follow"]
        assert created.json()["already_following"] is False
        assert follow["state"] == "active"
        assert follow["tabs"] == ["videos"]
        assert follow["min_duration_s"] == 480
        assert follow["max_per_check"] == 5
        assert follow["mode"] == "auto"
        assert isinstance(follow["check_interval_s"], int)
        assert isinstance(follow["next_check_at"], int)
        assert follow["last_check_at"] is None

        again = post(client, f"{ROOT}/following", data=body)
        assert again.json()["already_following"] is True
        assert again.json()["follow"]["slug"] == follow["slug"]

        # A refusal is the tool's, in the tool's words.
        refused = post(
            client,
            f"{ROOT}/following",
            data={"url": "https://youtu.be/kCc8FmEb1nY", "tab_videos": "1"},
        )
        assert refused.status_code == 400
        assert "a follow watches a channel or a playlist" in refused.json()["message"]


def test_pause_resume_and_check_answer_the_row_they_changed(tmp_path: Path) -> None:
    """Re-read, never assumed: `set_state` re-arms the clock when it resumes."""
    slug = "andrej-karpathy"
    with follows_client(tmp_path) as client:
        follows_sign_in(client)
        paused = post(client, f"{ROOT}/following/{slug}/state", data={"action": "pause"})
        assert paused.status_code == 200
        assert paused.json()["follow"]["state"] == "paused"

        resumed = post(
            client, f"{ROOT}/following/{slug}/state", data={"action": "resume"}
        )
        assert resumed.json()["follow"]["state"] == "active"

        checked = post(client, f"{ROOT}/following/{slug}/check")
        assert checked.status_code == 200
        assert isinstance(checked.json()["follow"]["next_check_at"], int)

        # The vocabulary of that one control is two words, and the third is a
        # typed refusal rather than a silent no-op.
        wrong = post(client, f"{ROOT}/following/{slug}/state", data={"action": "stop"})
        assert wrong.status_code == 400
        assert wrong.json()["error"] == "E_BAD_PARAM"


def test_editing_the_rules_answers_the_rule_the_store_kept(tmp_path: Path) -> None:
    slug = "andrej-karpathy"
    with follows_client(tmp_path) as client:
        follows_sign_in(client)
        edited = post(
            client,
            f"{ROOT}/following/{slug}/rules",
            data={
                "tab_videos": "1",
                "tab_streams": "1",
                "min_duration": "20:00",
                "max_per_check": "3",
                "mode": "review",
                "channel_transcript": "1",
            },
        )
        assert edited.status_code == 200
        follow = edited.json()["follow"]
        assert follow["tabs"] == ["videos", "streams"]
        assert follow["min_duration_s"] == 1200
        assert follow["max_per_check"] == 3
        assert follow["mode"] == "review"
        assert follow["channels"] == "transcript"

        # The floor the shared validator owns, refused in its own words — not
        # clamped silently, and not clamped differently for this branch.
        refused = post(
            client,
            f"{ROOT}/following/{slug}/rules",
            data={"tab_videos": "1", "check_interval_s": "60"},
        )
        assert refused.status_code == 400
        assert refused.json()["error"] == "E_BAD_PARAM"
        assert "at least 900 seconds" in refused.json()["message"]


def test_index_anyway_and_unfollow_answer_what_they_did(tmp_path: Path) -> None:
    slug = "andrej-karpathy"
    with follows_client(tmp_path) as client:
        follows_sign_in(client)
        queued = post(
            client,
            f"{ROOT}/following/{slug}/queue",
            data={"url": "https://youtu.be/nearmiss001"},
        )
        assert queued.status_code == 200
        assert queued.json()["slug"] == slug
        assert queued.json()["url"] == "https://youtu.be/nearmiss001"
        assert queued.json()["job_id"].startswith("job_")

        # No URL is nothing to do, on both branches.
        idle = post(client, f"{ROOT}/following/{slug}/queue", data={})
        assert idle.status_code == 200
        assert idle.json()["job_id"] is None

        gone = post(client, f"{ROOT}/following/{slug}/delete")
        assert gone.status_code == 200
        assert gone.json()["slug"] == slug
        assert gone.json()["deleted"] is True
        # The videos it brought in stay: they are corpus, not membership.
        assert isinstance(gone.json()["videos_kept"], int)
        assert client.get(f"{ROOT}/following/{slug}", headers=BEARER).status_code == 404


def test_an_unknown_follow_is_the_same_404_on_both_branches(tmp_path: Path) -> None:
    with follows_client(tmp_path) as client:
        follows_sign_in(client)
        for path in (
            f"{ROOT}/following/no-such-thing/state",
            f"{ROOT}/following/no-such-thing/check",
            f"{ROOT}/following/no-such-thing/rules",
            f"{ROOT}/following/no-such-thing/delete",
            f"{ROOT}/following/no-such-thing/queue",
        ):
            refused = post(client, path, data={"action": "pause", "url": "x"})
            assert refused.status_code == 404, path
            assert refused.json()["error"] == "E_UNKNOWN_FOLLOW"

            form = client.post(
                path,
                data={"action": "pause", "url": "x"},
                headers={**SAME_ORIGIN, **FORM},
            )
            assert form.status_code == refused.status_code, path
            assert "E_UNKNOWN_FOLLOW" in form.text


def test_the_follow_writes_are_absent_where_the_surface_is(tmp_path: Path) -> None:
    """§18.6: nothing of this surface exists in a deployment with no write side.

    Including to a `fetch` — which is the case this file adds, because the
    negotiation must not be a way to reach a route that is not registered.
    """
    with follows_client(tmp_path, readonly=True) as demo:
        for path in FOLLOW_WRITES:
            assert demo.post(path, headers={**BEARER, **JSON}).status_code == 404, path
            assert demo.post(path, headers={**BEARER, **FORM}).status_code == 404, path

    with follows_make_client(tmp_path) as none_mode:  # VIDTHEQUE_AUTH=none
        for path in FOLLOW_WRITES:
            assert none_mode.post(path, headers=JSON).status_code == 404, path


def test_the_follow_writes_keep_their_303(tmp_path: Path) -> None:
    slug = "andrej-karpathy"
    with follows_client(tmp_path) as client:
        follows_sign_in(client)
        headers = {**SAME_ORIGIN, **FORM}
        paused = client.post(
            f"{ROOT}/following/{slug}/state",
            data={"action": "pause"},
            headers=headers,
            follow_redirects=False,
        )
        assert paused.status_code == 303
        assert paused.headers["location"] == f"{ROOT}/following/{slug}"

        gone = client.post(
            f"{ROOT}/following/{slug}/delete", headers=headers, follow_redirects=False
        )
        assert gone.status_code == 303
        assert gone.headers["location"] == f"{ROOT}/following"


# ------------------------------------------------------- 6. values, not renderings


def test_no_outcome_carries_a_rendered_clock_or_a_spoken_duration(
    tmp_path: Path,
) -> None:
    """Decision 5, applied to the write side: React formats, Python decides.

    The refusal text is deliberately not scanned — `message` and `next` are
    policy and stay Python's, and one of them quotes a duration floor.
    """
    with owner_client(tmp_path) as client:
        sign_in(client)
        for response in (
            post(client, f"{ROOT}/jobs/job_running001/cancel"),
            post(client, f"{ROOT}/index", data={"urls": "vid00000045"}),
            post(client, f"{ROOT}/videos/zduSFxRajkE/reindex"),
            post(client, f"{ROOT}/videos/kCc8FmEb1nY/tags", data={"add": "topic:typed"}),
        ):
            assert response.status_code == 200
            assert scan(response.json()) == [], response.text


def test_a_follow_outcome_sends_epochs_where_the_tool_sends_stamps(
    tmp_path: Path,
) -> None:
    """`tools/follows._follow_fields` answers this row in `iso_minute` strings.

    That is the model's shape and it is exactly what a dashboard payload may
    not carry, so `_follow_payload` is the browser's shape of the same row —
    built from `Rules.from_row`, the parser the check itself uses, so the two
    cannot disagree about what a CSV column meant.

    A separate test from the one above because the two fixture corpora are
    seeded into the same `tmp_path/data` and the first one there wins.
    """
    with follows_client(tmp_path) as client:
        follows_sign_in(client)
        row = post(
            client, f"{ROOT}/following/andrej-karpathy/state", data={"action": "pause"}
        )
        assert row.status_code == 200
        assert scan(row.json()) == [], row.text
        follow = row.json()["follow"]
        assert isinstance(follow["next_check_at"], int)
        assert isinstance(follow["last_check_at"], int)
        assert isinstance(follow["check_interval_s"], int)
        assert follow["tags"] == ["topic:llm"]
