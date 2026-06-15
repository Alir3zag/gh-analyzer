# gh-analyzer

[![Tests](https://github.com/Alir3zag/gh-analyzer/actions/workflows/test.yml/badge.svg)](https://github.com/Alir3zag/gh-analyzer/actions/workflows/test.yml)
[![PyPI version](https://badge.fury.io/py/gh-analyzer.svg)](https://badge.fury.io/py/gh-analyzer)
[![Python versions](https://img.shields.io/pypi/pyversions/gh-analyzer)](https://pypi.org/project/gh-analyzer/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Tested across Python 3.11, 3.12, and 3.13 with 61 unit tests covering the analytics engine, risk detector, and rate limit handling.

A command-line tool that scores GitHub repository health across commit momentum, bus factor, issue resolution, PR latency, and release cadence.

---

## Installation

```
pip install gh-analyzer
```

Requires Python 3.11+.

---

## Authentication

Set a GitHub Personal Access Token to raise the rate limit from 60 to 5,000 requests/hour:

**Mac/Linux:**
```
export GITHUB_TOKEN=your_token_here
```

**Windows:**
```
set GITHUB_TOKEN=your_token_here
```

Without a token the tool is limited to 60 requests/hour and results may be incomplete.

---

## Usage

```
gh-analyzer OWNER/REPO [options]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--since DAYS` | 30 | How many days back to analyze |
| `--limit N` | 100 | Maximum number of results to fetch per category |
| `--format` | text | Output format: `text`, `table`, or `json` |
| `--output FILE` | — | Write output to a file instead of stdout |
| `--token TOKEN` | — | GitHub token (overrides GITHUB_TOKEN) |
| `--no-token` | — | Force unauthenticated mode |
| `--validate-token` | — | Validate token via GitHub /user before running |
| `--no-cache` | — | Bypass local cache and fetch fresh data |
| `--verbose` | — | Enable debug logging |
| `--ai-summary` | — | Append AI-generated narrative (requires GEMINI_API_KEY) |

### Examples

```
gh-analyzer psf/requests
gh-analyzer psf/requests --since 90
gh-analyzer psf/requests --format json
gh-analyzer psf/requests --format json --output report.json
gh-analyzer psf/requests --format table
gh-analyzer torvalds/linux --since 30 --verbose
gh-analyzer psf/requests --ai-summary
gh-analyzer psf/requests --no-cache
gh-analyzer psf/requests --validate-token
```

---

## Output

### Text output (default)

```
Analyzing last 30 days (2026-05-16 to 2026-06-15)

 psf/requests
 A simple, yet elegant, HTTP library.

 * 54,041  f 9,969  229 open issues  Python

Commits

  Metric               Value
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Total commits        41
  Unique authors       14
  Most active author   nateprewitt (20 commits)
  Date range           2026-04-19 to 2026-06-09

  Author                Commits   Share
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  nateprewitt           20        ######### 48.8%
  dependabot            6         ## 14.6%
  jorenham              3         # 7.3%

Issues

  Metric                Value
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Total issues          16
  Open                  2
  Closed                14
  Resolution rate       87.5%
  Avg resolution time   7.6h

Pull Requests

  Metric              Value
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Total PRs           129
  Merged              38
  Open                11
  Merge rate          29.5%
  Avg time to merge   12.0h

Releases

  Metric               Value
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Total releases       3
  Latest tag           v2.34.2
  Latest release       2026-05-14
  Days since release   31

 Repository Health Score
 56/100  Grade C

Score Breakdown

  Signal            Score           Weight   Contribution
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  commit_momentum   .......... 4    30%      1.2
  bus_factor        #######... 73   25%      18.2
  issue_health      ########.. 88   20%      17.5
  pr_latency        ########.. 80   15%      12.0
  release_cadence   #######... 75   10%      7.5

Bus factor:  HHI=0.273  top contributor: nateprewitt (48.8%)  total contributors: 14
Commit trend:  v 67.7% vs prior 30-day window

Risk Assessment
  x ERROR  MOMENTUM DROP: commit frequency down 68% vs prior period
  v OK     ISSUE HEALTH: 88% resolution rate -- maintainer is responsive
```

### Table output (`--format table`)

Compact single-table summary, useful for quick comparisons:

```
                psf/requests  30d (2026-05-16 to 2026-06-15)

  Signal            Value                           Score
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Repository        *54,041  f9,969  229 open issues
  Commit momentum   41 commits (v68%)                   4
  Bus factor        HHI=0.273  top=49%                 73
  Issue health      88% resolved (14/16)               88
  PR latency        12.0h avg merge                    80
  Release cadence   31d since last release             75

Health Score: 56/100  Grade C
```

### JSON output (`--format json`)

```json
{
  "repo": {
    "name": "requests",
    "full_name": "psf/requests",
    "description": "A simple, yet elegant, HTTP library.",
    "stars": 54041,
    "forks": 9969,
    "open_issues": 229,
    "language": "Python",
    "url": "https://github.com/psf/requests"
  },
  "analysis_window": {
    "days": 30,
    "from": "2026-05-16",
    "to": "2026-06-15"
  },
  "summary": {
    "commits": 41,
    "unique_authors": 14,
    "issues_total": 16,
    "issues_open": 2,
    "issues_closed": 14,
    "prs_total": 129,
    "prs_merged": 38,
    "releases_total": 3
  },
  "score": {
    "value": 56,
    "grade": "C",
    "components": [
      { "signal": "commit_momentum", "score": 4.09,  "weight": 0.3,  "effective_weight": 0.3,  "contribution": 1.23  },
      { "signal": "bus_factor",      "score": 72.69, "weight": 0.25, "effective_weight": 0.25, "contribution": 18.17 },
      { "signal": "issue_health",    "score": 87.5,  "weight": 0.2,  "effective_weight": 0.2,  "contribution": 17.5  },
      { "signal": "pr_latency",      "score": 80.0,  "weight": 0.15, "effective_weight": 0.15, "contribution": 12.0  },
      { "signal": "release_cadence", "score": 75.0,  "weight": 0.1,  "effective_weight": 0.1,  "contribution": 7.5   }
    ]
  },
  "bus_factor": {
    "hhi": 0.2731,
    "health_score": 72.69,
    "top_author": "nateprewitt",
    "top_author_pct": 48.8,
    "contributor_count": 14
  },
  "trend": {
    "commit_trend_pct": -67.74,
    "current_window": 10,
    "prior_window": 31,
    "low_confidence": false
  },
  "flags": [
    { "level": "ERROR", "code": "MOMENTUM_DROP_SEVERE", "message": "MOMENTUM DROP: commit frequency down 68% vs prior period" },
    { "level": "OK",    "code": "ISSUE_HEALTH_GOOD",    "message": "ISSUE HEALTH: 88% resolution rate -- maintainer is responsive" }
  ],
  "ai_summary": null
}
```

---

## Scoring Model

| Signal | Weight | Description |
|---|---|---|
| Commit momentum | 30% | Activity trend vs prior period |
| Bus factor | 25% | Contributor concentration via HHI |
| Issue health | 20% | Resolution rate |
| PR latency | 15% | Average time from open to merge |
| Release cadence | 10% | Days since last release |

**Bus factor** uses the Herfindahl-Hirschman Index (HHI) to measure contributor concentration. HHI closer to 1.0 means one person dominates; closer to 0.0 means contributions are evenly distributed.

**Missing signals** (e.g. a repo with no releases) are excluded and weights are renormalized — the score always reflects available data honestly.

**Grades:** A (80–100) · B (60–79) · C (40–59) · D (0–39)

---

## Risk Flags

Each report includes risk flags sorted by severity:

| Code | Level | Trigger |
|---|---|---|
| `BUS_FACTOR_CRITICAL` | ERROR | >80% of commits from one author |
| `MOMENTUM_DROP_SEVERE` | ERROR | >50% drop in commits vs prior period |
| `NO_ACTIVITY` | ERROR | Zero commits in the analysis window |
| `ISSUE_BACKLOG_CRITICAL` | ERROR | <30% issue resolution rate |
| `PR_LATENCY_CRITICAL` | ERROR | Average PR merge time >7 days |
| `BUS_FACTOR_HIGH` | WARN | 60–80% of commits from one author |
| `MOMENTUM_DROP_MODERATE` | WARN | 25–50% drop in commits |
| `ISSUE_BACKLOG_MODERATE` | WARN | 30–50% issue resolution rate |
| `PR_LATENCY_HIGH` | WARN | Average PR merge time 3–7 days |
| `STALE_RELEASES` | WARN | >365 days since last release |
| `MOMENTUM_HEALTHY` | OK | >20% commit growth |
| `ISSUE_HEALTH_GOOD` | OK | >75% issue resolution rate |

---

## Caching

API responses are cached at `~/.cache/gh-analyzer/` with a 5-minute TTL. Repeated runs within a session skip the network entirely. Use `--no-cache` to force a fresh fetch.

---

## AI Summary

The `--ai-summary` flag appends a 3–4 sentence plain English interpretation of the health data, generated via the Gemini API. Requires `GEMINI_API_KEY` environment variable. The tool works normally without it.

```
export GEMINI_API_KEY=your_key_here   # Mac/Linux
set GEMINI_API_KEY=your_key_here      # Windows
```

---

## Project Structure

```
gh-analyzer/
├── .github/
│   └── workflows/
│       └── test.yml          # GitHub Actions CI (Python 3.11, 3.12, 3.13)
├── gh_analyzer/
│   ├── main.py               # Entry point and fetch orchestration
│   ├── cli.py                # Argument parsing
│   ├── github_api.py         # Async GitHub API client with rate limit handling
│   ├── models.py             # Domain models
│   ├── exceptions.py         # Structured error types
│   ├── analytics.py          # Health scoring engine
│   ├── risk.py               # Risk flag detector
│   ├── ai_summary.py         # Gemini AI narrative summary
│   ├── reporter.py           # Rich terminal output and JSON serialization
│   └── cache.py              # Disk-based API response cache (5-min TTL)
├── tests/
│   ├── test_analytics.py     # 36 tests: HHI, scoring, edge cases
│   ├── test_risk.py          # 16 tests: all 12 risk flag rules
│   └── test_rate_limit_warning.py  # 9 tests: rate limit warning thresholds
├── pyproject.toml
└── README.md
```

---

## Development

Clone the repo and install in development mode with Poetry:

```
git clone https://github.com/Alir3zag/gh-analyzer
cd gh-analyzer
poetry install --with dev
```

Run the test suite (61 tests):

```
poetry run pytest -v
```

Tests run automatically on every push via GitHub Actions across Python 3.11, 3.12, and 3.13.

---

## License

MIT