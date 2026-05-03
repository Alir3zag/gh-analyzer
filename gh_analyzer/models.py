from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class CLIArgs:
    username: str
    repo_name: str
    since: datetime | None = None
    limit: int | None = None
    output_format: str = "text"
    top: int | None = None
    verbose: bool = False
    token: str | None = None
    validate_token: bool = False

    def __post_init__(self) -> None:
        if self.output_format not in {"text", "json", "table"}:
            raise ValueError("Output format must be 'text', 'json', or 'table'")


@dataclass
class Repo:
    name: str
    full_name: str
    description: str | None
    stars: int
    forks: int
    open_issues: int
    default_branch: str
    created_at: datetime
    updated_at: datetime
    language: str | None
    url: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Repo":
        created_at_str = data.get("created_at")
        updated_at_str = data.get("updated_at")

        if not isinstance(created_at_str, str):
            raise ValueError("Repo payload missing/invalid created_at")
        if not isinstance(updated_at_str, str):
            raise ValueError("Repo payload missing/invalid updated_at")

        return cls(
            name=data["name"],
            full_name=data["full_name"],
            description=data.get("description"),
            stars=data["stargazers_count"],
            forks=data["forks_count"],
            open_issues=data["open_issues_count"],
            default_branch=data["default_branch"],
            created_at=datetime.fromisoformat(created_at_str.replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(updated_at_str.replace("Z", "+00:00")),
            language=data.get("language"),
            url=data.get("html_url") or "",
        )


@dataclass
class Commit:
    sha: str
    message: str
    author_login: str | None
    author_email: str | None
    author_name: str | None
    date: datetime
    url: str

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Commit":
        commit_obj = data.get("commit") or {}
        commit_author = commit_obj.get("author") or {}
        api_author = data.get("author") or {}

        date_str = commit_author.get("date")
        if not isinstance(date_str, str):
            raise ValueError("Commit payload missing/invalid commit.author.date")

        return cls(
            sha=data["sha"],
            message=(commit_obj.get("message") or "").strip(),
            author_login=api_author.get("login"),
            author_name=commit_author.get("name"),
            author_email=commit_author.get("email"),
            date=datetime.fromisoformat(date_str.replace("Z", "+00:00")),
            url=data.get("html_url") or "",
        )

    def __str__(self) -> str:
        who = self.author_login or self.author_name or "unknown"
        msg = self.message.replace("\n", " ").strip()  # Normalize to single line
        # Truncate to 80 characters for concise display
        max_len = 80
        if len(msg) > max_len:
            msg = msg[: max_len - 1].rstrip() + "…"
        return f"{self.short_sha} - {msg} (by {who} on {self.date:%Y-%m-%d})"


@dataclass
class Issue:
    id: int
    number: int
    title: str
    state: str                    # "open" | "closed"
    author_login: str | None
    labels: list[str]
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    url: str

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    @property
    def resolution_time(self) -> float | None:
        """Resolution time in hours. None if still open."""
        if self.closed_at is None:
            return None
        return (self.closed_at - self.created_at).total_seconds() / 3600

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Issue":
        # GitHub's issues endpoint also returns PRs callers must filter
        # by checking `data.get("pull_request") is None` before calling this.
        created_at_str = data.get("created_at")
        updated_at_str = data.get("updated_at")
        closed_at_str = data.get("closed_at")

        if not isinstance(created_at_str, str):
            raise ValueError("Issue payload missing/invalid created_at")
        if not isinstance(updated_at_str, str):
            raise ValueError("Issue payload missing/invalid updated_at")

        return cls(
            id=data["id"],
            number=data["number"],
            title=data.get("title") or "",
            state=data.get("state") or "open",
            author_login=(data.get("user") or {}).get("login"),
            labels=[lbl["name"] for lbl in data.get("labels") or [] if "name" in lbl],
            created_at=datetime.fromisoformat(created_at_str.replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(updated_at_str.replace("Z", "+00:00")),
            closed_at=(
                datetime.fromisoformat(closed_at_str.replace("Z", "+00:00"))
                if isinstance(closed_at_str, str)
                else None
            ),
            url=data.get("html_url") or "",
        )


@dataclass
class PullRequest:
    id: int
    number: int
    title: str
    state: str                    # "open" | "closed"
    author_login: str | None
    merged: bool
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    merged_at: datetime | None    # None if not merged
    url: str

    @property
    def is_merged(self) -> bool:
        return self.merged

    @property
    def time_to_merge(self) -> float | None:
        """Time from open to merge in hours. None if not merged."""
        if self.merged_at is None:
            return None
        return (self.merged_at - self.created_at).total_seconds() / 3600

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PullRequest":
        created_at_str = data.get("created_at")
        updated_at_str = data.get("updated_at")
        closed_at_str = data.get("closed_at")
        merged_at_str = data.get("merged_at")

        if not isinstance(created_at_str, str):
            raise ValueError("PullRequest payload missing/invalid created_at")
        if not isinstance(updated_at_str, str):
            raise ValueError("PullRequest payload missing/invalid updated_at")

        return cls(
            id=data["id"],
            number=data["number"],
            title=data.get("title") or "",
            state=data.get("state") or "open",
            author_login=(data.get("user") or {}).get("login"),
            merged=merged_at_str is not None,
            created_at=datetime.fromisoformat(created_at_str.replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(updated_at_str.replace("Z", "+00:00")),
            closed_at=(
                datetime.fromisoformat(closed_at_str.replace("Z", "+00:00"))
                if isinstance(closed_at_str, str)
                else None
            ),
            merged_at=(
                datetime.fromisoformat(merged_at_str.replace("Z", "+00:00"))
                if isinstance(merged_at_str, str)
                else None
            ),
            url=data.get("html_url") or "",
        )


@dataclass
class Release:
    id: int
    tag: str
    name: str | None
    prerelease: bool
    published_at: datetime
    url: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Release":
        published_at_str = data.get("published_at")

        if not isinstance(published_at_str, str):
            raise ValueError("Release payload missing/invalid published_at")

        return cls(
            id=data["id"],
            tag=data.get("tag_name") or "",
            name=data.get("name"),
            prerelease=data.get("prerelease") or False,
            published_at=datetime.fromisoformat(published_at_str.replace("Z", "+00:00")),
            url=data.get("html_url") or "",
        )


@dataclass
class PRReview:
    id: int
    pr_number: int
    reviewer_login: str | None
    state: str                   # "APPROVED" | "CHANGES_REQUESTED" | "COMMENTED" | "DISMISSED"
    submitted_at: datetime
    url: str

    @property
    def is_approval(self) -> bool:
        return self.state == "APPROVED"

    @property
    def requested_changes(self) -> bool:
        return self.state == "CHANGES_REQUESTED"

    @classmethod
    def from_api(cls, data: dict[str, Any], pr_number: int) -> "PRReview":
        submitted_at_str = data.get("submitted_at")

        if not isinstance(submitted_at_str, str):
            raise ValueError("PRReview payload missing/invalid submitted_at")

        return cls(
            id=data["id"],
            pr_number=pr_number,
            reviewer_login=(data.get("user") or {}).get("login"),
            state=data.get("state") or "",
            submitted_at=datetime.fromisoformat(submitted_at_str.replace("Z", "+00:00")),
            url=data.get("html_url") or "",
        )