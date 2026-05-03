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
from gh_analyzer.analytics import MetricsComputer, HealthScorer
from gh_analyzer.risk import RiskDetector
from gh_analyzer.reporter import RepoReport, Reporter

# ─────────────────────────────────────────
# Constants
# ─────────────────────────────────────────

ANALYTICS_COMMIT_FLOOR    = 500
ANALYTICS_ISSUE_FLOOR     = 200
ANALYTICS_PR_FLOOR        = 200
RATE_LIMIT_WARN_THRESHOLD = 10

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
    elif rem < RATE_LIMIT_WARN_THRESHOLD:
        logger.warning(
            "Rate limit low: %s remaining. Reset epoch=%s", rem, s["reset_time"]
        )
    else:
        logger.debug("Rate limit: %s remaining. Reset epoch=%s", rem, s["reset_time"])


def warn_if_rate_limit_low(rate: RateLimitTracker) -> bool:
    s         = rate.snapshot()
    remaining = s["remaining"]
    if remaining is None:
        return False
    if remaining == 0:
        console.print(
            "\n[bold red]✗ Rate limit exhausted.[/bold red] "
            "All requests are blocked until the quota resets. "
            "Results shown may be incomplete or empty. "
            "[dim]Set GITHUB_TOKEN for 5,000 requests/hour.[/dim]"
        )
        return True
    if remaining < RATE_LIMIT_WARN_THRESHOLD:
        console.print(
            f"\n[bold yellow]⚠ Rate limit low:[/bold yellow] "
            f"{remaining} requests remaining. "
            "Some data may be missing from this report. "
            "[dim]Set GITHUB_TOKEN for 5,000 requests/hour.[/dim]"
        )
        return True
    return False


# ─────────────────────────────────────────
# Fetch layer
# ─────────────────────────────────────────

async def _fetch_all(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    rate: RateLimitTracker,
    cli_args: CLIArgs,
    analytics_since: datetime,
    logger: logging.Logger,
) -> tuple[Repo, list[Commit], list[Issue], list[PullRequest], list[Release]]:
    commit_limit = max(cli_args.limit, ANALYTICS_COMMIT_FLOOR)
    issue_limit  = max(cli_args.limit, ANALYTICS_ISSUE_FLOOR)
    pr_limit     = max(cli_args.limit, ANALYTICS_PR_FLOOR)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
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
                fetch_commits(
                    session, sem, cli_args.username, cli_args.repo_name, rate,
                    since=analytics_since, limit=commit_limit,
                ),
                "commits",
            ),
            tracked(
                fetch_issues(
                    session, sem, cli_args.username, cli_args.repo_name, rate,
                    since=analytics_since, limit=issue_limit, state="all",
                ),
                "issues",
            ),
            tracked(
                fetch_prs(
                    session, sem, cli_args.username, cli_args.repo_name, rate,
                    since=analytics_since, limit=pr_limit, state="all",
                ),
                "prs",
            ),
            tracked(
                fetch_releases(
                    session, sem, cli_args.username, cli_args.repo_name, rate,
                    since=analytics_since, limit=cli_args.limit,
                ),
                "releases",
            ),
            return_exceptions=True,
        )

    repo, commits, issues, prs, releases = results

    if isinstance(repo, BaseException):
        raise repo

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

    now             = datetime.now(timezone.utc)
    analytics_since = cli_args.since - (now - cli_args.since)

    sem     = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    rate    = RateLimitTracker()
    timeout = aiohttp.ClientTimeout(total=30, connect=5, sock_read=20)

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=build_headers(cli_args.token),
    ) as session:
        try:
            repo, commits, issues, prs, releases = await _fetch_all(
                session, sem, rate, cli_args, analytics_since, logger
            )
        except GitHubAPIError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            return 1

    log_rate_limit(logger, rate)
    warn_if_rate_limit_low(rate)

    # ── Compute analytics ──
    metrics = MetricsComputer().compute(
        commits        = commits,
        issues         = issues,
        prs            = prs,
        releases       = releases,
        display_since  = cli_args.since,
        analysis_since = analytics_since,
    )
    score = HealthScorer().score(metrics)
    flags = RiskDetector().evaluate(metrics)

    # ── Build report ──
    report         = RepoReport(
        repo     = repo,
        commits  = commits,
        issues   = issues,
        prs      = prs,
        releases = releases,
        since    = cli_args.since,
        metrics  = metrics,
        score    = score,
        flags    = flags,
    )

    # ── Render ──
    Reporter(console).render(report, fmt=cli_args.output_format)

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()