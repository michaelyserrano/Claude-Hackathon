"""Scrape Change.org for open Cambridge-related petitions with traction.

Approach:
  - Search Change.org with seed queries: "Cambridge MA <topic>" for each topic
    in the taxonomy (housing, transit, climate, schools, parking, etc.).
  - Filter: status == open, signature_count >= 100.
  - Change.org is JS-heavy and bot-aware. Default to Playwright; fall back to
    raw `requests` against any embedded JSON if it works.

Risk: this scraper is the most likely to break (anti-bot).
If it fails at hackathon time, the rest of the pipeline still works with
just Reddit as the buzz signal — fail loud, don't crash the whole ingest.

Writes raw rows to petitions via ingest.db.upsert_petition.
Does NOT call any LLM — that happens in enrich.py.

Output contract (one dict per petition):
    {
      "id":              "<change.org slug>",
      "url":             "https://www.change.org/p/...",
      "title":           "...",
      "description":     "<first ~500 chars of the petition body>",
      "signature_count": 3400,
      "scraped_at":      "<ISO timestamp>",
      # topics/embedding left NULL — enrich.py fills them.
    }
"""

SEED_QUERIES = [
    "Cambridge MA housing",
    "Cambridge MA zoning",
    "Cambridge MA transit",
    "Cambridge MA bike",
    "Cambridge MA climate",
    "Cambridge MA schools",
    "Cambridge MA parking",
    "Cambridge MA park",
    "Cambridge Massachusetts",
]

MIN_SIGNATURES = 100


# TODO entry points:
#   - search(query: str) -> list[PetitionRef]
#   - fetch_petition(url) -> dict
#   - main() — iterate SEED_QUERIES, dedupe by id, filter, upsert.


def main() -> None:
    # TODO
    ...


if __name__ == "__main__":
    main()
