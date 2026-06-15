import asyncio
import time
import random
import aiohttp
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gh_analyzer.cache import ResponseCache

from gh_analyzer.models import Repo, Commit, Issue, PullRequest, Release, PRReview
from gh_analyzer.exceptions import (GitHubAPIError, RateLimitError, InvalidRepoPathError,
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
        headers["Authorization"] = f"Bearer {token.strip()}"

    return headers


class RateLimitTracker:
    """
    Tracks GitHub rate-limit state extracted from response headers.
    Observational only — exposes state so orchestration can react.
    """

    def __init__(self):
        self.remaining: int | None = None
        self.reset_time: int | None = None
        self.limit: int | None = None
        self.retry_after: int | None = None
        self.secondary_limited: bool = False

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

        self.remaining = _parse_int("X-RateLimit-Remaining", self.remaining)
        self.reset_time = _parse_int("X-RateLimit-Reset", self.reset_time)
        self.limit = _parse_int("X-RateLimit-Limit", self.limit)

        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                self.retry_after = int(retry_after)
            except ValueError:
                dt = parsedate_to_datetime(retry_after)
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


async def _request(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    rate_limiter: RateLimitTracker,
    url: str,
    params: dict | None = None,
    cache: "ResponseCache | None" = None,
) -> dict | list:
    """
    Single HTTP GET with retry, rate-limit awareness, concurrency control,
    and optional response caching.

    If a cache is provided:
    - Returns the cached response immediately on a hit (no network call).
    - Stores the response in the cache after a successful 200.
    """
    # ── Cache read ──────────────────────────────────────────────
    if cache is not None:
        cached = cache.get(url, params)
        if cached is not None:
            return cached

    # ── Network fetch with retries ───────────────────────────────
    for attempt in range(1, MAX_RETRIES + 1):
        delay = rate_limiter.compute_delay_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            async with sem:
                async with session.get(url, params=params) as resp:
                    rate_limiter.update_from_headers(resp.headers)

                    if resp.status == 200:
                        data = await resp.json()
                        # ── Cache write ──────────────────────────
                        if cache is not None:
                            cache.set(url, params, data)
                        return data

                    if resp.status in (403, 429):
                        text = await resp.text()

                        is_secondary = (
                            "secondary rate limit" in text.lower()
                            or rate_limiter.retry_after is not None
                        )

                        if is_secondary:
                            rate_limiter.secondary_limited = True
                            if attempt < MAX_RETRIES:
                                backoff = (
                                    float(rate_limiter.retry_after)
                                    if rate_limiter.retry_after
                                    else SECONDARY_RATE_LIMIT_FALLBACK_DELAY
                                ) + random.uniform(0.1, 0.5)
                                await asyncio.sleep(backoff)
                                continue
                            raise SecondaryRateLimitError(retry_after=rate_limiter.retry_after)

                        # B3 FIX: distinguish forbidden from rate limit
                        if resp.status == 403:
                            remaining = rate_limiter.remaining
                            if remaining is None or remaining > 0:
                                raise ForbiddenError(
                                    "Access denied -- the repository may be private, "
                                    "require SSO, or your token lacks the required scopes."
                                )

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
                sleep = (0.5 * (2 ** attempt)) + random.uniform(0, 0.3)
                await asyncio.sleep(sleep)
                continue
            raise NetworkError(
                f"Request failed after {MAX_RETRIES} attempts: {type(e).__name__}: {e}"
            ) from e

    raise NetworkError("Retries exhausted.")


# ── B1 FIX: validate_token ──────────────────────────────────────

async def validate_token(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    rate_limiter: RateLimitTracker,
) -> str:
    """
    Validate the token by calling GET /user.
    Returns the authenticated GitHub username on success.
    Raises GitHubAPIError on failure.
    """
    url = "https://api.github.com/user"
    data = await _request(session, sem, rate_limiter, url)
    return data.get("login") or "unknown"


async def fetch_repo(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    username: str,
    repo_name: str,
    rate_limiter: RateLimitTracker,
    cache: "ResponseCache | None" = None,
) -> Repo:
    """Fetch repository metadata."""
    url = f"{BASE_URL}/{username}/{repo_name}"
    data = await _request(session, sem, rate_limiter, url, cache=cache)
    return Repo.from_api(data)


async def _paginate(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    rate_limiter: RateLimitTracker,
    url: str,
    params: dict | None = None,
    cache: "ResponseCache | None" = None,
):
    """
    Async generator that yields individual items across all pages of a GitHub
    list endpoint. Callers apply their own stop conditions (since, limit).
    """
    params = dict(params or {})
    params["per_page"] = PAGE_SIZE
    page = 1

    while True:
        params["page"] = page
        page_data = await _request(session, sem, rate_limiter, url,
                                   params=params, cache=cache)

        if not page_data:
            return

        for item in page_data:
            yield item

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
    cache: "ResponseCache | None" = None,
) -> list[Commit]:
    """Fetch commits with pagination, stopping at --since boundary or --limit cap."""
    url = f"{BASE_URL}/{username}/{repo_name}/commits"

    params: dict = {}
    if since:
        params["since"] = since.isoformat()

    commits: list[Commit] = []

    async for item in _paginate(session, sem, rate_limiter, url, params,
                                cache=cache):
        commit = Commit.from_api(item)

        if since and commit.date < since:
            break

        commits.append(commit)

        if limit and len(commits) >= limit:
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
    state: str = "all",
    cache: "ResponseCache | None" = None,
) -> list[Issue]:
    """
    Fetch issues with pagination.
    Note: GitHub's issues endpoint returns both issues and PRs -- PRs are skipped.
    """
    url = f"{BASE_URL}/{username}/{repo_name}/issues"
    params: dict = {"state": state, "sort": "created", "direction": "desc"}
    if since:
        params["since"] = since.isoformat()

    issues: list[Issue] = []

    async for item in _paginate(session, sem, rate_limiter, url, params,
                                cache=cache):
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
    state: str = "all",
    cache: "ResponseCache | None" = None,
) -> list[PullRequest]:
    """Fetch pull requests with pagination."""
    url = f"{BASE_URL}/{username}/{repo_name}/pulls"
    params: dict = {"state": state, "sort": "created", "direction": "desc"}

    prs: list[PullRequest] = []

    async for item in _paginate(session, sem, rate_limiter, url, params,
                                cache=cache):
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
    cache: "ResponseCache | None" = None,
) -> list[Release]:
    """Fetch releases with pagination."""
    url = f"{BASE_URL}/{username}/{repo_name}/releases"

    releases: list[Release] = []

    async for item in _paginate(session, sem, rate_limiter, url, params={},
                                cache=cache):
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
    cache: "ResponseCache | None" = None,
) -> list[PRReview]:
    """Fetch all reviews for a single PR."""
    url = f"{BASE_URL}/{username}/{repo_name}/pulls/{pr_number}/reviews"

    reviews: list[PRReview] = []

    async for item in _paginate(session, sem, rate_limiter, url, params={},
                                cache=cache):
        review = PRReview.from_api(item, pr_number=pr_number)

        if since and review.submitted_at < since:
            break

        reviews.append(review)

        if limit and len(reviews) >= limit:
            break

    return reviews