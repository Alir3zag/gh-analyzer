import os
import asyncio
import logging
import aiohttp

from cli import from_args
from github_api import fetch_repo, build_headers, RateLimitTracker, MAX_CONCURRENT_REQUESTS

warned_no_token = False


def configure_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    return logging.getLogger("gh_analyzer")


def log_rate_limit(logger: logging.Logger, rate: RateLimitTracker) -> None:
    s = rate.snapshot()

    if s["secondary_limited"]:
        logger.warning("Secondary rate limit detected (abuse protection).")
    if s["retry_after"]:
        logger.warning("Retry-After=%ss requested by GitHub.", s["retry_after"])

    rem = s["remaining"]
    if rem is None:
        return
    if rem == 0:
        logger.error("Rate limit exhausted. Reset epoch=%s", s["reset_time"])
    elif rem < 10:
        logger.warning("Rate limit low: %s remaining. Reset epoch=%s", rem, s["reset_time"])
    else:
        logger.debug("Rate limit: %s remaining. Reset epoch=%s", rem, s["reset_time"])


async def run(argv=None) -> int:
    global warned_no_token
    cli_args = from_args(argv)
    logger = configure_logging(cli_args.verbose)
    token = os.getenv("GITHUB_TOKEN")
    if not token and not warned_no_token:
        logger.warning("GITHUB_TOKEN not set — running unauthenticated (60 requests/hour).")
        warned_no_token = True

    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    rate = RateLimitTracker()
    timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)

    async with aiohttp.ClientSession(timeout=timeout, headers=build_headers(token)) as session:
        repo = await fetch_repo(session, sem, cli_args, rate)

    log_rate_limit(logger, rate)
    print(repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
