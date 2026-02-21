import asyncio
import aiohttp
from cli import from_args
from github_api import fetch_repo, RateLimitTracker, build_headers, MAX_CONCURRENT_REQUESTS
from exceptions import RepoNotFoundError, UnauthorizedError, RateLimitError, NetworkError

async def run(argv: list[str] | None = None) -> int:
    cli_args = from_args(argv)
    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    rate_limiter = RateLimitTracker()

    timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=build_headers()) as session:
            repo = await fetch_repo(session, sem, cli_args, rate_limiter)
            print(repo)

    except RepoNotFoundError as e:
        print(f"❌ Not found: {e}")
        return 1
    except UnauthorizedError as e:
        print(f"❌ Unauthorized: {e}")
        return 1
    except RateLimitError as e:
        print(f"❌ Rate limited: {e}")
        return 1
    except NetworkError as e:
        print(f"❌ Network error: {e}")
        return 1
    finally:
        rate_limiter.report()

    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))


# repo, commits, issues, prs = await asyncio.gather(
#     fetch_repo(...),
#     fetch_recent_commits(...),
#     fetch_issues(...),
#     fetch_pull_requests(...),
# )
