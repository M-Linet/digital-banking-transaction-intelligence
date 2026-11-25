-- 07_customer_activity.sql: who is active, who is going quiet, and does a bad experience matter?

-- @query q23_activity_by_segment
-- Q23: how active is each segment? Average monthly active rate and activity per active customer.
SELECT segment, COUNT(DISTINCT customer_id) AS customers,
       ROUND(100.0 * AVG(is_active::int), 1) AS avg_monthly_active_rate_pct,
       ROUND(AVG(successful) FILTER (WHERE is_active), 1) AS txns_per_active_month,
       ROUND(AVG(value_kes) FILTER (WHERE is_active), 0) AS value_per_active_month_kes,
       ROUND(AVG(fee_income) FILTER (WHERE is_active), 1) AS fees_per_active_month_kes
FROM bank.mv_customer_month GROUP BY 1 ORDER BY avg_monthly_active_rate_pct DESC;

-- @query q24_activity_status
-- Q24: how many customers are active, at risk or dormant at the end of October 2026?
SELECT segment, activity_status, COUNT(*) AS customers,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY segment), 1) AS share_of_segment_pct
FROM bank.v_customer_summary GROUP BY 1, 2 ORDER BY 1, 2;

-- @query q25_dormancy_by_onboarding_channel
-- Q25: does the way a customer joined relate to whether they stay active?
SELECT onboarding_channel,
       COUNT(*) FILTER (WHERE activity_status <> 'Too new to assess') AS assessable_customers,
       ROUND(100.0 * COUNT(*) FILTER (WHERE activity_status = 'Dormant (90+ days)')
             / NULLIF(COUNT(*) FILTER (WHERE activity_status <> 'Too new to assess'), 0), 1) AS dormant_pct,
       ROUND(AVG(channels_used) FILTER (WHERE activity_status <> 'Too new to assess'), 2) AS avg_channels_used
FROM bank.v_customer_summary GROUP BY 1 ORDER BY dormant_pct DESC;

-- @query q26_new_customer_activation
-- Q26: do new customers become active? Share of each onboarding cohort active k months after joining.
WITH c AS (
    SELECT customer_id, month, date_trunc('month', cs.onboarding_date)::date AS cohort, is_active
    FROM bank.mv_customer_month cm JOIN bank.customers cs USING (customer_id)
    WHERE cs.onboarding_date >= DATE '2025-05-01'
)
SELECT cohort, COUNT(DISTINCT customer_id) AS cohort_size,
       (EXTRACT(year FROM age(month, cohort)) * 12 + EXTRACT(month FROM age(month, cohort)))::int AS months_since_onboarding,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_active) / COUNT(*), 1) AS active_pct
FROM c GROUP BY cohort, months_since_onboarding
HAVING (EXTRACT(year FROM age(month, cohort)) * 12 + EXTRACT(month FROM age(month, cohort)))::int <= 12
ORDER BY cohort, months_since_onboarding;

-- @query q27_multichannel_customers
-- Q27: are customers who use several channels more valuable? Customers active in Oct 2026, by channel count
-- over their last six months (May - Oct 2026).
WITH six AS (
    SELECT customer_id, segment, COUNT(DISTINCT channel) AS channels,
           COUNT(*) AS successful, SUM(fee_charged) AS fees, SUM(amount) AS value_kes
    FROM bank.mv_transactions WHERE is_success AND month >= DATE '2026-05-01'
    GROUP BY 1, 2
)
SELECT CASE WHEN channels >= 3 THEN '3+ channels' ELSE channels || ' channel' || CASE WHEN channels > 1 THEN 's' ELSE '' END END AS channel_count,
       COUNT(*) AS customers,
       ROUND(AVG(successful) / 6, 1) AS txns_per_month,
       ROUND(AVG(fees) / 6, 1) AS fees_per_month_kes,
       ROUND(AVG(value_kes) / 6, 0) AS value_per_month_kes
FROM six GROUP BY 1 ORDER BY 1;

