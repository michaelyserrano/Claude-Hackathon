# Cambridge Civic Feed

A plain-English, filterable feed of Cambridge City Council agenda items, ranked by a Buzz Score derived from matched Change.org petitions and Reddit threads.

See [docs/superpowers/specs/2026-05-03-cambridge-civic-feed-design.md](docs/superpowers/specs/2026-05-03-cambridge-civic-feed-design.md) for the full design.

## Layout

```
ingest/   Offline pipeline: scrape, enrich with LLM, match signals to agenda items.
app/      FastAPI server: reads SQLite, renders feed + detail pages.
data/     SQLite database lives here (gitignored).
docs/     Design docs and specs.
```

## Workstreams (for parallel work)

These can be built independently. Each has a clear input/output contract via the SQLite schema in `schema.sql`.

1. **Agenda scraper** — `ingest/scrape_agenda.py` → writes raw rows to `agenda_items`.
2. **Change.org scraper** — `ingest/scrape_changeorg.py` → writes raw rows to `petitions`.
3. **Reddit scraper** — `ingest/scrape_reddit.py` → writes raw rows to `reddit_posts`.
4. **LLM enrichment** — `ingest/enrich.py` → fills `summary`, `topics`, `embedding` on existing rows.
5. **Matching + Buzz Score** — `ingest/match.py` → writes `matches` and `buzz_scores`.
6. **Web app** — `app/main.py` + templates → reads SQLite, serves UI.

## Run (once everything is built)

```bash
pip install -r requirements.txt
python -m ingest.init_db          # create schema
python -m ingest.scrape_agenda    # populate
python -m ingest.scrape_changeorg
python -m ingest.scrape_reddit
python -m ingest.enrich
python -m ingest.match
uvicorn app.main:app --reload
```
