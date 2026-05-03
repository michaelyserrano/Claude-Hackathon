"""Shared SQLite helpers for ingest scripts.

All ingest scripts read/write through this module. Keep it small — connection
management, JSON encode/decode for embeddings/topics, and a couple of upserts.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "civic.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def connect() -> sqlite3.Connection:
    """Open a connection with row factory set to sqlite3.Row."""
    # TODO
    ...


def init_schema() -> None:
    """Apply schema.sql. Idempotent (CREATE IF NOT EXISTS)."""
    # TODO
    ...


def upsert_agenda_item(conn, item: dict) -> None:
    """Insert or replace a row in agenda_items. `item` is a dict matching schema columns."""
    # TODO
    ...


def upsert_petition(conn, petition: dict) -> None:
    # TODO
    ...


def upsert_reddit_post(conn, post: dict) -> None:
    # TODO
    ...


def get_unembedded(conn, table: str) -> list[sqlite3.Row]:
    """Return rows in `table` where embedding IS NULL. Used by enrich.py."""
    # TODO
    ...


def write_embedding(conn, table: str, row_id: str, embedding: list[float]) -> None:
    # TODO
    ...


def write_topics(conn, table: str, row_id: str, topics: list[str]) -> None:
    # TODO
    ...