-- @query q28_failures_and_next_month_activity
-- Q28: is a bad experience followed by lower activity? Customer-months in which the customer was active, split by
-- the operational failures they hit; outcome = still active next month. Busy customers both transact more and hit
-- more failures, so the comparison is made within bands of monthly activity (attempts in the month).
WITH x AS (
    SELECT customer_id, month, is_active, attempts, operational_failures,
           LEAD(is_active) OVER (PARTITION BY customer_id ORDER BY month) AS active_next_month
    FROM bank.mv_customer_month
)
SELECT CASE WHEN attempts <= 3 THEN 'a) 1-3 attempts' WHEN attempts <= 9 THEN 'b) 4-9 attempts' ELSE 'c) 10+ attempts' END AS activity_band,
       CASE WHEN operational_failures = 0 THEN 'a) none' WHEN operational_failures = 1 THEN 'b) 1' ELSE 'c) 2 or more' END AS operational_failures_in_month,
       COUNT(*) AS customer_months,
       ROUND(100.0 * AVG(active_next_month::int), 1) AS active_next_month_pct
FROM x
WHERE is_active AND active_next_month IS NOT NULL
GROUP BY 1, 2 ORDER BY 1, 2;

-- @query q29_value_concentration
-- Q29: how concentrated are value and fees across customers?
WITH c AS (
    SELECT customer_id, value_kes, fee_income, NTILE(10) OVER (ORDER BY value_kes DESC) AS value_decile
    FROM bank.v_customer_summary WHERE successful_txns > 0
)
SELECT value_decile, COUNT(*) AS customers,
       ROUND(100.0 * SUM(value_kes) / SUM(SUM(value_kes)) OVER (), 1) AS share_of_value_pct,
       ROUND(100.0 * SUM(fee_income) / SUM(SUM(fee_income)) OVER (), 1) AS share_of_fees_pct,
       ROUND(100.0 * SUM(SUM(value_kes)) OVER (ORDER BY value_decile) / SUM(SUM(value_kes)) OVER (), 1) AS cumulative_value_pct
FROM c GROUP BY 1 ORDER BY 1;

-- @query q30_digital_adoption_by_segment
-- Q30: how quickly is each segment moving to the app? Share of active customers who used the app, and who
-- used USSD, in the first and last month of the window.
SELECT segment, month,
       COUNT(*) FILTER (WHERE is_active) AS active_customers,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_active AND used_app) / NULLIF(COUNT(*) FILTER (WHERE is_active), 0), 1) AS used_app_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_active AND used_ussd) / NULLIF(COUNT(*) FILTER (WHERE is_active), 0), 1) AS used_ussd_pct
FROM bank.mv_customer_month WHERE month IN (DATE '2025-05-01', DATE '2026-10-01')
GROUP BY 1, 2 ORDER BY 1, 2;

-- @query q31_market_context
-- Q31: is the bank growing faster than the national mobile-payments market? Both indexed to May 2025 = 100.
-- Market = CBK published agent cash-in/cash-out volume (REAL data); bank = synthetic successful transactions.
WITH b AS (SELECT month, successful, value_kes FROM bank.v_monthly_kpis),
     k AS (SELECT month, transactions_millions, value_kes_billions FROM bank.context_cbk_mobile_payments)
SELECT b.month,
       ROUND(100.0 * b.successful / FIRST_VALUE(b.successful) OVER (ORDER BY b.month), 1)                 AS bank_volume_index,
       ROUND(100.0 * k.transactions_millions / FIRST_VALUE(k.transactions_millions) OVER (ORDER BY b.month), 1) AS market_volume_index,
       ROUND(100.0 * b.value_kes / FIRST_VALUE(b.value_kes) OVER (ORDER BY b.month), 1)                   AS bank_value_index,
       ROUND(100.0 * k.value_kes_billions / FIRST_VALUE(k.value_kes_billions) OVER (ORDER BY b.month), 1) AS market_value_index
FROM b JOIN k USING (month) ORDER BY b.month;
