"""osu! API v2 client: client-credentials token, rate limit, GET/POST."""
import os
import time

import httpx
from dotenv import load_dotenv

from .paths import ENV_FILE

BASE = "https://osu.ppy.sh/api/v2"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
# Newer score format (statistics.great/ok/meh/miss), matches lazer.
API_VERSION = "20240529"
MAX_PER_MIN = 60


class OsuError(Exception):
    """One-line, secret-free error for the CLI."""


class Client:
    def __init__(self) -> None:
        load_dotenv(ENV_FILE)
        self.cid = os.getenv("OSU_CLIENT_ID", "").strip()
        self.secret = os.getenv("OSU_CLIENT_SECRET", "").strip()
        self.user_id = os.getenv("OSU_USER_ID", "").strip()
        if not self.cid or not self.secret:
            raise OsuError("OSU_CLIENT_ID / OSU_CLIENT_SECRET missing in .env")
        self._http = httpx.Client(timeout=30)
        self._token: str | None = None
        self._calls: list[float] = []

    def _throttle(self) -> None:
        now = time.monotonic()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= MAX_PER_MIN:
            time.sleep(60 - (now - self._calls[0]) + 0.1)
        self._calls.append(time.monotonic())

    def _auth(self) -> str:
        if self._token:
            return self._token
        try:
            r = self._http.post(TOKEN_URL, json={
                "client_id": int(self.cid) if self.cid.isdigit() else self.cid,
                "client_secret": self.secret,
                "grant_type": "client_credentials",
                "scope": "public",
            })
        except httpx.HTTPError as e:
            raise OsuError(f"token network error: {type(e).__name__}") from None
        if r.status_code != 200:
            raise OsuError(f"token request failed (HTTP {r.status_code}); check client id/secret")
        try:
            token = r.json()["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise OsuError("token response was invalid") from None
        self._token = token
        return self._token

    def request(self, method: str, path: str, **kw):
        for attempt in range(2):  # one retry after a 429
            self._throttle()
            headers = {
                "Authorization": f"Bearer {self._auth()}",
                "x-api-version": API_VERSION,
                "Accept": "application/json",
            }
            try:
                r = self._http.request(method, BASE + path, headers=headers, **kw)
            except httpx.HTTPError as e:
                raise OsuError(f"network error: {type(e).__name__}") from None
            if r.status_code == 429 and attempt == 0:
                time.sleep(30)
                continue
            if r.status_code >= 400:
                raise OsuError(f"{method} {path} -> HTTP {r.status_code}")
            return r.json()

    def get(self, path: str, **params):
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict):
        return self.request("POST", path, json=body)
