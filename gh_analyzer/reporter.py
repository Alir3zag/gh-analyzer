from __future__ import annotations

from datetime import datetime, timezone
from collections import Counter

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from gh_analyzer.models import Repo, Commit, Issue, PullRequest, Release

console = Console()


# ─────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────

def _make_table(*headers: str) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    for h in headers:
        t.add_column(h)
    return t


def _hours_to_human(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)}m"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


# ─────────────────────────────────────────
# Stage 1: raw report printers
# ─────────────────────────────────────────

def print_repo_header(repo: Repo) -> None:
    desc     = repo.description or "No description."
    language = repo.language or "Unknown"
    content  = (
        f"[bold white]{repo.full_name}[/bold white]\n"
        f"[dim]{desc}[/dim]\n\n"
        f"[yellow]★ {repo.stars:,}[/yellow]  "
        f"[blue]⑂ {repo.forks:,}[/blue]  "
        f"[red]● {repo.open_issues:,} open issues[/red]  "
        f"[green]{language}[/green]"
    )
    console.print(Panel(content, expand=False, border_style="dim"))
    console.print()


def print_commit_summary(commits: list[Commit], since: datetime) -> None:
    if not commits:
        console.print("[dim]No commits found in analysis window.[/dim]\n")
        return

    author_counts: Counter[str] = Counter(
        c.author_login or c.author_name or "unknown" for c in commits
    )
    top_author, top_count = author_counts.most_common(1)[0]
    unique_authors = len(author_counts)

    t = _make_table("Metric", "Value")
    t.add_row("Total commits", str(len(commits)))
    t.add_row("Unique authors", str(unique_authors))
    t.add_row("Most active author", f"{top_author} ({top_count} commits)")
    t.add_row(
        "Date range",
        f"{commits[-1].date:%Y-%m-%d} → {commits[0].date:%Y-%m-%d}",
    )

    console.print("[bold]Commits[/bold]")
    console.print(t)

    if unique_authors > 1:
        top_t = _make_table("Author", "Commits", "Share")
        for author, count in author_counts.most_common(5):
            share = count / len(commits) * 100
            bar   = "█" * int(share / 5)
            top_t.add_row(author, str(count), f"{bar} {share:.1f}%")
        console.print("[dim]Top contributors[/dim]")
        console.print(top_t)

    console.print()


def print_issue_summary(issues: list[Issue]) -> None:
    if not issues:
        console.print("[dim]No issues found in analysis window.[/dim]\n")
        return

    open_issues   = [i for i in issues if i.is_open]
    closed_issues = [i for i in issues if not i.is_open]
    resolution_times = [
        i.resolution_time for i in closed_issues if i.resolution_time is not None
    ]
    avg_resolution = (
        _hours_to_human(sum(resolution_times) / len(resolution_times))
        if resolution_times else "N/A"
    )
    resolution_rate = len(closed_issues) / len(issues) * 100

    t = _make_table("Metric", "Value")
    t.add_row("Total issues", str(len(issues)))
    t.add_row("Open",   f"[red]{len(open_issues)}[/red]")
    t.add_row("Closed", f"[green]{len(closed_issues)}[/green]")
    t.add_row("Resolution rate", f"{resolution_rate:.1f}%")
    t.add_row("Avg resolution time", avg_resolution)

    console.print("[bold]Issues[/bold]")
    console.print(t)
    console.print()


def print_pr_summary(prs: list[PullRequest]) -> None:
    if not prs:
        console.print("[dim]No pull requests found in analysis window.[/dim]\n")
        return

    merged   = [pr for pr in prs if pr.is_merged]
    open_prs = [pr for pr in prs if pr.state == "open"]
    merge_times = [pr.time_to_merge for pr in merged if pr.time_to_merge is not None]
    avg_merge = (
        _hours_to_human(sum(merge_times) / len(merge_times))
        if merge_times else "N/A"
    )
    merge_rate = len(merged) / len(prs) * 100

    t = _make_table("Metric", "Value")
    t.add_row("Total PRs", str(len(prs)))
    t.add_row("Merged", f"[green]{len(merged)}[/green]")
    t.add_row("Open",   f"[yellow]{len(open_prs)}[/yellow]")
    t.add_row("Merge rate", f"{merge_rate:.1f}%")
    t.add_row("Avg time to merge", avg_merge)

    console.print("[bold]Pull Requests[/bold]")
    console.print(t)
    console.print()


def print_release_summary(releases: list[Release]) -> None:
    if not releases:
        console.print("[dim]No releases found.[/dim]\n")
        return

    now        = datetime.now(timezone.utc)
    latest     = releases[0]
    days_since = (now - latest.published_at).days

    t = _make_table("Metric", "Value")
    t.add_row("Total releases", str(len(releases)))
    t.add_row("Latest tag", latest.tag)
    t.add_row("Latest release", f"{latest.published_at:%Y-%m-%d}")
    t.add_row("Days since release", str(days_since))
    if latest.prerelease:
        t.add_row("Status", "[yellow]Pre-release[/yellow]")

    console.print("[bold]Releases[/bold]")
    console.print(t)
    console.print()


# ─────────────────────────────────────────
# Stage 2: health score output
# ─────────────────────────────────────────

def print_health_score(score) -> None:
    from gh_analyzer.analytics import HealthScore

    grade_color = {"A": "green", "B": "yellow", "C": "orange1", "D": "red"}
    color       = grade_color.get(score.grade, "white")

    content = (
        f"[bold {color}]{score.value}/100[/bold {color}]  "
        f"[bold {color}]Grade {score.grade}[/bold {color}]"
    )
    console.print(Panel(
        content,
        title="[bold]Repository Health Score[/bold]",
        expand=False,
        border_style=color,
    ))
    console.print()

    # Component breakdown
    t = _make_table("Signal", "Score", "Weight", "Contribution")
    for c in score.components:
        if c.raw_score is None:
            t.add_row(
                c.name,
                "[dim]no data[/dim]",
                f"{c.weight:.0%}",
                "[dim]—[/dim]",
            )
        else:
            bar_len = int(c.raw_score / 10)
            bar     = "█" * bar_len + "░" * (10 - bar_len)
            t.add_row(
                c.name,
                f"{bar} {c.raw_score:.0f}",
                f"{c.weight:.0%}",
                f"{c.contribution:.1f}",
            )

    console.print("[bold]Score Breakdown[/bold]")
    console.print(t)

    # Bus factor detail
    m  = score.metrics
    bf = m.bus_factor
    if bf is not None:
        console.print(
            f"\n[dim]Bus factor:[/dim]  "
            f"HHI={bf.hhi:.3f}  "
            f"top contributor: {bf.top_author} ({bf.top_author_pct:.1f}%)  "
            f"total contributors: {bf.contributor_count}"
        )

    # Commit trend
    if m.commit_trend_pct is not None:
        direction   = "▲" if m.commit_trend_pct >= 0 else "▼"
        trend_color = "green" if m.commit_trend_pct >= 0 else "red"
        window_days = int((m.display_since - m.analysis_since).days)
        console.print(
            f"[dim]Commit trend:[/dim]  "
            f"[{trend_color}]{direction} {abs(m.commit_trend_pct):.1f}%"
            f"[/{trend_color}] vs prior {window_days}-day window"
        )
    else:
        console.print(
            "[dim]Commit trend: insufficient data "
            "(fewer than 10 commits in one or both windows)[/dim]"
        )

    console.print()