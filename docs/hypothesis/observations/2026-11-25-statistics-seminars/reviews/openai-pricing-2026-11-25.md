# Снимок цен OpenAI

> **Вывод модели, не факт.** Отчёт субагента (Claude Opus, общий агент), 2026-11-25 18:23 UTC. Сохранён без правок; оценка его выводов — в карточках гипотез.

## Задание, которое получил агент

```text
Fetch https://developers.openai.com/api/docs/pricing.md (if it fails, https://developers.openai.com/api/docs/pricing). Report the current Standard-tier text-token prices per 1M tokens (input, cached input, output) for every listed model, especially the cheapest small models (e.g. gpt-6-luna, gpt-5.4-nano, gpt-5.4-mini, gpt-5-mini, gpt-5-nano) and mid models (gpt-6-sol, gpt-6.1-sol, gpt-6.1-terra). Also report Batch and Flex discounts, and embedding model prices (text-embedding-3-small/large). Quote the numbers exactly as the page states them and say the page date if shown.
```

## Отчёт

Source: https://developers.openai.com/api/docs/pricing.md (HTTP 200, text/markdown, fetched on the first try, so the non-.md URL was not needed). The page shows no publication or "last updated" date. The only dates in the text are the Ultrafast/Fast and promo notes: "Priority processing was renamed Fast mode on July 30, 2026" and "GPT-5.6 Sol's promotional pricing is available until November 30, 2026." Treat the prices as the page's current values as of the fetch (2026-11-25).

All numbers are USD per 1M text tokens, Standard tier, short context (<=272K input tokens) unless noted. Values are copied from the page's tables.

**Standard, small/cheap models** (table columns: Input / Output / Cached input)
- gpt-6-luna: $0.10 / $0.50 / $0.01 (long context: $0.20 / $0.75 / $0.02)
- gpt-5.6-luna: $0.20 / $1.20 / $0.02 (long context: $0.40 / $1.80 / $0.04)
- gpt-5.4-nano: $0.20 / $1.25 / $0.02
- gpt-5.4-mini: $0.75 / $4.50 / $0.075
- gpt-5-nano: $0.05 / $0.40 / $0.005
- gpt-5-mini: $0.25 / $2.00 / $0.025
- gpt-4.1-mini: $0.40 / $1.60 / $0.10
- gpt-4o-mini: $0.15 / $0.60 / $0.075

**Standard, mid models**
- gpt-6-sol: $2.00 / $10.00 / $0.20 (long context: $4.00 / $15.00 / $0.40)
- gpt-6.1-sol: $2.00 / $10.00 / $0.10 (long context: $4.00 / $15.00 / $0.20)
- gpt-6.1-terra: $1.00 / $5.00 / $0.05 (long context: $2.00 / $7.50 / $0.10)
- gpt-6.2-sol: $2.00 / $10.00 / $0.10 (long context: $4.00 / $15.00 / $0.20)
- gpt-5.6-sol: $4.00 / $20.00 / $0.40 (long context: $8.00 / $30.00 / $0.80)
- gpt-5.6-terra: $2.00 / $12.00 / $0.20 (long context: $4.00 / $18.00 / $0.40)

**Standard, other listed text models**
- gpt-6.2-astra: $8.00 / $40.00 / $0.40
- gpt-6-astra: $10.00 / $50.00 / $1.00
- gpt-5.5 (<272K): $5.00 / $30.00 / $0.50
- gpt-5.5-pro (<272K): $30.00 / $180.00 / -
- gpt-5.4 (<272K): $2.50 / $15.00 / $0.25
- gpt-5.4-pro (<272K): $30.00 / $180.00 / -
- gpt-5.2: $1.75 / $14.00 / $0.175
- gpt-5.2-pro: $21.00 / $168.00 / -
- gpt-5.1: $1.25 / $10.00 / $0.125
- gpt-5: $1.25 / $10.00 / $0.125
- gpt-5-pro: $15.00 / $120.00 / -
- gpt-4.1: $2.00 / $8.00 / $0.50
- gpt-4o: $2.50 / $10.00 / $1.25
- o3-pro: $20.00 / $80.00 / -
- o3: $2.00 / $8.00 / $0.50

The page also lists Fast, Ultrafast, specialized (cyber, healthcare, legal, Codex, chat-latest, search), image, audio/realtime, and fine-tuning tables. I did not transcribe those beyond what is needed here.

**Batch (Standard table values, same column order)**
- gpt-6-luna: $0.05 / $0.25 / $0.005
- gpt-6.1-terra: $0.50 / $2.50 / $0.025
- gpt-6.1-sol: $1.00 / $5.00 / $0.05
- gpt-6-sol: $1.00 / $5.00 / $0.10
- gpt-5.4-nano: $0.10 / $0.625 / $0.01
- gpt-5.4-mini: $0.375 / $2.25 / $0.0375
- gpt-5-nano: $0.025 / $0.20 / $0.0025
- gpt-5-mini: $0.125 / $1.00 / $0.0125
- gpt-5: $0.625 / $5.00 / $0.0625
- gpt-4.1: $1.00 / $4.00 / - (no cached rate listed)
- gpt-4o-mini: $0.075 / $0.30 / - (no cached rate listed)

The page gives explicit Batch numbers and no percentage. For most rows the Batch price is exactly half the Standard price, which is my own arithmetic, not a page statement.

**Flex (beta; the page says it is being wound down and will not be added to new models; retirement date not yet published)**
- gpt-6-luna: $0.05 / $0.25 / $0.005
- gpt-6.1-terra: $0.50 / $2.50 / $0.025
- gpt-6.1-sol: $1.00 / $5.00 / $0.05
- gpt-6-sol: $1.00 / $5.00 / $0.10
- gpt-5.4-nano: $0.10 / $0.625 / $0.01
- gpt-5.4-mini: $0.375 / $2.25 / $0.0375
- gpt-5-nano: $0.025 / $0.20 / $0.0025
- gpt-5-mini: $0.125 / $1.00 / $0.0125
- gpt-5: $0.625 / $5.00 / $0.0625

**Other discount notes from the page**
- For background requests on gpt-6.2-astra, gpt-6.2-sol, gpt-6.1-terra and gpt-6-luna, the page says model token rates are 30% below Standard for the 2-hour completion window and 40% below Standard for the 8-hour window. These are background-request windows, not the Batch tier.
- Regional processing (data residency) endpoints carry a 10% uplift for models released on or after March 5, 2026. FedRAMP endpoints also carry a 10% uplift.
- Fast mode costs more than Standard (for example gpt-6.1-sol Fast is $4.00 / $20.00 / $0.20).

**Embeddings (Standard, per 1M tokens; the page lists only an input price)**
- text-embedding-3-small: $0.02
- text-embedding-3-large: $0.13
- text-embedding-ada-002: $0.10 (for reference)
- No Batch or Flex embedding prices are listed.

Read: https://developers.openai.com/api/docs/pricing.md (only this URL was fetched).
