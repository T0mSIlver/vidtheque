"""Firebase Cloud Messaging, HTTP v1, from a service-account key.

No Firebase SDK: the service account signs a JWT (RS256, `pyjwt[crypto]` is
already a dependency), trades it for an hour-long access token, and each send
is one POST. The key file is `VIDTHEQUE_FCM_CREDENTIALS`; it is read, never
logged and never echoed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import httpx2 as httpx
import jwt

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class Delivery(Enum):
    SENT = "sent"
    # The phone uninstalled the app or the token rotated: forget the device.
    GONE = "gone"
    FAILED = "failed"


@dataclass(frozen=True)
class ServiceAccount:
    project_id: str
    client_email: str
    private_key: str
    token_uri: str = TOKEN_URI

    @classmethod
    def load(cls, path: str | Path) -> "ServiceAccount":
        raw = json.loads(Path(path).read_text())
        return cls(raw["project_id"], raw["client_email"], raw["private_key"], raw.get("token_uri") or TOKEN_URI)


class FcmSender:
    def __init__(self, account: ServiceAccount, http: httpx.AsyncClient, clock=time.time) -> None:
        self.account = account
        self.http = http
        self.clock = clock
        self._token: tuple[str, float] | None = None

    async def send(self, device_token: str, data: dict[str, str]) -> Delivery:
        """One data message to one phone; the app draws the notification itself."""
        body = {"message": {"token": device_token, "data": data, "android": {"priority": "HIGH"}}}
        url = f"https://fcm.googleapis.com/v1/projects/{self.account.project_id}/messages:send"
        response = await self.http.post(url, json=body, headers={"Authorization": f"Bearer {await self._access()}"}, timeout=15)
        if response.status_code == 200:
            return Delivery.SENT
        # v1's codes for a token that will never work again (FCM docs, "Error codes").
        if response.status_code == 404 or _fcm_error(response) == "UNREGISTERED":
            return Delivery.GONE
        return Delivery.FAILED

    async def _access(self) -> str:
        now = self.clock()
        if self._token and self._token[1] > now + 60:
            return self._token[0]
        assertion = jwt.encode(
            {"iss": self.account.client_email, "scope": SCOPE, "aud": self.account.token_uri, "iat": int(now), "exp": int(now) + 3600},
            self.account.private_key,
            algorithm="RS256",
        )
        response = await self.http.post(
            self.account.token_uri,
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        self._token = (payload["access_token"], now + float(payload.get("expires_in", 3600)))
        return self._token[0]


def _fcm_error(response: httpx.Response) -> str | None:
    try:
        details: list[dict[str, Any]] = response.json()["error"].get("details", [])
    except Exception:
        return None
    for detail in details:
        if detail.get("@type", "").endswith("FcmError"):
            return detail.get("errorCode")
    return None
