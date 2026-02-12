import sys
import re
import argparse
from models import Repo

def positive_int(value: str) -> int:
    """Custom argparse type: positive integer (> 0)."""
    try:
        ivalue = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be an integer") from e

    if ivalue <= 0:
        raise argparse.ArgumentTypeError("must be positive (> 0)")
    return ivalue

def valid_path(path: str) -> None:
    """Raise ValueError if path is not in owner/repo format"""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
        raise ValueError("Wrong Path Format")

def extract_repo(path: str) -> tuple[str, str]:
    return path.split("/", 1)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze GitHub repository activity."
    )

    parser.add_argument(
        "repo",
        help="Repository in owner/name format (e.g. 'owner/repo')."
    )
    parser.add_argument(
        "--since",
        type=positive_int,
        default=30,
        help="How many days back to analyze (default 30)."
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=100,
        help="Maximum number of results (default 100)."
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", "table"],
        default="text",
        help="Output format (text/json/table)."
    )
    parser.add_argument(
        "--top",
        type=positive_int,
        help="Show top N results (optional)."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging."
    )

    return parser

def parse_and_validate_args(argv: list[str] | None = None) -> Repo:
    """Parse CLI args, validate repo format, and return Repo object."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        valid_path(args.repo)
        username, repo_name = extract_repo(args.repo)
    except ValueError:
        print("Wrong Path Format! Must be owner/repo", file=sys.stderr)
        sys.exit(2)

    # Build Repo object directly
    return Repo(
        username=username,
        repo_name=repo_name,
        since=args.since,
        limit=args.limit,
        format=args.format,
        top=args.top,
        verbose=args.verbose
    )
