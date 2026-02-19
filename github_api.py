import asyncio
import os
import time
import aiohttp
from datetime import datetime, timezone
from models import CLIArgs, Repo
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
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")

    if token:
        headers["Authorization"] = f"Bearer {token}"
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

    def update_from_headers(self, headers) -> None:
        def _parse_int_header(key: str) -> int | None:
            val = headers.get(key)
            if val is None:
                return None
            try:
                return int(str(val).strip())
            except (TypeError, ValueError):
                return None

        self.remaining = _parse_int_header("X-RateLimit-Remaining")
        self.reset_time = _parse_int_header("X-RateLimit-Reset")
        self.limit = _parse_int_header("X-RateLimit-Limit")

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
        if self.remaining is None or self.reset_time is None:
            return

        if self.remaining == 0:
            wait_time = self.reset_time - time.time()
            if wait_time > 0:
                print(f"⚠️ Rate limit exhausted. Sleeping ~{wait_time + 1:.0f}s until reset...")
                await asyncio.sleep(wait_time + 1)
        elif self.remaining < RATE_LIMIT_THRESHOLD:
            print(f"⚠️ Rate limit low: {self.remaining} requests remaining")


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
