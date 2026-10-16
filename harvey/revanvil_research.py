from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import re
from urllib.parse import urlparse

from harvey.collectors.profile import ProfileCollector

CONFIRMED = {
    "TECH_STACK",
    "HIRING_ROLE",
    "CONTACT_FORM_URL",
    "CONTACT_FOUND",
    "DECISION_MAKER_TITLE",
    "SITE_PAGE_COUNT",
    "BLOG_STALE",
}

RFQ_PATTERNS = (
    r"\brfq\b",
    r"request\s+(?:a\s+)?quote",
    r"quote\s+request",
    r"get\s+(?:a\s+)?quote",
    r"submit\s+(?:an?\s+)?rfq",
)

INDUSTRIAL_TERMS = (
    "hydraulic", "fluid power", "industrial", "distributor", "distribution",
    "mro", "power transmission", "automation", "pneumatic", "hose",
    "fitting", "bearing", "valve", "pump", "cylinder", "manufacturing",
)

RELEVANT_HIRING_TERMS = (
    "sales", "business development", "revenue", "account manager",
    "inside sales", "sales operations", "revenue operations", "crm", "erp",
)


def _domain(row: dict) -> str:
    value = (row.get("company_domain") or row.get("domain") or "").strip()
    if value:
        value = re.sub(r"^https?://", "", value, flags=re.I).split("/")[0]
        return value.lower().removeprefix("www.")
    website = (row.get("website") or "").strip()
    if website:
        return (urlparse(website).netloc or "").lower().removeprefix("www.")
    return ""


def _company_id(row: dict, domain: str) -> str:
    raw = f"{row.get('company_name','')}|{domain}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def _signal_values(obs: list[dict], code: str) -> list[str]:
    out = []
    for item in obs:
        if item.get("signal_code") != code:
            continue
        text = str(item.get("value_text") or "").strip()
        num = item.get("value_num")
        if text:
            out.append(text)
        elif num is not None:
            out.append(str(num))
    return out


def _join(values: list[str]) -> str:
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return " | ".join(seen)


def _best_signal(row: dict, *, rfq: bool, hiring: list[str], tech: list[str], dms: list[str], industrial_hits: list[str]) -> tuple[str, int]:
    if hiring:
        role = hiring[0]
        return f"Hiring signal: {role}", 10
    if rfq:
        return "Public RFQ / request-a-quote workflow detected", 9
    if dms:
        return f"Decision-maker identified publicly: {dms[0]}", 7
    if tech:
        return f"Public sales/marketing technology detected: {tech[0]}", 4
    if industrial_hits:
        return f"Industrial fit confirmed from website language: {industrial_hits[0]}", 4
    existing = (row.get("scout_signal") or row.get("outreach_angle") or "").strip()
    return existing or "No strong public buying signal found", 0


