# SQL query guide

This guide covers the 31 analytical queries and 9 data-quality queries. Each query is listed with:
- its business question;
- the SQL techniques it uses;
- what the result showed.

The SQL is in `sql/`. The full result of every query is in `reports/sql_outputs/<name>.csv` (regenerate with `python -m src.run_sql`). All figures describe the **fictional** Tausi Digital Bank, built from synthetic data, except the CBK context table.

Technique key: **J** = joins · **RJ** = range join (to the versioned tariff) · **A** = aggregation · **CW** = CASE WHEN · **F** = conditional aggregation (`FILTER`) · **CTE** · **W** = window function · **R** = `ROLLUP` · **P** = percentiles · **GS** = `generate_series` · **V** = semantic view

## Semantic layer (`03_views.sql`)
| View | Why it exists |
|---|---|
| `mv_transactions` (materialized) | One row per attempt (2.02M). It is joined to the customer, the channel group, the type category, the failure owner and **the tariff row in force on the transaction date** (RJ), which gives the expected fee and the fee variance. It uses `LEAD()` over each account's history to infer retries and whether they recovered the failure. |
| `mv_customer_month` (materialized) | One row per customer per month after onboarding (430,738 rows), built with `generate_series`. It holds attempts, successes, value, fees, operational failures, channels used and the active flag. Activity rates and the failure–inactivity analysis use it. |
| `v_monthly_kpis` | One row per month: attempts, success and failure rates, value, fees, fee yield, customers onboarded, active customers and active rate. |
| `v_customer_summary` | One row per customer: lifetime value and fees, channels used, main channel, last successful transaction and activity status (Active / At risk / Dormant / Too new). |
| `mv_channel_hour` (materialized) | Every channel × hour in the window, including hours with no activity (79,056 rows): attempts, failures and p95 latency. It feeds the anomaly model and the hourly reliability chart. |

## Data quality (`02_data_quality_checks.sql`)
Part A profiles the raw staging layer:
- `dq_a01`: 60 extra customer rows and 6,000 extra transaction rows.
- `dq_a02`: label variants such as `APP`, `MobileApp`, `IB` and `SUCCESS`.
- `dq_a03`: missing values, failure reasons that contradict the status, and the −1 latency sentinel.
- `dq_a04`: amounts by channel. ATM amounts above the KES 40,000 limit are exactly 100× valid amounts.
- `dq_a05`: timestamps in 2035/2036.
- `dq_a06`: onboarding years such as 2065.

Part B proves the clean layer is sound:
- `dq_b01`: eight integrity checks, all returning 0.
- `dq_b02`: the semantic views carry every transaction exactly once, and value and fees reconcile to the shilling.
- `dq_b03`: summary of the exception log.

## Volume and channels (`04_volume_and_channels.sql`)

**q01_headline_kpis**: *How big is the business, and how did the latest month compare with a year earlier?* CTE, A, UNION ALL, V.
Result: 2,022,688 attempts, 1,951,275 successful (96.47%), KES 29.33bn moved, KES 51.72M of fees (KES 26.50 per successful transaction). October 2026 compared with October 2025: successful transactions +49%, fees +47%, active customers 21,445 vs 15,729.

**q02_monthly_trend**: *How do volume, value, fees and activity move month by month?* W (`LAG`, `LAG(…, 12)` for year-on-year, 3-month moving average with `ROWS BETWEEN`), V.
Result: successful transactions grew from 79,845 to 142,328 a month, and fee yield stayed at 17–18 bps.

**q03_channel_mix_over_time**: *Is usage shifting between channels?* F, A.
Result: the app's share rose from 39.0% to 46.1% and USSD's fell from 19.8% to 12.9%. The other channels were flat.

**q04_channel_summary**: *What does each channel carry?* F, W (share of total with `SUM() OVER ()`), P.
Result: the app has 43% of volume and 31% of fees. Internet Banking has 51% of value (median amount KES 23,663). Agents have 12% of volume but 20% of fees. USSD has the lowest success rate (94.2%).

**q05_channel_by_type**: *Which transaction types run through which channels?* F, W (`RANK() OVER (PARTITION BY channel)`).
Result: sending money to mobile wallets is the top use of both the app and USSD, and interbank transfers are the top use of Internet Banking.

**q06_hour_by_weekday**: *When do customers transact?* A.
Result: the busiest slots are Friday 18:00–20:00 (about 300–310 successful transactions an hour). Nights and Sunday mornings are quiet.

**q07_payday_effect**: *How much busier is payday?* CTE, CW, F.
Result: from the 25th to the 2nd there are about 4,370 successful transactions a day, against 3,240 on other days (+35%). Agent withdrawals rise from 219 to 301 a day.

**q08_value_vs_fees_by_type**: *Which transaction types move money, and which earn fees?* F, W.
Result: interbank transfers are 56% of value and 42% of fees, and cash withdrawals are 9% of value and 35% of fees. Internal transfers, deposits, top-ups, merchant payments and airtime earn no fees.

## Reliability (`05_reliability.sql`)

**q09_success_rate_by_channel**: *How reliable is each channel once customer-caused failures are separated out?* F, W (`RANK`).
Result: operational failure rates are USSD 3.28%, Agent 2.56%, Wallet Link 1.38%, Internet Banking 1.37%, App 1.15% and ATM 0.56%.

**q10_failure_reasons_by_channel**: *Which failure reasons dominate each channel?* A, W (share within channel).
Result: USSD's top reason is session drops (36% of its failures). The agent channel's top reason is float unavailable. On the app, the largest share of failures is insufficient funds, a customer-caused reason.

