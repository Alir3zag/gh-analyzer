from typing import Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CLIArgs:
    username: str
    repo_name: str
    since: Optional[datetime] = None
    limit: Optional[int] = None
    format: str = "text"
    top: Optional[int] = None
    verbose: bool = False


@dataclass
class Repo:
    name: str
    full_name: str
    description: Optional[str]
    stars: int
    forks: int
    open_issues: int
    default_branch: str
    created_at: datetime
    updated_at: datetime
    language: Optional[str]
    url: str

    @classmethod
    def from_api(cls, data: dict) -> "Repo":
        return cls(
            name=data["name"],
            full_name=data["full_name"],
            description=data.get("description"),
            stars=data["stargazers_count"],
            forks=data["forks_count"],
            open_issues=data["open_issues_count"],
            default_branch=data["default_branch"],
            created_at=datetime.fromisoformat(data["created_at"].rstrip("Z")),
            updated_at=datetime.fromisoformat(data["updated_at"].rstrip("Z")),
            language=data.get("language"),
            url=data["html_url"],
        )


@dataclass
class Commit:
    sha: str
    message: str
    author_name: str
    author_email: str
    date: datetime
    url: str

    @property
    def short_sha(self) -> str:
        """Return the first 7 characters of the SHA, useful for display."""
        return self.sha[:7]

    @classmethod
    def from_api(cls, data: dict) -> "Commit":
        this_commit = data["commit"]
        return cls(
            sha=data["sha"],
            message=this_commit["message"].strip(),
            author_name=this_commit["author"]["name"],
            author_email=this_commit["author"]["email"],
            date=datetime.fromisoformat(this_commit["author"]["date"].rstrip("Z")),
            url=data["html_url"],
        )
