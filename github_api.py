import asyncio
import os
import time
import aiohttp
from datetime import datetime, timezone
from models import CLIArgs, Repo
import random
from cli import from_args
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
            print("⚠️  GITHUB_TOKEN not set. Running unauthenticated (60 requests/hour).")
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
        # Handle both seconds and HTTP-date formats for Retry-After
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                self.retry_after = int(retry_after)
            except ValueError:
                # Try parsing as HTTP-date
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(retry_after)
                # Calculate seconds until retry
                self.retry_after = max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))



    def report(self) -> None:
        """Print a concise, human-friendly summary of remaining requests and reset."""
        if self.remaining is None:
            print("\n📊 Rate Limit Remaining: unknown")
            return

        reset_info = ""
        reset_dt = None
        seconds_left = None

        if self.reset_time is not None:
            try:
                reset_dt = datetime.fromtimestamp(self.reset_time, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                seconds_left = int(self.reset_time - time.time())
                reset_info = (
                    f", resets in ~{seconds_left}s at {reset_dt}"
                    if seconds_left > 0
                    else f", reset at {reset_dt}"
                )
            except Exception:
                reset_info = ""

        if self.remaining == 0:
            limit_info = f" Expected quota after reset: {self.limit}" if self.limit is not None else ""
            if seconds_left is not None and seconds_left > 0:
                print(f"\n⚠️ Rate limit exhausted. Waiting ~{seconds_left}s until reset at {reset_dt}.{limit_info}")
            else:
                print("\n⚠️ Rate limit exhausted. Reset time unknown.")
            return

        if self.remaining < RATE_LIMIT_THRESHOLD:
            if seconds_left is not None and seconds_left > 0:
                print(f"\n⚠️ Rate limit low: {self.remaining} remaining; resets in ~{seconds_left}s at {reset_dt}")
            else:
                print(f"\n⚠️ Rate limit low: {self.remaining} remaining")
            return

        print(f"\n📊 Rate Limit Remaining: {self.remaining}{reset_info}")

    async def wait_if_needed(self) -> None:
        if self.retry_after is not None and self.retry_after > 0:
            wait = max(self.retry_after, 0)
            print(f"⚠️ Received Retry-After: sleeping for {self.retry_after}s...")
            await asyncio.sleep(wait + random.uniform(0.1, 0.5))  # Add jitter to avoid thundering herd
            return
        
        if self.remaining is None or self.reset_time is None:
            return

        now = time.time()
        seconds_left = self.reset_time - now

        # Hard limit(Exhausted)
        if self.remaining == 0:
            if seconds_left > 0:
                print(f"⚠️ Rate limit exhausted. Sleeping ~{seconds_left + 1:.0f}s until reset...")
                await asyncio.sleep(seconds_left + 1)
            return

        # Soft throttling when remaining requests are low spread the requests until reset
        if self.remaining < RATE_LIMIT_THRESHOLD:
            # clock skew or stale header
            if seconds_left <= 0:
                return

            delay = seconds_left / max(self.remaining, 1)

            # Clamp delay to reasonable range
            delay = min(max(delay, 0.05), 2.0)

            print(
                f"⚠️ Rate limit low ({self.remaining} left). "
                f"Soft throttling: sleeping ~{delay:.2f}s"
            )

            await asyncio.sleep(delay)


async def fetch_repo(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    cli_args: CLIArgs,
    rate_limiter: RateLimitTracker,
) -> Repo:
    url = f"{BASE_URL}/{cli_args.username}/{cli_args.repo_name}"

    for attempt in range(1, MAX_RETRIES + 1):
        async with sem:
            try:
                async with session.get(url) as resp:
                    rate_limiter.update_from_headers(resp.headers)

                    if resp.status == 200:
                        data = await resp.json()
                        return Repo.from_api(data)

                    if resp.status in (403, 429):
                        text = await resp.text()
                        # Detect secondary rate limit
                        if "secondary rate limit" in text.lower() or rate_limiter.retry_after:
                            print("⚠️ Secondary rate limit detected.")
                            await rate_limiter.wait_if_needed()
                            rate_limiter.retry_after = None  # reset retry after waiting
                            continue

                        # Otherwise handle primary rate limit
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
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise NetworkError(f"Request failed after {MAX_RETRIES} attempts: {type(e).__name__}: {e}") from e

    raise NetworkError("Retries exhausted.")  # should never hit
