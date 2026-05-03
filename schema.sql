-- Cambridge Civic Feed schema. Single SQLite file.
-- Embeddings stored as JSON-encoded float arrays; cosine similarity computed in Python.

CREATE TABLE IF NOT EXISTS agenda_items (
    id              TEXT PRIMARY KEY,        -- e.g. "CMA-2026-99"
    meeting_date    TEXT,                    -- ISO date "YYYY-MM-DD"
    agenda_url      TEXT,                    -- PrimeGov rendered agenda page for the meeting
    title           TEXT,
    raw_text        TEXT,                    -- original agenda blurb
    summary         TEXT,                    -- LLM plain-English (1 paragraph)
    topics          TEXT,                    -- JSON array, e.g. ["Housing","Budget"]
    stage           TEXT,                    -- Upcoming | Passed | Awaiting Report | ...
    dollar_amount   INTEGER,                 -- nullable, parsed from text
    sponsors        TEXT,                    -- JSON array of councillor names
    embedding       TEXT                     -- JSON array of floats
);

CREATE TABLE IF NOT EXISTS petitions (
    id              TEXT PRIMARY KEY,        -- change.org slug
    url             TEXT,
    title           TEXT,
    description     TEXT,
    signature_count INTEGER,
    topics          TEXT,                    -- JSON array
    embedding       TEXT,                    -- JSON array of floats
    scraped_at      TEXT                     -- ISO timestamp
);

CREATE TABLE IF NOT EXISTS reddit_posts (
    id              TEXT PRIMARY KEY,        -- reddit post id
    url             TEXT,
    subreddit       TEXT,
    title           TEXT,
    body            TEXT,                    -- post body + concatenated top comments
    score           INTEGER,                 -- upvotes
    comment_count   INTEGER,
    created_at      TEXT,                    -- ISO timestamp
    topics          TEXT,                    -- JSON array
    embedding       TEXT                     -- JSON array of floats
);

CREATE TABLE IF NOT EXISTS matches (
    agenda_item_id  TEXT,
    signal_type     TEXT,                    -- 'petition' | 'reddit'
    signal_id       TEXT,
    similarity      REAL,                    -- cosine, 0-1
    PRIMARY KEY (agenda_item_id, signal_type, signal_id)
);

CREATE TABLE IF NOT EXISTS buzz_scores (
    agenda_item_id  TEXT PRIMARY KEY,
    score           REAL,                    -- normalized 0-100
    petition_count  INTEGER,
    reddit_count    INTEGER,
    top_signal_id   TEXT                     -- for headline quote in detail view
);

CREATE TABLE IF NOT EXISTS meeting_transcripts (
    meeting_id      INTEGER PRIMARY KEY,     -- PrimeGov meeting id
    meeting_date    TEXT,                    -- ISO date "YYYY-MM-DD"
    title           TEXT,                    -- e.g. "Regular City Council Meeting"
    swagit_video_id INTEGER,                 -- numeric id from videoUrl
    source_url      TEXT,                    -- canonical Swagit transcript URL
    transcript      TEXT,                    -- full plain text (auto-generated)
    char_count      INTEGER,                 -- length(transcript), for quick filters
    fetched_at      TEXT                     -- ISO timestamp
);

CREATE INDEX IF NOT EXISTS idx_matches_agenda ON matches(agenda_item_id);
CREATE INDEX IF NOT EXISTS idx_agenda_meeting ON agenda_items(meeting_date);
CREATE INDEX IF NOT EXISTS idx_transcripts_date ON meeting_transcripts(meeting_date);
