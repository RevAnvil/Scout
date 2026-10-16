from __future__ import annotations

import argparse
import asyncio
import csv
import re
from urllib.parse import urljoin, urlparse

import httpx

TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=8.0, pool=8.0)
PAGES = ("", "/contact", "/contact-us", "/about", "/about-us", "/team", "/our-team", "/leadership")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]\d{4}(?!\d)")
LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_%\-./]+", re.I)
ROLE_RE = re.compile(r"\b(owner|founder|president|ceo|chief executive|general manager|vice president|vp|sales director|director of sales|revenue operations|sales operations)\b", re.I)


def _domain(row: dict) -> str:
    value = (row.get("company_domain") or row.get("scout_research_domain") or row.get("domain") or "").strip()
    if value:
        value = re.sub(r"^https?://", "", value, flags=re.I).split("/")[0]
        return value.lower().removeprefix("www.")
    website = (row.get("website") or "").strip()
    return (urlparse(website).netloc or "").lower().removeprefix("www.") if website else ""


def _clean_html(html: str) -> str:
    html = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", " ", html)
    return re.sub(r"\s+", " ", html)


def _visible_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", _clean_html(html)))


def _same_domain(email: str, domain: str) -> bool:
    return email.lower().split("@")[-1].removeprefix("www.") == domain.lower().removeprefix("www.")


def _generic(email: str) -> bool:
    local = email.lower().split("@")[0]
    return local in {"info", "sales", "contact", "support", "office", "hello", "admin", "service", "customerservice"}


def _dedupe(items):
    out = []
    seen = set()
    for item in items:
        key = str(item).lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


async def enrich_one(row: dict, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        result = dict(row)
        domain = _domain(row)
        if not domain:
            result["scout_contact_enrichment_status"] = "no_domain"
            return result

        pages = []
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            base = ""
            for candidate in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}", f"http://www.{domain}"):
                try:
                    r = await client.get(candidate)
                    if r.status_code == 200 and "text" in r.headers.get("content-type", ""):
                        base = str(r.url).rstrip("/")
                        pages.append((base, r.text))
                        break
                except Exception:
                    pass
            if not base:
                result["scout_contact_enrichment_status"] = "site_unreachable"
                return result

            for path in PAGES[1:]:
                try:
                    url = urljoin(base + "/", path.lstrip("/"))
                    r = await client.get(url)
                    if r.status_code == 200 and "text" in r.headers.get("content-type", "") and len(r.text) > 300:
                        pages.append((str(r.url), r.text))
                except Exception:
                    continue

        emails, phones, linkedin = [], [], []
        evidence = []
        exec_snippets = []
        for url, html in pages:
            cleaned = _clean_html(html)
            text = _visible_text(html)
            found_emails = _dedupe(EMAIL_RE.findall(cleaned))
            found_phones = _dedupe(PHONE_RE.findall(text))
            found_linkedin = _dedupe(LINKEDIN_RE.findall(cleaned))
            if found_emails or found_phones or found_linkedin:
                evidence.append(url)
            emails.extend(found_emails)
            phones.extend(found_phones)
            linkedin.extend(found_linkedin)

            for m in ROLE_RE.finditer(text):
                start = max(0, m.start() - 100)
                end = min(len(text), m.end() + 140)
                snippet = text[start:end].strip()
                if len(snippet) <= 260:
                    exec_snippets.append(snippet)
                if len(exec_snippets) >= 8:
                    break

        emails = _dedupe([e.lower() for e in emails if _same_domain(e, domain)])
        named = [e for e in emails if not _generic(e)]
        generic = [e for e in emails if _generic(e)]
        phones = _dedupe([re.sub(r"\s+", " ", p).strip() for p in phones])
        linkedin = _dedupe(linkedin)
        exec_snippets = _dedupe(exec_snippets)

        result.update({
            "scout_contact_enrichment_status": "complete",
            "scout_public_work_emails": " | ".join(named[:10]),
            "scout_public_generic_emails": " | ".join(generic[:10]),
            "scout_public_business_phones": " | ".join(phones[:10]),
            "scout_public_linkedin_profiles": " | ".join(linkedin[:10]),
            "scout_public_exec_context": " | ".join(exec_snippets[:5]),
            "scout_contact_evidence_urls": " | ".join(_dedupe(evidence)[:10]),
        })
        return result


async def enrich(input_csv: str, output_csv: str, concurrency: int = 6) -> list[dict]:
    with open(input_csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    semaphore = asyncio.Semaphore(max(1, concurrency))
    targets = [r for r in rows if (r.get("scout_research_priority") or "").lower() == "yes"]
    enriched = await asyncio.gather(*[enrich_one(r, semaphore) for r in targets])
    by_key = {((r.get("email") or "").lower(), (r.get("company_name") or "").lower()): r for r in enriched}
    merged = []
    for row in rows:
        key = ((row.get("email") or "").lower(), (row.get("company_name") or "").lower())
        merged.append(by_key.get(key, row))

    extras = [
        "scout_contact_enrichment_status", "scout_public_work_emails",
        "scout_public_generic_emails", "scout_public_business_phones",
        "scout_public_linkedin_profiles", "scout_public_exec_context",
        "scout_contact_evidence_urls",
    ]
    fields = list(rows[0].keys()) if rows else []
    fields += [x for x in extras if x not in fields]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(merged)
    return enriched


def main():
    p = argparse.ArgumentParser(description="Enrich RevAnvil leads with public business contact details")
    p.add_argument("input_csv")
    p.add_argument("-o", "--output", default="revanvil_scout_contacts.csv")
    p.add_argument("--concurrency", type=int, default=6)
    args = p.parse_args()
    results = asyncio.run(enrich(args.input_csv, args.output, args.concurrency))
    print(f"Enriched {len(results)} priority leads | output={args.output}")

if __name__ == "__main__":
    main()