async def _research_one(row: dict, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        result = dict(row)
        domain = _domain(row)
        result["scout_research_domain"] = domain
        if not domain:
            result.update({
                "scout_research_status": "no_domain",
                "scout_public_signal": "",
                "scout_research_score_bonus": "0",
            })
            return result

        try:
            async with ProfileCollector(None, CONFIRMED) as collector:
                company_id = _company_id(row, domain)
                obs = await collector.profile_company(company_id, domain)
                site, home = await collector._resolve_site(domain)

                rfq = False
                industrial_hits: list[str] = []
                if home:
                    text = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", home)).lower()
                    rfq = any(re.search(p, text, re.I) for p in RFQ_PATTERNS)
                    industrial_hits = [term for term in INDUSTRIAL_TERMS if term in text][:5]

                    if not rfq:
                        for path in ("/rfq", "/request-a-quote", "/request-quote", "/quote", "/get-a-quote"):
                            page = await collector._get(f"{site}{path}")
                            if page and (re.search(r"(?is)<form\b", page) or any(re.search(p, page, re.I) for p in RFQ_PATTERNS)):
                                rfq = True
                                break

                tech = _signal_values(obs, "TECH_STACK")
                hiring_all = _signal_values(obs, "HIRING_ROLE")
                hiring = [h for h in hiring_all if any(term in h.lower() for term in RELEVANT_HIRING_TERMS)]
                dms = _signal_values(obs, "DECISION_MAKER_TITLE")
                contacts = _signal_values(obs, "CONTACT_FOUND")
                forms = _signal_values(obs, "CONTACT_FORM_URL")
                page_count = _signal_values(obs, "SITE_PAGE_COUNT")
                blog_stale = _signal_values(obs, "BLOG_STALE")

                signal, bonus = _best_signal(
                    row, rfq=rfq, hiring=hiring, tech=tech, dms=dms,
                    industrial_hits=industrial_hits,
                )

                try:
                    base_score = int(float(row.get("scout_score") or 0))
                except (TypeError, ValueError):
                    base_score = 0
                researched_score = min(100, base_score + bonus)

                result.update({
                    "scout_research_status": "complete" if home else "site_unreachable",
                    "scout_public_signal": signal,
                    "scout_research_score_bonus": str(bonus),
                    "scout_researched_score": str(researched_score),
                    "scout_rfq_detected": "Yes" if rfq else "No",
                    "scout_website_tech": _join(tech),
                    "scout_hiring_roles": _join(hiring_all),
                    "scout_relevant_hiring_signal": _join(hiring),
                    "scout_decision_makers_found": _join(dms),
                    "scout_public_contacts_found": _join(contacts),
                    "scout_contact_form": _join(forms),
                    "scout_site_page_count": _join(page_count),
                    "scout_blog_stale": _join(blog_stale),
                    "scout_industrial_terms": _join(industrial_hits),
                    "scout_evidence_urls": _join([str(o.get("evidence_url") or "") for o in obs]),
                })
        except Exception as exc:
            result.update({
                "scout_research_status": "error",
                "scout_research_error": f"{type(exc).__name__}: {exc}"[:300],
                "scout_public_signal": "",
                "scout_research_score_bonus": "0",
            })
        return result


async def research(input_csv: str, output_csv: str, *, limit: int = 0, concurrency: int = 6) -> list[dict]:
    with open(input_csv, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    priority = [r for r in rows if (r.get("scout_research_priority") or "").strip().lower() == "yes"]
    if limit > 0:
        priority = priority[:limit]

    semaphore = asyncio.Semaphore(max(1, concurrency))
    researched = await asyncio.gather(*[_research_one(row, semaphore) for row in priority])
    by_key = {
        ((r.get("email") or "").strip().lower(), (r.get("company_name") or "").strip().lower()): r
        for r in researched
    }

    merged = []
    for row in rows:
        key = ((row.get("email") or "").strip().lower(), (row.get("company_name") or "").strip().lower())
        merged.append(by_key.get(key, row))

    extras = [
        "scout_research_domain", "scout_research_status", "scout_public_signal",
        "scout_research_score_bonus", "scout_researched_score", "scout_rfq_detected",
        "scout_website_tech", "scout_hiring_roles", "scout_relevant_hiring_signal",
        "scout_decision_makers_found", "scout_public_contacts_found",
        "scout_contact_form", "scout_site_page_count", "scout_blog_stale",
        "scout_industrial_terms", "scout_evidence_urls", "scout_research_error",
    ]
    original = list(rows[0].keys()) if rows else []
    fields = original + [f for f in extras if f not in original]
    with open(output_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)
    return researched


def main() -> None:
    parser = argparse.ArgumentParser(description="Research RevAnvil Scout priority leads using public company websites")
    parser.add_argument("input_csv")
    parser.add_argument("-o", "--output", default="revanvil_scout_researched.csv")
    parser.add_argument("--limit", type=int, default=0, help="Research only the first N priority leads; 0 = all")
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()

    results = asyncio.run(research(args.input_csv, args.output, limit=args.limit, concurrency=args.concurrency))
    counts: dict[str, int] = {}
    for row in results:
        status = row.get("scout_research_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    print(f"Researched {len(results)} priority leads | status={counts} | output={args.output}")


if __name__ == "__main__":
    main()
