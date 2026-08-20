"""TIDAL OAuth and OpenAPI integration.

The client secret is deliberately kept out of the extension configuration because
configuration is readable by browser clients.  Credentials live in a mode-0600
file and access tokens are retained in memory only.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

from core.actor import Actor

logger = logging.getLogger(__name__)

AUTH_URL = "https://auth.tidal.com/v1/oauth2/token"
API_URL = "https://openapi.tidal.com/v2"
CREDENTIALS_PATH = Path(__file__).parent.parent / "core" / "db" / "tidal_credentials.json"
ALLOWED_SCOPES = frozenset(
    {
        "collection.read",
        "collection.write",
        "entitlements.read",
        "playback",
        "playlists.read",
        "playlists.write",
        "recommendations.read",
        "search.read",
        "search.write",
        "user.read",
    }
)


class TidalError(RuntimeError):
    pass


class TidalExtension(Actor):
    def __init__(self, name, core, db, config):
        super().__init__()
        self._name = name
        self._core = core
        self._db = db
        self._config = config.get(name, {})
        self._credentials = self._read_credentials()
        self._token = None
        self._token_expires_at = 0
        self._session = None
        self._token_lock = asyncio.Lock()

    async def on_start(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        logger.info("Started")

    async def on_stop(self):
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("Stopped")

    async def on_event(self, message):
        pass

    def _read_credentials(self):
        try:
            data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
            return {
                "client_id": str(data.get("client_id", "")).strip(),
                "client_secret": str(data.get("client_secret", "")).strip(),
            }
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"client_id": "", "client_secret": ""}

    def _write_credentials(self, client_id, client_secret):
        CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CREDENTIALS_PATH.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"client_id": client_id, "client_secret": client_secret}, stream)
        os.replace(temporary, CREDENTIALS_PATH)
        os.chmod(CREDENTIALS_PATH, 0o600)

    def on_status(self):
        return {
            "configured": bool(
                self._credentials["client_id"] and self._credentials["client_secret"]
            ),
            "client_id_hint": (
                f"...{self._credentials['client_id'][-4:]}"
                if self._credentials["client_id"]
                else None
            ),
            "country_code": self._config.get("country_code", "US"),
            "scopes": sorted(ALLOWED_SCOPES),
        }

    async def on_configure(self, client_id, client_secret, country_code="US"):
        client_id = str(client_id).strip()
        client_secret = str(client_secret).strip()
        country_code = str(country_code).strip().upper()
        if not client_id or not client_secret:
            raise ValueError("Client ID and Client Secret are required")
        if len(country_code) != 2 or not country_code.isalpha():
            raise ValueError("Country code must be a two-letter ISO code")

        previous = self._credentials
        self._credentials = {"client_id": client_id, "client_secret": client_secret}
        self._token = None
        self._token_expires_at = 0
        try:
            await self._access_token()
        except Exception:
            self._credentials = previous
            raise

        self._write_credentials(client_id, client_secret)
        self._config["country_code"] = country_code
        self._db.set_config({self._name: {"country_code": country_code}})
        return self.on_status()

    async def on_disconnect(self):
        self._credentials = {"client_id": "", "client_secret": ""}
        self._token = None
        self._token_expires_at = 0
        try:
            CREDENTIALS_PATH.unlink()
        except FileNotFoundError:
            pass
        return self.on_status()

    async def _access_token(self):
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not all(self._credentials.values()):
            raise TidalError("TIDAL credentials have not been configured")

        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            if not self._session:
                raise TidalError("TIDAL service is not started")
            async with self._session.post(
                AUTH_URL,
                data={"grant_type": "client_credentials"},
                auth=aiohttp.BasicAuth(
                    self._credentials["client_id"], self._credentials["client_secret"]
                ),
            ) as response:
                payload = await self._response_json(response)
            self._token = payload.get("access_token")
            if not self._token:
                raise TidalError("TIDAL did not return an access token")
            self._token_expires_at = time.monotonic() + max(
                int(payload.get("expires_in", 3600)) - 60, 1
            )
            return self._token

    async def _response_json(self, response):
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, json.JSONDecodeError):
            payload = {"message": (await response.text())[:300]}
        if response.status >= 400:
            message = payload.get("error_description") or payload.get("message")
            raise TidalError(message or f"TIDAL request failed ({response.status})")
        return payload

    async def _get(self, path, params=None):
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.tidal.v1+json",
        }
        async with self._session.get(
            f"{API_URL}/{path.lstrip('/')}", headers=headers, params=params
        ) as response:
            if response.status == 401:
                self._token = None
                token = await self._access_token()
                headers["Authorization"] = f"Bearer {token}"
                async with self._session.get(
                    f"{API_URL}/{path.lstrip('/')}", headers=headers, params=params
                ) as retry:
                    return await self._response_json(retry)
            return await self._response_json(response)

    async def on_search(self, query, limit=20):
        query = str(query).strip()
        if not query:
            return {}
        limit = min(max(int(limit), 1), 50)
        return await self._get(
            "searchResults/" + urlencode({"query": query})[6:],
            {
                "countryCode": self._config.get("country_code", "US"),
                "include": "artists,albums,tracks",
                "limit": limit,
            },
        )

    async def on_album(self, album_id):
        return await self._get(
            f"albums/{album_id}",
            {"countryCode": self._config.get("country_code", "US")},
        )
