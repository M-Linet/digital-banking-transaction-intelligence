-- 04_volume_and_channels.sql: how much moves, through which channels, and when.
-- Value (KES moved) is reported separately from fee income (KES earned) throughout.

-- @query q01_headline_kpis
-- Q1: what is the size of the business, and how did the latest month compare with a year earlier?
WITH m AS (SELECT * FROM bank.v_monthly_kpis)
SELECT 'May 2025 - Oct 2026' AS period, SUM(attempts) AS attempts, SUM(successful) AS successful_transactions,
       ROUND(100.0 * SUM(successful) / SUM(attempts), 2) AS success_rate_pct,
       ROUND(SUM(value_kes) / 1e9, 2) AS value_kes_bn, ROUND(SUM(fee_income) / 1e6, 2) AS fee_income_kes_m,
       ROUND(SUM(fee_income) / SUM(successful), 2) AS fee_per_successful_txn,
       NULL::bigint AS active_customers, NULL::numeric AS active_rate_pct
FROM m
UNION ALL
SELECT to_char(month, 'Mon YYYY'), attempts, successful, success_rate_pct, ROUND(value_kes / 1e9, 2), ROUND(fee_income / 1e6, 2),
       fee_per_successful_txn, active_customers, active_rate_pct
FROM m WHERE month IN (DATE '2025-10-01', DATE '2026-10-01');

-- @query q02_monthly_trend
-- Q2: how do volume, value, fee income and active customers move month by month?
SELECT month, attempts, successful, success_rate_pct, ROUND(value_kes / 1e6, 1) AS value_kes_m,
       ROUND(fee_income / 1e3, 0) AS fee_income_kes_k, fee_yield_bps, active_customers, active_rate_pct,
       ROUND(100.0 * (successful::numeric / LAG(successful) OVER (ORDER BY month) - 1), 1) AS successful_mom_pct,
       ROUND(AVG(success_rate_pct) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS success_rate_3m_avg,
       ROUND(100.0 * (fee_income / LAG(fee_income, 12) OVER (ORDER BY month) - 1), 1) AS fee_income_yoy_pct
FROM bank.v_monthly_kpis ORDER BY month;

-- @query q03_channel_mix_over_time
-- Q3: is usage shifting between channels? Share of successful transactions by channel, each month.
SELECT month,
       ROUND(100.0 * COUNT(*) FILTER (WHERE channel = 'Mobile App') / COUNT(*), 1)       AS mobile_app_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE channel = 'USSD') / COUNT(*), 1)             AS ussd_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE channel = 'Agent') / COUNT(*), 1)            AS agent_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE channel = 'ATM') / COUNT(*), 1)              AS atm_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE channel = 'Internet Banking') / COUNT(*), 1) AS internet_banking_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE channel = 'Wallet Link') / COUNT(*), 1)      AS wallet_link_pct
FROM bank.mv_transactions WHERE is_success GROUP BY month ORDER BY month;

-- @query q04_channel_summary
-- Q4: what does each channel carry: volume, value, fees, reliability and speed?
SELECT channel, channel_group,
       COUNT(*) FILTER (WHERE is_success)                                         AS successful,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_success) / SUM(COUNT(*) FILTER (WHERE is_success)) OVER (), 1) AS share_of_volume_pct,
       ROUND(SUM(amount) FILTER (WHERE is_success) / 1e6, 1)                      AS value_kes_m,
       ROUND(100.0 * SUM(amount) FILTER (WHERE is_success) / SUM(SUM(amount) FILTER (WHERE is_success)) OVER (), 1) AS share_of_value_pct,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success) / 1e6, 2)                 AS fee_income_kes_m,
       ROUND(100.0 * SUM(fee_charged) FILTER (WHERE is_success) / SUM(SUM(fee_charged) FILTER (WHERE is_success)) OVER (), 1) AS share_of_fees_pct,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount)::numeric, 0)     AS median_amount_kes,
       ROUND(100.0 * AVG(is_success::int), 2)                                     AS success_rate_pct,
       ROUND(100.0 * AVG(COALESCE(is_operational_failure, FALSE)::int), 2)        AS operational_failure_pct
FROM bank.mv_transactions GROUP BY 1, 2 ORDER BY successful DESC;

-- @query q05_channel_by_type
-- Q5: which transaction types run through which channels?
SELECT channel, transaction_type, COUNT(*) FILTER (WHERE is_success) AS successful,
       ROUND(SUM(amount) FILTER (WHERE is_success) / 1e6, 1) AS value_kes_m,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success) / 1e3, 0) AS fee_income_kes_k,
       RANK() OVER (PARTITION BY channel ORDER BY COUNT(*) FILTER (WHERE is_success) DESC) AS rank_in_channel
FROM bank.mv_transactions GROUP BY 1, 2 ORDER BY 1, 3 DESC;

-- @query q06_hour_by_weekday
-- Q6: when do customers transact? Average successful transactions per hour slot, by weekday.
SELECT iso_dow, to_char(DATE '2024-01-01' + (iso_dow - 1), 'Dy') AS weekday, hour,
       ROUND(COUNT(*) FILTER (WHERE is_success)::numeric
             / COUNT(DISTINCT txn_date), 1) AS avg_successful_per_day_slot
FROM bank.mv_transactions GROUP BY 1, 2, 3 ORDER BY 1, 3;

-- @query q07_payday_effect
-- Q7: how much busier are the payday days (25th to 2nd) than the rest of the month?
WITH d AS (
    SELECT txn_date, is_payday_window, COUNT(*) FILTER (WHERE is_success) AS successful,
           SUM(amount) FILTER (WHERE is_success) AS value_kes,
           COUNT(*) FILTER (WHERE channel = 'Agent' AND transaction_type = 'Cash Withdrawal') AS agent_withdrawal_attempts
    FROM bank.mv_transactions GROUP BY 1, 2
)
SELECT CASE WHEN is_payday_window THEN 'Payday window (25th-2nd)' ELSE 'Rest of month' END AS period,
       COUNT(*) AS days, ROUND(AVG(successful), 0) AS avg_successful_per_day,
       ROUND(AVG(value_kes) / 1e6, 1) AS avg_value_kes_m_per_day,
       ROUND(AVG(agent_withdrawal_attempts), 0) AS avg_agent_withdrawal_attempts_per_day
FROM d GROUP BY 1 ORDER BY 1;

-- @query q08_value_vs_fees_by_type
-- Q8: value moved is not revenue. Which transaction types move money, and which earn fees?
SELECT transaction_type, type_category,
       COUNT(*) FILTER (WHERE is_success)                                        AS successful,
       ROUND(SUM(amount) FILTER (WHERE is_success) / 1e6, 1)                     AS value_kes_m,
       ROUND(100.0 * SUM(amount) FILTER (WHERE is_success) / SUM(SUM(amount) FILTER (WHERE is_success)) OVER (), 1) AS share_of_value_pct,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success) / 1e6, 2)                AS fee_income_kes_m,
       ROUND(100.0 * SUM(fee_charged) FILTER (WHERE is_success) / SUM(SUM(fee_charged) FILTER (WHERE is_success)) OVER (), 1) AS share_of_fees_pct,
       ROUND(10000.0 * SUM(fee_charged) FILTER (WHERE is_success) / NULLIF(SUM(amount) FILTER (WHERE is_success), 0), 1) AS fee_yield_bps
FROM bank.mv_transactions GROUP BY 1, 2 ORDER BY value_kes_m DESC;
