# Interview preparation: Digital Banking Transaction Intelligence

Practise answering aloud in under two minutes each. The "covers" notes are what an interviewer listens for, not a script.

## Business questions

1. **Walk me through this project in two minutes.**
   *Covers:*
   - The setting: a fictional Kenyan bank with six channels, growing fast but blind to reliability and fee leakage.
   - The data: synthetic, and why.
   - The build: a PostgreSQL model with a versioned tariff and a semantic view.
   - The headlines: value ≠ revenue; USSD evening failures; agent float on paydays; KES 633k of fees lost to unrecovered failures; KES 96k of overcharges from two fee-engine defects; 19% dormant.
   - Six recommendations, then the limitations.

2. **Why not use PaySim?**
   *Covers:*
   - PaySim is itself simulated and covers 30 days.
   - It has no channel, status, failure reason, fee or customer fields, so none of my questions could be answered with it.
   - It was built for fraud detection, which I deliberately avoided because I already have a fraud project.
   - I used its transaction-type mix as a reference and generated data with documented mechanisms and a defect manifest.

3. **Why is "value moved" not revenue, and why does it matter?**
   *Covers:*
   - The bank earns fees, not the amount moved.
   - Interbank transfers are 56% of value but 42% of fees.
   - Cash withdrawals are 9% of value but 35% of fees.
   - Internal transfers earn nothing.
   - Ranking channels by value would send investment to the wrong place. Every chart labels the two separately.

4. **How did you define a failure rate, and why split it?**
   *Covers:*
   - About half of failures are the customer's own: insufficient funds, wrong PIN, limits.
   - A `failure_reasons` reference table gives each reason an owner.
   - The operational failure rate (bank or partner) is the reliability KPI.
   - Mixed together, USSD's problem would be diluted.

5. **What's the single most valuable fix?**
   *Covers:*
   - Agent float on paydays and weekends: 6% vs 1% float failures.
   - It is the largest source of lost fees, and it hits customers when they need cash.
   - It is cheap to start: alerts for the top 100 agents.

6. **How did you find the billing defects?**
   *Covers:*
   - Join every transaction to the tariff row in force on its date (a range join on amount band and effective dates).
   - Expected fee = tariff fee if successful, else 0. Variance = charged − expected.
   - That found two clusters with exact date boundaries: KES 96k across 4,051 transactions.
   - No data-quality rule would catch this, because each row is internally valid.

7. **Did the fee waiver work?**
   *Covers:*
   - It moved small transfers from USSD to the app (60% → 75%) at about KES 62k a month of foregone fees, about 2% of fee income.
   - It also moved load off the least reliable channel.
   - The comparison is before/after, not an experiment, so a regional pilot would measure it properly next time.

8. **Do failures make customers leave?**
   *Covers:*
   - The raw comparison misleads: busy customers hit more failures *and* stay active.
   - Within activity bands, customers with 2+ failures were about twice as likely to be inactive the next month (4.4% vs 2.0%).
   - It is an association, not proof. It is still enough to justify a proactive follow-up with a hold-out group.

## SQL questions

9. **How did you infer retries?**
   *Covers:*
   - `LEAD()` over each account's transactions ordered by time.
   - A retry is the next attempt with the same type and amount within 30 minutes.
   - Recovered means the retry succeeded.
   - The window choice is documented and tested in Python against the SQL.
   - The limitation: a real system would provide a session or idempotency key.

10. **Explain the tariff join.**
    *Covers:*
    - `LEFT JOIN fee_schedule ON type = type AND channel = channel AND amount BETWEEN band_min AND band_max AND date >= effective_from AND (effective_to IS NULL OR date <= effective_to)`.
    - A data-quality check proves no transaction matches two rows.
    - Free types get `COALESCE(fee, 0)`.

11. **Why materialized views?**
    *Covers:*
    - Window functions over 2M rows and the tariff join are expensive. Computing them once makes 40 queries fast and consistent.
    - The grids (`mv_customer_month`, `mv_channel_hour`) include rows for months and hours with zero activity, which a plain GROUP BY would leave out.
    - They are refreshed in the pipeline.

12. **Show me a window function you used.**
    *Covers:*
    - `LEAD` for retries and next-month activity.
    - `LAG(…, 12)` for year-on-year.
    - `NTILE(10)` plus a cumulative `SUM() OVER (ORDER BY decile)` for concentration.
    - `FIRST_VALUE` to index the bank and CBK series to 100.
    - `RANK() OVER (PARTITION BY channel)`.

13. **How did you measure what an incident cost?**
    *Covers:*
    - A time-window join from `system_incidents` to transactions on channel and timestamp.
    - The failure rate inside the window is compared with the same channel's normal rate at the same hours of the day.
    - Unrecovered failures × tariff fee gives the fees lost.

## Python and data-quality questions

14. **What data problems did you find, and what did you *not* clean?**
    *Covers:*
    - The problems: duplicates; channel and status variants; 2065 onboarding years; ×100 ATM amounts; negative amounts; future timestamps; orphan accounts; failures without a reason; the −1 latency sentinel.
    - The handling: fix what is certain, quarantine what would be a guess (`quarantined_transactions` with a reason), log everything.
    - What I did *not* clean: wrong fees. They are the finding.

15. **Why Isolation Forest, and how do you know it worked?**
    *Covers:*
    - It is unsupervised: there are no incident labels to train on.
    - The unit is a channel-hour, with features for volume against normal, smoothed failure rate and p95 latency, standardised within each channel. Quiet hours are skipped.
    - It found all 11 logged incidents and three unlogged ones. The synthetic data let me confirm those three against the generator's private list.
    - The weakness: it misses slow degradations such as the app release, which the daily view catches.

16. **What went wrong along the way?**
    *Covers:*
    - Retries switched to a channel that does not offer the transaction type. An integrity check caught 293 rows, and I fixed the generator.
    - Account open dates were clipped before onboarding, caught by another integrity check.
    - The incident "normal rate" was computed from failures only (a NULL in `AVG`), so I added `COALESCE` in the view.
    - My first anomaly settings flagged noisy night hours, so I added the smoothing and the minimum volume.

## Power BI questions

17. **Explain your model.**
    *Covers:*
    - Facts are pre-aggregated to a daily grain (2M rows → about 48k), because the dashboard never needs individual rows.
    - Separate facts for transactions, failures (with retry outcomes) and channel-hours, plus the customer-month grid.
    - Date, channel and segment dimensions, all with single-direction relationships.
    - The market-context table is deliberately unrelated to the slicers.

18. **Write the channel share measure.**
    *Covers:* `DIVIDE([Successful], CALCULATE([Successful], REMOVEFILTERS(dim_channel)))`, used with the channel as a legend series over months.

## Behavioural questions

19. **What would you do first with real data?**
    *Covers:*
    - Agree failure-owner mappings with operations and the fee tariff history with finance.
    - Get session IDs for retries and balances for insufficient funds.
    - Set up the daily fee reconciliation before anything else.

20. **What was the hardest part?**
    *Covers:*
    - Definitions, not code: what counts as a failure, as a retry, as revenue.
    - Resisting the urge to "clean" the fee defects.
