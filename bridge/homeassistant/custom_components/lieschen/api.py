"""Kleiner Client fuer die Sprachbefehl-API von Lieschen (/api/voice/*)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)


class LieschenError(Exception):
    """Antwort von Lieschen mit Fehler (status 0 = nicht erreichbar)."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status = status
        self.text = text


class LieschenClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, key: str) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._key = key

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    async def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            async with self._session.request(method, self.url + path, json=body, headers=self._headers,
                                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                text = await r.text()
                if r.status >= 400:
                    try:
                        text = json.loads(text).get("detail") or text
                    except ValueError:
                        pass
                    raise LieschenError(r.status, str(text)[:300])
                return json.loads(text) if text else {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise LieschenError(0, f"Lieschen nicht erreichbar ({type(e).__name__})") from e

    async def info(self) -> dict:
        return await self._req("GET", "/api/voice/info")

    async def connect(self, device: str, target: str) -> dict:
        return await self._req("POST", "/api/voice/connect", {"device": device, "target": target})

    async def assistant(self, device: str) -> dict:
        return await self._req("POST", "/api/voice/assistant", {"device": device})

    async def hangup(self, call_id: int | None = None, device: str | None = None) -> dict:
        body = {"call_id": call_id} if call_id else {"device": device}
        return await self._req("POST", "/api/voice/hangup", body)

    async def listen(self, on_snapshot: Callable[[list], Awaitable[None] | None],
                     on_call: Callable[[dict], Awaitable[None] | None],
                     on_state: Callable[[bool], None]) -> None:
        """Ereignisstrom (Server-Sent Events) halten, bei Abbruch neu verbinden (5 s bis 5 min)."""
        delay = 5
        while True:
            try:
                async with self._session.get(self.url + "/api/voice/events", headers=self._headers,
                                             timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)) as r:
                    if r.status >= 400:
                        raise LieschenError(r.status, (await r.text())[:200])
                    on_state(True)
                    delay = 5
                    event, data = "", []
                    async for raw in r.content:
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif line == "":
                            if data and event in ("", "snapshot"):
                                payload = json.loads("\n".join(data))
                                res = on_snapshot(payload) if event == "snapshot" else on_call(payload)
                                if asyncio.iscoroutine(res):
                                    await res
                            if event == "bye":
                                break
                            event, data = "", []
            except asyncio.CancelledError:
                raise
            except LieschenError as e:
                _LOGGER.warning("Lieschen-Ereignisstrom: %s (%s)", e.text, e.status)
                delay = max(delay, 60)
            except Exception as e:  # noqa: BLE001 - Verbindung fehlt, Lieschen neu gestartet ...
                _LOGGER.debug("Lieschen-Ereignisstrom unterbrochen: %s", e)
            on_state(False)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
