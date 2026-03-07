# GitHub Repository Analyzer

A command-line tool for analyzing GitHub repositories — collecting activity data and computing metrics across commits, issues, pull requests, and contributors.

> **Status:** In development — API foundation complete, analytics layer in progress.

---

## Installation

```bash
git clone https://github.com/yourusername/gh_analyzer.git
cd gh_analyzer
pip install -r requirements.txt
```

---

## Authentication

Set a GitHub Personal Access Token to raise the rate limit from 60 to 5000 requests/hour:

```bash
export GITHUB_TOKEN=your_token_here
```

Without a token the tool still works but is limited to 60 requests/hour.

---

## Usage

```bash
python -m gh_analyzer.main OWNER/REPO [options]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--since DAYS` | 30 | How many days back to analyze |
| `--limit N` | 100 | Maximum number of results to fetch |
| `--format` | text | Output format: `text`, `json`, or `table` |
| `--top N` | — | Show only top N results |
| `--token TOKEN` | — | GitHub token (overrides `GITHUB_TOKEN`) |
| `--no-token` | — | Force unauthenticated mode |
| `--validate-token` | — | Validate token before running |
| `--verbose` | — | Enable debug logging |

### Examples

```bash
# Analyze last 30 days
python -m gh_analyzer.main torvalds/linux

# Analyze last 90 days, JSON output
python -m gh_analyzer.main torvalds/linux --since 90 --format json

# Use a specific token, show top 10
python -m gh_analyzer.main torvalds/linux --token ghp_xxx --top 10
```

---

## Project Structure

```
gh_analyzer/
│
├── gh_analyzer/
│   ├── __init__.py
│   ├── cli.py          # Argument parsing and CLI routing
│   ├── github_api.py   # Async GitHub API client
│   ├── models.py       # Domain models (Repo, Commit, Contributor)
│   ├── exceptions.py   # Structured error types
│   └── main.py         # Entry point
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

## API Client

- Async requests via `aiohttp` with concurrency control
- Automatic retry with exponential backoff and jitter
- Primary and secondary rate limit detection and backoff
- Structured exceptions for all failure modes
