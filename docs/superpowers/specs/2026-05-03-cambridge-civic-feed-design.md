# Cambridge Civic Feed — Design

**Date:** 2026-05-03
**Status:** Approved (brainstorm), pending implementation plan
**Working name:** TBD

## 1. Problem & Product

### Problem

Cambridge City Council publishes dense ~10-page agendas every week covering 50+ items: budget appropriations, policy orders, ordinances, awaiting reports. Residents who would care can't realistically read these. Meanwhile, the same residents are signing petitions on Change.org and posting on r/Cambridge about issues they don't realize are being voted on next Monday.

### User

The **Civic Lurker** — a Cambridge resident who vaguely cares about local government but never reads agendas. Goal: get them informed in 60 seconds.

### Product

A web app that gives the Civic Lurker a 60-second scan of what city hall is doing, ranked by what residents actually care about.

The single primary screen is a **filterable feed of agenda items** from the rolling 2-month window. Each card shows:

- Plain-English summary (one paragraph, LLM-generated)
- Topic tags (Housing, Transit, etc.)
- Stage badge (Upcoming Vote / Passed / Awaiting Report / etc.)
- **Buzz Score** — heat indicator backed by matched Change.org petitions and Reddit threads

Filters across the top: **Topic**, **Stage**, **Buzz Score** (sort).

Click a card → detail view: summary plus *"Why people care"* — quoted snippets from the actual petitions and top Reddit comments that matched.

### Deferred

