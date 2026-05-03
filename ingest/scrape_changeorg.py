"""Scrape Change.org for open Cambridge-related petitions.

How this works
--------------
Change.org's public site is a thin React shell that fetches petition data from
a Typesense search backend. The Typesense API key is exposed in the page (a
search-only key scoped to `discoverable:true`). We hit that endpoint directly
with `requests` — no Playwright, no anti-bot games.

If Change.org rotates the key (or changes the endpoint), `_extract_api_key`
re-discovers it from the search page HTML.

Two-pronged search:
  1. Location filter: every petition with location.city_state == "Cambridge, MA".
  2. Seed queries (housing/transit/climate/etc.) post-filtered to mentions of
     "cambridge" — picks up topical petitions started elsewhere.

Combine, dedupe by petition id, filter to status='published' and
signature_count >= MIN_SIGNATURES, upsert.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

import requests

from ingest import db

SEARCH_URL_BASE = "https://www.change.org/ts/multi_search"
SEARCH_PAGE_URL = "https://www.change.org/search?q=Cambridge"
COLLECTION = "petitions_en"
PRESET = "petitions_initial_en"
PER_PAGE = 50
MAX_PAGES = 5  # cap pagination per query (50 * 5 = 250 results)
HTTP_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

MIN_SIGNATURES = 100

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

# Seen on the live site as of this build. Auto-rediscovered if it stops working.
DEFAULT_API_KEY = (
    "aDRYdTkyTVFQVU5FczRwWmhjNjFRbHB1ZWdrOTF0TGdjSjF3VUJ5ZDVZVT04VzVXey"
    "JmaWx0ZXJfYnkiOiJkaXNjb3ZlcmFibGU6dHJ1ZSJ9"
)
API_KEY_PATTERN = re.compile(r"x-typesense-api-key=([A-Za-z0-9+/=_\-]{40,})")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Origin": "https://www.change.org",
            "Referer": "https://www.change.org/",
            "Accept": "application/json",
        }
    )
    return s


def _extract_api_key(sess: requests.Session) -> str:
    """Best-effort re-discovery if the embedded default key stops working.

    Looks for `x-typesense-api-key=...` inside any inline script/asset on the
    public search page. Falls back to the default if not found.
    """
    try:
        r = sess.get(SEARCH_PAGE_URL, timeout=HTTP_TIMEOUT)
        m = API_KEY_PATTERN.search(r.text)
        if m:
            return m.group(1)
    except requests.RequestException:
        pass
    return DEFAULT_API_KEY


def _search(
    sess: requests.Session,
    api_key: str,
    *,
    q: str = "*",
    filter_by: str | None = None,
    page: int = 1,
) -> dict:
    body_inner = {
        "preset": PRESET,
        "collection": COLLECTION,
        "q": q,
        "page": page,
        "per_page": PER_PAGE,
    }
    if filter_by:
        body_inner["filter_by"] = filter_by
    r = sess.post(
        SEARCH_URL_BASE,
        params={"x-typesense-api-key": api_key},
        json={"searches": [body_inner]},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["results"][0]


def _paginate(sess, api_key, *, q, filter_by=None) -> list[dict]:
    """Pull every page until we've drained results or hit MAX_PAGES."""
    out: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        res = _search(sess, api_key, q=q, filter_by=filter_by, page=page)
        hits = res.get("hits") or []
        if not hits:
            break
        out.extend(h["document"] for h in hits)
        # If we've fetched everything Typesense reports, stop early.
        if len(out) >= (res.get("found") or 0):
            break
    return out


def _is_cambridge_relevant(doc: dict) -> bool:
    loc = doc.get("location") or {}
    if (loc.get("city_state") or "").lower() == "cambridge, ma":
        return True
    haystack = " ".join(
        str(doc.get(k) or "") for k in ("ask", "description")
    ).lower()
    # Require "cambridge" *and* some Massachusetts marker — avoids matching
    # Cambridge UK / Cambridge MD / Cambridge ON petitions.
    if "cambridge" not in haystack:
        return False
    return any(
        token in haystack
        for token in (" ma ", " ma.", "massachusetts", "cambridge, ma", "cambridgeport")
    )


def _to_petition(doc: dict, scraped_at: str) -> dict | None:
    slug = doc.get("slug")
    title = doc.get("ask")
    if not slug or not title:
        return None
    return {
        "id": slug,
        "url": f"https://www.change.org/p/{slug}",
        "title": title,
        # Cap to ~2KB so embedding/LLM costs stay bounded; the index entry
        # already trims long bodies.
        "description": (doc.get("description") or "")[:2000],
        "signature_count": int(doc.get("total_signature_count") or 0),
        "scraped_at": scraped_at,
    }


def main() -> None:
    sess = _session()
    api_key = _extract_api_key(sess)
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    docs_by_id: dict[str, dict] = {}

    # 1. Everything tagged Cambridge, MA by location — guaranteed local.
    location_hits = _paginate(
        sess, api_key,
        q="*",
        filter_by='location.city_state:="Cambridge, MA"',
    )
    print(f"location filter: {len(location_hits)} petitions in Cambridge, MA", file=sys.stderr)
    for d in location_hits:
        docs_by_id[d["id"]] = d

    # 2. Seed queries — picks up Cambridge-relevant petitions started elsewhere.
    seed_total = 0
    for q in SEED_QUERIES:
        try:
            hits = _paginate(sess, api_key, q=q)
        except requests.RequestException as e:
            print(f"  [warn] seed '{q}' failed: {e}", file=sys.stderr)
            continue
        kept = 0
        for d in hits:
            if not _is_cambridge_relevant(d):
                continue
            if d["id"] not in docs_by_id:
                kept += 1
            docs_by_id[d["id"]] = d
        seed_total += kept
        print(f"  seed '{q}': +{kept} new (of {len(hits)})", file=sys.stderr)

    print(f"merged unique: {len(docs_by_id)} (seed contributed {seed_total})", file=sys.stderr)

    # 3. Filter & upsert.
    conn = db.connect()
    try:
        kept = skipped_status = skipped_sigs = malformed = 0
        for d in docs_by_id.values():
            if d.get("status") != "published":
                skipped_status += 1
                continue
            if int(d.get("total_signature_count") or 0) < MIN_SIGNATURES:
                skipped_sigs += 1
                continue
            row = _to_petition(d, scraped_at)
            if not row:
                malformed += 1
                continue
            db.upsert_petition(conn, row)
            kept += 1
        conn.commit()
        print(
            f"upserted {kept} petitions  "
            f"(filtered out: {skipped_status} non-published, "
            f"{skipped_sigs} below {MIN_SIGNATURES} sigs, {malformed} malformed)",
            file=sys.stderr,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