**q11_failure_by_hour**: *Do operational failures rise at peak hours?* F.
Result: USSD goes from about 2.3% in the daytime to 5.0–5.3% between 18:00 and 21:00. The app stays at 1.0–1.6%.

**q12_agent_float_month_end**: *Do agents run out of cash at month end and weekends?* CW, F.
Result: float failures are 6.2% on payday weekdays, 6.3% on payday weekends and 5.6% on other weekends, against 1.0% on ordinary weekdays.

**q13_retry_and_recovery**: *After a failure, do customers retry, and does it work?* F, R (`ROLLUP` over owner and reason).
Result: 52.8% of operational failures are retried within 30 minutes and 49.1% are recovered. 20–30% of those retries happen on another channel. During outages only 19.1% are recovered. Wrong-PIN failures are recovered 57% of the time.

**q14_incident_impact**: *What did each logged incident cost?* J (time-window join), CTE, A.
Result: affected channels failed 41–62% of attempts during incidents, against 0.6–5.2% at the same hours normally. The largest losses were the June 2026 agent outage (197 unrecovered, KES 10.7k in fees) and the March 2026 app outage (245 unrecovered).

**q15_daily_app_timeouts**: *What happened to the app in March 2026?* F, P.
Result: the daily timeout rate was 3.3–4.1% from 10 to 19 March, against about 0.5% before and after, and p95 latency doubled.

**q16_latency_by_channel_month**: *How fast does each channel respond?* P.
Result: p95 processing time is about 3.2s on Internet Banking, 3.9s on the app, 4.7s at ATMs, 5.3s at agents, 8.5s on Wallet Link and 9s on USSD.

## Fees (`06_fees.sql`)

**q17_fee_income_by_channel_month**: *Where does fee income come from, and how is it trending?* F.
Result: monthly fee income grew from KES 2.10M to 3.75M. The app earns the most (KES 1.22M in October 2026).

**q18_fee_income_by_segment**: *Which segments pay the fees?* F, W.
Result: SME customers pay 41.5% of fees (KES 6,375 per customer over the period), Salaried customers 20.9%, Mass Retail 16.2%, Premier 12.8% and Youth 8.6%.

**q19_fees_lost_to_failures**: *What fee income is lost when an operational failure is not recovered?* F, R.
Result: 16,822 unrecovered failures cost KES 632,764 of fees and left KES 251M unmoved. Agent float shortages alone cost KES 209k.

**q20_fee_billing_exceptions**: *Were customers charged what the tariff says?* RJ (via the view), CW, A.
Result: 4,051 transactions were overcharged by KES 96,444 in total, in two defects with exact date boundaries: the old fee kept on waived transfers (1–14 Feb 2026), and fees on failed transfers (2 Mar – 12 Apr 2026). No transaction was undercharged.

**q21_wallet_fee_waiver**: *Did the waiver move small transfers to the app?* CTE, F, W.
Result: the app's share of transfers of KES 1,000 or less rose from 60.4% to 75.1% (Aug 2025 – Jan 2026 compared with Feb – Jul 2026).

**q22_waiver_fee_foregone**: *What does the waiver cost?* CW, A.
Result: about KES 54–72k a month, KES 562k from February to October 2026.

## Customer activity (`07_customer_activity.sql`)

**q23_activity_by_segment**: *How active is each segment?* A, V.
Result: average monthly active rates are Premier 83.8%, Salaried 79.8%, SME 79.0%, Mass Retail 67.7% and Youth 67.6%. Active SME customers make 11.8 successful transactions a month.

**q24_activity_status**: *Who is active, at risk or dormant at 31 October 2026?* A, W, V.
Result: 5,591 customers are dormant (19% overall: Youth 24%, Mass Retail 21%, Premier 11%).

**q25_dormancy_by_onboarding_channel**: *Does the way a customer joined matter?* F, V.
Result: dormancy is 19–21% whichever way the customer joined. There is no meaningful difference.

**q26_new_customer_activation**: *Do new customers become active?* CTE, date arithmetic (`age()`), F.
Result: about 67% are active in the month they join (a partial month) and about 72% in each month after that.

**q27_multichannel_customers**: *Are multi-channel customers more valuable?* CTE, A, CW.
Result: customers using 3+ channels make 5.8 transactions a month, against 0.3 for single-channel customers. This is descriptive: heavy users naturally use more channels.

**q28_failures_and_next_month_activity**: *Is a bad experience followed by lower activity?* W (`LEAD` over each customer's months), CW, F.
Result: within the same activity band, customers who hit 2+ operational failures were more often inactive the next month: 4.4% vs 2.0% (10+ attempts) and 6.8% vs 4.4% (4–9 attempts).

**q29_value_concentration**: *How concentrated are value and fees?* W (`NTILE(10)`, cumulative `SUM() OVER (ORDER BY …)`), V.
Result: the top decile of customers moves 71.3% of value but pays 47.3% of fees.

**q30_digital_adoption_by_segment**: *How fast is each segment moving to the app?* F.
Result: among active Mass Retail customers, the share using the app rose from 56% to 74% and the share using USSD fell from 67% to 56%.

**q31_market_context**: *Is the bank growing faster than the national market?* J, W (`FIRST_VALUE` for indexing).
Result: by September 2026 the bank's volume index was 173, against 100 for national agent transactions (CBK, real data).
