import asyncio
import os
import time
import aiohttp
import logging
from datetime import datetime, timezone
from models import CLIArgs, Repo
import random
from exceptions import (
    GitHubAPIError,
    RepoNotFoundError,
    UnauthorizedError,
    RateLimitError,
    NetworkError,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.github.com/repos"

MAX_CONCURRENT_REQUESTS = 5
RATE_LIMIT_THRESHOLD = 10
MAX_RETRIES = 3

_warned_no_token = False


def build_headers() -> dict:
    global _warned_no_token
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-analyzer-cli/1.0",
    }

    token = os.getenv("GITHUB_TOKEN")
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    else:
        if not _warned_no_token:
            logger.warning("GITHUB_TOKEN not set. Running unauthenticated (60 requests/hour).")
            _warned_no_token = True

    return headers


class RateLimitTracker:
    def __init__(self):
        self.remaining: int | None = None
        self.reset_time: int | None = None
        self.limit: int | None = None
        self.retry_after: int | None = None

    def update_from_headers(self, headers) -> None:
        def _parse_int_header(key: str, current: int | None) -> int | None:
            val = headers.get(key)
            if val is None:
                return current
            try:
                return int(str(val).strip())
            except (TypeError, ValueError):
                return current

        self.remaining = _parse_int_header("X-RateLimit-Remaining", self.remaining)
        self.reset_time = _parse_int_header("X-RateLimit-Reset", self.reset_time)
        self.limit = _parse_int_header("X-RateLimit-Limit", self.limit)
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                self.retry_after = int(retry_after)
            except ValueError:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(retry_after)
                self.retry_after = max(
                    0,
                    int((dt - datetime.now(timezone.utc)).total_seconds())
                )
        else:
            self.retry_after = None

    def report(self) -> dict:
        """Return structured rate-limit info for the caller (CLI) to display."""
        return {
            "remaining": self.remaining,
            "reset_time": self.reset_time,
            "limit": self.limit,
        }

    async def wait_if_needed(self) -> None:
        # Secondary rate limit
        if self.retry_after is not None and self.retry_after > 0:
            wait = max(self.retry_after, 0)
            logger.warning(f"Retry-After received: sleeping {wait}s")
            await asyncio.sleep(wait + random.uniform(0.1, 0.5))
            return

        # No primary rate-limit info
        if self.remaining is None or self.reset_time is None:
            return

        now = time.time()
        seconds_left = self.reset_time - now

        # Hard limit
        if self.remaining == 0:
            if seconds_left > 0:
                logger.warning(f"Rate limit exhausted. Sleeping {seconds_left}s")
                await asyncio.sleep(seconds_left + 1)
            return

        # Soft throttling
        if self.remaining < RATE_LIMIT_THRESHOLD:
            if seconds_left <= 0:
                return

            delay = seconds_left / max(self.remaining, 1)
            delay = min(max(delay, 0.05), 2.0)

            # Soft throttling is internal — no logging
            await asyncio.sleep(delay)


async def fetch_repo(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    cli_args: CLIArgs,
    rate_limiter: RateLimitTracker,
) -> Repo:

    url = f"{BASE_URL}/{cli_args.username}/{cli_args.repo_name}"

    for attempt in range(1, MAX_RETRIES + 1):

        # wait BEFORE sending request (soft throttling)
        await rate_limiter.wait_if_needed()

        async with sem:
            try:
                async with session.get(url) as resp:
                    rate_limiter.update_from_headers(resp.headers)

                    if resp.status == 200:
                        data = await resp.json()
                        return Repo.from_api(data)

                    if resp.status in (403, 429):
                        text = await resp.text()

                        # Secondary rate limit
                        if "secondary rate limit" in text.lower() or rate_limiter.retry_after:
                            logger.warning("Secondary rate limit detected.")
                            await rate_limiter.wait_if_needed()
                            rate_limiter.retry_after = None
                            continue

                        # Primary rate limit
                        await rate_limiter.wait_if_needed()
                        if attempt < MAX_RETRIES:
                            continue
                        raise RateLimitError(reset_time=rate_limiter.reset_time)

                    if resp.status == 404:
                        raise RepoNotFoundError(
                            f"Repo {cli_args.username}/{cli_args.repo_name} does not exist or is private."
                        )

                    if resp.status == 401:
                        raise UnauthorizedError("Bad token or missing scopes.")

                    raise GitHubAPIError(f"Unexpected HTTP {resp.status}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < MAX_RETRIES:
                    sleep = (0.5 * (2 ** attempt)) + random.uniform(0, 0.3) # Exponential backoff + jitter
                    await asyncio.sleep(sleep)
                    continue
                raise NetworkError(
                    f"Request failed after {MAX_RETRIES} attempts: {type(e).__name__}: {e}"
                ) from e

    raise NetworkError("Retries exhausted.")
