import asyncio
import os
import time
import aiohttp
from datetime import datetime
from models import Repo, Commit
from cli import parse_and_validate_args

BASE_URL = "https://api.github.com/repos"
MAX_CONCURRENT_REQUESTS = 5
RATE_LIMIT_THRESHOLD = 10
MAX_RETRIES = 3

_warned_no_token = False

def build_headers():
    global _warned_no_token
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")

    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        if not _warned_no_token:
            print("⚠️  GITHUB_TOKEN not set. Running unauthenticated (60 requests/hour).")
            _warned_no_token = True
    return headers


class RateLimitTracker:
    def __init__(self):
        self.remaining: int | None = None
        self.reset_time: int | None = None

    def update_from_headers(self, headers) -> None:
        rem = headers.get("X-RateLimit-Remaining")
        rst = headers.get("X-RateLimit-Reset")
        if rem is not None:
            self.remaining = int(rem)
        if rst is not None:
            self.reset_time = int(rst)

    async def wait_if_needed(self) -> None:
        if self.remaining is None or self.reset_time is None:
            return

        if self.remaining == 0:
            wait_time = self.reset_time - time.time()
            if wait_time > 0:
                print(f"⚠️ Rate limit exhausted. Sleeping ~{wait_time:.0f}s until reset...")
                await asyncio.sleep(wait_time + 1)
        elif self.remaining < RATE_LIMIT_THRESHOLD:
            print(f"⚠️ Rate limit low: {self.remaining} requests remaining")

async def fetch_repo_data(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    repo: str,
    rate_limiter: RateLimitTracker,
) -> dict:
    url = f"{BASE_URL}/{repo}"

    for attempt in range(1, MAX_RETRIES + 1):
        async with sem:
            try:
                async with session.get(url) as resp:
                    rate_limiter.update_from_headers(resp.headers)

                    if resp.status == 200:
                        return await resp.json()

                    if resp.status in (403, 429):
                        # could be rate limit or abuse protection; try wait+retry
                        await rate_limiter.wait_if_needed()
                        if attempt < MAX_RETRIES:
                            continue
                        return {"error": f"Rate limited/forbidden: {resp.status}", "details": await resp.text()}

                    if resp.status == 404:
                        return {"error": "Not found", "details": f"Repo {repo} does not exist or is private."}

                    if resp.status == 401:
                        return {"error": "Unauthorized", "details": "Bad token or missing scopes."}

                    # other errors
                    return {"error": f"HTTP {resp.status}", "details": await resp.text()}

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * attempt)  # small backoff
                    continue
                return {"error": f"Request failed: {type(e).__name__}", "details": str(e)}

    return {"error": "Unexpected: retries exhausted"}  # should never hit

async def fetch_commits(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    repo: str,
    since: int | None,
    limit: int | None,
    rate_limiter: RateLimitTracker,
) -> list[dict]:
    url = f"{BASE_URL}/{repo}/commits"
    params = {}
    if since:
        params["since"] = datetime.utcfromtimestamp(since).isoformat() + "Z"
    if limit:
        params["per_page"] = min(limit, 100)

    commits = []
    page = 1

    for attempt in range(1, MAX_RETRIES + 1):   
        async with sem:
            try:
                async with session.get(url, params={**params, "page": page}) as resp:
                    rate_limiter.update_from_headers(resp.headers)

                    if resp.status == 200:
                        data = await resp.json()
                        commits.extend(data)
                        if len(data) < (params.get("per_page", 30)):
                            return commits[:limit] if limit else commits
                        page += 1
                        continue

                        if resp.status in (403, 429):
                            await rate_limiter.wait_if_needed()
                            if attempt < MAX_RETRIES:
                                continue
                            return {"error": f"Rate limited/forbidden: {resp.status}", "details": await resp.text()}

                        if resp.status == 404:
                            return {"error": "Not found", "details": f"Repo {repo} does not exist or is private."}

                        if resp.status == 401:
                            return {"error": "Unauthorized", "details": "Bad token or missing scopes."}

                        return {"error": f"HTTP {resp.status}", "details": await resp.text()}

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * attempt)
                        continue
                    return {"error": f"Request failed: {type(e).__name__}", "details": str(e)}

        else:
            return {"error": "Unexpected: retries exhausted"}




async def main(argv: list[str] | None = None) -> int:
    repo = parse_and_validate_args(argv)
    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    rate_limiter = RateLimitTracker()

    timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)

    async with aiohttp.ClientSession(timeout=timeout, headers=build_headers()) as session:
        data = await fetch_repo_data(session, sem, f"{repo.username}/{repo.repo_name}", rate_limiter)
        print(data)

    if rate_limiter.remaining is not None:
        print(f"\n📊 Rate Limit Remaining: {rate_limiter.remaining}")

    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
