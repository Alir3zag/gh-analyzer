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

    def __post_init__(self) -> None:
        if self.output_format not in {"text", "json"}:
            raise ValueError("Output format must be 'text' or 'json'")


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
        msg = self.message.replace("\n", " ").strip() # Normalize to single line
        # Truncate to 80 characters for concise display
        max_len = 80
        if len(msg) > max_len:
            msg = msg[: max_len - 1].rstrip() + "…"
        return f"{self.short_sha} - {msg} (by {who} on {self.date:%Y-%m-%d})"
