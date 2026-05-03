from __future__ import annotations

from gh_analyzer.risk import RiskDetector, RiskFlag
from gh_analyzer.analytics import (
    HealthMetrics, WindowMetrics, BusFactorMetrics,
    IssueMetrics, PRMetrics, ReleaseMetrics,
)
from datetime import datetime, timezone


def _base_metrics(**overrides) -> HealthMetrics:
    base = HealthMetrics(
        current_window  = WindowMetrics(20, 5, False),
        prior_window    = WindowMetrics(15, 4, False),
        commit_trend_pct= 33.0,
        bus_factor      = BusFactorMetrics(0.2, 80.0, "alice", 40.0, 5),
        issues          = IssueMetrics(20, 4, 16, 0.80, 10.0, True),
        prs             = PRMetrics(10, 8, 2, 0.80, 24.0, True),
        releases        = ReleaseMetrics(3, 45, True),
        analysis_since  = datetime(2026, 1, 1, tzinfo=timezone.utc),
        display_since   = datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    for key, value in overrides.items():
        object.__setattr__(base, key, value)
    return base


def _codes(flags: list[RiskFlag]) -> list[str]:
    return [f.code for f in flags]


def _levels(flags: list[RiskFlag]) -> list[str]:
    return [f.level for f in flags]


# ── bus factor ───────────────────────────

def test_bus_factor_error_above_80_pct():
    m = _base_metrics(
        bus_factor=BusFactorMetrics(0.85, 15.0, "alice", 85.0, 3)
    )
    flags = RiskDetector().evaluate(m)
    assert "BUS_FACTOR_CRITICAL" in _codes(flags)
    assert flags[0].level == "ERROR"


def test_bus_factor_warn_between_60_and_80():
    m = _base_metrics(
        bus_factor=BusFactorMetrics(0.65, 35.0, "alice", 65.0, 4)
    )
    flags = RiskDetector().evaluate(m)
    assert "BUS_FACTOR_HIGH" in _codes(flags)
    assert any(f.level == "WARN" for f in flags)


def test_no_bus_factor_flag_below_60():
    m = _base_metrics(
        bus_factor=BusFactorMetrics(0.3, 70.0, "alice", 40.0, 8)
    )
    flags = RiskDetector().evaluate(m)
    assert "BUS_FACTOR_CRITICAL" not in _codes(flags)
    assert "BUS_FACTOR_HIGH" not in _codes(flags)


# ── momentum ─────────────────────────────

def test_no_activity_flag_when_zero_commits():
    m = _base_metrics(
        current_window=WindowMetrics(0, 0, True),
        commit_trend_pct=None,
    )
    flags = RiskDetector().evaluate(m)
    assert "NO_ACTIVITY" in _codes(flags)
    assert flags[0].level == "ERROR"


def test_momentum_drop_error_above_50_pct():
    m = _base_metrics(commit_trend_pct=-60.0)
    flags = RiskDetector().evaluate(m)
    assert "MOMENTUM_DROP_SEVERE" in _codes(flags)


def test_momentum_drop_warn_between_25_and_50():
    m = _base_metrics(commit_trend_pct=-35.0)
    flags = RiskDetector().evaluate(m)
    assert "MOMENTUM_DROP_MODERATE" in _codes(flags)


def test_momentum_ok_above_20_pct_growth():
    m = _base_metrics(commit_trend_pct=30.0)
    flags = RiskDetector().evaluate(m)
    assert "MOMENTUM_HEALTHY" in _codes(flags)
    assert any(f.level == "OK" for f in flags)


# ── issue health ─────────────────────────

def test_issue_error_below_30_pct_resolution():
    m = _base_metrics(
        issues=IssueMetrics(20, 15, 5, 0.25, 10.0, True)
    )
    flags = RiskDetector().evaluate(m)
    assert "ISSUE_BACKLOG_CRITICAL" in _codes(flags)


def test_issue_warn_between_30_and_50():
    m = _base_metrics(
        issues=IssueMetrics(20, 12, 8, 0.40, 10.0, True)
    )
    flags = RiskDetector().evaluate(m)
    assert "ISSUE_BACKLOG_MODERATE" in _codes(flags)


def test_issue_ok_above_75_pct():
    m = _base_metrics(
        issues=IssueMetrics(20, 4, 16, 0.80, 10.0, True)
    )
    flags = RiskDetector().evaluate(m)
    assert "ISSUE_HEALTH_GOOD" in _codes(flags)


# ── PR latency ───────────────────────────

def test_pr_latency_error_above_168_hours():
    m = _base_metrics(
        prs=PRMetrics(10, 8, 2, 0.80, 200.0, True)
    )
    flags = RiskDetector().evaluate(m)
    assert "PR_LATENCY_CRITICAL" in _codes(flags)


def test_pr_latency_warn_between_72_and_168():
    m = _base_metrics(
        prs=PRMetrics(10, 8, 2, 0.80, 100.0, True)
    )
    flags = RiskDetector().evaluate(m)
    assert "PR_LATENCY_HIGH" in _codes(flags)


def test_no_pr_flag_below_72_hours():
    m = _base_metrics(
        prs=PRMetrics(10, 8, 2, 0.80, 24.0, True)
    )
    flags = RiskDetector().evaluate(m)
    assert "PR_LATENCY_CRITICAL" not in _codes(flags)
    assert "PR_LATENCY_HIGH" not in _codes(flags)


# ── release cadence ──────────────────────

def test_release_warn_above_365_days():
    m = _base_metrics(
        releases=ReleaseMetrics(2, 400, True)
    )
    flags = RiskDetector().evaluate(m)
    assert "STALE_RELEASES" in _codes(flags)


def test_no_release_flag_below_365_days():
    m = _base_metrics(
        releases=ReleaseMetrics(2, 45, True)
    )
    flags = RiskDetector().evaluate(m)
    assert "STALE_RELEASES" not in _codes(flags)


# ── ordering ─────────────────────────────

def test_flags_sorted_error_first():
    m = _base_metrics(
        bus_factor    = BusFactorMetrics(0.85, 15.0, "alice", 85.0, 2),
        commit_trend_pct = 30.0,
        issues        = IssueMetrics(20, 4, 16, 0.80, 10.0, True),
    )
    flags = RiskDetector().evaluate(m)
    levels = _levels(flags)
    error_idx = next((i for i, l in enumerate(levels) if l == "ERROR"), None)
    ok_idx    = next((i for i, l in enumerate(levels) if l == "OK"),    None)
    if error_idx is not None and ok_idx is not None:
        assert error_idx < ok_idx


def test_no_flags_for_healthy_repo():
    m = _base_metrics(
        bus_factor       = BusFactorMetrics(0.1, 90.0, "alice", 20.0, 10),
        commit_trend_pct = 5.0,
        issues           = IssueMetrics(20, 5, 15, 0.60, 5.0, True),
        prs              = PRMetrics(10, 9, 1, 0.90, 12.0, True),
        releases         = ReleaseMetrics(5, 20, True),
    )
    flags = RiskDetector().evaluate(m)
    error_flags = [f for f in flags if f.level == "ERROR"]
    assert len(error_flags) == 0