- Dedicated **Gap UI** surface (what residents care about that *isn't* on the agenda) — TBD; design later.

### Explicit non-goals (YAGNI)

- User accounts, auth, saved filters
- Email digests, notifications
- Mobile-responsive layout (desktop web only)
- Pagination (small dataset)
- Real-time refresh
- Scheduled re-ingest (manual run before demo)

## 2. Architecture

One Python process, three layers:

```
ingest/  (run manually, populates SQLite)
  scrape_agenda.py     → agenda items
  scrape_changeorg.py  → petitions
  scrape_reddit.py     → posts/comments
  enrich.py            → LLM pipeline (summarize, tag, embed)
  match.py             → similarity matching + buzz scores
        ↓
SQLite file (single .db)
        ↓
app/  (FastAPI server)
  main.py              → routes
  db.py                → read-only queries
  templates/           → Jinja (feed + detail)
  static/              → minimal CSS
```

### Two flows

1. **Ingest (offline, before demo):** scrape → store raw → enrich with LLM → match signals → write Buzz Scores to DB.
2. **Serve (live during demo):** FastAPI reads SQLite, renders feed and detail pages. No LLM calls at request time.

### Why this shape

- Offline ingest means the demo never waits on a slow scrape or flaky third party.
- Expensive work (embeddings, summaries) happens once, cached in DB.
- SQLite is one file → trivial to commit a snapshot as a fallback for the demo.
- No background jobs, queues, or auth.

### Trade-off

Data is only as fresh as the last ingest run. Acceptable for hackathon; would need a cron in production.

## 3. Data Model

SQLite. Embeddings stored as JSON blobs; cosine similarity computed in Python (dataset is small enough — ~50 items × a few hundred signals).

```sql
agenda_items
  id              TEXT PRIMARY KEY   -- e.g. "CMA-2026-99"
  meeting_date    DATE
  title           TEXT
  raw_text        TEXT               -- original agenda blurb
  summary         TEXT               -- LLM plain-English (1 paragraph)
  topics          JSON               -- ["Housing", "Budget"]
  stage           TEXT               -- Upcoming | Passed | Awaiting Report | ...
  dollar_amount   INTEGER NULL       -- parsed from text if present
  sponsors        JSON               -- ["Councillor Nolan", ...]
  embedding       JSON               -- vector

petitions
  id              TEXT PRIMARY KEY   -- change.org slug
  url             TEXT
  title           TEXT
  description     TEXT
  signature_count INTEGER
  topics          JSON
  embedding       JSON
  scraped_at      TIMESTAMP

reddit_posts
  id              TEXT PRIMARY KEY   -- reddit post id
  url             TEXT
  subreddit       TEXT
  title           TEXT
  body            TEXT               -- post body + concatenated top comments
  score           INTEGER            -- upvotes
  comment_count   INTEGER
  created_at      TIMESTAMP
  topics          JSON
  embedding       JSON

matches             -- which signals back which agenda item
  agenda_item_id  TEXT
  signal_type     TEXT               -- 'petition' | 'reddit'
  signal_id       TEXT
  similarity      REAL               -- cosine, 0-1
  PRIMARY KEY (agenda_item_id, signal_type, signal_id)

buzz_scores         -- precomputed per agenda item
  agenda_item_id  TEXT PRIMARY KEY
  score           REAL               -- normalized 0-100
  petition_count  INTEGER
  reddit_count    INTEGER
  top_signal_id   TEXT               -- for headline quote in detail view
```

### Buzz Score formula

```
raw = Σ (petition.signature_count × similarity)  for matches above threshold
    + Σ (reddit.score × similarity × 5)          (reddit weighted lower per unit)
score = log-normalize across all items, scale to 0–100
```

### Match threshold

A signal matches an agenda item only if **topic tags overlap** AND **cosine similarity ≥ 0.65**. Both filters required (per Q8: topic-filter + semantic ranking).

### Topic taxonomy (fixed)

`Housing`, `Transit`, `Climate`, `Schools`, `Public Safety`, `Budget/Spending`, `Parks`, `Civic Process`, `Other`.

## 4. Ingest Pipeline

Five scripts in `ingest/`, run in order. Each is independent and idempotent.

### 4.1 `scrape_agenda.py`

- Source: `cambridgema.gov` city council meetings page → list of recent meeting agendas (last 2 months).
- Fallback: parse the published PDF using `pdfplumber` if HTML structure changes. Agendas are well-structured (numbered items, bold IDs like `CMA 2026-118`).
- Extract: id, meeting_date, title/blurb, sponsors, stage. Stage is inferred from section headers — `POLICY ORDERS` → Upcoming; `AWAITING REPORT LIST` → Awaiting Report; `UNFINISHED BUSINESS` with `ELIGIBLE TO BE ADOPTED` date → Upcoming; passed items → Passed.
- Write raw rows to `agenda_items` (no LLM yet).

### 4.2 `scrape_changeorg.py`

- Search Change.org for "Cambridge MA" + topic seed terms (housing, transit, climate, schools, parking).
- Filter: open petitions, `signature_count ≥ 100`.
- Tool: Playwright (Change.org is JS-heavy and bot-aware) or `requests` against embedded JSON if accessible.
- Write to `petitions`.

### 4.3 `scrape_reddit.py`

- PRAW client. Subreddits: `r/Cambridge`, `r/CambridgeMA`, `r/boston` (filter posts mentioning Cambridge).
- Window: last 60 days. Threshold: post `score ≥ 10` OR `comment_count ≥ 5`.
- Pull top comments per post, concatenate into `body`.
- Write to `reddit_posts`.

### 4.4 `enrich.py`

Single Anthropic client. For each agenda item, petition, and reddit post:

- **Topic-tag** via Claude with the fixed taxonomy → JSON list.
- **Embed** with Voyage or OpenAI embeddings (whichever key is available).
- For agenda items only: **summarize** into one plain-English paragraph; parse `dollar_amount` via regex.

Batch where possible (one Claude call covers many tag-or-summarize requests).

### 4.5 `match.py`

- For each agenda item, compute cosine similarity vs. every petition and reddit post.
- Keep matches where topics overlap AND similarity ≥ 0.65.
- Write `matches` rows.
- Compute and write `buzz_scores` per the formula in §3.

### Order

`scrape_agenda → scrape_changeorg → scrape_reddit → enrich → match`

### Risk

Change.org scraping is the most likely failure (anti-bot). If it fails during the hackathon, the project still works with Reddit alone — degrade gracefully to a Reddit-only Buzz Score.

## 5. Web App & UI

One FastAPI app, two routes plus a JSON endpoint, two Jinja templates. Tailwind via CDN, htmx for filter updates. No build step. Desktop web only.

### Routes

```
GET  /                  → feed page
GET  /item/{id}         → detail page
GET  /api/items?...     → JSON, used by feed for filter updates
```

### Feed page (`/`)

Single column, header with filters, list of cards.

```
┌─────────────────────────────────────────┐
│  Cambridge Council, in Plain English    │
│  Showing 47 items from last 2 months    │
├─────────────────────────────────────────┤
│ [Topic ▾] [Stage ▾] [Sort: Buzz ▾]      │
├─────────────────────────────────────────┤
│ 🔥🔥🔥  HOUSING · UPCOMING VOTE         │
│ City may borrow $14M to fix Gold Star   │
│ Mothers Park — vote May 18              │
│ 3,400 signatures · 12 reddit threads    │
├─────────────────────────────────────────┤
│ 🔥        TRANSIT · AWAITING REPORT     │
│ Council asked staff to study restricting│
│ resident parking permits in new TOD...  │
│ 220 signatures · 4 reddit threads       │
└─────────────────────────────────────────┘
```

- Buzz Score → 0–3 🔥 emoji (≥66 = 3, ≥33 = 2, >0 = 1, 0 = none).
- Filters update via htmx → `/api/items` (no full page reload).
- Sort options: Buzz (default), Most Recent, Biggest $.

### Detail page (`/item/{id}`)

```
┌─────────────────────────────────────────┐
│  ← Back                                 │
│  HOUSING · UPCOMING VOTE · May 18, 2026 │
│  City may borrow $14M to fix Gold Star  │
│  Mothers Park                           │
├─────────────────────────────────────────┤
│  Plain-English summary                  │
│  [LLM-generated paragraph]              │
├─────────────────────────────────────────┤
│  🔥 Why people care                     │
│                                         │
│  📝 Petition · 3,400 signatures         │
│  "Save Gold Star Mothers Park from..."  │
│  → change.org/p/...                     │
│                                         │
│  💬 r/Cambridge · 142 upvotes           │
│  "Anyone know what's happening with     │
│   Gold Star? My kid plays there..."     │
│  → reddit.com/r/Cambridge/...           │
├─────────────────────────────────────────┤
│  Original agenda text (collapsed ▾)     │
└─────────────────────────────────────────┘
```

- Top 3 matched signals shown, sorted by `similarity × signal_weight`.
- Snippets: petition description first 200 chars, or Reddit post title + body excerpt.

### Stack

- Python 3.11+, FastAPI, Jinja2
- SQLite (read-only at request time)
- Tailwind CDN, htmx
- Anthropic SDK for summarization + tagging
- Voyage or OpenAI for embeddings
- PRAW for Reddit, Playwright or requests for Change.org, pdfplumber as agenda fallback

## 6. Open Questions / Deferred

- **Gap UI** — surfacing top resident concerns *not* on the agenda. Approach (inline tag, separate panel, alignment score) deferred.
- **Project name** — TBD.
- **Embedding provider** — pick whichever API key is available at hackathon time.
- **Hosting** — local-only for the demo is fine; deploy if time permits.

## 7. Demo Flow (target)

1. Open the feed at `localhost:8000`.
2. Show the highest-buzz item — talk through the summary, click into detail, show a real petition + reddit thread quoted underneath.
3. Filter by topic (e.g., Housing) → show how the feed re-ranks.
4. Filter by Stage = Upcoming Vote → "here's what residents could still influence this week."
5. Land on a low-buzz, high-dollar item to make the implicit gap argument.
