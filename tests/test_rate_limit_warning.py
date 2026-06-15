from __future__ import annotations

import io
import pytest
from unittest.mock import patch

from gh_analyzer.github_api import RateLimitTracker
from gh_analyzer.main import warn_if_rate_limit_low


def _make_rate(remaining: int | None) -> RateLimitTracker:
    r = RateLimitTracker()
    r.remaining  = remaining
    r.reset_time = 9999999999
    r.limit      = 60
    return r


def _capture(rate: RateLimitTracker) -> tuple[bool, str]:
    from rich.console import Console
    buffer = io.StringIO()
    cap    = Console(file=buffer, highlight=False)
    with patch("gh_analyzer.main.err_console", cap):
        shown = warn_if_rate_limit_low(rate)
    return shown, buffer.getvalue()


def test_no_warning_when_remaining_is_none():
    shown, output = _capture(_make_rate(None))
    assert shown is False
    assert output.strip() == ""


def test_no_warning_when_remaining_is_healthy():
    shown, output = _capture(_make_rate(50))
    assert shown is False
    assert output.strip() == ""


def test_no_warning_at_exact_threshold():
    shown, _ = _capture(_make_rate(10))
    assert shown is False


def test_warning_shown_when_remaining_below_threshold():
    shown, output = _capture(_make_rate(5))
    assert shown is True
    assert "Rate limit low" in output
    assert "5" in output
    assert "GITHUB_TOKEN" in output


def test_warning_shown_at_one_remaining():
    shown, output = _capture(_make_rate(1))
    assert shown is True
    assert "1" in output


def test_exhausted_warning_when_remaining_is_zero():
    shown, output = _capture(_make_rate(0))
    assert shown is True
    assert "exhausted" in output.lower()
    assert "GITHUB_TOKEN" in output


def test_exhausted_message_differs_from_low_message():
    _, output_zero = _capture(_make_rate(0))
    _, output_low  = _capture(_make_rate(5))
    assert "exhausted" in output_zero.lower()
    assert "exhausted" not in output_low.lower()
    assert "low" in output_low.lower()


def test_warning_includes_token_instructions():
    for remaining in [0, 3]:
        _, output = _capture(_make_rate(remaining))
        assert "GITHUB_TOKEN" in output
