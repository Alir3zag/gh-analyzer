import asyncio
import time
import random
import aiohttp
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from models import Repo, Commit, Issue, PullRequest, Release, PRReview
from exceptions import (GitHubAPIError, RateLimitError, InvalidRepoPathError,
    SecondaryRateLimitError, RepoNotFoundError, UnauthorizedError, ForbiddenError, NetworkError)

# Base GitHub REST endpoint for repository resources
BASE_URL = "https://api.github.com/repos"

# Maximum concurrent in-flight HTTP requests.
MAX_CONCURRENT_REQUESTS = 5

RATE_LIMIT_THRESHOLD = 10

MAX_RETRIES = 3

# GitHub's maximum allowed page size for list endpoints
PAGE_SIZE = 100

# Secondary fallback delay when GitHub signals abuse detection but primary quota looks healthy.
# This is intentionally conservative — secondary limits are opaque and retrying too fast re-triggers them.
SECONDARY_RATE_LIMIT_FALLBACK_DELAY = 60.0


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
        headers["Authorization"] = f"Bearer {token.strip()}"  # OAuth 2.0 standard; preferred over legacy 'token' scheme

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
        self.secondary_limited: bool = False  # Flag to indicate if we've hit secondary rate limits (abuse detection)

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


async def _request(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    rate_limiter: RateLimitTracker,
    url: str,
    params: dict | None = None,
) -> dict | list:
    """
    Single HTTP GET with retry, rate-limit awareness, and concurrency control.
    Shared by all fetch functions to avoid duplicating error handling logic.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        # Throttle BEFORE acquiring semaphore to avoid occupying concurrency slots while idle
        delay = rate_limiter.compute_delay_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            async with sem:
                async with session.get(url, params=params) as resp:
                    rate_limiter.update_from_headers(resp.headers)

                    if resp.status == 200:
                        return await resp.json()

                    if resp.status in (403, 429):
                        text = await resp.text()

                        # Detect secondary rate limit: GitHub returns 403/429 with a body
                        # mentioning "secondary rate limit" even when X-RateLimit-Remaining > 0.
                        # This is an independent abuse-detection system — primary quota headers
                        # cannot be trusted to reflect it.
                        is_secondary = (
                            "secondary rate limit" in text.lower()
                            or rate_limiter.retry_after is not None
                        )

                        if is_secondary:
                            rate_limiter.secondary_limited = True
                            if attempt < MAX_RETRIES:
                                # Respect Retry-After if provided, otherwise use a conservative
                                # fixed fallback — retrying immediately re-triggers abuse detection.
                                backoff = (
                                    float(rate_limiter.retry_after)
                                    if rate_limiter.retry_after
                                    else SECONDARY_RATE_LIMIT_FALLBACK_DELAY
                                ) + random.uniform(0.1, 0.5)
                                await asyncio.sleep(backoff)
                                continue
                            raise SecondaryRateLimitError(retry_after=rate_limiter.retry_after)

                        # Primary rate limit exhausted
                        if attempt < MAX_RETRIES:
                            continue
                        raise RateLimitError(reset_time=rate_limiter.reset_time)

                    if resp.status == 404:
                        raise RepoNotFoundError(f"Resource not found: {url}")

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


async def fetch_repo(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    username: str,
    repo_name: str,
    rate_limiter: RateLimitTracker,
) -> Repo:
    """Fetch repository metadata."""
    url = f"{BASE_URL}/{username}/{repo_name}"
    data = await _request(session, sem, rate_limiter, url)
    return Repo.from_api(data)


async def _paginate(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    rate_limiter: RateLimitTracker,
    url: str,
    params: dict | None = None,
):
    """
    Async generator that yields individual items across all pages of a GitHub list endpoint.

    Handles all pagination mechanics — page incrementing, empty page detection, and
    short-page termination. Callers apply their own stop conditions (since, limit)
    by simply breaking out of the async for loop.

    Used by: fetch_commits, fetch_issues, fetch_prs, fetch_releases, fetch_pr_reviews
    """
    params = dict(params or {})
    params["per_page"] = PAGE_SIZE
    page = 1

    while True:
        params["page"] = page
        page_data = await _request(session, sem, rate_limiter, url, params=params)

        if not page_data:
            # Empty page — no more items
            return

        for item in page_data:
            yield item

        # Short page means we've reached the last page
        if len(page_data) < PAGE_SIZE:
            return

        page += 1


async def fetch_commits(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    username: str,
    repo_name: str,
    rate_limiter: RateLimitTracker,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[Commit]:
    """
    Fetch commits with pagination, stopping at --since boundary or --limit cap.
    Commits are returned newest-first, as GitHub provides them.
    """
    url = f"{BASE_URL}/{username}/{repo_name}/commits"

    # Pass `since` to GitHub to reduce pages fetched for narrow time windows.
    # We also check locally per-commit as a safeguard against clock skew.
    params: dict = {}
    if since:
        params["since"] = since.isoformat()

    commits: list[Commit] = []

    async for item in _paginate(session, sem, rate_limiter, url, params):
        commit = Commit.from_api(item)

        if since and commit.date < since:
            # First commit older than the window — stop immediately
            break

        commits.append(commit)

        if limit and len(commits) >= limit:
            # Hard cap reached — stop immediately
            break

    return commits


async def fetch_issues(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    username: str,
    repo_name: str,
    rate_limiter: RateLimitTracker,
    since: datetime | None = None,
    limit: int | None = None,
    state: str = "all",           # "open" | "closed" | "all"
) -> list[Issue]:
    """
    Fetch issues with pagination, stopping at --since boundary or --limit cap.

    Note: GitHub's issues endpoint returns both issues and PRs. Items with a
    `pull_request` key are PRs and are skipped here — use fetch_prs for those.
    """
    url = f"{BASE_URL}/{username}/{repo_name}/issues"
    params: dict = {"state": state, "sort": "created", "direction": "desc"}
    if since:
        params["since"] = since.isoformat()

    issues: list[Issue] = []

    async for item in _paginate(session, sem, rate_limiter, url, params):
        # Skip PRs that appear in the issues endpoint
        if item.get("pull_request") is not None:
            continue

        issue = Issue.from_api(item)

        if since and issue.created_at < since:
            break

        issues.append(issue)

        if limit and len(issues) >= limit:
            break

    return issues


async def fetch_prs(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    username: str,
    repo_name: str,
    rate_limiter: RateLimitTracker,
    since: datetime | None = None,
    limit: int | None = None,
    state: str = "all",           # "open" | "closed" | "all"
) -> list[PullRequest]:
    """
    Fetch pull requests with pagination, stopping at --since boundary or --limit cap.

    Uses the dedicated /pulls endpoint which returns full PR objects including
    merged_at, unlike the issues endpoint which omits merge metadata.
    """
    url = f"{BASE_URL}/{username}/{repo_name}/pulls"
    params: dict = {"state": state, "sort": "created", "direction": "desc"}

    prs: list[PullRequest] = []

    async for item in _paginate(session, sem, rate_limiter, url, params):
        pr = PullRequest.from_api(item)

        if since and pr.created_at < since:
            break

        prs.append(pr)

        if limit and len(prs) >= limit:
            break

    return prs


async def fetch_releases(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    username: str,
    repo_name: str,
    rate_limiter: RateLimitTracker,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[Release]:
    """
    Fetch releases with pagination, stopping at --since boundary or --limit cap.
    Releases are returned newest-first by the API.
    """
    url = f"{BASE_URL}/{username}/{repo_name}/releases"

    releases: list[Release] = []

    async for item in _paginate(session, sem, rate_limiter, url, params={}):
        release = Release.from_api(item)

        if since and release.published_at < since:
            break

        releases.append(release)

        if limit and len(releases) >= limit:
            break

    return releases


async def fetch_pr_reviews(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    username: str,
    repo_name: str,
    pr_number: int,
    rate_limiter: RateLimitTracker,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[PRReview]:
    """
    Fetch all reviews for a single PR, stopping at --since boundary or --limit cap.

    Note: This fetches reviews for one PR at a time. To get reviews across all PRs,
    call this per PR from the results of fetch_prs.
    """
    url = f"{BASE_URL}/{username}/{repo_name}/pulls/{pr_number}/reviews"

    reviews: list[PRReview] = []

    async for item in _paginate(session, sem, rate_limiter, url, params={}):
        review = PRReview.from_api(item, pr_number=pr_number)

        if since and review.submitted_at < since:
            break

        reviews.append(review)

        if limit and len(reviews) >= limit:
            break

    return reviews