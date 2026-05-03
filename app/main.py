"""FastAPI server. Two HTML routes + one JSON endpoint for htmx filter updates.

Run:
    uvicorn app.main:app --reload
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db

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
