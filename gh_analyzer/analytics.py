from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from gh_analyzer.models import Commit, Issue, PullRequest, Release


# ─────────────────────────────────────────
# Output dataclasses
# ─────────────────────────────────────────

@dataclass
class WindowMetrics:
    """Commit activity for a single time window."""
    commit_count: int
    unique_authors: int
    low_confidence: bool        # True when commit_count < MIN_WINDOW_COMMITS


@dataclass
class BusFactorMetrics:
    """
    Contributor concentration measured via HHI.

    HHI = sum of squared commit-share per contributor.
    Range: 0.0 (perfectly distributed) → 1.0 (one person did everything).
    health_score = (1 - hhi) * 100  so higher = healthier.

    top_author_pct is stored separately as a human-readable supporting detail.
    """
    hhi: float                  # raw Herfindahl-Hirschman Index
    health_score: float         # (1 - hhi) * 100
    top_author: str
    top_author_pct: float       # % of commits from single top contributor
    contributor_count: int


@dataclass
class IssueMetrics:
    total: int
    open_count: int
    closed_count: int
    resolution_rate: float      # closed / total
    avg_resolution_hours: float | None
    available: bool             # False when total == 0


@dataclass
class PRMetrics:
    total: int
    merged_count: int
    open_count: int
    merge_rate: float
    avg_merge_hours: float | None
    available: bool             # False when total == 0


@dataclass
class ReleaseMetrics:
    total: int
    days_since_latest: int | None
    available: bool             # False when total == 0


@dataclass
class HealthMetrics:
    """All derived metrics in one place. Passed to HealthScorer."""
    current_window: WindowMetrics
    prior_window: WindowMetrics
    commit_trend_pct: float | None  # None when prior window is low-confidence
    bus_factor: BusFactorMetrics | None  # None when no commits at all
    issues: IssueMetrics
    prs: PRMetrics
    releases: ReleaseMetrics
    analysis_since: datetime        # actual start of the double window
    display_since: datetime         # user's --since boundary (midpoint)


@dataclass
class ComponentScore:
    """Score for one weighted component. None means signal was unavailable."""
    name: str
    raw_score: float | None     # 0-100 before weighting, None if no data
    weight: float               # configured weight
    effective_weight: float     # renormalized weight after missing signals removed
    contribution: float         # raw_score * effective_weight


@dataclass
class HealthScore:
    """Final output of the scoring engine."""
    value: int                  # 0-100 composite
    grade: str                  # A / B / C / D
    components: list[ComponentScore]
    metrics: HealthMetrics


# ─────────────────────────────────────────
# Constants
# ─────────────────────────────────────────

# Windows with fewer commits than this are flagged low-confidence.
# Trend percentages on tiny samples are statistically meaningless.
MIN_WINDOW_COMMITS = 10

# Configured weights — must reflect domain knowledge, not arbitrary choice.
# Commit momentum: strongest signal that a repo is actively developed.
# Bus factor: strongest signal of sustainability risk.
# Issue health: signal of maintainer responsiveness to users.
# PR latency: signal of review culture and team throughput.
# Release cadence: weakest — many healthy repos release infrequently.
WEIGHTS: dict[str, float] = {
    "commit_momentum": 0.30,
    "bus_factor":      0.25,
    "issue_health":    0.20,
    "pr_latency":      0.15,
    "release_cadence": 0.10,
}


# ─────────────────────────────────────────
# Metrics computer
# ─────────────────────────────────────────

