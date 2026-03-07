import os
import re
import argparse
from datetime import datetime, timezone, timedelta

from models import CLIArgs


def positive_int(value: str) -> int:
    try:
        ivalue = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be an integer") from e
    if ivalue <= 0:
        raise argparse.ArgumentTypeError("must be positive (> 0)")
    return ivalue


def valid_repo_path(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise argparse.ArgumentTypeError("must be in owner/repo format (e.g. 'owner/repo')")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze GitHub repository activity.")

    parser.add_argument("repo", type=valid_repo_path, metavar="OWNER/REPO",
                        help="Repository in owner/name format (e.g. 'owner/repo').")
    parser.add_argument("--since", type=positive_int, default=30, metavar="DAYS",
                        help="How many days back to analyze (default: 30).")
    parser.add_argument("--limit", type=positive_int, default=100, metavar="N",
                        help="Maximum number of results (default: 100).")
    parser.add_argument("--format", dest="output_format",
                        choices=["text", "json", "table"], default="text",
                        help="Output format (text/table/json).")
    parser.add_argument("--top", type=positive_int, default=None, metavar="N",
                        help="Show top N results (optional).")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable verbose logging.")

    parser.add_argument("--token", default=None, metavar="TOKEN",
                        help="GitHub token (overrides GITHUB_TOKEN). Warning: may be saved in shell history.")
    parser.add_argument("--no-token", action="store_true",
                        help="Ignore any token and run unauthenticated (useful for testing rate limits).")
    parser.add_argument("--validate-token", action="store_true",
                        help="Validate token once via GitHub /user endpoint before running analysis.")

    return parser


def from_args(argv: list[str] | None = None) -> CLIArgs:
    parser = build_parser()
    args = parser.parse_args(argv)

    username, repo_name = args.repo.split("/", 1)
    since_dt = datetime.now(timezone.utc) - timedelta(days=args.since)

    token: str | None = None
    if not args.no_token:
        token = (args.token or os.getenv("GITHUB_TOKEN") or "").strip() or None

    if args.validate_token and args.no_token:
        parser.error("--validate-token cannot be used with --no-token")
    if args.validate_token and not token:
        parser.error("--validate-token requires a token (use --token or set GITHUB_TOKEN)")

    return CLIArgs(
        username=username,
        repo_name=repo_name,
        since=since_dt,
        limit=args.limit,
        output_format=args.output_format,
        top=args.top,
        verbose=args.verbose,
        token=token,
        validate_token=args.validate_token,
    )
