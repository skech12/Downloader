import os
import time
import logging
from typing import Any

import httpx

logger = logging.getLogger("cc0_content")


class CC0APIError(Exception):
    """Raised when the API returns an error."""
    def __init__(self, status_code: int, error: str, message: str):
        self.status_code = status_code
        self.error = error
        self.message = message
        super().__init__(f"[{status_code}] {error}: {message}")


class CC0Client:
    """
    Python client for the CC0 Content API.

    All content returned is verified CC0 (Creative Commons Zero).
    No attribution required. No legal risk.
    """

    # Same default as websiteFolder/config.js API_BASE (Neurvance Heroku); override with CC0_CONTENT_BASE_URL
    DEFAULT_BASE_URL = "https://neurvance-bb82540cb249.herokuapp.com"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        self.api_key = api_key or os.environ.get("CC0_CONTENT_API_KEY")
        if not self.api_key:
            raise ValueError(
                "API key required. Pass api_key= or set CC0_CONTENT_API_KEY env var."
            )

        self.base_url = (base_url or os.environ.get("CC0_CONTENT_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        self.max_retries = max_retries

        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "X-API-Key": self.api_key,
                "Accept": "application/json",
                "User-Agent": "cc0-content-python/1.0",
            },
        )

    def search(self, query: str) -> dict[str, Any]:
        """
        Search for CC0 content.

        Args:
            query: What to search for (e.g. "dogs", "impressionist paintings")
                   Result count and filtering are controlled by backend policy.

        Returns:
            Dict with keys: query, total_results, chunks, sources_queried,
            license_proof, processing_time_ms

        Raises:
            CC0APIError: If the API returns an error
            httpx.TimeoutException: If the request times out
        """
        return self._request("GET", "/api/v1/search", params={"query": query})

    def list_sources(self) -> dict[str, Any]:
        """List all available CC0 content sources."""
        return self._request("GET", "/api/v1/sources")

    def health(self) -> dict[str, Any]:
        """Check if the API is running."""
        return self._request("GET", "/api/v1/health")

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Make a request with retry logic."""
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.request(method, path, **kwargs)

                if resp.status_code == 200:
                    return resp.json()

                # Parse error response
                try:
                    error_body = resp.json()
                    raise CC0APIError(
                        status_code=resp.status_code,
                        error=error_body.get("error", "unknown"),
                        message=error_body.get("message", resp.text),
                    )
                except (ValueError, KeyError):
                    raise CC0APIError(
                        status_code=resp.status_code,
                        error="unknown",
                        message=resp.text,
                    )

            except CC0APIError as e:
                # Retry on server errors and rate limits
                if e.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    wait = min(0.5 * (2 ** attempt), 8)
                    logger.info(f"Retrying in {wait}s (attempt {attempt + 1}): {e}")
                    time.sleep(wait)
                    last_error = e
                    continue
                raise

            except httpx.TimeoutException as e:
                if attempt < self.max_retries:
                    wait = min(1.0 * (2 ** attempt), 10)
                    logger.info(f"Timeout, retrying in {wait}s (attempt {attempt + 1})")
                    time.sleep(wait)
                    last_error = e
                    continue
                raise

        raise last_error or Exception("Max retries exceeded")

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self):
        return f"CC0Client(base_url='{self.base_url}')"


# ── Async client for async applications ───────────────────────────────

class AsyncCC0Client:
    """Async version of the CC0 Content API client."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        self.api_key = api_key or os.environ.get("CC0_CONTENT_API_KEY")
        if not self.api_key:
            raise ValueError(
                "API key required. Pass api_key= or set CC0_CONTENT_API_KEY env var."
            )

        self.base_url = (base_url or os.environ.get("CC0_CONTENT_BASE_URL") or CC0Client.DEFAULT_BASE_URL).rstrip("/")
        self.max_retries = max_retries

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "X-API-Key": self.api_key,
                "Accept": "application/json",
                "User-Agent": "cc0-content-python/1.0",
            },
        )

    async def search(self, query: str) -> dict:
        return await self._request("GET", "/api/v1/search", params={"query": query})

    async def list_sources(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/sources")

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/health")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        import asyncio
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.request(method, path, **kwargs)
                if resp.status_code == 200:
                    return resp.json()
                try:
                    err = resp.json()
                    raise CC0APIError(resp.status_code, err.get("error", ""), err.get("message", ""))
                except (ValueError, KeyError):
                    raise CC0APIError(resp.status_code, "unknown", resp.text)
            except CC0APIError as e:
                if e.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    await asyncio.sleep(min(0.5 * (2 ** attempt), 8))
                    continue
                raise
        raise Exception("Max retries exceeded")

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
