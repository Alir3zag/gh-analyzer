class GitHubAPIError(Exception):
    """Base exception for all GitHub API errors."""

class RepoNotFoundError(GitHubAPIError):
    """404 — repo doesn't exist or is private."""

class UnauthorizedError(GitHubAPIError):
    """401 — bad token or missing scopes."""

class RateLimitError(GitHubAPIError):
    """403/429 — rate limited or abuse protection triggered."""
    def __init__(self, reset_time: int | None = None):
        self.reset_time = reset_time
        super().__init__("Rate limit exhausted" if reset_time is None else f"Rate limit exhausted, resets at {reset_time}")

class NetworkError(GitHubAPIError):
    """aiohttp/timeout failure after all retries exhausted."""

class InvalidRepoPathError(ValueError):
    """CLI input not in owner/repo format."""
    