"""Shared SQLite helpers for ingest scripts."""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "civic.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema() -> None:
    conn = connect()
    try:
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


def upsert_agenda_item(conn: sqlite3.Connection, item: dict) -> None:
    """Insert or replace an agenda_items row.

    Preserves summary/topics/embedding from any prior row with the same id so
    re-scraping doesn't blow away enrichment work.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO agenda_items
          (id, meeting_date, title, raw_text, summary, topics, stage,
           dollar_amount, sponsors, embedding)
        VALUES (
          :id, :meeting_date, :title, :raw_text,
          (SELECT summary   FROM agenda_items WHERE id = :id),
          (SELECT topics    FROM agenda_items WHERE id = :id),
          :stage, :dollar_amount, :sponsors,
          (SELECT embedding FROM agenda_items WHERE id = :id)
        )
        """,
        {
            "id": item["id"],
            "meeting_date": item["meeting_date"],
            "title": item["title"],
            "raw_text": item["raw_text"],
            "stage": item["stage"],
            "dollar_amount": item.get("dollar_amount"),
            "sponsors": json.dumps(item.get("sponsors") or []),
        },
    )


def upsert_petition(conn, petition: dict) -> None:  # filled in scrape_changeorg
    ...


def upsert_reddit_post(conn, post: dict) -> None:  # filled in scrape_reddit
    ...


def get_unembedded(conn, table: str) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT * FROM {table} WHERE embedding IS NULL"
    ).fetchall()


def write_embedding(conn, table: str, row_id: str, embedding: list[float]) -> None:
    conn.execute(
        f"UPDATE {table} SET embedding = ? WHERE id = ?",
        (json.dumps(embedding), row_id),
    )


def write_topics(conn, table: str, row_id: str, topics: list[str]) -> None:
    conn.execute(
        f"UPDATE {table} SET topics = ? WHERE id = ?",
        (json.dumps(topics), row_id),
    )
