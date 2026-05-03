"""Read-only SQLite queries for the FastAPI app.

The app never writes — ingest/ owns all writes. Keep these queries thin
and return plain dicts/lists so templates don't depend on sqlite3.Row.
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "civic.db"


def connect() -> sqlite3.Connection:
    """Open a read-only connection. Set row_factory to sqlite3.Row."""
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def fire_emoji(score: float) -> str:
    """Map a 0–100 buzz score to 0–3 fire emojis. Used in templates."""
    if score >= 66:
        return "🔥🔥🔥"
    if score >= 33:
        return "🔥🔥"
    if score > 0:
        return "🔥"
    return ""


def list_items(topic: str | None = None,
               stage: str | None = None,
               sort: str = "buzz") -> list[dict]:
    """Agenda items joined with buzz_scores, filtered + sorted."""
    sort_clause = {
        "buzz": "COALESCE(b.score, 0) DESC, a.meeting_date DESC",
        "recent": "a.meeting_date DESC",
        "dollars": "COALESCE(a.dollar_amount, 0) DESC",
    }.get(sort, "COALESCE(b.score, 0) DESC, a.meeting_date DESC")

    sql = f"""
        SELECT a.id, a.title, a.summary, a.topics, a.stage,
               a.meeting_date, a.dollar_amount, a.agenda_url,
               COALESCE(b.score, 0) AS buzz_score,
               COALESCE(b.petition_count, 0) AS petition_count,
               COALESCE(b.reddit_count, 0) AS reddit_count
        FROM agenda_items a
        LEFT JOIN buzz_scores b ON b.agenda_item_id = a.id
        WHERE 1=1
          {"AND a.stage = :stage" if stage else ""}
        ORDER BY {sort_clause}
    """
    params: dict = {}
    if stage:
        params["stage"] = stage

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    items = []
    for r in rows:
        topics = _parse_json_list(r["topics"])
        if topic and topic not in topics:
            continue
        items.append({
            "id": r["id"],
            "title": r["title"],
            "summary": r["summary"],
            "topics": topics,
            "stage": r["stage"],
            "meeting_date": r["meeting_date"],
            "dollar_amount": r["dollar_amount"],
            "agenda_url": r["agenda_url"],
            "buzz_score": r["buzz_score"],
            "petition_count": r["petition_count"],
            "reddit_count": r["reddit_count"],
            "fire_emoji": fire_emoji(r["buzz_score"]),
        })
    return items


def get_item(id: str) -> dict | None:
    """Single agenda item with everything for the detail page."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT a.*,
                   COALESCE(b.score, 0) AS buzz_score,
                   COALESCE(b.petition_count, 0) AS petition_count,
                   COALESCE(b.reddit_count, 0) AS reddit_count,
                   b.top_signal_id
            FROM agenda_items a
            LEFT JOIN buzz_scores b ON b.agenda_item_id = a.id
            WHERE a.id = ?
            """,
            (id,),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["topics"] = _parse_json_list(d.get("topics"))
    d["sponsors"] = _parse_json_list(d.get("sponsors"))
    d.pop("embedding", None)
    d["fire_emoji"] = fire_emoji(d["buzz_score"])
    return d


def get_top_matches(item_id: str, n: int = 3) -> list[dict]:
    """Best-scoring matched signals for the detail page's 'Why people care'."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT m.signal_type, m.signal_id, m.similarity,
                   p.title    AS p_title, p.description AS p_desc,
                   p.url      AS p_url,   p.signature_count AS p_weight,
                   r.title    AS r_title, r.body AS r_body,
                   r.url      AS r_url,   r.score AS r_weight,
                   r.subreddit AS r_subreddit
            FROM matches m
            LEFT JOIN petitions    p ON m.signal_type='petition' AND m.signal_id = p.id
            LEFT JOIN reddit_posts r ON m.signal_type='reddit'   AND m.signal_id = r.id
            WHERE m.agenda_item_id = ?
            ORDER BY m.similarity DESC
            LIMIT ?
            """,
            (item_id, n),
        ).fetchall()

    out = []
    for r in rows:
        if r["signal_type"] == "petition":
            snippet = (r["p_desc"] or r["p_title"] or "")[:300]
            out.append({
                "signal_type": "petition",
                "title": r["p_title"],
                "snippet": snippet,
                "url": r["p_url"],
                "weight": r["p_weight"] or 0,
                "similarity": r["similarity"],
                "subreddit": None,
            })
        else:
            snippet = (r["r_body"] or r["r_title"] or "")[:300]
            out.append({
                "signal_type": "reddit",
                "title": r["r_title"],
                "snippet": snippet,
                "url": r["r_url"],
                "weight": r["r_weight"] or 0,
                "similarity": r["similarity"],
                "subreddit": r["r_subreddit"],
            })
    return out


def list_topics() -> list[str]:
    """Distinct topics actually present in the data."""
    seen: set[str] = set()
    with connect() as conn:
        for row in conn.execute("SELECT topics FROM agenda_items WHERE topics IS NOT NULL"):
            for t in _parse_json_list(row["topics"]):
                if isinstance(t, str) and t.strip():
                    seen.add(t.strip())
    return sorted(seen)


def list_stages() -> list[str]:
    """Distinct stages actually present."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT stage FROM agenda_items WHERE stage IS NOT NULL AND stage != '' ORDER BY stage"
        ).fetchall()
    return [r["stage"] for r in rows]
