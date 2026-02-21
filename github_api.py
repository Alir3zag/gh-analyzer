import asyncio
import time
import random
import aiohttp
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from models import CLIArgs, Repo
from exceptions import (
    GitHubAPIError,
    RepoNotFoundError,
    UnauthorizedError,
    RateLimitError,
    NetworkError,
)

BASE_URL = "https://api.github.com/repos"

MAX_CONCURRENT_REQUESTS = 5
RATE_LIMIT_THRESHOLD = 10
MAX_RETRIES = 3


def build_headers(token: str | None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-analyzer-cli/1.0",
    }
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


class RateLimitTracker:
    def __init__(self):
        self.remaining: int | None = None
        self.reset_time: int | None = None
        self.limit: int | None = None
        self.retry_after: int | None = None
        self.secondary_limited: bool = False

    def update_from_headers(self, headers) -> None:
        def _parse_int(key: str, current: int | None) -> int | None:
            val = headers.get(key)
            if val is None:
                return current
            try:
                return int(str(val).strip())
            except (TypeError, ValueError):
                return current

        self.remaining = _parse_int("X-RateLimit-Remaining", self.remaining)
        self.reset_time = _parse_int("X-RateLimit-Reset", self.reset_time)
        self.limit = _parse_int("X-RateLimit-Limit", self.limit)

        ra = headers.get("Retry-After")
        if ra:
            try:
                self.retry_after = int(ra)
            except ValueError:
                dt = parsedate_to_datetime(ra)
                self.retry_after = max(
                    0, int((dt - datetime.now(timezone.utc)).total_seconds())
                )
        else:
            self.retry_after = None

    def snapshot(self) -> dict:
        return {
            "remaining": self.remaining,
            "reset_time": self.reset_time,
            "limit": self.limit,
            "retry_after": self.retry_after,
            "secondary_limited": self.secondary_limited,
        }

    def compute_delay_seconds(self) -> float:
        if self.retry_after is not None and self.retry_after > 0:
            return float(self.retry_after) + random.uniform(0.1, 0.5)

        if self.remaining is None or self.reset_time is None:
            return 0.0

        seconds_left = self.reset_time - time.time()

        if self.remaining == 0:
            return (seconds_left + 1.0) if seconds_left > 0 else 0.0

        if self.remaining < RATE_LIMIT_THRESHOLD and seconds_left > 0:
            delay = seconds_left / max(self.remaining, 1)
            return float(min(max(delay, 0.05), 2.0))

        return 0.0


async def fetch_repo(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    cli_args: CLIArgs,
    rate_limiter: RateLimitTracker,
) -> Repo:
    url = f"{BASE_URL}/{cli_args.username}/{cli_args.repo_name}"

    for attempt in range(1, MAX_RETRIES + 1):
        delay = rate_limiter.compute_delay_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            async with sem:
                async with session.get(url) as resp:
                    rate_limiter.update_from_headers(resp.headers)

                    if resp.status == 200:
                        return Repo.from_api(await resp.json())

                    if resp.status in (403, 429):
                        text = await resp.text()
                        if "secondary rate limit" in text.lower() or rate_limiter.retry_after:
                            rate_limiter.secondary_limited = True

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
                sleep = (0.5 * (2 ** attempt)) + random.uniform(0, 0.3)
                await asyncio.sleep(sleep)
                continue
            raise NetworkError(
                f"Request failed after {MAX_RETRIES} attempts: {type(e).__name__}: {e}"
            ) from e

    raise NetworkError("Retries exhausted.")