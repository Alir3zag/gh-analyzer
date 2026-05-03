from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from gh_analyzer.cli import from_args
from gh_analyzer.github_api import (
    RateLimitTracker,
    MAX_CONCURRENT_REQUESTS,
    build_headers,
    fetch_repo,
    fetch_commits,
    fetch_issues,
    fetch_prs,
    fetch_releases,
)
from gh_analyzer.models import Repo, Commit, Issue, PullRequest, Release, CLIArgs
from gh_analyzer.exceptions import GitHubAPIError
from gh_analyzer import reporter

# ─────────────────────────────────────────
# Constants
# ─────────────────────────────────────────

# Minimum commit fetch regardless of --limit.
# Bus factor computed on <200 commits is statistically meaningless.
ANALYTICS_COMMIT_FLOOR = 500
ANALYTICS_ISSUE_FLOOR  = 200
ANALYTICS_PR_FLOOR     = 200

console = Console()


# ─────────────────────────────────────────
# Logging
# ─────────────────────────────────────────

def configure_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    return logging.getLogger("gh_analyzer")


def log_rate_limit(logger: logging.Logger, rate: RateLimitTracker) -> None:
    s = rate.snapshot()
    if s["secondary_limited"]:
        logger.warning("Secondary rate limit detected (abuse protection).")
    if s["retry_after"]:
        logger.warning("Retry-After=%ss requested by GitHub.", s["retry_after"])
    rem = s["remaining"]
    if rem is None:
        return
    if rem == 0:
        logger.error("Rate limit exhausted. Reset epoch=%s", s["reset_time"])
    elif rem < 10:
        logger.warning("Rate limit low: %s remaining. Reset epoch=%s", rem, s["reset_time"])
    else:
        logger.debug("Rate limit: %s remaining. Reset epoch=%s", rem, s["reset_time"])


# ─────────────────────────────────────────
# Fetch layer
# ─────────────────────────────────────────

async def _fetch_all(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    rate: RateLimitTracker,
    cli_args: CLIArgs,
    logger: logging.Logger,
) -> tuple[Repo, list[Commit], list[Issue], list[PullRequest], list[Release]]:
    """
    Fetch all repository data concurrently with a live progress spinner.

    Failure policy:
      - repo:    hard failure — raises immediately, nothing else makes sense
      - others:  soft failure — logs warning, returns empty list, report continues
    
    Window policy:
      We fetch double the user's --since window so Stage 2 trend analysis
      has both the current and prior periods available without extra API calls.
    """
    now = datetime.now(timezone.utc)

    # Double window: if user asked for 30 days, fetch 60 so we have
    # current (days 0-30) and prior (days 30-60) for trend computation.
    analytics_since = cli_args.since - (now - cli_args.since)

    # Never fetch fewer than the analytics floors regardless of --limit.
    commit_limit  = max(cli_args.limit, ANALYTICS_COMMIT_FLOOR)
    issue_limit   = max(cli_args.limit, ANALYTICS_ISSUE_FLOOR)
    pr_limit      = max(cli_args.limit, ANALYTICS_PR_FLOOR)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,   # erases spinner lines when done, leaving clean output
    )

    with progress:
        task_ids = {
            "repo":     progress.add_task("Fetching repository...",    total=None),
            "commits":  progress.add_task("Fetching commits...",       total=None),
            "issues":   progress.add_task("Fetching issues...",        total=None),
            "prs":      progress.add_task("Fetching pull requests...", total=None),
            "releases": progress.add_task("Fetching releases...",      total=None),
        }

        async def tracked(coro, key: str):
            """Run coro, then mark its spinner row as complete."""
            result = await coro
            progress.update(
                task_ids[key],
                description=f"[green]✓[/green] {key}",
                completed=True,
            )
            return result

        results = await asyncio.gather(
            tracked(
                fetch_repo(session, sem, cli_args.username, cli_args.repo_name, rate),
                "repo",
            ),
            tracked(
                fetch_commits(session, sem, cli_args.username, cli_args.repo_name, rate,
                              since=analytics_since, limit=commit_limit),
                "commits",
            ),
            tracked(
                fetch_issues(session, sem, cli_args.username, cli_args.repo_name, rate,
                             since=analytics_since, limit=issue_limit, state="all"),
                "issues",
            ),
            tracked(
                fetch_prs(session, sem, cli_args.username, cli_args.repo_name, rate,
                          since=analytics_since, limit=pr_limit, state="all"),
                "prs",
            ),
            tracked(
                fetch_releases(session, sem, cli_args.username, cli_args.repo_name, rate,
                               since=analytics_since, limit=cli_args.limit),
                "releases",
            ),
            return_exceptions=True,
        )

    repo, commits, issues, prs, releases = results

    # Hard fail on repo
    if isinstance(repo, BaseException):
        raise repo

    # Soft fail on everything else
    for name, result in [
        ("commits",  commits),
        ("issues",   issues),
        ("prs",      prs),
        ("releases", releases),
    ]:
        if isinstance(result, BaseException):
            logger.warning(
                "Could not fetch %s: %s — report will be partial.", name, result
            )

    return (
        repo,
        [] if isinstance(commits,  BaseException) else commits,
        [] if isinstance(issues,   BaseException) else issues,
        [] if isinstance(prs,      BaseException) else prs,
        [] if isinstance(releases, BaseException) else releases,
    )


# ─────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────

async def run(argv=None) -> int:
    cli_args = from_args(argv)
    logger   = configure_logging(cli_args.verbose)

    if not cli_args.token:
        logger.warning("No token — running unauthenticated (60 requests/hour).")

    sem     = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    rate    = RateLimitTracker()
    timeout = aiohttp.ClientTimeout(total=30, connect=5, sock_read=20)

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=build_headers(cli_args.token),
    ) as session:
        try:
            repo, commits, issues, prs, releases = await _fetch_all(
                session, sem, rate, cli_args, logger
            )
        except GitHubAPIError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            return 1

    log_rate_limit(logger, rate)

    # Render report
    reporter.print_repo_header(repo)
    reporter.print_commit_summary(commits, cli_args.since)
    reporter.print_issue_summary(issues)
    reporter.print_pr_summary(prs)
    reporter.print_release_summary(releases)

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
