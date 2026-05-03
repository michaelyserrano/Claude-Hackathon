"""FastAPI server. Two HTML routes + one JSON endpoint for htmx filter updates.

Run:
    uvicorn app.main:app --reload
"""

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Cambridge Civic Feed")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def feed(request: Request, topic: str | None = None,
         stage: str | None = None, sort: str = "buzz"):
    """Render the feed page with current filter state.

    Filters live in the URL so the page is bookmarkable.
    The card list itself is a partial that htmx swaps in on filter change
    via /api/items.
    """
    # TODO
    ...


@app.get("/item/{item_id}", response_class=HTMLResponse)
def detail(request: Request, item_id: str):
    """Render the detail page: summary + 'Why people care' (top 3 matches)."""
    # TODO
    ...


@app.get("/api/items")
def api_items(topic: str | None = None, stage: str | None = None,
              sort: str = "buzz"):
    """Returns the rendered cards partial as HTML for htmx, OR JSON if
    the client asks for it. Decide based on Accept header."""
    # TODO
    ...


@app.get("/api/feed")
def api_feed():
    """Frontend contract: {govItems: [...], pubItems: [...]}.

    Each item has: title, tags[], src, score, url.
    pubItems additionally have: buzz (raw signal weight).
    """
    conn = db.connect()
    try:
        gov_rows = conn.execute(
            """
            SELECT a.id, a.title, a.topics, a.agenda_url,
                   COALESCE(b.score, 0) AS score
            FROM agenda_items a
            LEFT JOIN buzz_scores b ON b.agenda_item_id = a.id
            ORDER BY score DESC, a.meeting_date DESC
            """
        ).fetchall()

        gov_items = [
            {
                "title": r["title"],
                "tags": _parse_tags(r["topics"]),
                "src": "gov",
                "score": r["score"],
                "url": r["agenda_url"],
            }
            for r in gov_rows
        ]

        petition_rows = conn.execute(
            "SELECT title, topics, url, signature_count FROM petitions"
        ).fetchall()
        reddit_rows = conn.execute(
            "SELECT title, topics, url, score FROM reddit_posts"
        ).fetchall()

        pub_items = [
            {
                "title": r["title"],
                "tags": _parse_tags(r["topics"]),
                "src": "petition",
                "buzz": r["signature_count"] or 0,
                "score": r["signature_count"] or 0,
                "url": r["url"],
            }
            for r in petition_rows
        ] + [
            {
                "title": r["title"],
                "tags": _parse_tags(r["topics"]),
                "src": "reddit",
                "buzz": r["score"] or 0,
                "score": r["score"] or 0,
                "url": r["url"],
            }
            for r in reddit_rows
        ]

        pub_items.sort(key=lambda x: x["buzz"], reverse=True)

        return JSONResponse({"govItems": gov_items, "pubItems": pub_items})
    finally:
        conn.close()
