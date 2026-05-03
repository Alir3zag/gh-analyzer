# GitHub Repository Analyzer

A command-line tool that analyzes GitHub repository health across commits, issues, pull requests, and releases.

## Installation

git clone https://github.com/Alir3zag/gh-analyzer.git
cd gh-analyzer
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

## Authentication

Set a GitHub Personal Access Token to raise the rate limit from 60 to 5,000 requests/hour:

export GITHUB_TOKEN=your_token_here

Without a token the tool still works but is limited to 60 requests/hour.

## Usage

python -m gh_analyzer.main OWNER/REPO [options]

## Options

| Flag             | Default | Description                            |
|------------------|---------|----------------------------------------|
| --since DAYS     | 30      | How many days back to analyze          |
| --limit N        | 100     | Maximum number of results to fetch     |
| --format         | text    | Output format: text or json            |
| --token TOKEN    | —       | GitHub token (overrides GITHUB_TOKEN)  |
| --no-token       | —       | Force unauthenticated mode             |
| --validate-token | —       | Validate token before running          |
| --verbose        | —       | Enable debug logging                   |

## Examples

python -m gh_analyzer.main psf/requests
python -m gh_analyzer.main psf/requests --since 90
python -m gh_analyzer.main psf/requests --token ghp_xxx

## Project Structure

gh-analyzer/
├── gh_analyzer/
│   ├── __init__.py
│   ├── main.py          # Entry point and fetch orchestration
│   ├── cli.py           # Argument parsing
│   ├── github_api.py    # Async GitHub API client
│   ├── models.py        # Domain models
│   ├── exceptions.py    # Structured error types
│   ├── reporter.py      # Rich terminal output
│   ├── analytics.py     # Health scoring
│   └── cache.py         # Response caching
├── requirements.txt
├── .gitignore
└── README.md

## API Client

- Async requests via aiohttp with semaphore-based concurrency control
- Automatic retry with exponential backoff and jitter
- Primary and secondary rate limit detection and backoff
- Structured exceptions for all failure modes
- Pagination across all list endpoints