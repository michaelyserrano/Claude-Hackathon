"""LLM enrichment pass: summarize, topic-tag, embed.

Reads rows missing summary/topics/embedding and fills them in. Idempotent:
re-running only processes rows still missing fields.

Models:
  - Claude Haiku 4.5 for summaries + topic tagging (cheap + fast).
  - OpenAI text-embedding-3-small for embeddings (1536 dims).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from typing import Any

from dotenv import load_dotenv

from ingest.db import connect, write_embedding, write_topics


def _retry_on_lock(fn, *args, attempts=10, delay=2.0):
    """Retry a write on sqlite3 'database is locked' errors. Other writers
    (e.g. a parallel scrape_reddit session) can hold the write lock for >30s,
    so we retry with backoff instead of failing the whole enrichment run."""
    for i in range(attempts):
        try:
            return fn(*args)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or i == attempts - 1:
                raise
            time.sleep(delay)
    return None

load_dotenv()

TOPIC_TAXONOMY = [
    "Housing",
    "Transit",
    "Climate",
    "Schools",
    "Public Safety",
    "Budget/Spending",
    "Parks",
    "Civic Process",
    "Other",
]

CLAUDE_MODEL = "claude-haiku-4-5"
EMBEDDING_MODEL = "text-embedding-3-small"

TAG_BATCH = 20
EMBED_BATCH = 100

_anthropic = None
_openai = None


def _claude():
    global _anthropic
    if _anthropic is None:
        import anthropic
        _anthropic = anthropic.Anthropic()
    return _anthropic


def _openai_client():
    global _openai
    if _openai is None:
        from openai import OpenAI
        _openai = OpenAI()
    return _openai


# ----- text field per table (used for tagging + embedding) -----
TEXT_EXPR = {
    "agenda_items": "COALESCE(summary, raw_text, title)",
    "petitions": "title || ' ' || COALESCE(description, '')",
    "reddit_posts": "title || ' ' || COALESCE(body, '')",
}


# ============================================================
# 1) Summaries (agenda items only)
# ============================================================

SUMMARY_SYSTEM = """You write 1-paragraph plain-English summaries of Cambridge, MA city council agenda items for residents who don't follow local politics closely.

Rules:
- 2-4 sentences, one paragraph.
- Concretely explain what is being decided and what passing would mean in practice.
- Mention the dollar amount if one is present.
- No jargon; no acronyms without explanation.
- Do not start with "This item" or "This agenda item" — state what's happening directly."""


def summarize_agenda_items(conn) -> None:
    rows = conn.execute(
        "SELECT id, title, raw_text, dollar_amount "
        "FROM agenda_items WHERE summary IS NULL"
    ).fetchall()
    if not rows:
        print("summaries: nothing to do")
        return
    print(f"summaries: {len(rows)} agenda items")

    for i, row in enumerate(rows, 1):
        text = (row["raw_text"] or row["title"] or "").strip()
        if not text:
            continue
        dollar_note = (
            f"\nDollar amount: ${row['dollar_amount']:,}"
            if row["dollar_amount"]
            else ""
        )
        msg = _claude().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            system=[
                {
                    "type": "text",
                    "text": SUMMARY_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Title: {row['title']}{dollar_note}\n\n"
                        f"Agenda text:\n{text[:4000]}\n\n"
                        "Write the summary."
                    ),
                }
            ],
        )
        summary = msg.content[0].text.strip()

        def _write_summary():
            conn.execute(
                "UPDATE agenda_items SET summary = ? WHERE id = ?",
                (summary, row["id"]),
            )
            conn.commit()

        _retry_on_lock(_write_summary)
        if i % 10 == 0:
            print(f"  summarized {i}/{len(rows)}")
    conn.commit()


# ============================================================
# 2) Topic tagging (all three tables)
# ============================================================

TAG_SYSTEM = (
    "You tag civic items with 1-3 topics from a fixed taxonomy.\n\n"
    f"Taxonomy: {json.dumps(TOPIC_TAXONOMY)}\n\n"
    "Use ONLY labels from the taxonomy. Pick 1-3 that best fit. "
    'Use "Other" only if nothing else fits.\n\n'
    "ORDERING IS SIGNIFICANT: the first tag MUST be the single best-fitting "
    "primary topic — the one a reader should see as the headline category. "
    "Any additional tags are secondary, ordered by relevance.\n\n"
    'Output strict JSON: a list of {"id": "...", "topics": ["...", ...]} '
    "where topics[0] is the primary topic. No prose, no code fences."
)


def _parse_json_list(text: str) -> list[dict] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def tag_topics(conn, table: str) -> None:
    text_expr = TEXT_EXPR[table]
    rows = conn.execute(
        f"SELECT id, {text_expr} AS text FROM {table} WHERE topics IS NULL"
    ).fetchall()
    if not rows:
        print(f"topics ({table}): nothing to do")
        return
    print(f"topics ({table}): {len(rows)} rows")

    valid = set(TOPIC_TAXONOMY)
    for i in range(0, len(rows), TAG_BATCH):
        batch = rows[i : i + TAG_BATCH]
        items = [
            {"id": r["id"], "text": (r["text"] or "")[:1500]} for r in batch
        ]
        msg = _claude().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": TAG_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Tag these items. Return JSON only.\n\n"
                        + json.dumps(items)
                    ),
                }
            ],
        )
        parsed = _parse_json_list(msg.content[0].text)
        if parsed is None:
            print(f"  warning: bad JSON in batch starting at {i}, skipping")
            continue
        for item in parsed:
            if not isinstance(item, dict) or "id" not in item:
                continue
            topics = [t for t in item.get("topics", []) if t in valid]
            if not topics:
                topics = ["Other"]
            _retry_on_lock(write_topics, conn, table, item["id"], topics[:3])
        _retry_on_lock(conn.commit)
        print(f"  tagged {min(i + TAG_BATCH, len(rows))}/{len(rows)}")


# ============================================================
# 3) Embeddings (all three tables)
# ============================================================


def embed_rows(conn, table: str) -> None:
    text_expr = TEXT_EXPR[table]
    rows = conn.execute(
        f"SELECT id, {text_expr} AS text FROM {table} WHERE embedding IS NULL"
    ).fetchall()
    if not rows:
        print(f"embeddings ({table}): nothing to do")
        return
    print(f"embeddings ({table}): {len(rows)} rows")

    client = _openai_client()
    for i in range(0, len(rows), EMBED_BATCH):
        batch = rows[i : i + EMBED_BATCH]
        inputs = [(r["text"] or "").strip()[:8000] or " " for r in batch]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=inputs)
        for row, datum in zip(batch, resp.data):
            _retry_on_lock(write_embedding, conn, table, row["id"], datum.embedding)
        _retry_on_lock(conn.commit)
        print(f"  embedded {min(i + EMBED_BATCH, len(rows))}/{len(rows)}")


# ============================================================
# Driver
# ============================================================


def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("error: ANTHROPIC_API_KEY missing in .env")
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("error: OPENAI_API_KEY missing in .env")

    conn = connect()
    try:
        summarize_agenda_items(conn)
        for table in ("agenda_items", "petitions", "reddit_posts"):
            tag_topics(conn, table)
        for table in ("agenda_items", "petitions", "reddit_posts"):
            embed_rows(conn, table)
    finally:
        conn.close()
    print("enrichment done.")


if __name__ == "__main__":
    main()
