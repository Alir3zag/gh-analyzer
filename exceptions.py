from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class HTTPContext:
    status: int | None = None
    url: str | None = None
    api_message: str | None = None
    request_id: str | None = None


class GitHubAPIError(Exception):
    """Base exception for all GitHub API errors."""

    def __init__(
        self,
        message: str = "GitHub API error occurred.",
        ctx: HTTPContext | None = None,
    ):
        self.ctx: HTTPContext | None = ctx
        super().__init__(message)


class RepoNotFoundError(GitHubAPIError):
    """404 — repo doesn't exist or is private."""

    def __init__(
        self,
        message: str = "Repository not found.",
        ctx: HTTPContext | None = None,
    ):
        super().__init__(message, ctx)


class UnauthorizedError(GitHubAPIError):
    """401 — bad token or missing scopes."""

    def __init__(
        self,
        message: str = "Unauthorized access.",
        ctx: HTTPContext | None = None,
    ):
        super().__init__(message, ctx)


class ForbiddenError(GitHubAPIError):
    """403 — forbidden (private repo, SSO required, insufficient scopes, etc.)."""

    def __init__(
        self,
        message: str = "Forbidden.",
        ctx: HTTPContext | None = None,
    ):
        super().__init__(message, ctx)


class RateLimitError(GitHubAPIError):
    """403/429 — primary rate limit exhausted."""
    
    def __init__(
        self,
        reset_time: int | None = None,
        ctx: HTTPContext | None = None,
    ):
        self.reset_time: int | None = reset_time
        self.reset_datetime: datetime | None = (
            datetime.fromtimestamp(reset_time, tz=timezone.utc)
            if reset_time is not None
            else None
        )

        if self.reset_datetime is None:
            message = "Rate limit exhausted."
        else:
            message = f"Rate limit exhausted; resets at {self.reset_datetime.isoformat()}"

        super().__init__(message, ctx)


class SecondaryRateLimitError(GitHubAPIError):
    """403/429 — secondary rate limit / abuse detection (often with Retry-After)."""

    def __init__(
        self,
        retry_after: int | None = None,
        ctx: HTTPContext | None = None,
    ):
        self.retry_after: int | None = retry_after

        message = "Secondary rate limit triggered."
        if retry_after is not None:
            message += f" Retry after {retry_after}s."

        super().__init__(message, ctx)


class NetworkError(GitHubAPIError):
    """aiohttp/timeout failure after all retries exhausted."""

    def __init__(self, message: str = "Network error.", ctx: HTTPContext | None = None):
        super().__init__(message, ctx)


class InvalidRepoPathError(ValueError):
    """CLI input not in owner/repo format."""