class MetricsComputer:
    """
    Derives structured metrics from raw API data.
    All inputs are the full double-window datasets fetched in main.py.
    """

    def compute(
        self,
        commits:  list[Commit],
        issues:   list[Issue],
        prs:      list[PullRequest],
        releases: list[Release],
        display_since: datetime,    # user's --since boundary (midpoint of double window)
        analysis_since: datetime,   # start of double window
    ) -> HealthMetrics:

        current_window, prior_window = self._split_commit_windows(commits, display_since)
        trend = self._compute_trend(current_window, prior_window)

        return HealthMetrics(
            current_window  = current_window,
            prior_window    = prior_window,
            commit_trend_pct= trend,
            bus_factor      = self._compute_bus_factor(commits),
            issues          = self._compute_issue_metrics(issues),
            prs             = self._compute_pr_metrics(prs),
            releases        = self._compute_release_metrics(releases),
            analysis_since  = analysis_since,
            display_since   = display_since,
        )

    # ── private helpers ──────────────────

    def _split_commit_windows(
        self,
        commits: list[Commit],
        midpoint: datetime,
    ) -> tuple[WindowMetrics, WindowMetrics]:
        """
        Split commits into current (>= midpoint) and prior (< midpoint) windows
        using datetime comparison — not index splitting.

        Commits are newest-first from the API so current will be at the front.
        """
        current = [c for c in commits if c.date >= midpoint]
        prior   = [c for c in commits if c.date <  midpoint]

        def _window(cs: list[Commit]) -> WindowMetrics:
            authors = {c.author_login or c.author_name or "unknown" for c in cs}
            return WindowMetrics(
                commit_count   = len(cs),
                unique_authors = len(authors),
                low_confidence = len(cs) < MIN_WINDOW_COMMITS,
            )

        return _window(current), _window(prior)

    def _compute_trend(
        self,
        current: WindowMetrics,
        prior: WindowMetrics,
    ) -> float | None:
        """
        Percentage change in commit count from prior to current window.
        Returns None when either window is low-confidence to avoid
        surfacing misleading trend numbers.
        """
        if current.low_confidence or prior.low_confidence:
            return None
        if prior.commit_count == 0:
            return None
        return (
            (current.commit_count - prior.commit_count) / prior.commit_count
        ) * 100.0

    def _compute_bus_factor(
        self,
        commits: list[Commit],
    ) -> BusFactorMetrics | None:
        """
        Herfindahl-Hirschman Index for contributor concentration.

        HHI = Σ (share_i)²  where share_i = commits_by_author_i / total_commits

        HHI → 1.0 : one contributor dominates
        HHI → 0.0 : perfectly distributed contributions

        health_score = (1 - HHI) * 100
        so a perfectly distributed repo scores 100,
        and a single-author repo scores 0.
        """
        if not commits:
            return None

        total = len(commits)
        author_counts: Counter[str] = Counter(
            c.author_login or c.author_name or "unknown" for c in commits
        )

        shares = [count / total for count in author_counts.values()]
        hhi = sum(s ** 2 for s in shares)

        top_author, top_count = author_counts.most_common(1)[0]

        return BusFactorMetrics(
            hhi              = round(hhi, 4),
            health_score     = round((1 - hhi) * 100, 2),
            top_author       = top_author,
            top_author_pct   = round(top_count / total * 100, 1),
            contributor_count= len(author_counts),
        )

    def _compute_issue_metrics(self, issues: list[Issue]) -> IssueMetrics:
        if not issues:
            return IssueMetrics(
                total=0, open_count=0, closed_count=0,
                resolution_rate=0.0, avg_resolution_hours=None,
                available=False,
            )

        closed = [i for i in issues if not i.is_open]
        times  = [i.resolution_time for i in closed if i.resolution_time is not None]

        return IssueMetrics(
            total                = len(issues),
            open_count           = sum(1 for i in issues if i.is_open),
            closed_count         = len(closed),
            resolution_rate      = len(closed) / len(issues),
            avg_resolution_hours = sum(times) / len(times) if times else None,
            available            = True,
        )

    def _compute_pr_metrics(self, prs: list[PullRequest]) -> PRMetrics:
        if not prs:
            return PRMetrics(
                total=0, merged_count=0, open_count=0,
                merge_rate=0.0, avg_merge_hours=None,
                available=False,
            )

        merged = [pr for pr in prs if pr.is_merged]
        times  = [pr.time_to_merge for pr in merged if pr.time_to_merge is not None]

        return PRMetrics(
            total           = len(prs),
            merged_count    = len(merged),
            open_count      = sum(1 for pr in prs if pr.state == "open"),
            merge_rate      = len(merged) / len(prs),
            avg_merge_hours = sum(times) / len(times) if times else None,
            available       = True,
        )

    def _compute_release_metrics(self, releases: list[Release]) -> ReleaseMetrics:
        if not releases:
            return ReleaseMetrics(total=0, days_since_latest=None, available=False)

        now = datetime.now(timezone.utc)
        return ReleaseMetrics(
            total             = len(releases),
            days_since_latest = (now - releases[0].published_at).days,
            available         = True,
        )


# ─────────────────────────────────────────
# Health scorer
# ─────────────────────────────────────────

