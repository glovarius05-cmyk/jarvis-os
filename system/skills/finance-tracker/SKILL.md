---
name: Finance Tracker
description: "Parse receipts, transactions, and expense logs from chat messages into monthly Markdown balance sheets."
group: Productivity
---

# Finance Tracker

## When to use
Activate when the user shares receipt data, expense descriptions, payment confirmations, or asks for spending summaries.

## Procedure
1. Parse the transaction details:
   - Date / Timestamp
   - Category (e.g. Hosting/SaaS, Equipment, Dining, Transport, Subscriptions)
   - Amount and Currency
   - Description / Vendor
2. Read or initialize the monthly ledger `finances/YYYY-MM.md` in Second Brain.
3. Append the transaction row to the ledger table:
   `| Date | Category | Vendor | Amount | Notes |`
4. Recalculate monthly totals by category and return a concise receipt confirmation.
