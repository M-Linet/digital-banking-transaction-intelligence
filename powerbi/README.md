# Power BI: Tausi Digital Bank transaction dashboard

> The dashboard is a **Power BI project**: `TausiDigitalBank.pbip`, plus the `TausiDigitalBank.SemanticModel/` and `TausiDigitalBank.Report/` folders. It is stored as text (TMDL model, PBIR report), and `scripts/build_pbip.py` generates both parts. The model was loaded and refreshed in Power BI Desktop (Nov 2026 build), and the card values were checked against `kpi_reconciliation.csv`.
>
> **To open it:**
> 1. Run `python -m src.export_powerbi` to write the CSV extract to `data/processed/powerbi/`.
> 2. Run `python scripts/build_pbip.py` to point the `DataFolder` parameter at your copy of the project.
> 3. Open `TausiDigitalBank.pbip` and click **Refresh**.
>
> To get a single file, use *File → Save as → .pbix*. All data is synthetic, for a fictional bank, except the CBK market index.

## 1. Data model
```
                 dim_date (date, marked)        dim_channel (channel)        dim_segment (segment)
                /     |       |       \           /      |       \            /        |        \
 fact_txn_daily  fact_failures  fact_channel_hour  fact_customer_month (month, segment)   dim_customer (segment)

 market_context   (CBK index vs bank index; no relationships)
```
| Table | Grain | Rows |
|---|---|---|
| dim_date | day, 1 May 2025 – 31 Oct 2026 | 549 |
| dim_channel / dim_segment | channel / segment | 6 / 5 |
| dim_customer | customer, with activity status at 31 Oct 2026 | 30,000 |
| fact_txn_daily | date × channel × type × segment: attempts, successes, failures by owner, value, fees, overcharge | 47,848 |
| fact_failures | date × channel × reason: failures, retried, recovered, fees lost | 13,058 |
| fact_channel_hour | date × hour × channel: attempts, operational failures | 79,056 |
| fact_customer_month | customer × month: active flag, successes, value, fees | 430,738 |
| market_context | month: bank and CBK volume indices | 17 |

Decisions:
- **Daily pre-aggregation.** The 2M attempts are reduced to about 48k rows. Every measure the dashboard needs is additive at that grain. The one exception is "active customers", which needs the customer-month grid.
- **Single-direction relationships** from dimensions to facts only. `fact_customer_month` is filtered by date and segment, not by channel, because activity is defined across all channels.
- **The market-context table has no relationships**, because it is national data. Its chart title says so.

## 2. Measures (50 in `_Measures`: 32 business measures plus 18 formatting measures)
| Measure | DAX (short form) | Definition |
|---|---|---|
| Attempts / Successful Transactions | `SUM(fact_txn_daily[attempts])` / `[successful]` | a retry is a new attempt |
| Success Rate % | `DIVIDE([Successful], [Attempts])` | |
| Operational / Customer Failure Rate % | `DIVIDE(SUM(operational_failures), [Attempts])` | failures split by owner |
| Value Moved (KES) | `SUM(value_kes)` | successful attempts only; **not revenue** |
| Fee Income (KES) | `SUM(fee_income)` | revenue |
| Fee Yield (bps) | `DIVIDE([Fee Income], [Value Moved]) * 10000` | |
| Share of Transactions | `DIVIDE([Successful], CALCULATE([Successful], REMOVEFILTERS(dim_channel)))` | channel mix |
| Share of Value / Share of Fees | `REMOVEFILTERS` over transaction type | value vs revenue by type |
| Overcharge to Refund (KES) | `SUM(overcharge_kes)` | fee charged − tariff fee |
| Fees Lost to Failures (KES) | `SUM(fact_failures[fees_lost_kes])` | tariff fee of unrecovered operational failures |
| Recovered by Retry % | recovered ÷ operational failures | |
| Hourly Operational Failure Rate % | from `fact_channel_hour` | hour-of-day profile |
| Active Customers / Active Rate % | rows with `is_active` / customer-months | |
| … (latest month) | `VAR m = MAX(month) RETURN CALCULATE(…, month = m)` | |
| Dormant Customers / Dormant % | from `dim_customer[activity_status]` | |

## 3. Pages
Canvas 1280 × 720. Every page has the same slicers: **Date**, **Channel** and **Segment**.

| Page | Question | Visuals |
|---|---|---|
| Executive overview | Is the bank growing, and is the growth earning fees? | 6 KPI cards; successful transactions per month; fee income per month; share of value vs share of fees by type; bank vs CBK market index |
| Channels and fees | Which channels carry the volume, and which earn the fees? | 5 cards (incl. fees lost and overcharge); channel share over time; fee income by channel; value by channel; channel comparison table |
| Reliability | Where do transactions fail, and what does it cost? | 5 cards; operational failure rate by channel; failure rate by hour of day per channel; failures by reason; daily operational failure rate |
| Customer activity | Who is active, and who is going quiet? | 5 cards; active customers per month; active rate by segment; dormant share by segment; segment × activity-status matrix |

## 4. Validation
With no filters applied, the cards match SQL (`kpi_reconciliation.csv`):

| KPI | Power BI | SQL |
|---|---|---|
| Successful transactions | 1,951,275 | 1,951,275 |
| Value moved (KES) | 29,326,528,004 | 29,326,528,004 |
| Fee income (KES) | 51,717,546 | 51,717,546 |
| Success rate | 96.47% | 96.47% |
| Operational failure rate | 1.63% | 1.63% |
| Fees lost to unrecovered failures (KES) | 632,764 | 632,764 |
| Overcharge to refund (KES) | 96,444 | 96,444 |
| Active customers, Oct 2026 | 21,445 | 21,445 |
| Active rate, Oct 2026 | 71.5% | 71.5% |
| Dormant customers | 5,591 | 5,591 |

## 5. Design and accessibility
The layout is editorial: each page reads like a page of a business newspaper rather than a grid of equal boxes.
- **Story first:** each page opens with a kicker, a headline that states the finding (Georgia serif) and a two-line standfirst. One large hero chart carries the main evidence, and two smaller charts support it.
- **Right column:** big-number tiles show period-over-period deltas with ▲/▼ arrows. Below them, a navy **"What we'd do"** box turns the page into a recommendation.
- **Notes on the charts:** dashed reference lines mark events found in the data, such as the faulty app release, the two unlogged outages and the fee waiver.
- **Control-room touch:** the Reliability page has a channel status panel (`▲ Watch` / `● Normal`, from the `Channel Status` measure, threshold 2.5%).
- **Kenyan identity:** a kitenge-style geometric strip under the navy masthead (`kitenge_strip.png`) and the gold *Tausi* (peacock) wordmark.
- **Colour has one job per chart:** blue = focus, orange = the problem, grey = context. Highlight rules are measures (`Colour …`, display folder *6 Formatting*).
- **Colour-blind safe:** blue `#2a78d6` and orange `#eb6834` are the first two slots of a validated categorical palette. Their worst-case separation under simulated protanopia/deuteranopia is ΔE 24.7 (target ≥ 8), and both have at least 3:1 contrast on white. There is no red/green pairing. Colour is never the only cue: bars carry data labels, subtitles name what the colours mean, status uses text plus symbol, and deltas use arrows.

## 6. Screenshots
`screenshots/page1_executive_overview.png` … `page4_customer_activity.png`, taken from Power BI Desktop after refresh.
