from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from src.utils.config import get_config

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Base scraper with rate limiting, retries, and async HTTP client."""

    def __init__(self) -> None:
        self.config = get_config()
        self._last_request_time: float = 0.0
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self.config.scraping.user_agent},
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _rate_limit(self) -> None:
        """Enforce minimum delay between requests."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        delay = self.config.scraping.rate_limit_seconds
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = time.monotonic()

    async def fetch(self, url: str) -> str:
        """Fetch a URL with rate limiting and exponential backoff retry."""
        await self._rate_limit()
        client = await self._get_client()

        max_retries = self.config.scraping.max_retries
        for attempt in range(max_retries):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response.text
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to fetch {url} after {max_retries} attempts: {e}")
                    raise
                wait = 2 ** (attempt + 1)
                logger.warning(
                    f"Retry {attempt + 1}/{max_retries} for {url}, waiting {wait}s: {e}"
                )
                await asyncio.sleep(wait)

        raise RuntimeError("Unreachable")

    @abstractmethod
    async def scrape(self, **kwargs: Any) -> int:
        """Run the scrape. Return number of records upserted."""
        ...
