"""Scrape Cambridge City Council agendas from cambridgema.gov.

Window: rolling 2 months (most recent ~8 weekly meetings).

For each meeting:
  - Fetch the agenda (HTML preferred, PDF via pdfplumber as fallback).
  - Parse out individual items. Items are numbered within sections like:
        V. CITY MANAGER'S AGENDA
        VI. POLICY ORDERS
        VII. CALENDAR  (subsections: CHARTER RIGHT, UNFINISHED BUSINESS)
        IX. RESOLUTIONS
        X. COMMITTEE REPORTS
        XI. COMMUNICATIONS FROM OTHER CITY OFFICERS
        AWAITING REPORT LIST
  - Each item has an ID like "CMA 2026-118", "POR 2026-93", "AR 2026-02".
  - Stage is inferred from the section it appears in (see _infer_stage).

Writes raw rows to agenda_items via ingest.db.upsert_agenda_item.
Does NOT call any LLM — that happens in enrich.py.

Output contract (one dict per item):
    {
      "id":           "CMA-2026-118",
      "meeting_date": "2026-05-04",
      "title":        "Federal update including court cases",
      "raw_text":     "<full original blurb>",
      "stage":        "Upcoming",
      "dollar_amount": 14000000,    # or None
      "sponsors":     ["Mayor Siddiqui"],
      # summary/topics/embedding left NULL — enrich.py fills them.
    }
"""

# TODO entry points:
#   - list_recent_meetings() -> list[MeetingRef]
#   - fetch_agenda_html(meeting) -> str
#   - parse_items(html_or_pdf_text, meeting_date) -> list[dict]
#   - _infer_stage(section_header, item_text) -> str
#   - _parse_dollar_amount(text) -> int | None
#   - _parse_sponsors(text) -> list[str]
#   - main() — orchestrates the above and writes via db.upsert_agenda_item


def main() -> None:
    # TODO
    ...


if __name__ == "__main__":
    main()