class HealthScorer:
    """
    Converts HealthMetrics into a weighted 0-100 score with A-D grade.

    Missing signals are excluded and weights are renormalized so the score
    always reflects available data honestly rather than penalizing repos
    for not using certain GitHub features.
    """

    def score(self, m: HealthMetrics) -> HealthScore:
        # Compute raw 0-100 score per component (None = no data)
        raw: dict[str, float | None] = {
            "commit_momentum": self._score_commit_momentum(m),
            "bus_factor":      self._score_bus_factor(m.bus_factor),
            "issue_health":    self._score_issue_health(m.issues),
            "pr_latency":      self._score_pr_latency(m.prs),
            "release_cadence": self._score_release_cadence(m.releases),
        }

        # Renormalize weights over available signals only (Decision 4 Option C)
        available = {k: v for k, v in raw.items() if v is not None}
        total_available_weight = sum(WEIGHTS[k] for k in available)

        components: list[ComponentScore] = []
        composite = 0.0

        for name, weight in WEIGHTS.items():
            raw_score = raw[name]
            if raw_score is None:
                components.append(ComponentScore(
                    name=name, raw_score=None,
                    weight=weight, effective_weight=0.0, contribution=0.0,
                ))
                continue

            effective_weight = weight / total_available_weight
            contribution     = raw_score * effective_weight
            composite       += contribution

            components.append(ComponentScore(
                name=name,
                raw_score=round(raw_score, 2),
                weight=weight,
                effective_weight=round(effective_weight, 4),
                contribution=round(contribution, 2),
            ))

        value = max(0, min(100, int(composite)))
        grade = (
            "A" if value >= 80 else
            "B" if value >= 60 else
            "C" if value >= 40 else
            "D"
        )

        return HealthScore(value=value, grade=grade, components=components, metrics=m)

    # ── component scorers (each returns 0-100 or None) ───

    def _score_commit_momentum(self, m: HealthMetrics) -> float | None:
        """
        Score based on trend percentage when confidence is high,
        fall back to raw commit count when trend is unavailable.
        """
        current = m.current_window.commit_count

        if current == 0:
            return 0.0

        # Trend available — use it
        if m.commit_trend_pct is not None:
            trend = m.commit_trend_pct
            # +50% trend or better = 100
            # flat (0%) = 60
            # -50% = 10
            # Below -80% = 0
            if trend >= 50:   return 100.0
            if trend >= 0:    return 60.0 + (trend / 50.0) * 40.0
            if trend >= -50:  return 60.0 + (trend / 50.0) * 50.0
            return max(0.0, 10.0 + (trend + 50) / 30.0 * 10.0)

        # Trend unavailable (low confidence) — score on raw count alone
        # 50+ commits in window = 80, 20+ = 60, 5+ = 40, below 5 = 20
        if current >= 50: return 80.0
        if current >= 20: return 60.0
        if current >= 5:  return 40.0
        return 20.0

    def _score_bus_factor(self, bf: BusFactorMetrics | None) -> float | None:
        if bf is None:
            return None
        # HHI health score is already 0-100 via (1 - hhi) * 100
        return bf.health_score

    def _score_issue_health(self, issues: IssueMetrics) -> float | None:
        if not issues.available:
            return None
        # Resolution rate maps linearly to 0-100
        # 100% resolved = 100, 75% = 75, 50% = 50, 0% = 0
        return round(issues.resolution_rate * 100, 2)

    def _score_pr_latency(self, prs: PRMetrics) -> float | None:
        if not prs.available or prs.avg_merge_hours is None:
            return None
        hours = prs.avg_merge_hours
        # < 4h = 100 (excellent)
        # < 24h = 80 (good)
        # < 72h = 60 (acceptable)
        # < 168h = 40 (slow)
        # < 336h = 20 (very slow)
        # >= 336h = 0 (bottleneck)
        if hours < 4:    return 100.0
        if hours < 24:   return 80.0
        if hours < 72:   return 60.0
        if hours < 168:  return 40.0
        if hours < 336:  return 20.0
        return 0.0

    def _score_release_cadence(self, releases: ReleaseMetrics) -> float | None:
        if not releases.available or releases.days_since_latest is None:
            return None
        days = releases.days_since_latest
        # < 30 days = 100
        # < 90 days = 75
        # < 180 days = 50
        # < 365 days = 25
        # >= 365 days = 0
        if days < 30:  return 100.0
        if days < 90:  return 75.0
        if days < 180: return 50.0
        if days < 365: return 25.0
        return 0.0