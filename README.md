# gh-analyzer

A command-line tool that scores GitHub repository health across commit momentum, bus factor, issue resolution, PR latency, and release cadence.

## Installation

pip install gh-analyzer

Requires Python 3.11+.

## Authentication

Set a GitHub Personal Access Token to raise the rate limit from 60 to 5,000 requests/hour:

Mac/Linux:
export GITHUB_TOKEN=your_token_here

Windows:
set GITHUB_TOKEN=your_token_here

Without a token the tool is limited to 60 requests/hour and results may be incomplete.

## Usage

gh-analyzer OWNER/REPO [options]

## Options

| Flag             | Default | Description                           |
|------------------|---------|---------------------------------------|
| --since DAYS     | 30      | How many days back to analyze         |
| --limit N        | 100     | Maximum number of results to fetch    |
| --format         | text    | Output format: text or json           |
| --token TOKEN    | —       | GitHub token (overrides GITHUB_TOKEN) |
| --no-token       | —       | Force unauthenticated mode            |
| --validate-token | —       | Validate token before running         |
| --verbose        | —       | Enable debug logging                  |

## Examples

gh-analyzer psf/requests
gh-analyzer psf/requests --since 90
gh-analyzer psf/requests --format json
gh-analyzer torvalds/linux --since 30 --verbose

## Output

The tool produces a full health report including:

- Commit activity and top contributors
- Issue resolution rate and average resolution time
- Pull request merge rate and average merge time
- Release cadence
- Health score (0-100, grade A-D) weighted across five signals
- Risk flags (ERROR / WARN / OK) with plain-language descriptions
- JSON output via --format json for machine consumption

## Scoring Model

| Signal           | Weight | Description                              |
|------------------|--------|------------------------------------------|
| Commit momentum  | 30%    | Activity trend vs prior period           |
| Bus factor       | 25%    | Contributor concentration via HHI        |
| Issue health     | 20%    | Resolution rate                          |
| PR latency       | 15%    | Average time from open to merge          |
| Release cadence  | 10%    | Days since last release                  |

Bus factor uses the Herfindahl-Hirschman Index (HHI) to measure contributor
concentration. HHI closer to 1.0 means one person dominates. HHI closer to
0.0 means contributions are evenly distributed.

## Project Structure

gh-analyzer/
├── gh_analyzer/
│   ├── main.py          # Entry point and fetch orchestration
│   ├── cli.py           # Argument parsing
│   ├── github_api.py    # Async GitHub API client with rate limit handling
│   ├── models.py        # Domain models
│   ├── exceptions.py    # Structured error types
│   ├── analytics.py     # Health scoring engine
│   ├── risk.py          # Risk flag detector
│   └── reporter.py      # Rich terminal output and JSON serialization
├── tests/
│   ├── test_risk.py
│   └── test_rate_limit_warning.py
├── pyproject.toml
└── README.md

## License

MIT