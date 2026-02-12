from typing import Optional
from dataclasses import dataclass

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
