"""Scrape Cambridge City Council agendas from PrimeGov.

Source: https://cambridgema.primegov.com (the city's agenda management system).

Discovery API (no auth):
  GET /api/v2/PublicPortal/ListUpcomingMeetings
  GET /api/v2/PublicPortal/ListArchivedMeetings?year=YYYY
  GET /api/v2/PublicPortal/GetArchivedMeetingYears

Each meeting in the response carries a `documentList`. We pick the entry whose
`templateName == "Agenda"` and use its `templateId` to fetch the rendered
agenda page at:
  /Portal/Meeting?meetingTemplateId={templateId}

That page has each agenda item as a `<td class="agenda-item" data-itemid="...">`,
which is dramatically cleaner than parsing the PDF.

Window: rolling ~2 months of Regular City Council meetings (committeeId=1).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup, Tag

from ingest import db

BASE = "https://cambridgema.primegov.com"
COUNCIL_COMMITTEE_ID = 1  # "Regular City Council Meeting"
WINDOW_DAYS = 60
HTTP_TIMEOUT = 30
USER_AGENT = "cambridge-civic-feed/0.1 (+hackathon)"

ID_PATTERN = re.compile(r"\b(CMA|POR|RES|AR|ORD|HS|COM|COF|CC)\s+(\d{4}-\d{1,3})\b")
# Sponsors render two ways depending on section:
#   - Regular items:    "COUNCILLOR MCGOVERN" each on its own line, all caps.
#   - Awaiting Reports: "Councillor Nolan, Councillor Sobrinho-Wheeler, ..." comma-joined.
# So match case-insensitively and don't anchor; we let _parse_sponsors dedupe.
SPONSOR_PATTERN = re.compile(
    r"\b(MAYOR|VICE MAYOR|COUNCILLOR)\s+([A-Z][A-Za-z\-]+)\b",
    re.IGNORECASE,
)

# When input is ALL-CAPS (regular agenda sections), str.capitalize() turns
# "MCGOVERN" into "Mcgovern". Override for known councillors.
COUNCILLOR_CANON = {
    "MCGOVERN": "McGovern",
    "SOBRINHO-WHEELER": "Sobrinho-Wheeler",
    "AL-ZUBI": "Al-Zubi",
    "SIDDIQUI": "Siddiqui",
    "AZEEM": "Azeem",
    "NOLAN": "Nolan",
    "SIMMONS": "Simmons",
    "ZUSY": "Zusy",
    "FLAHERTY": "Flaherty",
    "TONER": "Toner",
}
DOLLAR_PATTERN = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")

# Section headings as they appear in the rendered agenda. First match wins.
SECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Section headings appear as standalone uppercase paragraphs, sometimes with
    # the roman numeral in a separate <p>, so match the heading word itself.
    (re.compile(r"^CITY MANAGER'S AGENDA$", re.I), "City Manager's Agenda"),
    (re.compile(r"^POLICY ORDERS$", re.I), "Policy Orders"),
    (re.compile(r"^CHARTER RIGHT$", re.I), "Charter Right"),
    (re.compile(r"^UNFINISHED BUSINESS$", re.I), "Unfinished Business"),
    (re.compile(r"^RESOLUTIONS$", re.I), "Resolutions"),
    (re.compile(r"^COMMITTEE REPORTS$", re.I), "Committee Reports"),
    (re.compile(r"^COMMUNICATIONS FROM OTHER CITY OFFICERS$", re.I), "Communications from Other City Officers"),
    (re.compile(r"^COMMUNICATIONS$", re.I), "Communications"),
    (re.compile(r"^AWAITING REPORT LIST$", re.I), "Awaiting Report List"),
    (re.compile(r"^HEARING SCHEDULE$", re.I), "Hearing Schedule"),
]


@dataclass
class MeetingRef:
    meeting_id: int
    meeting_date: str  # ISO YYYY-MM-DD
    template_id: int
    title: str


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def list_recent_meetings(sess: requests.Session | None = None) -> list[MeetingRef]:
    sess = sess or _session()
    today = datetime.now()
    cutoff = today - timedelta(days=WINDOW_DAYS)

    raw: list[dict] = []
    raw.extend(_get_json(sess, f"{BASE}/api/v2/PublicPortal/ListUpcomingMeetings"))
    for year in sorted({today.year, cutoff.year}):
        raw.extend(
            _get_json(
                sess,
                f"{BASE}/api/v2/PublicPortal/ListArchivedMeetings",
                params={"year": year},
            )
        )

    meetings: dict[int, MeetingRef] = {}
    for m in raw:
        if m.get("committeeId") != COUNCIL_COMMITTEE_ID:
            continue
        try:
            dt = datetime.fromisoformat(m["dateTime"])
        except (KeyError, ValueError):
            continue
        if dt < cutoff:
            continue
        agenda_doc = next(
            (d for d in m.get("documentList") or [] if d.get("templateName") == "Agenda"),
            None,
        )
        if not agenda_doc:
            continue
        ref = MeetingRef(
            meeting_id=m["id"],
            meeting_date=dt.date().isoformat(),
            template_id=agenda_doc["templateId"],
            title=m.get("title") or "City Council",
        )
        meetings[ref.template_id] = ref  # dedupe upcoming/archived overlap

    return sorted(meetings.values(), key=lambda r: r.meeting_date)


def fetch_agenda_html(meeting: MeetingRef, sess: requests.Session | None = None) -> str:
    sess = sess or _session()
    r = sess.get(
        f"{BASE}/Portal/Meeting",
        params={"meetingTemplateId": meeting.template_id},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.text


def parse_items(html: str, meeting_date: str) -> list[dict]:
    """Walk every <tr> inside an item table and turn it into one agenda item.

    We iterate rows (not `td.agenda-item`) because section layouts differ:
    regular City Council items put their content in a `td.agenda-item`, but
    Awaiting Report rows put it in an unclassed `<td>` and reserve
    `td.agenda-item` for the empty attachment-icon cell. Iterating rows
    handles both layouts uniformly.
    """
    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now().date().isoformat()
    items: list[dict] = []
    seen_ids: set[str] = set()

    for tr in soup.select("table.item-table-fromdocx tr"):
        text = tr.get_text("\n", strip=True)
        if not text:
            continue
        # Multiple ID-shaped strings can appear in a row (e.g. an Awaiting
        # Report row references its old AR-25-XX id in the blurb and its
        # current AR-2026-XX id at the bottom). The formal id is always last.
        matches = list(ID_PATTERN.finditer(text))
        if not matches:
            continue
        id_m = matches[-1]
        item_id = f"{id_m.group(1)}-{id_m.group(2)}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        section = _find_section(tr)
        title = _derive_title(text, id_m.start())
        sponsors = _parse_sponsors(text)
        dollars = _parse_dollar_amount(text)

        items.append(
            {
                "id": item_id,
                "meeting_date": meeting_date,
                "title": title,
                "raw_text": text,
                "stage": _infer_stage(section, item_id, meeting_date, today),
                "dollar_amount": dollars,
                "sponsors": sponsors,
            }
        )

    return items


def _find_section(item_el: Tag) -> str | None:
    # Walk parent-level blocks (paragraphs / headings) backwards, getting their
    # combined text so headings split across <span> children still match.
    for prev in item_el.find_all_previous(["p", "h1", "h2", "h3", "h4"]):
        s = prev.get_text(" ", strip=True)
        if not s:
            continue
        for pat, name in SECTION_PATTERNS:
            if pat.search(s):
                return name
    return None


def _derive_title(text: str, id_offset: int) -> str:
    """First substantive line of the row — the human-readable blurb.

    The cell may start with a list number ("1.") or have other short noise;
    we want the first line of real content. Capped at 240 chars.
    """
    head = text[:id_offset] if id_offset > 0 else text
    for raw in head.split("\n"):
        line = raw.strip()
        # Skip pure list numbers, attachment glyphs, and other short noise.
        if len(line) >= 25 and not SPONSOR_PATTERN.match(line):
            return re.sub(r"\s+", " ", line)[:240]
    # Fallback: longest line.
    lines = [l.strip() for l in head.split("\n") if l.strip()]
    return (max(lines, key=len, default="")[:240]) if lines else ""


def _parse_sponsors(text: str) -> list[str]:
    seen: list[str] = []
    for m in SPONSOR_PATTERN.finditer(text):
        role = "Vice Mayor" if m.group(1).upper() == "VICE MAYOR" else m.group(1).title()
        raw = m.group(2)
        upper = raw.upper()
        if upper in COUNCILLOR_CANON:
            name = COUNCILLOR_CANON[upper]
        elif raw.isupper():
            # Unknown all-caps surname — fall back to title-case-with-hyphens.
            name = "-".join(p.capitalize() for p in raw.split("-"))
        else:
            # Preserves "McGovern", "Sobrinho-Wheeler" from title-case sections.
            name = raw
        full = f"{role} {name}"
        if full not in seen:
            seen.append(full)
    return seen


def _parse_dollar_amount(text: str) -> int | None:
    biggest = 0
    for m in DOLLAR_PATTERN.finditer(text):
        try:
            v = int(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
        if v > biggest:
            biggest = v
    return biggest or None


def _infer_stage(
    section: str | None, item_id: str, meeting_date: str, today: str
) -> str:
    prefix = item_id.split("-", 1)[0]
    if section == "Awaiting Report List" or prefix == "AR":
        return "Awaiting Report"
    if section == "Charter Right":
        return "Charter Right"
    if section == "Unfinished Business":
        return "Unfinished Business"
    if section == "Committee Reports" or prefix == "CC":
        return "Committee Report"
    if prefix == "RES":
        return "Resolution"
    if prefix == "ORD":
        return "Ordinance"
    if prefix == "HS":
        return "Hearing Schedule"
    return "Passed" if meeting_date < today else "Upcoming"


def _get_json(sess: requests.Session, url: str, params: dict | None = None) -> list:
    r = sess.get(url, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def main() -> None:
    sess = _session()
    meetings = list_recent_meetings(sess)
    print(f"found {len(meetings)} council meetings in the last {WINDOW_DAYS}d", file=sys.stderr)

    conn = db.connect()
    try:
        total = 0
        for m in meetings:
            try:
                html = fetch_agenda_html(m, sess)
            except requests.RequestException as e:
                print(f"  [skip] {m.meeting_date} fetch failed: {e}", file=sys.stderr)
                continue
            items = parse_items(html, m.meeting_date)
            for item in items:
                db.upsert_agenda_item(conn, item)
            conn.commit()
            print(f"  {m.meeting_date}  {len(items):3d} items  ({m.title})", file=sys.stderr)
            total += len(items)
        print(f"upserted {total} agenda items", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
