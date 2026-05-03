"""FastAPI server. Two HTML routes + JSON endpoints.

Run:
    uvicorn app.main:app --reload
"""

import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Cambridge Civic Feed")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
_static = BASE_DIR / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=_static), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


_TOPIC_MAP = {
    "housing":         "housing",
    "transit":         "transport",
    "climate":         "env",
    "schools":         "edu",
    "public safety":   "safety",
    "budget/spending": "budget",
    "parks":           "env",
    # "civic process" and "other" have no frontend equivalent — omit
}


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _map_topics(raw_json: str | None) -> list[str]:
    seen: set[str] = set()
    result = []
    for t in _parse_tags(raw_json):
        if not isinstance(t, str):
            continue
        slug = _TOPIC_MAP.get(t.lower())
        if slug and slug not in seen:
            result.append(slug)
            seen.add(slug)
    return result


def _format_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso[:10])
        return d.strftime("%b %-d")
    except (ValueError, TypeError):
        return iso or ""


def _truncate(text: str | None, n: int = 500) -> str:
    if not text:
        return ""
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0] + "…"


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


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Serve the standalone dashboard HTML."""
    return FileResponse(BASE_DIR / "static" / "dashboard.html")


@app.get("/api/feed")
def api_feed():
    conn = db.connect()
    try:
        gov_rows = conn.execute("""
            SELECT a.id, a.title, a.topics, a.agenda_url, a.summary,
                   a.meeting_date, a.stage,
                   COALESCE(b.score, 0) AS buzz_score,
                   COALESCE(b.petition_count, 0) + COALESCE(b.reddit_count, 0) AS signal_count
            FROM agenda_items a
            LEFT JOIN buzz_scores b ON b.agenda_item_id = a.id
            ORDER BY buzz_score DESC, a.meeting_date DESC
        """).fetchall()

        petition_rows = conn.execute(
            "SELECT id, url, title, description, signature_count, topics FROM petitions"
        ).fetchall()
        reddit_rows = conn.execute(
            "SELECT id, url, title, body, score, subreddit, topics FROM reddit_posts"
        ).fetchall()

        all_raw = (
            [r["signature_count"] or 0 for r in petition_rows] +
            [r["score"] or 0 for r in reddit_rows]
        )
        max_raw = max(all_raw) if all_raw else 1
        if max_raw <= 0:
            max_raw = 1

        def _norm(v):
            return round((v or 0) / max_raw * 100)

        gov_items = []
        for r in gov_rows:
            tags    = _map_topics(r["topics"])
            primary = tags[0] if tags else "budget"
            if not tags:
                tags = [primary]
            n = r["signal_count"]
            score_label = f"{n} signal{'s' if n != 1 else ''}" if n else (r["stage"] or "")
            gov_items.append({
                "id":         r["id"],
                "title":      r["title"],
                "primaryTag": primary,
                "tags":       tags,
                "score":      score_label,
                "date":       _format_date(r["meeting_date"]),
                "summary":    r["summary"] or "",
                "url":        r["agenda_url"] or "",
            })

        pub_items = []
        for r in petition_rows:
            tags    = _map_topics(r["topics"])
            primary = tags[0] if tags else "housing"
            if not tags:
                tags = [primary]
            sig = r["signature_count"] or 0
            pub_items.append({
                "id":         r["id"],
                "title":      r["title"],
                "primaryTag": primary,
                "tags":       tags,
                "src":        "changeorg",
                "buzz":       _norm(sig),
                "score":      f"{sig:,} signatures",
                "summary":    _truncate(r["description"]),
                "url":        r["url"] or "",
            })

        for r in reddit_rows:
            tags    = _map_topics(r["topics"])
            primary = tags[0] if tags else "safety"
            if not tags:
                tags = [primary]
            upvotes = r["score"] or 0
            pub_items.append({
                "id":         r["id"],
                "title":      r["title"],
                "primaryTag": primary,
                "tags":       tags,
                "src":        "reddit",
                "buzz":       _norm(upvotes),
                "score":      f"{upvotes:,} upvotes",
                "summary":    _truncate(r["body"]),
                "url":        r["url"] or "",
            })

        pub_items.sort(key=lambda x: x["buzz"], reverse=True)
        return JSONResponse({"govItems": gov_items, "pubItems": pub_items})
    finally:
        conn.close()
