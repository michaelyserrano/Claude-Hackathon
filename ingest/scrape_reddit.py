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

SUBREDDITS = ["Cambridge", "CambridgeMA", "boston"]
WINDOW_DAYS = 60
MIN_SCORE = 10
MIN_COMMENTS = 5
TOP_COMMENTS_PER_POST = 5


# TODO entry points:
#   - reddit_client() -> praw.Reddit
#   - fetch_recent_posts(subreddit, since: datetime) -> Iterable[Submission]
#   - mentions_cambridge(submission) -> bool   # only used for r/boston
#   - top_comments_text(submission, n) -> str
#   - main()


def main() -> None:
    # TODO
    ...


if __name__ == "__main__":
    main()
