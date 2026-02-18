from typing import Optional
from dataclasses import dataclass
from datetime import datetime

# Dataclass to hold all repository info
@dataclass
class Repo:
    username: str
    repo_name: str
    since: Optional[int] = None
    limit: Optional[int] = None
    format: str = "text"
    top: Optional[int] = None
    verbose: bool = False

@dataclass
class Commit:
    sha: str
    message: str
    date: datetime