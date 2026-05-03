"""Read-only SQLite queries for the FastAPI app.

The app never writes — ingest/ owns all writes. Keep these queries thin
and return plain dicts/lists so templates don't depend on sqlite3.Row.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "civic.db"


def connect() -> sqlite3.Connection:
    """Open a read-only connection. Set row_factory to sqlite3.Row."""
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# Query contracts — what the templates need.
#
#   list_items(topic: str | None,
#              stage: str | None,
#              sort: str = "buzz") -> list[dict]
#       Returns one dict per agenda item, JOINed with buzz_scores:
#         id, title, summary, topics, stage, meeting_date,
#         dollar_amount, buzz_score, petition_count, reddit_count, fire_emoji
#       Sort options: "buzz" (default), "recent" (meeting_date desc),
#                     "dollars" (dollar_amount desc).
#
#   get_item(id: str) -> dict | None
#       Single agenda item with everything for the detail page.
#
#   get_top_matches(item_id: str, n: int = 3) -> list[dict]
#       Best-scoring matched signals for the detail page's "Why people care".
#       Returns dicts with: signal_type, title, snippet, url, weight (signatures
#       or upvotes), similarity.
#
#   list_topics() -> list[str]
#       Distinct topics actually present in the data — for the topic filter dropdown.
#
#   list_stages() -> list[str]
#       Distinct stages actually present — for the stage filter dropdown.


def fire_emoji(score: float) -> str:
    """Map a 0–100 buzz score to 0–3 fire emojis. Used in templates."""
    if score >= 66:
        return "🔥🔥🔥"
    if score >= 33:
        return "🔥🔥"
    if score > 0:
        return "🔥"
    return ""
