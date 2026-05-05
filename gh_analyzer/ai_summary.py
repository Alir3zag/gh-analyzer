from __future__ import annotations

import os
import logging

logger = logging.getLogger("gh_analyzer")


def generate_summary(metrics, score, flags: list) -> str | None:
    """
    Generate a 3-4 sentence plain English summary of repository health
    using the Gemini API.

    Returns the summary string on success.
    Returns None if:
      - GEMINI_API_KEY is not set
      - The API call fails for any reason

    Caller is responsible for displaying or skipping the result.
    This function never raises — all failures are soft.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except Exception as e:
        logger.warning("AI summary — failed to initialise Gemini client: %s", e)
        return None

    # ── Build prompt from computed metrics only ───────────────────────────
    bf = metrics.bus_factor
    bus_factor_line = (
        f"Bus factor HHI: {bf.hhi:.3f} "
        f"(top contributor: {bf.top_author} at {bf.top_author_pct:.1f}% of commits, "
        f"{bf.contributor_count} total contributors)"
        if bf is not None
        else "Bus factor: insufficient data"
    )

    trend_line = (
        f"Commit trend: {metrics.commit_trend_pct:+.1f}% vs prior period"
        if metrics.commit_trend_pct is not None
        else "Commit trend: insufficient data (low commit volume)"
    )

    issue_line = (
        f"Issue resolution rate: {metrics.issues.resolution_rate * 100:.1f}%"
        if metrics.issues.available
        else "Issue resolution: no data"
    )

    pr_line = (
        f"Avg PR merge time: {metrics.prs.avg_merge_hours:.1f} hours"
        if metrics.prs.available and metrics.prs.avg_merge_hours is not None
        else "PR merge time: no data"
    )

    release_line = (
        f"Days since last release: {metrics.releases.days_since_latest}"
        if metrics.releases.available
        else "Release cadence: no releases found"
    )

    flag_lines = (
        "\n".join(f"- [{f.level}] {f.message}" for f in flags)
        if flags
        else "None"
    )

    prompt = f"""You are a senior software engineer reviewing a GitHub repository's health data.

Health score: {score.value}/100 (Grade {score.grade})

Metrics:
- {trend_line}
- {bus_factor_line}
- {issue_line}
- {pr_line}
- {release_line}

Risk flags:
{flag_lines}

Write 3-4 sentences of plain English analysis for a developer deciding whether to depend on or contribute to this repository.
Focus on the most important signal.
Name the biggest risk clearly if one exists.
Do not use bullet points or headers.
Do not repeat the numbers verbatim — interpret what they mean.
Do not start with "This repository".
Do not start with "The repository"."""

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        text = response.text.strip()
        if not text:
            logger.warning("AI summary — empty response from Gemini")
            return None
        return text
    except Exception as e:
        logger.warning("AI summary — Gemini API call failed: %s", e)
        return None