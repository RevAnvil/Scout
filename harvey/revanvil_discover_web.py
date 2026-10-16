from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import html
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

SEARCH_URL = "https://html.duckduckgo.com/html/"
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
UA = "Mozilla/5.0 (compatible; RevAnvilScout/1.0; +https://getrevanvil.com)"

ICP_QUERIES = (
    '"hydraulic distributor" "request a quote"',
    '"fluid power distributor" "request a quote"',
    '"hydraulic hose distributor" "request a quote"',
    '"pneumatic distributor" "request a quote"',
    '"power transmission distributor" "request a quote"',
    '"industrial supply" hydraulics "request a quote"',
    '"industrial distributor" hydraulics RFQ',
    '"automation distributor" pneumatics hydraulics',
)

STATES = (
    "Alabama", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Florida", "Georgia", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Nebraska", "Nevada", "New Jersey", "New York",
    "North Carolina", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "South Carolina", "Tennessee", "Texas", "Utah", "Virginia", "Washington",
    "West Virginia", "Wisconsin",
)

JUNK = (
    "duckduckgo.com", "google.com", "bing.com", "yahoo.com", "facebook.com",
    "instagram.com", "linkedin.com", "youtube.com", "wikipedia.org", "reddit.com",
    "yelp.com", "yellowpages.com", "mapquest.com", "indeed.com", "ziprecruiter.com",
    "glassdoor.com", "thomasnet.com", "manta.com", "bbb.org", "amazon.com",
)

HIGH_INTENT_TERMS = (
    "hydraulic", "fluid power", "pneumatic", "hose", "fitting", "valve",
    "pump", "cylinder", "power transmission", "industrial supply", "distributor",
)


def normalize_domain(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def unwrap_ddg(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        query = parse_qs(urlparse(href).query)
        target = query.get("uddg", [""])[0]
        return unquote(target)
    return href


def result_rows(markup: str, query: str, state: str) -> list[dict]:
    rows: list[dict] = []
    pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.I | re.S,
    )
    for href, raw_title in pattern.findall(markup):
        url = unwrap_ddg(href)
        domain = normalize_domain(url)
        if not domain or any(domain == j or domain.endswith("." + j) for j in JUNK):
            continue
        title = re.sub(r"<[^>]+>", " ", html.unescape(raw_title))
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        rows.append({
            "company_name": title.split(" - ")[0].split(" | ")[0].strip(),
            "company_domain": domain,
            "website": f"https://{domain}",
            "discovery_query": query,
            "discovery_state": state,
            "discovery_source": "duckduckgo_html",
            "discovery_result_url": url,
        })
    return rows


async def fetch_query(client: httpx.AsyncClient, query: str, state: str) -> list[dict]:
    full_query = f"{query} {state}"
    try:
        response = await client.post(
            SEARCH_URL,
            data={"q": full_query},
            headers={"User-Agent": UA},
        )
        response.raise_for_status()
        return result_rows(response.text, query, state)
    except Exception:
        return []


async def homepage_fit(client: httpx.AsyncClient, row: dict) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    try:
        r = await client.get(row["website"], headers={"User-Agent": UA}, follow_redirects=True)
        text = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", r.text)).lower()
    except Exception:
        return score, reasons

    hits = [term for term in HIGH_INTENT_TERMS if term in text]
    if any(term in hits for term in ("hydraulic", "fluid power")):
        score += 35
        reasons.append("hydraulic/fluid-power website fit")
    elif hits:
        score += 22
        reasons.append("industrial component website fit")

    if "distributor" in text or "distribution" in text:
        score += 20
        reasons.append("distribution model")
    elif "manufacturer" in text or "manufacturing" in text:
        score += 10
        reasons.append("industrial manufacturer")

    if re.search(r"request\s+(?:a\s+)?quote|\brfq\b|get\s+(?:a\s+)?quote", text, re.I):
        score += 15
        reasons.append("RFQ/quote sales motion")

    if any(term in text for term in ("locations", "branches", "our locations", "find a location")):
        score += 5
        reasons.append("multi-location signal")

    return min(score, 75), reasons


def read_seen(path: str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    with p.open(newline="", encoding="utf-8-sig") as handle:
        return {normalize_domain(r.get("company_domain", "")) for r in csv.DictReader(handle)}


def write_rows(path: str, rows: list[dict]) -> None:
    fields = [
        "company_name", "company_domain", "website", "discovery_query",
        "discovery_state", "discovery_source", "discovery_result_url",
        "first_seen_at", "scout_score", "scout_tier", "scout_score_reasons",
        "scout_research_priority", "scout_send_ready",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


async def discover(output_csv: str, seen_csv: str = "", target: int = 40, states_per_run: int = 3) -> list[dict]:
    seen = read_seen(seen_csv) if seen_csv else set()
    today = dt.datetime.now(dt.timezone.utc)
    offset = today.timetuple().tm_yday % len(STATES)
    states = [STATES[(offset + i) % len(STATES)] for i in range(max(1, states_per_run))]

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        batches = await asyncio.gather(*[
            fetch_query(client, query, state)
            for state in states
            for query in ICP_QUERIES
        ])

        raw: list[dict] = []
        local_seen = set(seen)
        for batch in batches:
            for row in batch:
                domain = row["company_domain"]
                if not domain or domain in local_seen:
                    continue
                local_seen.add(domain)
                raw.append(row)

        semaphore = asyncio.Semaphore(8)

        async def qualify(row: dict) -> dict:
            async with semaphore:
                fit, reasons = await homepage_fit(client, row)
            row = dict(row)
            row["first_seen_at"] = today.isoformat()
            row["scout_score"] = fit
            row["scout_tier"] = "B" if fit >= 65 else "C" if fit >= 50 else "D"
            row["scout_score_reasons"] = "; ".join(reasons)
            row["scout_research_priority"] = "Yes" if fit >= 50 else "No"
            row["scout_send_ready"] = "No"
            return row

        qualified = await asyncio.gather(*[qualify(row) for row in raw[: max(target * 4, 100)]])
        qualified.sort(key=lambda r: int(r.get("scout_score") or 0), reverse=True)
        selected = [r for r in qualified if int(r.get("scout_score") or 0) >= 50][:target]

    write_rows(output_csv, selected)
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover fresh RevAnvil ICP prospects from the public web")
    parser.add_argument("-o", "--output", default="revanvil_fresh_leads.csv")
    parser.add_argument("--seen", default="")
    parser.add_argument("--target", type=int, default=40)
    parser.add_argument("--states-per-run", type=int, default=3)
    args = parser.parse_args()
    rows = asyncio.run(discover(args.output, args.seen, target=args.target, states_per_run=args.states_per_run))
    print(f"Discovered {len(rows)} fresh RevAnvil candidates -> {args.output}")


if __name__ == "__main__":
    main()
