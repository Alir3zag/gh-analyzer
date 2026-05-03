from __future__ import annotations

from dataclasses import dataclass

from gh_analyzer.analytics import HealthMetrics


# ─────────────────────────────────────────
# Risk flag dataclass
# ─────────────────────────────────────────

@dataclass
class RiskFlag:
    level:   str    # "ERROR" | "WARN" | "OK"
    code:    str    # machine-readable identifier for tests and JSON
    message: str    # human-readable plain English


# ─────────────────────────────────────────
# Thresholds — documented with rationale
# ─────────────────────────────────────────

# Bus factor: >80% from one author means the project effectively
# depends on one person. 60-80% is concentrated but survivable.
BUS_FACTOR_ERROR_THRESHOLD = 80.0
BUS_FACTOR_WARN_THRESHOLD  = 60.0

# Momentum: a >50% drop signals serious decline. 25-50% is a slowdown
# worth watching but not necessarily alarming.
MOMENTUM_DROP_ERROR_THRESHOLD = 50.0
MOMENTUM_DROP_WARN_THRESHOLD  = 25.0

# Issue resolution: <30% means the maintainer is not keeping up.
# 30-50% is struggling but not critical.
ISSUE_ERROR_THRESHOLD = 0.30
ISSUE_WARN_THRESHOLD  = 0.50

# PR latency: >168h (7 days) avg merge time is a bottleneck.
# 72-168h is slow but not critical.
PR_ERROR_HOURS = 168.0
PR_WARN_HOURS  = 72.0

# Release cadence: >365 days with no release on a repo that has
# releases is worth flagging. No ERROR here — many healthy repos
# simply do not use GitHub releases.
RELEASE_WARN_DAYS = 365

# Positive thresholds — signals worth surfacing as OK
ISSUE_OK_THRESHOLD   = 0.75   # >75% resolution rate is healthy
MOMENTUM_OK_THRESHOLD = 20.0  # >20% commit growth is healthy


# ─────────────────────────────────────────
# Risk detector
# ─────────────────────────────────────────

