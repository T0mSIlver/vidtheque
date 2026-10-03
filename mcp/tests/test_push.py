"""Push: when a verdict becomes a notification, and the FCM wire it goes out on."""

from __future__ import annotations

import time

import httpx2 as httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.config import ConfigError
from vidtheque_mcp.push.fcm import Delivery, FcmSender, ServiceAccount
from vidtheque_mcp.push.notify import Notifier, PushSettings
from vidtheque_mcp.verdicts.stage import VerdictStage
from vidtheque_mcp.verdicts import store

from .test_verdicts import FakeModel, FixedRoll, rows, verdict, video_id


class FakeSender:
    def __init__(self, outcomes: dict[str, Delivery]) -> None:
        self.outcomes = outcomes
        self.sent: list[tuple[str, dict[str, str]]] = []

    async def send(self, device_token: str, data: dict[str, str]) -> Delivery:
        self.sent.append((device_token, data))
        return self.outcomes[device_token]


async def judge(parts: Assembled, vid: int, score: int, sender: FakeSender, *, published_days_ago: float = 0) -> None:
    now = time.time()
    await parts.db.write(
        lambda c: c.execute("UPDATE videos SET published_at = ? WHERE id = ?", (int(now - published_days_ago * 86_400), vid))
    )
    cue = (await rows(parts.db, "SELECT id, start_s FROM cues WHERE video_id = ? ORDER BY seq", (vid,)))[0]
    model = FakeModel(verdict({"cue_id": cue["id"], "offset_s": float(cue["start_s"]), "why": "his eval setup"}, score=score))
    parts.runner.handlers["verdict"] = VerdictStage(
        parts.deps, model, "api:fake", rng=FixedRoll(1.0), push=Notifier(parts.db, sender, min_score=3)
    )
    await parts.db.write(lambda c: store.queue(c, vid))
    assert await parts.runner.run_once() is True


async def devices(parts: Assembled, *tokens: str) -> None:
    await parts.db.write(lambda c: c.executemany("INSERT INTO devices (token) VALUES (?)", [(t,) for t in tokens]))


async def test_a_fresh_score_3_reaches_every_phone_once_and_forgets_a_dead_token(assembled: Assembled) -> None:
    vid = await video_id(assembled.db, "kCc8FmEb1nY")
    await devices(assembled, "live:phone", "dead:phone")
    sender = FakeSender({"live:phone": Delivery.SENT, "dead:phone": Delivery.GONE})

    await judge(assembled, vid, 3, sender)

    assert sorted(t for t, _ in sender.sent) == ["dead:phone", "live:phone"]
    data = sender.sent[0][1]
    assert (data["score"], data["reason"], data["moment_why"]) == ("3", "evals ↑", "his eval setup")
    assert [r["token"] for r in await rows(assembled.db, "SELECT token FROM devices")] == ["live:phone"]
    assert (await assembled.db.read(lambda c: store.get(c, vid)))["notified_at"] is not None

    # A rerun of the same verdict does not buzz again.
    await judge(assembled, vid, 3, sender)
    assert len(sender.sent) == 2


@pytest.mark.parametrize(("score", "days_ago"), [(2, 0), (3, 10)])
async def test_below_the_threshold_or_an_old_video_sends_nothing(assembled: Assembled, score: int, days_ago: float) -> None:
    """The backfill judges old videos through the same stage; they never push."""
    vid = await video_id(assembled.db, "kCc8FmEb1nY")
    await devices(assembled, "live:phone")
    sender = FakeSender({"live:phone": Delivery.SENT})

    await judge(assembled, vid, score, sender, published_days_ago=days_ago)

    assert sender.sent == []
    assert (await assembled.db.read(lambda c: store.get(c, vid)))["notified_at"] is None


def test_the_threshold_must_be_a_score(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDTHEQUE_NOTIFY_MIN_SCORE", "4")
    with pytest.raises(ConfigError, match="0-3"):
        PushSettings.from_env()


async def test_fcm_signs_one_assertion_per_hour_and_reads_dead_tokens() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    account = ServiceAccount("proj", "push@proj.iam.gserviceaccount.com", pem)
    assertions: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = dict(httpx.QueryParams(request.content.decode()))
            assertions.append(jwt.decode(form["assertion"], key.public_key(), algorithms=["RS256"], audience=account.token_uri))
            return httpx.Response(200, json={"access_token": "ya29.t", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer ya29.t"
        assert request.url.path == "/v1/projects/proj/messages:send"
        token = __import__("json").loads(request.content)["message"]["token"]
        if token == "gone":
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})
        if token == "unregistered":
            detail = {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": "UNREGISTERED"}
            return httpx.Response(400, json={"error": {"details": [detail]}})
        if token == "busy":
            return httpx.Response(503)
        return httpx.Response(200, json={"name": "projects/proj/messages/1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sender = FcmSender(account, http)
        results = [await sender.send(t, {"video_id": "x"}) for t in ("ok", "gone", "unregistered", "busy")]

    assert results == [Delivery.SENT, Delivery.GONE, Delivery.GONE, Delivery.FAILED]
    assert len(assertions) == 1
    assert assertions[0]["iss"] == account.client_email
    assert assertions[0]["scope"] == "https://www.googleapis.com/auth/firebase.messaging"
