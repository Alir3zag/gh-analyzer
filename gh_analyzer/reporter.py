from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import Counter

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from gh_analyzer.models import Repo, Commit, Issue, PullRequest, Release

console = Console()


@dataclass
class RepoReport:
    """
    Single source of truth for everything the reporter renders.
    Analytics are computed once in run() and passed in here.
    The reporter is a pure presentation layer — no computation happens here.
    """
    repo:       Repo
    commits:    list[Commit]
    issues:     list[Issue]
    prs:        list[PullRequest]
    releases:   list[Release]
    since:      datetime
    metrics:    object       = None   # HealthMetrics
    score:      object       = None   # HealthScore
    flags:      list         = None   # list[RiskFlag]
    ai_summary: str | None   = None

    def __post_init__(self):
        if self.flags is None:
            self.flags = []

    def to_dict(self) -> dict:
        """
        Serialize to summary JSON.
        ai_summary is always included — null when not requested.
        """
        result: dict = {
            "repo": {
                "name":        self.repo.name,
                "full_name":   self.repo.full_name,
                "description": self.repo.description,
                "stars":       self.repo.stars,
                "forks":       self.repo.forks,
                "open_issues": self.repo.open_issues,
                "language":    self.repo.language,
                "url":         self.repo.url,
            },
            "summary": {
                "commits":        len(self.commits),
                "unique_authors": len({
                    c.author_login or c.author_name or "unknown"
                    for c in self.commits
                }),
                "issues_total":   len(self.issues),
                "issues_open":    sum(1 for i in self.issues if i.is_open),
                "issues_closed":  sum(1 for i in self.issues if not i.is_open),
                "prs_total":      len(self.prs),
                "prs_merged":     sum(1 for p in self.prs if p.is_merged),
                "releases_total": len(self.releases),
            },
        }

        if self.score is not None:
            result["score"] = {
                "value": self.score.value,
                "grade": self.score.grade,
                "components": [
                    {
                        "signal":           c.name,
                        "score":            c.raw_score,
                        "weight":           c.weight,
                        "effective_weight": c.effective_weight,
                        "contribution":     c.contribution,
                    }
                    for c in self.score.components
                ],
            }

        if self.metrics is not None and self.metrics.bus_factor is not None:
            bf = self.metrics.bus_factor
            result["bus_factor"] = {
                "hhi":               bf.hhi,
                "health_score":      bf.health_score,
                "top_author":        bf.top_author,
                "top_author_pct":    bf.top_author_pct,
                "contributor_count": bf.contributor_count,
            }

        if self.metrics is not None:
            result["trend"] = {
                "commit_trend_pct": self.metrics.commit_trend_pct,
                "current_window":   self.metrics.current_window.commit_count,
                "prior_window":     self.metrics.prior_window.commit_count,
                "low_confidence":   self.metrics.current_window.low_confidence,
            }

        if self.flags:
            result["flags"] = [
                {
                    "level":   f.level,
                    "code":    f.code,
                    "message": f.message,
                }
                for f in self.flags
            ]

        # Always include ai_summary key — null when not requested
        result["ai_summary"] = self.ai_summary

        return result


