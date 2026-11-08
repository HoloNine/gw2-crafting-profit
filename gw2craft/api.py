"""Small async client for the official GW2 API.

- Splits big ID lists into chunks of 200 (the API limit).
- Runs a few requests at once (default 4) to stay under the rate limit.
- Retries on 429 (too many requests) and 5xx errors, waiting longer each time.
- Never prints or logs the API key.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable
from typing import Any

import httpx

BASE_URL = "https://api.guildwars2.com/v2"
SCHEMA_VERSION = "2022-03-09T02:00:00.000Z"
MAX_IDS = 200


def chunks(ids: list[int], size: int = MAX_IDS) -> Iterable[list[int]]:
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


class GW2Api:
    def __init__(self, api_key: str | None = None, parallel: int = 4, retries: int = 5):
        self.api_key = api_key if api_key is not None else os.environ.get("GW2_API_KEY") or None
        self._sem = asyncio.Semaphore(parallel)
        self._retries = retries
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"X-Schema-Version": SCHEMA_VERSION},
            timeout=30.0,
        )

    async def __aenter__(self) -> GW2Api:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None, auth: bool = False) -> Any:
        """GET one endpoint. Returns parsed JSON. 206 (some IDs invalid) counts as OK."""
        headers = {}
        if auth:
            if not self.api_key:
                raise RuntimeError("This needs an API key. Set GW2_API_KEY.")
            headers["Authorization"] = f"Bearer {self.api_key}"

        delay = 1.0
        for attempt in range(self._retries + 1):
            async with self._sem:
                try:
                    r = await self._client.get(path, params=params, headers=headers)
                except httpx.TransportError:
                    if attempt == self._retries:
                        raise
                    r = None
            if r is not None:
                if r.status_code in (200, 206):
                    return r.json()
                # 404 on a bulk call means none of the IDs were valid.
                if r.status_code == 404 and params and "ids" in params:
                    return []
                if r.status_code != 429 and r.status_code < 500:
                    # Do not include the request URL/headers: they could hold the key.
                    raise RuntimeError(f"GW2 API error {r.status_code} on {path}: {r.text[:200]}")
                if attempt == self._retries:
                    raise RuntimeError(f"GW2 API gave {r.status_code} on {path} after retries")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
        raise AssertionError("unreachable")

    async def get_pages(self, path: str, auth: bool = False, page_size: int = 200) -> list[dict]:
        """Fetch every page of a paginated endpoint (e.g. TP transactions).

        Loops page=0.. until the X-Page-Total header says we have them all.
        """
        out: list[dict] = []
        page, total = 0, 1
        headers = {}
        if auth:
            if not self.api_key:
                raise RuntimeError("This needs an API key. Set GW2_API_KEY.")
            headers["Authorization"] = f"Bearer {self.api_key}"
        while page < total:
            delay = 1.0
            for attempt in range(self._retries + 1):
                async with self._sem:
                    r = await self._client.get(path, params={"page": page, "page_size": page_size},
                                               headers=headers)
                if r.status_code in (200, 206):
                    break
                if r.status_code != 429 and r.status_code < 500:
                    raise RuntimeError(f"GW2 API error {r.status_code} on {path}: {r.text[:200]}")
                if attempt == self._retries:
                    raise RuntimeError(f"GW2 API gave {r.status_code} on {path} after retries")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
            out.extend(r.json())
            total = int(r.headers.get("X-Page-Total", 1) or 1)
            page += 1
        return out

    async def get_many(self, path: str, ids: Iterable[int], auth: bool = False) -> list[dict]:
        """Fetch many objects by ID, 200 per call, several calls at once."""
        id_list = sorted(set(ids))
        tasks = [
            self.get(path, {"ids": ",".join(map(str, part))}, auth=auth)
            for part in chunks(id_list)
        ]
        results: list[dict] = []
        for batch in await asyncio.gather(*tasks):
            results.extend(batch)
        return results
