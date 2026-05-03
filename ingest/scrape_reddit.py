"""Scrape Reddit for Cambridge-related discussion using PRAW.

Subreddits:
  - r/Cambridge       (general)
  - r/CambridgeMA     (smaller, sometimes city-specific)
  - r/boston          (filter posts mentioning "Cambridge")

Window: last 60 days.
Threshold: post score >= 10 OR comment_count >= 5.

For each qualifying post, pull the top ~5 comments (by score) and
concatenate into `body` so the LLM can tag/embed against richer text.

Writes raw rows to reddit_posts via ingest.db.upsert_reddit_post.
Does NOT call any LLM — that happens in enrich.py.

Output contract (one dict per post):
    {
      "id":            "<reddit post id>",
      "url":           "https://reddit.com/...",
      "subreddit":     "Cambridge",
      "title":         "...",
      "body":          "<post selftext>\\n---\\n<top comment 1>\\n---\\n...",
      "score":         142,
      "comment_count": 37,
      "created_at":    "<ISO timestamp>",
      # topics/embedding left NULL — enrich.py fills them.
    }

Auth: PRAW reads REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
from .env (via python-dotenv).
"""

from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from ingest.db import connect, init_schema, upsert_reddit_post

SUBREDDITS = ["Cambridge", "CambridgeMA", "boston"]
WINDOW_DAYS = 60
MIN_SCORE = 10
MIN_COMMENTS = 5
TOP_COMMENTS_PER_POST = 5
CAMBRIDGE_PATTERN = re.compile(
    r"\bcambridge\b|\bcambridge,\s*ma\b|\bcambridge\s+ma\b|\bcambridge\s+mass(?:achusetts)?\b",
    re.IGNORECASE,
)
REMOVED_TEXT = {"[deleted]", "[removed]"}


def reddit_client() -> Any:
    """Build a PRAW client from .env credentials."""
    try:
        from dotenv import load_dotenv
        import praw
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing Reddit scraper dependencies. Run `pip install -r requirements.txt`."
        ) from exc

    load_dotenv()

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT")
    missing = [
        name
        for name, value in (
            ("REDDIT_CLIENT_ID", client_id),
            ("REDDIT_CLIENT_SECRET", client_secret),
            ("REDDIT_USER_AGENT", user_agent),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing Reddit credentials in .env: " + ", ".join(missing)
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        check_for_async=False,
    )


def fetch_recent_posts(
    reddit: Any, subreddit_name: str, since: datetime
) -> Iterable[Any]:
    """Yield newest subreddit posts until the scrape window is exhausted."""
    since_timestamp = since.timestamp()
    subreddit = reddit.subreddit(subreddit_name)
    for submission in subreddit.new(limit=None):
        if getattr(submission, "created_utc", 0) < since_timestamp:
            break
        yield submission


def clean_text(value: str | None) -> str:
    """Normalize Reddit text fields and hide deleted/removed placeholders."""
    if not value:
        return ""
    text = value.strip()
    if text.lower() in REMOVED_TEXT:
        return ""
    return text


def mentions_cambridge(submission: Any) -> bool:
    """Return whether a post appears to discuss Cambridge in local context."""
    haystack = " ".join(
        (
            clean_text(getattr(submission, "title", "")),
            clean_text(getattr(submission, "selftext", "")),
        )
    )
    return bool(CAMBRIDGE_PATTERN.search(haystack))


def qualifies(submission: Any) -> bool:
    """Apply the minimum engagement threshold."""
    score = getattr(submission, "score", 0) or 0
    comment_count = getattr(submission, "num_comments", 0) or 0
    return score >= MIN_SCORE or comment_count >= MIN_COMMENTS


def top_comments_text(submission: Any, n: int = TOP_COMMENTS_PER_POST) -> str:
    """Return the top non-empty comment bodies, sorted by score."""
    comments = getattr(submission, "comments", [])
    if hasattr(comments, "replace_more"):
        comments.replace_more(limit=0)
    if hasattr(comments, "list"):
        comments_iterable = comments.list()
    else:
        comments_iterable = comments

    ranked_comments = []
    for comment in comments_iterable:
        if getattr(comment, "stickied", False):
            continue
        body = clean_text(getattr(comment, "body", ""))
        if not body:
            continue
        ranked_comments.append((getattr(comment, "score", 0) or 0, body))

    ranked_comments.sort(key=lambda item: item[0], reverse=True)
    return "\n---\n".join(body for _, body in ranked_comments[:n])


def submission_to_post(
    submission: Any, comments_text: str | None = None
) -> dict[str, Any]:
    """Normalize a PRAW submission to the reddit_posts row contract."""
    selftext = clean_text(getattr(submission, "selftext", ""))
    if comments_text is None:
        comments_text = top_comments_text(submission)

    body_parts = [part for part in (selftext, comments_text) if part]
    created_at = datetime.fromtimestamp(
        getattr(submission, "created_utc"), tz=timezone.utc
    ).isoformat()

    return {
        "id": getattr(submission, "id"),
        "url": f"https://www.reddit.com{getattr(submission, 'permalink')}",
        "subreddit": str(getattr(submission, "subreddit")),
        "title": clean_text(getattr(submission, "title", "")),
        "body": "\n---\n".join(body_parts),
        "score": getattr(submission, "score", 0) or 0,
        "comment_count": getattr(submission, "num_comments", 0) or 0,
        "created_at": created_at,
    }


def main() -> None:
    reddit = reddit_client()
    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    stats: Counter[str] = Counter()

    init_schema()
    conn = connect()
    try:
        for subreddit_name in SUBREDDITS:
            subreddit_key = subreddit_name.lower()
            for submission in fetch_recent_posts(reddit, subreddit_name, since):
                stats[f"{subreddit_key}:seen"] += 1

                if subreddit_key == "boston" and not mentions_cambridge(submission):
                    stats[f"{subreddit_key}:skipped_not_cambridge"] += 1
                    continue

                if not qualifies(submission):
                    stats[f"{subreddit_key}:skipped_low_engagement"] += 1
                    continue

                try:
                    comments_text = top_comments_text(submission)
                except Exception as exc:  # PRAW/network hiccups should not kill the run.
                    print(
                        f"warning: failed to fetch comments for {submission.id}: {exc}"
                    )
                    stats[f"{subreddit_key}:comment_fetch_failed"] += 1
                    comments_text = ""

                upsert_reddit_post(conn, submission_to_post(submission, comments_text))
                stats[f"{subreddit_key}:saved"] += 1
        conn.commit()
    finally:
        conn.close()

    total_saved = sum(stats[f"{name.lower()}:saved"] for name in SUBREDDITS)
    print(f"saved {total_saved} reddit posts from the last {WINDOW_DAYS} days")
    for subreddit_name in SUBREDDITS:
        subreddit_key = subreddit_name.lower()
        print(
            f"r/{subreddit_name}: "
            f"seen={stats[f'{subreddit_key}:seen']} "
            f"saved={stats[f'{subreddit_key}:saved']} "
            f"not_cambridge={stats[f'{subreddit_key}:skipped_not_cambridge']} "
            f"low_engagement={stats[f'{subreddit_key}:skipped_low_engagement']} "
            f"comment_errors={stats[f'{subreddit_key}:comment_fetch_failed']}"
        )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(f"error: {exc}") from None
