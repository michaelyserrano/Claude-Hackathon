"""Initialize the SQLite database from schema.sql.

Run once before any scrape:
    python -m ingest.init_db
"""

from ingest.db import init_schema


def main() -> None:
    init_schema()
    print("schema applied")


if __name__ == "__main__":
    main()
