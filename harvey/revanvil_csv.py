from __future__ import annotations

import argparse
import csv

INDUSTRY_TERMS = {
    "hydraulic": 18, "fluid power": 18, "industrial distribution": 18,
    "industrial distributor": 18, "mro": 14, "industrial supply": 14,
    "power transmission": 14, "electrical distribution": 12,
    "metal distribution": 12, "manufacturer": 8, "manufacturing": 8,
    "distribution": 8, "distributor": 8,
}
TITLE_TERMS = {
    "owner": 15, "president": 15, "ceo": 15, "chief executive": 15,
    "general manager": 14, "vp sales": 15, "vice president of sales": 15,
    "sales director": 14, "director of sales": 14,
    "chief revenue officer": 15, "revenue operations": 15,
    "sales operations": 14, "vp": 10, "vice president": 10,
    "director": 9, "manager": 5,
}
TECH_TERMS = {
    "prophet 21": 10, "p21": 10, "epicor": 10, "infor": 10,
    "rubbertree": 8, "salesforce": 5, "hubspot": 5, "crm": 4, "erp": 4,
}
ANGLE_TERMS = {
    "declining": 10, "reorder": 9, "dormant": 10, "quote": 9, "rfq": 9,
    "cross-sell": 6, "pipeline": 6, "follow-up": 8, "overdue": 8, "account": 4,
}


def txt(row, key):
    return (row.get(key) or "").strip()


def low(row, key):
    return txt(row, key).lower()


def contains_score(text, mapping):
    t = text.lower()
    return max((score for term, score in mapping.items() if term in t), default=0)


def revenue_points(value):
    try:
        revenue = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return 0
    if 5_000_000 <= revenue <= 100_000_000:
        return 15
    if 2_000_000 <= revenue < 5_000_000 or 100_000_000 < revenue <= 200_000_000:
        return 8
    return 0


def normalize_status(row):
    status = low(row, "source_email_status")
    catch = low(row, "source_catch_all_status")
    if "verified" in status and "catch" not in catch:
        return "verified"
    if "invalid" in status or "undeliver" in status:
        return "invalid"
    if "catch" in catch or "catch" in status:
        return "risky"
    return "unknown"


def suppressed(row):
    text = " ".join([
        low(row, "selection_status"),
        low(row, "hold_reason"),
        low(row, "source_risk_flags"),
    ])
    return any(term in text for term in [
        "do not contact", "dnc", "opt out", "unsubscribe", "suppressed"
    ])


def score_row(row):
    industry_blob = " ".join([
        low(row, "segment"), low(row, "company_name"), low(row, "outreach_angle")
    ])
    title = low(row, "job_title")
    tech = low(row, "source_technologies")
    angle = low(row, "outreach_angle")
    score = 0
    reasons = []

    points = contains_score(industry_blob, INDUSTRY_TERMS)
    score += points
    if points:
        reasons.append("industrial/distribution fit")

    points = contains_score(title, TITLE_TERMS)
    score += points
    if points:
        reasons.append("decision-maker title")

    points = revenue_points(txt(row, "annual_revenue_usd"))
    score += points
    if points == 15:
        reasons.append("$5M-$100M revenue band")

    points = contains_score(tech, TECH_TERMS)
    score += points
    if points:
        reasons.append("relevant CRM/ERP signal")

    points = contains_score(angle, ANGLE_TERMS)
    score += points
    if points:
        reasons.append("revenue-leak angle present")

    decision_fit = low(row, "decision_maker_fit")
    if decision_fit == "primary":
        score += 10
        reasons.append("primary decision maker")
    elif decision_fit:
        score += 5

    try:
        source_score = float(txt(row, "source_score"))
        if source_score >= 80:
            score += 8
        elif source_score >= 65:
            score += 4
    except ValueError:
        pass

    email_status = normalize_status(row)
    if email_status == "verified":
        score += 4
        reasons.append("verified email")
    elif email_status == "invalid":
        score -= 25
        reasons.append("invalid email")
    elif email_status == "risky":
        score -= 3
        reasons.append("catch-all/risky email")

    if suppressed(row):
        score = 0
        reasons.append("suppressed/do-not-contact")

    score = max(0, min(100, int(round(score))))
    tier = "A" if score >= 75 else "B" if score >= 60 else "C" if score >= 45 else "D"
    return score, tier, email_status, reasons


def signal(row):
    angle = txt(row, "outreach_angle")
    if angle:
        return angle
    tech = low(row, "source_technologies")
    for name in ["Prophet 21", "Epicor", "Infor", "RubberTree", "Salesforce", "HubSpot"]:
        if name.lower() in tech:
            return f"Uses {name}; test whether current systems surface declining/dormant accounts"
    title = txt(row, "job_title")
    if title:
        return f"{title} is likely accountable for customer retention, quote follow-up, or sales visibility"
    return "Industrial revenue-leak diagnostic fit"


def run(input_csv, output_csv):
    with open(input_csv, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0].keys()) if rows else []

    extras = [
        "scout_score", "scout_tier", "scout_email_status",
        "scout_research_priority", "scout_send_ready",
        "scout_signal", "scout_score_reasons",
    ]

    for row in rows:
        score, tier, email_status, reasons = score_row(row)
        row.update({
            "scout_score": score,
            "scout_tier": tier,
            "scout_email_status": email_status,
            "scout_research_priority": "Yes" if score >= 60 and not suppressed(row) else "No",
            "scout_send_ready": "Yes" if score >= 60 and email_status == "verified" and not suppressed(row) else "No",
            "scout_signal": signal(row),
            "scout_score_reasons": "; ".join(reasons),
        })

    rows.sort(key=lambda row: (int(row["scout_score"]), row.get("source_score", "")), reverse=True)
    with open(output_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields + extras)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description="RevAnvil Scout CSV scoring pipeline")
    parser.add_argument("input_csv")
    parser.add_argument("-o", "--output", default="revanvil_scout_scored.csv")
    args = parser.parse_args()

    rows = run(args.input_csv, args.output)
    research = sum(row["scout_research_priority"] == "Yes" for row in rows)
    send_ready = sum(row["scout_send_ready"] == "Yes" for row in rows)
    tiers = {tier: sum(row["scout_tier"] == tier for row in rows) for tier in "ABCD"}
    print(
        f"Processed {len(rows)} leads | research_priority={research} | "
        f"send_ready={send_ready} | tiers={tiers} | output={args.output}"
    )


if __name__ == "__main__":
    main()