class Reporter:
    """
    Pure presentation layer. Renders a RepoReport in the requested format.
    No analytics or business logic lives here.
    """

    def __init__(self, con: Console | None = None):
        self.console = con or console

    def render(self, report: RepoReport, fmt: str) -> None:
        if fmt == "json":
            print(json.dumps(report.to_dict(), indent=2, default=str))
        elif fmt == "table":
            # B5 FIX: --format table renders a compact summary table,
            # distinct from the full rich text output.
            self._render_table(report)
        else:
            self._render_rich(report)

    # ── rich terminal renderer ────────────────────────────────

    def _render_rich(self, report: RepoReport) -> None:
        self._print_repo_header(report.repo)
        self._print_commit_summary(report.commits, report.since)
        self._print_issue_summary(report.issues)
        self._print_pr_summary(report.prs)
        self._print_release_summary(report.releases)

        if report.score is not None:
            self._print_health_score(report.score, report.metrics)

        if report.flags:
            self._print_risk_flags(report.flags)

        if report.ai_summary:
            self._print_ai_summary(report.ai_summary)

    # ── table renderer ────────────────────────────────────────

    def _render_table(self, report: RepoReport) -> None:
        """
        Compact summary table — one row of key metrics per signal.
        Useful for comparing multiple repos side-by-side in scripts.
        """
        t = Table(
            title=f"[bold]{report.repo.full_name}[/bold]",
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold cyan",
        )
        t.add_column("Signal")
        t.add_column("Value", justify="right")
        t.add_column("Score", justify="right")

        # Repo metadata row
        t.add_row(
            "Repository",
            f"★{report.repo.stars:,}  ⑂{report.repo.forks:,}  ⚑{report.repo.open_issues:,} open",
            "",
        )

        # Commit momentum
        commit_count = len(report.commits)
        trend_str = ""
        if report.metrics and report.metrics.commit_trend_pct is not None:
            pct = report.metrics.commit_trend_pct
            arrow = "↑" if pct >= 0 else "↓"
            trend_str = f" ({arrow}{abs(pct):.0f}%)"
        commit_score = self._component_score_str(report.score, "commit_momentum")
        t.add_row("Commit momentum", f"{commit_count} commits{trend_str}", commit_score)

        # Bus factor
        if report.metrics and report.metrics.bus_factor:
            bf = report.metrics.bus_factor
            bf_val = f"HHI={bf.hhi:.3f}  top={bf.top_author_pct:.0f}%"
        else:
            bf_val = "no data"
        t.add_row("Bus factor", bf_val, self._component_score_str(report.score, "bus_factor"))

        # Issue health
        if report.metrics and report.metrics.issues.available:
            iss = report.metrics.issues
            iss_val = f"{iss.resolution_rate * 100:.0f}% resolved ({iss.closed_count}/{iss.total})"
        else:
            iss_val = "no data"
        t.add_row("Issue health", iss_val, self._component_score_str(report.score, "issue_health"))

        # PR latency
        if report.metrics and report.metrics.prs.available and report.metrics.prs.avg_merge_hours is not None:
            pr_val = self._hours_to_human(report.metrics.prs.avg_merge_hours) + " avg merge"
        else:
            pr_val = "no data"
        t.add_row("PR latency", pr_val, self._component_score_str(report.score, "pr_latency"))

        # Release cadence
        if report.metrics and report.metrics.releases.available:
            rel_val = f"{report.metrics.releases.days_since_latest}d since last release"
        else:
            rel_val = "no releases"
        t.add_row("Release cadence", rel_val, self._component_score_str(report.score, "release_cadence"))

        self.console.print(t)

        # Health score summary line
        if report.score is not None:
            grade_color = {"A": "green", "B": "yellow", "C": "orange1", "D": "red"}
            color = grade_color.get(report.score.grade, "white")
            self.console.print(
                f"\nHealth Score: [{color}]{report.score.value}/100  Grade {report.score.grade}[/{color}]"
            )

        # Risk flags (compact, one line each)
        if report.flags:
            self.console.print()
            self._print_risk_flags(report.flags)

        if report.ai_summary:
            self._print_ai_summary(report.ai_summary)

    def _component_score_str(self, score, signal_name: str) -> str:
        """Return the raw score string for a named component, or 'N/A'."""
        if score is None:
            return "N/A"
        for c in score.components:
            if c.name == signal_name:
                if c.raw_score is None:
                    return "N/A"
                return f"{c.raw_score:.0f}"
        return "N/A"

    # ── section printers (shared between text and table) ─────

    def _make_table(self, *headers: str) -> Table:
        t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
        for h in headers:
            t.add_column(h)
        return t

    def _hours_to_human(self, hours: float) -> str:
        if hours < 1:
            return f"{int(hours * 60)}m"
        if hours < 48:
            return f"{hours:.1f}h"
        return f"{hours / 24:.1f}d"

    def _print_repo_header(self, repo: Repo) -> None:
        desc     = repo.description or "No description."
        language = repo.language or "Unknown"
        content  = (
            f"[bold white]{repo.full_name}[/bold white]\n"
            f"[dim]{desc}[/dim]\n\n"
            f"[yellow]★ {repo.stars:,}[/yellow]  "
            f"[blue]⑂ {repo.forks:,}[/blue]  "
            f"[red]⚑ {repo.open_issues:,} open issues[/red]  "
            f"[green]{language}[/green]"
        )
        self.console.print(Panel(content, expand=False, border_style="dim"))
        self.console.print()

    def _print_commit_summary(self, commits: list[Commit], since: datetime) -> None:
        if not commits:
            self.console.print("[dim]No commits found in analysis window.[/dim]\n")
            return

        author_counts: Counter[str] = Counter(
            c.author_login or c.author_name or "unknown" for c in commits
        )
        top_author, top_count = author_counts.most_common(1)[0]
        unique_authors = len(author_counts)

        t = self._make_table("Metric", "Value")
        t.add_row("Total commits", str(len(commits)))
        t.add_row("Unique authors", str(unique_authors))
        t.add_row("Most active author", f"{top_author} ({top_count} commits)")
        t.add_row(
            "Date range",
            f"{commits[-1].date:%Y-%m-%d} → {commits[0].date:%Y-%m-%d}",
        )
        self.console.print("[bold]Commits[/bold]")
        self.console.print(t)

        if unique_authors > 1:
            top_t = self._make_table("Author", "Commits", "Share")
            for author, count in author_counts.most_common(5):
                share = count / len(commits) * 100
                bar   = "█" * int(share / 5)
                top_t.add_row(author, str(count), f"{bar} {share:.1f}%")
            self.console.print("[dim]Top contributors[/dim]")
            self.console.print(top_t)

        self.console.print()

    def _print_issue_summary(self, issues: list[Issue]) -> None:
        if not issues:
            self.console.print("[dim]No issues found in analysis window.[/dim]\n")
            return

        open_issues   = [i for i in issues if i.is_open]
        closed_issues = [i for i in issues if not i.is_open]
        times = [
            i.resolution_time for i in closed_issues
            if i.resolution_time is not None
        ]
        avg_resolution = (
            self._hours_to_human(sum(times) / len(times)) if times else "N/A"
        )
        resolution_rate = len(closed_issues) / len(issues) * 100

        t = self._make_table("Metric", "Value")
        t.add_row("Total issues", str(len(issues)))
        t.add_row("Open",   f"[red]{len(open_issues)}[/red]")
        t.add_row("Closed", f"[green]{len(closed_issues)}[/green]")
        t.add_row("Resolution rate", f"{resolution_rate:.1f}%")
        t.add_row("Avg resolution time", avg_resolution)
        self.console.print("[bold]Issues[/bold]")
        self.console.print(t)
        self.console.print()

    def _print_pr_summary(self, prs: list[PullRequest]) -> None:
        if not prs:
            self.console.print("[dim]No pull requests found in analysis window.[/dim]\n")
            return

        merged   = [pr for pr in prs if pr.is_merged]
        open_prs = [pr for pr in prs if pr.state == "open"]
        times    = [pr.time_to_merge for pr in merged if pr.time_to_merge is not None]
        avg_merge = (
            self._hours_to_human(sum(times) / len(times)) if times else "N/A"
        )
        merge_rate = len(merged) / len(prs) * 100

        t = self._make_table("Metric", "Value")
        t.add_row("Total PRs", str(len(prs)))
        t.add_row("Merged", f"[green]{len(merged)}[/green]")
        t.add_row("Open",   f"[yellow]{len(open_prs)}[/yellow]")
        t.add_row("Merge rate", f"{merge_rate:.1f}%")
        t.add_row("Avg time to merge", avg_merge)
        self.console.print("[bold]Pull Requests[/bold]")
        self.console.print(t)
        self.console.print()

    def _print_release_summary(self, releases: list[Release]) -> None:
        if not releases:
            self.console.print("[dim]No releases found.[/dim]\n")
            return

        now        = datetime.now(timezone.utc)
        latest     = releases[0]
        days_since = (now - latest.published_at).days

        t = self._make_table("Metric", "Value")
        t.add_row("Total releases", str(len(releases)))
        t.add_row("Latest tag", latest.tag)
        t.add_row("Latest release", f"{latest.published_at:%Y-%m-%d}")
        t.add_row("Days since release", str(days_since))
        if latest.prerelease:
            t.add_row("Status", "[yellow]Pre-release[/yellow]")
        self.console.print("[bold]Releases[/bold]")
        self.console.print(t)
        self.console.print()

    def _print_health_score(self, score, metrics) -> None:
        grade_color = {"A": "green", "B": "yellow", "C": "orange1", "D": "red"}
        color       = grade_color.get(score.grade, "white")

        content = (
            f"[bold {color}]{score.value}/100[/bold {color}]  "
            f"[bold {color}]Grade {score.grade}[/bold {color}]"
        )
        self.console.print(Panel(
            content,
            title="[bold]Repository Health Score[/bold]",
            expand=False,
            border_style=color,
        ))
        self.console.print()

        t = self._make_table("Signal", "Score", "Weight", "Contribution")
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
        self.console.print("[bold]Score Breakdown[/bold]")
        self.console.print(t)

        if metrics is not None and metrics.bus_factor is not None:
            bf = metrics.bus_factor
            self.console.print(
                f"\n[dim]Bus factor:[/dim]  "
                f"HHI={bf.hhi:.3f}  "
                f"top contributor: {bf.top_author} ({bf.top_author_pct:.1f}%)  "
                f"total contributors: {bf.contributor_count}"
            )

        if metrics is not None and metrics.commit_trend_pct is not None:
            direction   = "↑" if metrics.commit_trend_pct >= 0 else "↓"
            trend_color = "green" if metrics.commit_trend_pct >= 0 else "red"
            window_days = int((metrics.display_since - metrics.analysis_since).days)
            self.console.print(
                f"[dim]Commit trend:[/dim]  "
                f"[{trend_color}]{direction} {abs(metrics.commit_trend_pct):.1f}%"
                f"[/{trend_color}] vs prior {window_days}-day window"
            )
        else:
            self.console.print(
                "[dim]Commit trend: insufficient data "
                "(fewer than 10 commits in one or both windows)[/dim]"
            )

        self.console.print()

    def _print_risk_flags(self, flags: list) -> None:
        icons  = {"ERROR": "✗", "WARN": "⚠", "OK": "✔"}
        colors = {"ERROR": "red", "WARN": "yellow", "OK": "green"}

        self.console.print("[bold]Risk Assessment[/bold]")
        for flag in flags:
            icon  = icons.get(flag.level, "•")
            color = colors.get(flag.level, "white")
            self.console.print(
                f"  [{color}]{icon}[/{color}] {flag.message}"
            )
        self.console.print()

    def _print_ai_summary(self, summary: str) -> None:
        self.console.print(Panel(
            summary,
            title="[bold cyan]AI Analysis[/bold cyan]",
            border_style="cyan",
            expand=False,
            padding=(1, 2),
        ))
        self.console.print()
