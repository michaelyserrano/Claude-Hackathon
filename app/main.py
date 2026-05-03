"""FastAPI server. Two HTML routes + JSON endpoints.

Run:
    uvicorn app.main:app --reload
"""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Cambridge Civic Feed")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "")


@app.get("/", response_class=HTMLResponse)
def feed(request: Request, topic: str | None = None,
         stage: str | None = None, sort: str = "buzz"):
    """Render the feed page with current filter state."""
    items = db.list_items(topic=topic, stage=stage, sort=sort)
    return templates.TemplateResponse(
        "feed.html",
        {
            "request": request,
            "items": items,
            "topics": db.list_topics(),
            "stages": db.list_stages(),
            "selected_topic": topic,
            "selected_stage": stage,
            "selected_sort": sort,
        },
    )


@app.get("/item/{item_id}", response_class=HTMLResponse)
def detail(request: Request, item_id: str):
    """Render the detail page: summary + 'Why people care' (top 3 matches)."""
    item = db.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    matches = db.get_top_matches(item_id, n=3)
    return templates.TemplateResponse(
        "detail.html",
        {"request": request, "item": item, "matches": matches},
    )


@app.get("/api/items")
def api_items(request: Request, topic: str | None = None,
              stage: str | None = None, sort: str = "buzz"):
    """Returns JSON if Accept: application/json, otherwise the cards partial HTML."""
    items = db.list_items(topic=topic, stage=stage, sort=sort)
    if _wants_json(request):
        return JSONResponse({"items": items})
    return templates.TemplateResponse(
        "_cards.html", {"request": request, "items": items}
    )


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
