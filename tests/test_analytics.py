from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from gh_analyzer.analytics import (
    BusFactorMetrics,
    HealthMetrics,
    HealthScorer,
    IssueMetrics,
    MetricsComputer,
    PRMetrics,
    ReleaseMetrics,
    WindowMetrics,
)


# ─────────────────────────────────────────
# Test fixtures — minimal mocks for input models
# ─────────────────────────────────────────

@dataclass
class FakeCommit:
    author_login: str | None
    author_name: str | None
    date: datetime


@dataclass
class FakeIssue:
    is_open: bool
    resolution_time: float | None


@dataclass
class FakePR:
    is_merged: bool
    state: str
    time_to_merge: float | None


@dataclass
class FakeRelease:
    published_at: datetime


def _commit(author: str, days_ago: int = 0) -> FakeCommit:
    return FakeCommit(
        author_login=author,
        author_name=author,
        date=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


def _full_metrics(**overrides) -> HealthMetrics:
    """Build a baseline HealthMetrics with sensible defaults; override any field."""
    defaults = dict(
        current_window  = WindowMetrics(20, 3, False),
        prior_window    = WindowMetrics(15, 3, False),
        commit_trend_pct= 33.0,
        bus_factor      = BusFactorMetrics(0.2, 80.0, "alice", 40.0, 5),
        issues          = IssueMetrics(20, 4, 16, 0.80, 10.0, True),
        prs             = PRMetrics(10, 8, 2, 0.80, 24.0, True),
        releases        = ReleaseMetrics(3, 45, True),
        analysis_since  = datetime(2026, 1, 1, tzinfo=timezone.utc),
        display_since   = datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return HealthMetrics(**defaults)


# ─────────────────────────────────────────
# Bus factor (HHI) — core algorithm
# ─────────────────────────────────────────

class TestBusFactorHHI:
    def test_empty_commits_returns_none(self):
        assert MetricsComputer()._compute_bus_factor([]) is None

    def test_single_contributor_hhi_is_one(self):
        commits = [_commit("alice") for _ in range(10)]
        bf = MetricsComputer()._compute_bus_factor(commits)
        assert bf is not None
        assert bf.hhi == 1.0
        assert bf.health_score == 0.0
        assert bf.top_author == "alice"
        assert bf.top_author_pct == 100.0
        assert bf.contributor_count == 1

    def test_two_equal_contributors(self):
        commits = [_commit("alice") for _ in range(5)] + [_commit("bob") for _ in range(5)]
        bf = MetricsComputer()._compute_bus_factor(commits)
        # 0.5² + 0.5² = 0.5
        assert bf.hhi == 0.5
        assert bf.health_score == 50.0
        assert bf.contributor_count == 2

    def test_four_equal_contributors(self):
        commits = []
        for name in ["alice", "bob", "carol", "dave"]:
            commits += [_commit(name) for _ in range(5)]
        bf = MetricsComputer()._compute_bus_factor(commits)
        # 4 × (0.25²) = 0.25
        assert bf.hhi == 0.25
        assert bf.health_score == 75.0
        assert bf.contributor_count == 4

    def test_dominant_contributor_high_hhi(self):
        commits = [_commit("alice") for _ in range(9)] + [_commit("bob")]
        bf = MetricsComputer()._compute_bus_factor(commits)
        # 0.9² + 0.1² = 0.82
        assert bf.hhi == 0.82
        assert bf.top_author == "alice"
        assert bf.top_author_pct == 90.0

    def test_unknown_authors_grouped(self):
        commits = [FakeCommit(None, None, datetime.now(timezone.utc)) for _ in range(3)]
        bf = MetricsComputer()._compute_bus_factor(commits)
        assert bf is not None
        assert bf.top_author == "unknown"
        assert bf.contributor_count == 1


# ─────────────────────────────────────────
# Commit windows
# ─────────────────────────────────────────

class TestCommitWindows:
    def test_low_confidence_when_few_commits(self):
        commits = [_commit("alice", days_ago=i) for i in range(5)]
        midpoint = datetime.now(timezone.utc) - timedelta(days=30)
        cur, _ = MetricsComputer()._split_commit_windows(commits, midpoint)
        assert cur.commit_count == 5
        assert cur.low_confidence is True

    def test_high_confidence_when_many_commits(self):
        commits = [_commit("alice", days_ago=i) for i in range(15)]
        midpoint = datetime.now(timezone.utc) - timedelta(days=30)
        cur, _ = MetricsComputer()._split_commit_windows(commits, midpoint)
        assert cur.commit_count == 15
        assert cur.low_confidence is False


# ─────────────────────────────────────────
# Trend
# ─────────────────────────────────────────

class TestCommitTrend:
    def test_trend_none_when_current_low_confidence(self):
        current = WindowMetrics(commit_count=3, unique_authors=1, low_confidence=True)
        prior   = WindowMetrics(commit_count=20, unique_authors=2, low_confidence=False)
        assert MetricsComputer()._compute_trend(current, prior) is None

    def test_trend_none_when_prior_low_confidence(self):
        current = WindowMetrics(commit_count=20, unique_authors=2, low_confidence=False)
        prior   = WindowMetrics(commit_count=3, unique_authors=1, low_confidence=True)
        assert MetricsComputer()._compute_trend(current, prior) is None

    def test_trend_positive_growth(self):
        current = WindowMetrics(commit_count=30, unique_authors=3, low_confidence=False)
        prior   = WindowMetrics(commit_count=20, unique_authors=2, low_confidence=False)
        # (30-20)/20 * 100 = 50%
        assert MetricsComputer()._compute_trend(current, prior) == 50.0

    def test_trend_negative_decline(self):
        current = WindowMetrics(commit_count=10, unique_authors=1, low_confidence=False)
        prior   = WindowMetrics(commit_count=20, unique_authors=2, low_confidence=False)
        # (10-20)/20 * 100 = -50%
        assert MetricsComputer()._compute_trend(current, prior) == -50.0


# ─────────────────────────────────────────
# Issue metrics
# ─────────────────────────────────────────

class TestIssueMetrics:
    def test_empty_issues_unavailable(self):
        m = MetricsComputer()._compute_issue_metrics([])
        assert m.available is False
        assert m.total == 0
        assert m.resolution_rate == 0.0
        assert m.avg_resolution_hours is None

    def test_all_resolved(self):
        issues = [FakeIssue(is_open=False, resolution_time=24.0) for _ in range(5)]
        m = MetricsComputer()._compute_issue_metrics(issues)
        assert m.total == 5
        assert m.closed_count == 5
        assert m.open_count == 0
        assert m.resolution_rate == 1.0
        assert m.avg_resolution_hours == 24.0

    def test_mixed_open_closed(self):
        issues = (
            [FakeIssue(is_open=False, resolution_time=10.0) for _ in range(4)] +
            [FakeIssue(is_open=True,  resolution_time=None) for _ in range(6)]
        )
        m = MetricsComputer()._compute_issue_metrics(issues)
        assert m.total == 10
        assert m.closed_count == 4
        assert m.open_count == 6
        assert m.resolution_rate == 0.4


# ─────────────────────────────────────────
# Release metrics
# ─────────────────────────────────────────

class TestReleaseMetrics:
    def test_empty_releases_unavailable(self):
        m = MetricsComputer()._compute_release_metrics([])
        assert m.available is False
        assert m.total == 0
        assert m.days_since_latest is None

    def test_recent_release(self):
        releases = [FakeRelease(datetime.now(timezone.utc) - timedelta(days=5))]
        m = MetricsComputer()._compute_release_metrics(releases)
        assert m.available is True
        assert m.total == 1
        assert m.days_since_latest == 5


# ─────────────────────────────────────────
# Health scorer — composite & grading
# ─────────────────────────────────────────

class TestHealthScorer:
    def test_grade_a_for_healthy_repo(self):
        m = _full_metrics(
            bus_factor       = BusFactorMetrics(0.1, 90.0, "alice", 30.0, 8),
            commit_trend_pct = 60.0,
            issues           = IssueMetrics(10, 1, 9, 0.90, 5.0, True),
            prs              = PRMetrics(10, 10, 0, 1.0, 2.0, True),
            releases         = ReleaseMetrics(5, 15, True),
        )
        score = HealthScorer().score(m)
        assert score.grade == "A"
        assert score.value >= 80

    def test_grade_d_for_unhealthy_repo(self):
        m = _full_metrics(
            bus_factor       = BusFactorMetrics(0.9, 10.0, "alice", 95.0, 2),
            commit_trend_pct = -85.0,
            issues           = IssueMetrics(20, 18, 2, 0.10, 200.0, True),
            prs              = PRMetrics(5, 1, 4, 0.20, 400.0, True),
            releases         = ReleaseMetrics(1, 500, True),
        )
        score = HealthScorer().score(m)
        assert score.grade == "D"
        assert score.value < 40

    def test_score_clamped_in_range(self):
        score = HealthScorer().score(_full_metrics())
        assert 0 <= score.value <= 100

    def test_healthy_outperforms_unhealthy(self):
        high = _full_metrics(
            commit_trend_pct=60.0,
            bus_factor=BusFactorMetrics(0.1, 90.0, "a", 30.0, 5),
        )
        low = _full_metrics(
            commit_trend_pct=-85.0,
            bus_factor=BusFactorMetrics(0.9, 10.0, "a", 95.0, 2),
            prs=PRMetrics(5, 1, 4, 0.2, 400.0, True),
            issues=IssueMetrics(20, 18, 2, 0.1, 200.0, True),
            releases=ReleaseMetrics(1, 500, True),
        )
        assert HealthScorer().score(high).value > HealthScorer().score(low).value

    def test_missing_signals_renormalize_weights(self):
        """Effective weights of available components must sum to 1.0."""
        m = _full_metrics(
            issues   = IssueMetrics(0, 0, 0, 0.0, None, False),
            releases = ReleaseMetrics(0, None, False),
        )
        score = HealthScorer().score(m)
        effective_sum = sum(c.effective_weight for c in score.components if c.raw_score is not None)
        assert abs(effective_sum - 1.0) < 0.0001

    def test_unavailable_components_have_zero_contribution(self):
        m = _full_metrics(issues=IssueMetrics(0, 0, 0, 0.0, None, False))
        score = HealthScorer().score(m)
        issue_comp = next(c for c in score.components if c.name == "issue_health")
        assert issue_comp.raw_score is None
        assert issue_comp.effective_weight == 0.0
        assert issue_comp.contribution == 0.0

    def test_all_five_components_returned(self):
        score = HealthScorer().score(_full_metrics())
        names = {c.name for c in score.components}
        assert names == {
            "commit_momentum", "bus_factor", "issue_health",
            "pr_latency", "release_cadence",
        }


# ─────────────────────────────────────────
# Component scorer thresholds
# ─────────────────────────────────────────

class TestCommitMomentumScoring:
    def test_zero_commits_scores_zero(self):
        m = _full_metrics(current_window=WindowMetrics(0, 0, True), commit_trend_pct=None)
        assert HealthScorer()._score_commit_momentum(m) == 0.0

    def test_strong_growth_scores_100(self):
        m = _full_metrics(commit_trend_pct=60.0)
        assert HealthScorer()._score_commit_momentum(m) == 100.0

    def test_severe_decline_scores_zero(self):
        m = _full_metrics(commit_trend_pct=-85.0)
        assert HealthScorer()._score_commit_momentum(m) == 0.0


class TestPRLatencyScoring:
    def test_very_fast_merge_scores_100(self):
        prs = PRMetrics(10, 10, 0, 1.0, 2.0, True)
        assert HealthScorer()._score_pr_latency(prs) == 100.0

    def test_slow_merge_scores_low(self):
        prs = PRMetrics(10, 5, 5, 0.5, 200.0, True)
        assert HealthScorer()._score_pr_latency(prs) == 20.0

    def test_unavailable_returns_none(self):
        prs = PRMetrics(0, 0, 0, 0.0, None, False)
        assert HealthScorer()._score_pr_latency(prs) is None


class TestReleaseCadenceScoring:
    def test_recent_release_scores_100(self):
        r = ReleaseMetrics(5, 10, True)
        assert HealthScorer()._score_release_cadence(r) == 100.0

    def test_stale_release_scores_zero(self):
        r = ReleaseMetrics(1, 500, True)
        assert HealthScorer()._score_release_cadence(r) == 0.0

    def test_unavailable_returns_none(self):
        r = ReleaseMetrics(0, None, False)
        assert HealthScorer()._score_release_cadence(r) is None


class TestIssueHealthScoring:
    def test_full_resolution_scores_100(self):
        issues = IssueMetrics(10, 0, 10, 1.0, 5.0, True)
        assert HealthScorer()._score_issue_health(issues) == 100.0

    def test_zero_resolution_scores_zero(self):
        issues = IssueMetrics(10, 10, 0, 0.0, None, True)
        assert HealthScorer()._score_issue_health(issues) == 0.0

    def test_unavailable_returns_none(self):
        issues = IssueMetrics(0, 0, 0, 0.0, None, False)
        assert HealthScorer()._score_issue_health(issues) is None