class RiskDetector:
    """
    Evaluates HealthMetrics against documented thresholds and returns
    a list of RiskFlags ordered by severity (ERROR first, then WARN, then OK).

    Each rule is independent — a metric can produce at most one flag.
    Rules are evaluated in severity order so the most critical fires first.
    """

    def evaluate(self, m: HealthMetrics) -> list[RiskFlag]:
        flags: list[RiskFlag] = []

        flags.extend(self._check_bus_factor(m))
        flags.extend(self._check_momentum(m))
        flags.extend(self._check_issue_health(m))
        flags.extend(self._check_pr_latency(m))
        flags.extend(self._check_release_cadence(m))

        # Sort: ERROR first, WARN second, OK last
        order = {"ERROR": 0, "WARN": 1, "OK": 2}
        flags.sort(key=lambda f: order.get(f.level, 99))

        return flags

    # ── individual checkers ───────────────

    def _check_bus_factor(self, m: HealthMetrics) -> list[RiskFlag]:
        if m.bus_factor is None:
            return []

        pct = m.bus_factor.top_author_pct
        author = m.bus_factor.top_author

        if pct > BUS_FACTOR_ERROR_THRESHOLD:
            return [RiskFlag(
                level   = "ERROR",
                code    = "BUS_FACTOR_CRITICAL",
                message = (
                    f"MAINTAINER RISK: {pct:.0f}% of commits from "
                    f"{author} — project depends on one contributor"
                ),
            )]

        if pct > BUS_FACTOR_WARN_THRESHOLD:
            return [RiskFlag(
                level   = "WARN",
                code    = "BUS_FACTOR_HIGH",
                message = (
                    f"CONCENTRATION RISK: {pct:.0f}% of commits from "
                    f"{author} — contributor base is narrow"
                ),
            )]

        return []

    def _check_momentum(self, m: HealthMetrics) -> list[RiskFlag]:
        # No commits at all is the most severe momentum signal
        if m.current_window.commit_count == 0:
            return [RiskFlag(
                level   = "ERROR",
                code    = "NO_ACTIVITY",
                message = "NO ACTIVITY: zero commits in the analysis window",
            )]

        # Trend available — use percentage drop
        if m.commit_trend_pct is not None and m.commit_trend_pct < 0:
            drop = abs(m.commit_trend_pct)

            if drop > MOMENTUM_DROP_ERROR_THRESHOLD:
                return [RiskFlag(
                    level   = "ERROR",
                    code    = "MOMENTUM_DROP_SEVERE",
                    message = (
                        f"MOMENTUM DROP: commit frequency down {drop:.0f}% "
                        "vs prior period — significant decline"
                    ),
                )]

            if drop > MOMENTUM_DROP_WARN_THRESHOLD:
                return [RiskFlag(
                    level   = "WARN",
                    code    = "MOMENTUM_DROP_MODERATE",
                    message = (
                        f"SLOWDOWN: commit frequency down {drop:.0f}% "
                        "vs prior period"
                    ),
                )]

        # Positive momentum — surface as OK
        if (
            m.commit_trend_pct is not None
            and m.commit_trend_pct > MOMENTUM_OK_THRESHOLD
        ):
            return [RiskFlag(
                level   = "OK",
                code    = "MOMENTUM_HEALTHY",
                message = (
                    f"ACTIVE DEVELOPMENT: commit frequency up "
                    f"{m.commit_trend_pct:.0f}% vs prior period"
                ),
            )]

        return []

    def _check_issue_health(self, m: HealthMetrics) -> list[RiskFlag]:
        if not m.issues.available:
            return []

        rate = m.issues.resolution_rate

        if rate < ISSUE_ERROR_THRESHOLD:
            return [RiskFlag(
                level   = "ERROR",
                code    = "ISSUE_BACKLOG_CRITICAL",
                message = (
                    f"ISSUE BACKLOG: only {rate * 100:.0f}% of issues resolved "
                    "— maintainer is not keeping up"
                ),
            )]

        if rate < ISSUE_WARN_THRESHOLD:
            return [RiskFlag(
                level   = "WARN",
                code    = "ISSUE_BACKLOG_MODERATE",
                message = (
                    f"ISSUE HEALTH: {rate * 100:.0f}% resolution rate "
                    "— backlog growing"
                ),
            )]

        if rate > ISSUE_OK_THRESHOLD:
            return [RiskFlag(
                level   = "OK",
                code    = "ISSUE_HEALTH_GOOD",
                message = (
                    f"ISSUE HEALTH: {rate * 100:.0f}% resolution rate "
                    "— maintainer is responsive"
                ),
            )]

        return []

    def _check_pr_latency(self, m: HealthMetrics) -> list[RiskFlag]:
        if not m.prs.available or m.prs.avg_merge_hours is None:
            return []

        hours = m.prs.avg_merge_hours
        days  = hours / 24

        if hours > PR_ERROR_HOURS:
            return [RiskFlag(
                level   = "ERROR",
                code    = "PR_LATENCY_CRITICAL",
                message = (
                    f"REVIEW BOTTLENECK: avg {days:.0f}d to merge PRs "
                    "— review process is a blocker"
                ),
            )]

        if hours > PR_WARN_HOURS:
            return [RiskFlag(
                level   = "WARN",
                code    = "PR_LATENCY_HIGH",
                message = (
                    f"SLOW REVIEWS: avg {days:.1f}d to merge PRs "
                    "— review turnaround is slow"
                ),
            )]

        return []

    def _check_release_cadence(self, m: HealthMetrics) -> list[RiskFlag]:
        if not m.releases.available or m.releases.days_since_latest is None:
            return []

        days = m.releases.days_since_latest

        if days > RELEASE_WARN_DAYS:
            return [RiskFlag(
                level   = "WARN",
                code    = "STALE_RELEASES",
                message = (
                    f"NO RELEASE: {days} days since last release "
                    "— may indicate reduced maintenance"
                ),
            )]

        return []