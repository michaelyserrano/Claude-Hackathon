"""Match resident signals to agenda items and compute Buzz Scores.

Inputs: requires enrich.py to have populated topics + embedding on rows.
Outputs: replaces the matches and buzz_scores tables.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from ingest import db

MATCH_THRESHOLD = 0.65
REDDIT_WEIGHT = 5.0


@dataclass(frozen=True)
class AgendaItem:
    id: str
    topics: tuple[str, ...]
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class Signal:
    id: str
    signal_type: str
    topics: tuple[str, ...]
    embedding: tuple[float, ...]
    weight: int


@dataclass(frozen=True)
class Match:
    agenda_item_id: str
    signal_type: str
    signal_id: str
    similarity: float


@dataclass(frozen=True)
class BuzzScore:
    agenda_item_id: str
    score: float
    petition_count: int
    reddit_count: int
    top_signal_id: str | None
    raw_score: float


def _normalized_topic(topic: str) -> str:
    return " ".join(topic.casefold().split())


def _parse_topics(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, list):
        return None
    topics = tuple(str(topic).strip() for topic in decoded if str(topic).strip())
    return topics or None


def _parse_embedding(value: str | None) -> tuple[float, ...] | None:
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, list) or not decoded:
        return None
    try:
        embedding = tuple(float(component) for component in decoded)
    except (TypeError, ValueError):
        return None
    return embedding


def _valid_row(row: sqlite3.Row, skipped: Counter[str], label: str) -> bool:
    if _parse_topics(row["topics"]) is None:
        skipped[f"{label}:missing_topics"] += 1
        return False
    if _parse_embedding(row["embedding"]) is None:
        skipped[f"{label}:missing_embedding"] += 1
        return False
    return True


def load_agenda_item_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT id FROM agenda_items ORDER BY meeting_date, id").fetchall()
    return [row["id"] for row in rows]


def load_agenda_items(conn: sqlite3.Connection) -> tuple[list[AgendaItem], Counter[str]]:
    rows = conn.execute(
        """
        SELECT id, topics, embedding
        FROM agenda_items
        ORDER BY meeting_date, id
        """
    ).fetchall()
    skipped: Counter[str] = Counter()
    items: list[AgendaItem] = []
    for row in rows:
        if not _valid_row(row, skipped, "agenda"):
            continue
        items.append(
            AgendaItem(
                id=row["id"],
                topics=_parse_topics(row["topics"]) or (),
                embedding=_parse_embedding(row["embedding"]) or (),
            )
        )
    return items, skipped


def load_signals(conn: sqlite3.Connection) -> tuple[list[Signal], Counter[str]]:
    skipped: Counter[str] = Counter()
    signals: list[Signal] = []

    petition_rows = conn.execute(
        """
        SELECT id, topics, embedding, COALESCE(signature_count, 0) AS weight
        FROM petitions
        """
    ).fetchall()
    for row in petition_rows:
        if not _valid_row(row, skipped, "petition"):
            continue
        signals.append(
            Signal(
                id=row["id"],
                signal_type="petition",
                topics=_parse_topics(row["topics"]) or (),
                embedding=_parse_embedding(row["embedding"]) or (),
                weight=max(0, int(row["weight"] or 0)),
            )
        )

    reddit_rows = conn.execute(
        """
        SELECT id, topics, embedding, COALESCE(score, 0) AS weight
        FROM reddit_posts
        """
    ).fetchall()
    for row in reddit_rows:
        if not _valid_row(row, skipped, "reddit"):
            continue
        signals.append(
            Signal(
                id=row["id"],
                signal_type="reddit",
                topics=_parse_topics(row["topics"]) or (),
                embedding=_parse_embedding(row["embedding"]) or (),
                weight=max(0, int(row["weight"] or 0)),
            )
        )

    return signals, skipped


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    left = tuple(a)
    right = tuple(b)
    if len(left) != len(right) or not left:
        return 0.0

    dot = sum(x * y for x, y in zip(left, right))
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(y * y for y in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def topics_overlap(a: Iterable[str], b: Iterable[str]) -> bool:
    left = {_normalized_topic(topic) for topic in a}
    right = {_normalized_topic(topic) for topic in b}
    return bool(left & right)


def build_signal_topic_index(signals: Iterable[Signal]) -> dict[str, list[Signal]]:
    index: dict[str, list[Signal]] = defaultdict(list)
    for signal in signals:
        for topic in {_normalized_topic(topic) for topic in signal.topics}:
            index[topic].append(signal)
    return dict(index)


def candidate_signals(
    item: AgendaItem, signals_by_topic: dict[str, list[Signal]]
) -> list[Signal]:
    candidates: dict[tuple[str, str], Signal] = {}
    for topic in {_normalized_topic(topic) for topic in item.topics}:
        for signal in signals_by_topic.get(topic, []):
            candidates[(signal.signal_type, signal.id)] = signal
    return list(candidates.values())


def compute_matches(
    items: Iterable[AgendaItem],
    signals: Iterable[Signal],
    threshold: float = MATCH_THRESHOLD,
) -> list[Match]:
    signals_by_topic = build_signal_topic_index(signals)
    matches: list[Match] = []
    for item in items:
        for signal in candidate_signals(item, signals_by_topic):
            similarity = cosine(item.embedding, signal.embedding)
            if similarity >= threshold:
                matches.append(
                    Match(
                        agenda_item_id=item.id,
                        signal_type=signal.signal_type,
                        signal_id=signal.id,
                        similarity=similarity,
                    )
                )
    return matches


def signal_contribution(match: Match, signal: Signal) -> float:
    if signal.signal_type == "reddit":
        return signal.weight * match.similarity * REDDIT_WEIGHT
    return signal.weight * match.similarity


def compute_buzz(
    agenda_item_ids: Iterable[str],
    matches: Iterable[Match],
    signals: Iterable[Signal],
) -> dict[str, BuzzScore]:
    signal_lookup = {(signal.signal_type, signal.id): signal for signal in signals}
    raw_by_item = {item_id: 0.0 for item_id in agenda_item_ids}
    petition_counts = {item_id: 0 for item_id in raw_by_item}
    reddit_counts = {item_id: 0 for item_id in raw_by_item}
    top_contribution = {item_id: 0.0 for item_id in raw_by_item}
    top_signal_id: dict[str, str | None] = {item_id: None for item_id in raw_by_item}

    for match in matches:
        if match.agenda_item_id not in raw_by_item:
            continue
        signal = signal_lookup.get((match.signal_type, match.signal_id))
        if signal is None:
            continue
        contribution = signal_contribution(match, signal)
        raw_by_item[match.agenda_item_id] += contribution
        if match.signal_type == "petition":
            petition_counts[match.agenda_item_id] += 1
        elif match.signal_type == "reddit":
            reddit_counts[match.agenda_item_id] += 1
        if contribution > top_contribution[match.agenda_item_id]:
            top_contribution[match.agenda_item_id] = contribution
            top_signal_id[match.agenda_item_id] = f"{match.signal_type}:{match.signal_id}"

    log_by_item = {
        item_id: math.log1p(max(0.0, raw_score))
        for item_id, raw_score in raw_by_item.items()
    }
    max_log = max(log_by_item.values(), default=0.0)

    scores: dict[str, BuzzScore] = {}
    for item_id, raw_score in raw_by_item.items():
        normalized = 0.0 if max_log == 0 else 100.0 * log_by_item[item_id] / max_log
        scores[item_id] = BuzzScore(
            agenda_item_id=item_id,
            score=normalized,
            petition_count=petition_counts[item_id],
            reddit_count=reddit_counts[item_id],
            top_signal_id=top_signal_id[item_id],
            raw_score=raw_score,
        )
    return scores


def write_matches(conn: sqlite3.Connection, matches: Iterable[Match]) -> None:
    conn.execute("DELETE FROM matches")
    conn.executemany(
        """
        INSERT INTO matches (agenda_item_id, signal_type, signal_id, similarity)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                match.agenda_item_id,
                match.signal_type,
                match.signal_id,
                match.similarity,
            )
            for match in matches
        ],
    )


