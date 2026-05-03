"""LLM enrichment pass: summarize, topic-tag, embed.

Reads rows that are missing summary/topics/embedding and fills them in.
Idempotent: re-running only processes rows still missing fields.

Three operations:
  1. SUMMARIZE  — agenda items only. One paragraph in plain English.
                  Should be readable in ~15 seconds. Mention $ amount and
                  what passing means.
  2. TOPIC-TAG  — agenda items + petitions + reddit posts. Constrained to
                  the fixed taxonomy below. Multi-label (1–3 tags).
  3. EMBED      — agenda items + petitions + reddit posts. Vector for
                  semantic similarity in match.py.

Batch where possible: one Claude call can tag many items at once via
JSON output. Embeddings are typically batchable per the provider.
"""

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


# TODO entry points:
#
#   summarize_agenda_items(conn) -> None
#       Pull rows where summary IS NULL. Send raw_text to Claude with a
#       prompt that emphasizes plain English and concrete impact. Write back.
#
#   tag_topics(conn, table: str) -> None
#       Pull rows where topics IS NULL. Batch into Claude. Constrain output
#       to TOPIC_TAXONOMY only. Write back as JSON.
#
#   embed_rows(conn, table: str) -> None
#       Pull rows where embedding IS NULL. Choose the text field per table:
#         - agenda_items: summary (or raw_text if summary still null)
#         - petitions:    title + " " + description
#         - reddit_posts: title + " " + body
#       Call the embedding provider, write JSON-encoded vectors back.
#
#   _claude_client() -> anthropic.Anthropic
#   _embedding_client() -> ...    # voyage or openai, whichever is configured
#
#   main() — runs summarize → tag (all 3 tables) → embed (all 3 tables).


def main() -> None:
    # TODO
    ...


if __name__ == "__main__":
    main()
