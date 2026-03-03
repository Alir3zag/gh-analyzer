import asyncio
import time
import random
import aiohttp
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from models import CLIArgs, Repo
from exceptions import (GitHubAPIError, RateLimitError, InvalidRepoPathError, 
    SecondaryRateLimitError, RepoNotFoundError, UnauthorizedError, ForbiddenError, NetworkError)

# Base GitHub REST endpoint for repository resources
BASE_URL = "https://api.github.com/repos"

# Maximum concurrent in-flight HTTP requests.
MAX_CONCURRENT_REQUESTS = 5

RATE_LIMIT_THRESHOLD = 10

MAX_RETRIES = 3


def build_headers(token: str | None) -> dict:
    """
    Build GitHub request headers.
    - Always include Accept + User-Agent
    - Include Authorization only if token is provided
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-analyzer-cli/1.0",
    }

    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"

    return headers


class RateLimitTracker:
    """
    Tracks GitHub rate-limit state extracted from response headers.

    IMPORTANT:
    This class is observational only.
    It simply exposes state so orchestration can decide how to react.
    """

    def __init__(self):
        self.remaining: int | None = None # requests left
        self.reset_time: int | None = None # when the window resets (epoch seconds)
        self.limit: int | None = None # maximum requests allowed in the window
        self.retry_after: int | None = None # Seconds to wait before retrying, if GitHub explicitly requests backoff
        self.secondary_limited: bool = False  # Flag to indicate if we've hit secondary ra1te limits (abuse detection)

    def update_from_headers(self, headers) -> None:
        """Extract rate-limit metadata from GitHub response headers."""

        def _parse_int(key: str, current: int | None) -> int | None:
            val = headers.get(key)
            if val is None:
                return current
            try:
                return int(str(val).strip())
            except (TypeError, ValueError):
                return current

        # Primary rate-limit headers
        self.remaining = _parse_int("X-RateLimit-Remaining", self.remaining)
        self.reset_time = _parse_int("X-RateLimit-Reset", self.reset_time)
        self.limit = _parse_int("X-RateLimit-Limit", self.limit)

        # Retry-After may warn secondary throttling
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                self.retry_after = int(retry_after)
            except ValueError:
                # GitHub may send HTTP-date format instead of seconds
                dt = parsedate_to_datetime(retry_after)
                self.retry_after = max(
                    0, int((dt - datetime.now(timezone.utc)).total_seconds())
                )
        else:
            self.retry_after = None

    def snapshot(self) -> dict:
        """
        Return structured state for CLI/logging/reporting layers.
        """
        return {
            "remaining": self.remaining,
            "reset_time": self.reset_time,
            "limit": self.limit,
            "retry_after": self.retry_after,
            "secondary_limited": self.secondary_limited,
        }

    def compute_delay_seconds(self) -> float:
        """
        Compute recommended delay before next request.
        It spreads requests across the reset window when quota is low
        and respects Retry-After if GitHub explicitly asks for backoff.
        """

        # Explicit backoff requested by GitHub
        if self.retry_after is not None and self.retry_after > 0:
            return float(self.retry_after) + random.uniform(0.1, 0.5) # A random jitter to avoid request bursts 

        if self.remaining is None or self.reset_time is None:
            return 0.0

        seconds_left = self.reset_time - time.time()

        # Hard exhaustion — wait until reset
        if self.remaining == 0:
            return (seconds_left + 1.0) if seconds_left > 0 else 0.0

        # Soft throttling — spread remaining requests over reset window
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
    """
    Fetch repository metadata with retry, backoffs, rate-limit awareness, and concurrency control.
    - semaphore protects only active HTTP requests, not idle backoff time
    - retries handle both transient network errors and rate limiting responses
    - rate-limiter is updated on every response to compute backoff
    """
    url = f"{BASE_URL}/{cli_args.username}/{cli_args.repo_name}"

    for attempt in range(1, MAX_RETRIES + 1):
        # Throttle BEFORE acquiring semaphore to avoid occupying concurrency slots while idle
        delay = rate_limiter.compute_delay_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
            
        try:
            # Semaphore guards only in-flight request
            async with sem:
                async with session.get(url) as resp:
                    rate_limiter.update_from_headers(resp.headers)

                    if resp.status == 200:
                        data = await resp.json()
                        return Repo.from_api(data)
                    # Rate limiting / abuse detection
                    if resp.status in (403, 429):
                        text = await resp.text()
                        # Secondary limits are not always reflected in headers
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
                sleep = (0.5 * (2 ** attempt)) + random.uniform(0, 0.3) # Exponential backoff with jitter for transient failures
                await asyncio.sleep(sleep)
                continue
            raise NetworkError(
                f"Request failed after {MAX_RETRIES} attempts: {type(e).__name__}: {e}"
            ) from e

    raise NetworkError("Retries exhausted.")
