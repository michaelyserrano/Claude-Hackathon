"""Scrape Reddit for Cambridge-related discussion via the public JSON API.

Why JSON, not PRAW
------------------
Reddit exposes every listing as JSON if you append `.json` (or hit `/new.json`).
That avoids OAuth, an extra dependency, and stale-token errors at hackathon
time. Polite requests (custom User-Agent, ~1 req/sec, retry on 429) work fine.

Subreddits
----------
  - r/CambridgeMA  — the actual Cambridge, Massachusetts subreddit.
  - r/boston       — large regional sub; we keep posts that mention Cambridge.
  (r/cambridge is *Cambridge, England*, so we deliberately don't crawl it.)

Window: last 60 days.
Threshold: post score >= 10 OR comment_count >= 5.

For each qualifying post we pull the top ~5 comments (by score) and concatenate
them into `body` so enrichment has more text to summarize / embed against.

Writes raw rows to reddit_posts via ingest.db.upsert_reddit_post.
"""

from __future__ import annotations

import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests

from ingest.db import connect, init_schema, upsert_reddit_post

API_BASE = "https://www.reddit.com"
USER_AGENT = "cambridge-civic-feed/0.1 (Cambridge MA civic dashboard; +github)"
SUBREDDITS = [
    "CambridgeMA",
    "boston",
]
WINDOW_DAYS = 60
MIN_SCORE = 10
MIN_COMMENTS = 5
TOP_COMMENTS_PER_POST = 5
PAGE_SIZE = 100  # Reddit max for listing endpoints
HTTP_TIMEOUT = 30
SLEEP_BETWEEN_REQUESTS = 1.0  # Reddit rate-limits anonymous traffic; stay polite.
RATE_LIMIT_BACKOFF = 8.0
MAX_RETRIES = 3

CAMBRIDGE_PATTERN = re.compile(
    r"\bcambridge\b|\bcambridge,?\s*ma\b|\bcambridge\s+mass(?:achusetts)?\b",
    re.IGNORECASE,
)
REMOVED_TEXT = {"[deleted]", "[removed]"}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def _get_json(sess: requests.Session, url: str, params: dict | None = None) -> dict:
    """GET a Reddit JSON endpoint, retrying on transient errors and 429s."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = sess.get(url, params=params, timeout=HTTP_TIMEOUT)
            if r.status_code == 429:
                # Reddit asks for backoff. Honor any Retry-After hint.
                wait = float(r.headers.get("Retry-After", RATE_LIMIT_BACKOFF))
                time.sleep(min(wait, 30))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            time.sleep(RATE_LIMIT_BACKOFF * (attempt + 1))
    raise RuntimeError(f"reddit GET failed after {MAX_RETRIES} retries: {last_exc}")


def _clean(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    if text.lower() in REMOVED_TEXT:
        return ""
    return text


def _mentions_cambridge(post: dict) -> bool:
    haystack = " ".join((_clean(post.get("title")), _clean(post.get("selftext"))))
    return bool(CAMBRIDGE_PATTERN.search(haystack))


def _qualifies(post: dict) -> bool:
    return (post.get("score") or 0) >= MIN_SCORE or (
        post.get("num_comments") or 0
    ) >= MIN_COMMENTS


def fetch_recent_posts(
    sess: requests.Session, subreddit: str, since_ts: float
) -> Iterator[dict]:
    """Yield raw post dicts in /new order until we cross the window cutoff."""
    after: str | None = None
    while True:
        params = {"limit": PAGE_SIZE, "raw_json": 1}
        if after:
            params["after"] = after
        data = _get_json(sess, f"{API_BASE}/r/{subreddit}/new.json", params=params)
        children = data.get("data", {}).get("children") or []
        if not children:
            return
        for child in children:
            post = child.get("data") or {}
            if (post.get("created_utc") or 0) < since_ts:
                return  # listings are time-ordered; stop on first too-old hit
            yield post
        after = data.get("data", {}).get("after")
        if not after:
            return
        time.sleep(SLEEP_BETWEEN_REQUESTS)


def top_comments_text(
    sess: requests.Session, post_id: str, n: int = TOP_COMMENTS_PER_POST
) -> str:
    """Return the top non-empty, non-stickied comment bodies, sorted by score."""
    data = _get_json(
        sess,
        f"{API_BASE}/comments/{post_id}.json",
        params={"limit": n * 4, "sort": "top", "raw_json": 1},
    )
    if not isinstance(data, list) or len(data) < 2:
        return ""
    children = data[1].get("data", {}).get("children") or []
    ranked: list[tuple[int, str]] = []
    for child in children:
        if child.get("kind") != "t1":
            continue
        c = child.get("data") or {}
        if c.get("stickied"):
            continue
        body = _clean(c.get("body"))
        if not body:
            continue
        ranked.append((c.get("score") or 0, body))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return "\n---\n".join(body for _, body in ranked[:n])


def post_to_row(post: dict, comments_text: str) -> dict:
    selftext = _clean(post.get("selftext"))
    body_parts = [p for p in (selftext, comments_text) if p]
    created_at = datetime.fromtimestamp(
        post.get("created_utc") or 0, tz=timezone.utc
    ).isoformat()
    permalink = post.get("permalink") or ""
    return {
        "id": post.get("id"),
        "url": f"https://www.reddit.com{permalink}",
        "subreddit": (post.get("subreddit") or "").strip(),
        "title": _clean(post.get("title")),
        "body": "\n---\n".join(body_parts),
        "score": post.get("score") or 0,
        "comment_count": post.get("num_comments") or 0,
        "created_at": created_at,
    }


def main() -> None:
    sess = _session()
    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    since_ts = since.timestamp()
    stats: Counter[str] = Counter()

    init_schema()
    conn = connect()
    try:
        for sub in SUBREDDITS:
            key = sub.lower()
            for post in fetch_recent_posts(sess, sub, since_ts):
                stats[f"{key}:seen"] += 1
                if post.get("removed_by_category"):
                    stats[f"{key}:removed"] += 1
                    continue
                if key == "boston" and not _mentions_cambridge(post):
                    stats[f"{key}:not_cambridge"] += 1
                    continue
                if not _qualifies(post):
                    stats[f"{key}:low_engagement"] += 1
                    continue
                try:
                    comments_text = top_comments_text(sess, post["id"])
                except RuntimeError as exc:
                    print(
                        f"warning: comments fetch failed for {post.get('id')}: {exc}",
                        file=sys.stderr,
                    )
                    stats[f"{key}:comment_fetch_failed"] += 1
                    comments_text = ""
                upsert_reddit_post(conn, post_to_row(post, comments_text))
                stats[f"{key}:saved"] += 1
                time.sleep(SLEEP_BETWEEN_REQUESTS)
            conn.commit()
    finally:
        conn.close()

    total_saved = sum(stats[f"{s.lower()}:saved"] for s in SUBREDDITS)
    print(f"saved {total_saved} reddit posts from the last {WINDOW_DAYS} days")
    for sub in SUBREDDITS:
        k = sub.lower()
        print(
            f"r/{sub}: seen={stats[f'{k}:seen']} saved={stats[f'{k}:saved']} "
            f"removed={stats[f'{k}:removed']} "
            f"not_cambridge={stats[f'{k}:not_cambridge']} "
            f"low_engagement={stats[f'{k}:low_engagement']} "
            f"comment_errors={stats[f'{k}:comment_fetch_failed']}"
        )


if __name__ == "__main__":
    main()
