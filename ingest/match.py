"""Match resident signals (petitions, reddit posts) to agenda items, then
compute Buzz Scores.

Algorithm (per design doc §3):

  For each agenda item A:
      For each signal S in petitions ∪ reddit_posts:
          If topics(A) ∩ topics(S) is non-empty
             AND cosine(embedding(A), embedding(S)) >= MATCH_THRESHOLD:
              Write a row to `matches` with the similarity.

  Then per agenda item, compute Buzz Score:
      raw  = Σ over petition matches:  signature_count × similarity
           + Σ over reddit matches:    score × similarity × REDDIT_WEIGHT
      score = log-normalize raw across all items, scale to 0–100.
      Write to `buzz_scores` along with petition_count, reddit_count, and
      top_signal_id (the single best-scoring matched signal — used by the
      detail page for the headline quote).

Inputs: requires enrich.py to have populated topics + embedding on every row.
Outputs: matches, buzz_scores tables.
"""

import math

MATCH_THRESHOLD = 0.65   # cosine similarity floor
REDDIT_WEIGHT = 5.0      # reddit upvote ≈ 5x weight of one petition signature * sim


# TODO entry points:
#
#   load_agenda_items(conn) -> list[dict]
#   load_signals(conn) -> list[dict]   # petitions + reddit_posts unified, with type tag
#
#   cosine(a: list[float], b: list[float]) -> float
#   topics_overlap(a: list[str], b: list[str]) -> bool
#
#   compute_matches(items, signals) -> list[Match]
#   compute_buzz(matches, signals) -> dict[item_id, BuzzScore]
#
#   write_matches(conn, matches) -> None
#   write_buzz_scores(conn, scores) -> None
#
#   main() — wire it up.


def main() -> None:
    # TODO
    ...


if __name__ == "__main__":
    main()
