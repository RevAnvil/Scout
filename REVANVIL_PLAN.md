# RevAnvil Scout v1

This fork adapts Harvey into an intelligence layer for RevAnvil's industrial outbound process.

## Objective

Scout should improve lead quality and research before prospects reach Instantly. It is not meant to replace the current Instantly campaign or run fully autonomous sales conversations.

## v1 operating model

1. Discover/import industrial companies
2. Profile company websites and public signals
3. Score against RevAnvil ICP
4. Identify one credible outreach signal
5. Produce personalization notes / sequencer-ready export
6. Hand off to Instantly
7. Keep human approval for outbound and important replies

## Guardrails

- Do not autonomously send replies on behalf of RevAnvil in v1
- Do not invent ERP/CRM usage
- Do not imply recoverable revenue when only prior revenue is known
- Do not contact explicit opt-outs
- Do not add new software-platform claims to RevAnvil's offer
- Prefer research/scoring value over higher send volume

## RevAnvil positioning

RevAnvil helps industrial distributors and manufacturers find commercial leakage already hiding in their customers, quoting process, and existing systems.

Core leak categories include:
- dormant customers
- declining accounts
- missed RFQ / quote follow-up
- overdue orders
- CRM/ERP visibility gaps
- sales-pipeline delays and ownership gaps

Approved proof point:
- One distributor had 68 customers disappear year over year, representing about $680k in prior revenue.

This is not a promise that all $680k is recoverable.

## Current acquisition stack

Scout -> qualified/researched prospect -> Instantly -> human-reviewed reply -> meeting -> RevAnvil diagnostic

## Next engineering steps

- Add RevAnvil-specific signal detectors where public website data supports them
- Add/export RevAnvil score and signal rationale
- Add a safe "research only" mode for existing lead CSVs
- Test on a small batch before connecting any live Instantly API key
- Keep autonomous sender/handler disabled until explicitly approved
