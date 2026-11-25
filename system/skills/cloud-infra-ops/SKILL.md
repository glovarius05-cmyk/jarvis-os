---
name: Cloud & SaaS Ops
description: "Manage Cloudflare DNS/Workers, check Stripe payments/customers, and query Airtable bases via Composio."
group: Engineering
---

# Cloud & SaaS Ops

## When to use
Activate when the user asks about Cloudflare domains/DNS/Workers, Stripe revenue/customers/invoices, or Airtable bases and records.

## Procedure
1. Identify the target service (`cloudflare`, `stripe`, `airtable`).
2. For quick overviews, call `composio_feed(platform="cloudflare" | "stripe" | "airtable", limit=10)`.
3. For specific queries or mutations (e.g., DNS records, Stripe payouts, Airtable record updates):
   - Discover the exact tool with `composio_search(app="...", query="...")`.
   - Execute with `composio_action(app="...", action_name="...", arguments={...})`.
4. Always verify destructive changes before executing and show the full summary in chat.
