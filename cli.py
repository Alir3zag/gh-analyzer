import sys
import re
import argparse
from datetime import datetime, timezone, timedelta
from models import CLIArgs

def positive_int(value: str) -> int:
    """Custom argparse type: positive integer (> 0)."""
    try:
        ivalue = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be an integer") from e

    if ivalue <= 0:
        raise argparse.ArgumentTypeError("must be positive (> 0)")
    return ivalue

def valid_repo_path(value: str) -> str:
    """Custom argparse type: validates owner/repo format."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise argparse.ArgumentTypeError("must be in owner/repo format (e.g. 'owner/repo')")
    return value

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze GitHub repository activity."
    )

    parser.add_argument(
        "repo",
        type=valid_repo_path,
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

def from_args(argv: list[str] | None = None) -> CLIArgs:
    """Parse CLI args, validate repo format, and return CLIArgs object."""
    parser = build_parser()
    args = parser.parse_args(argv)

    username, repo_name = args.repo.split("/", 1)
    since_dt = datetime.now(timezone.utc) - timedelta(days=args.since)

    return CLIArgs(
        username=username,
        repo_name=repo_name,
        since=since_dt,
        limit=args.limit,
        format=args.format,
        top=args.top,
        verbose=args.verbose
    )