def write_buzz_scores(
    conn: sqlite3.Connection, scores: dict[str, BuzzScore]
) -> None:
    conn.execute("DELETE FROM buzz_scores")
    conn.executemany(
        """
        INSERT INTO buzz_scores
          (agenda_item_id, score, petition_count, reddit_count, top_signal_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                score.agenda_item_id,
                score.score,
                score.petition_count,
                score.reddit_count,
                score.top_signal_id,
            )
            for score in scores.values()
        ],
    )


def main() -> None:
    conn = db.connect()
    try:
        agenda_item_ids = load_agenda_item_ids(conn)
        agenda_items, skipped_agenda = load_agenda_items(conn)
        signals, skipped_signals = load_signals(conn)
        matches = compute_matches(agenda_items, signals)
        scores = compute_buzz(agenda_item_ids, matches, signals)

        with conn:
            write_matches(conn, matches)
            write_buzz_scores(conn, scores)

        signal_counts = Counter(signal.signal_type for signal in signals)
        max_raw = max((score.raw_score for score in scores.values()), default=0.0)
        nonzero_buzz = sum(1 for score in scores.values() if score.score > 0)
        skipped = skipped_agenda + skipped_signals
        skipped_summary = ", ".join(
            f"{key}={value}" for key, value in sorted(skipped.items())
        ) or "none"
        print(
            "matched "
            f"{len(matches)} pairs; "
            f"agenda_items={len(agenda_items)}/{len(agenda_item_ids)} valid; "
            f"petitions={signal_counts['petition']} valid; "
            f"reddit={signal_counts['reddit']} valid; "
            f"nonzero_buzz={nonzero_buzz}; "
            f"max_raw={max_raw:.2f}; "
            f"skipped={skipped_summary}",
            file=sys.stderr,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
