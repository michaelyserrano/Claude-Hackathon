"""Scrape Swagit-hosted transcripts for past Cambridge City Council meetings.

PrimeGov links each meeting to its recording on Swagit via a `videoUrl`. Swagit
exposes a public plain-text transcript per video at:
    https://cambridgema.v3.swagit.com/videos/{video_id}/transcript

Transcripts are auto-generated voice-to-text — useful for cross-comparison
against agenda items, petitions, and Reddit posts even though the casing is
unusual.

Window: same rolling 60-day window the agenda scraper uses, restricted to
Regular City Council meetings (committeeId=1) that have already aired (the
transcript only exists post-meeting).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from ingest import db
from ingest.scrape_agenda import (
    BASE,
    COUNCIL_COMMITTEE_ID,
    HTTP_TIMEOUT,
    USER_AGENT,
    WINDOW_DAYS,
    _get_json,
)

SWAGIT_BASE = "https://cambridgema.v3.swagit.com"
VIDEO_URL_RE = re.compile(r"/videos/(\d+)")
# Plain text body must be at least this many bytes to count as a real
# transcript (anything smaller is almost certainly an empty/in-progress page).
MIN_TRANSCRIPT_BYTES = 2_000


@dataclass
class CouncilMeeting:
    meeting_id: int
    meeting_date: str
    title: str
    swagit_video_id: int


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def list_past_council_meetings(
    sess: requests.Session | None = None,
) -> list[CouncilMeeting]:
    sess = sess or _session()
    today = datetime.now()
    cutoff = today - timedelta(days=WINDOW_DAYS)

    raw: list[dict] = []
    for year in sorted({today.year, cutoff.year}):
        raw.extend(
            _get_json(
                sess,
                f"{BASE}/api/v2/PublicPortal/ListArchivedMeetings",
                params={"year": year},
            )
        )

    out: dict[int, CouncilMeeting] = {}
    for m in raw:
        if m.get("committeeId") != COUNCIL_COMMITTEE_ID:
            continue
        try:
            dt = datetime.fromisoformat(m["dateTime"])
        except (KeyError, ValueError):
            continue
        if dt < cutoff or dt > today:
            continue
        url = m.get("videoUrl") or ""
        match = VIDEO_URL_RE.search(url)
        if not match:
            continue
        out[m["id"]] = CouncilMeeting(
            meeting_id=m["id"],
            meeting_date=dt.date().isoformat(),
            title=m.get("title") or "City Council",
            swagit_video_id=int(match.group(1)),
        )
    return sorted(out.values(), key=lambda m: m.meeting_date)


def fetch_transcript(video_id: int, sess: requests.Session | None = None) -> str:
    sess = sess or _session()
    r = sess.get(
        f"{SWAGIT_BASE}/videos/{video_id}/transcript", timeout=HTTP_TIMEOUT
    )
    r.raise_for_status()
    return r.text


def main() -> None:
    sess = _session()
    meetings = list_past_council_meetings(sess)
    print(
        f"found {len(meetings)} past council meetings in last {WINDOW_DAYS}d",
        file=sys.stderr,
    )

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = db.connect()
    try:
        kept = empty = errored = 0
        for m in meetings:
            try:
                text = fetch_transcript(m.swagit_video_id, sess)
            except requests.RequestException as e:
                print(f"  [error] {m.meeting_date} fetch failed: {e}", file=sys.stderr)
                errored += 1
                continue
            if len(text) < MIN_TRANSCRIPT_BYTES:
                print(
                    f"  [empty] {m.meeting_date} video={m.swagit_video_id} "
                    f"({len(text)} bytes — transcript not yet available)",
                    file=sys.stderr,
                )
                empty += 1
                continue
            db.upsert_transcript(
                conn,
                {
                    "meeting_id": m.meeting_id,
                    "meeting_date": m.meeting_date,
                    "title": m.title,
                    "swagit_video_id": m.swagit_video_id,
                    "source_url": f"{SWAGIT_BASE}/videos/{m.swagit_video_id}/transcript",
                    "transcript": text,
                    "char_count": len(text),
                    "fetched_at": fetched_at,
                },
            )
            print(
                f"  {m.meeting_date}  {len(text):>7,d} chars  ({m.title})",
                file=sys.stderr,
            )
            kept += 1
        conn.commit()
        print(
            f"upserted {kept} transcripts  ({empty} empty, {errored} errored)",
            file=sys.stderr,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
