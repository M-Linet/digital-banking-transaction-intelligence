-- 03_views.sql: the semantic layer. Every KPI is defined once, here.
--
-- Definitions
--   Attempt            : one row in bank.transactions (a retry is a new attempt).
--   Success rate       : successful attempts / all attempts.
--   Operational failure: failure whose owner is the bank or a partner (timeout, USSD session dropped,
--                        agent float, third-party, outage). Customer failures (insufficient funds, wrong
--                        PIN, limit) are reported separately: they are not a reliability problem.
--   Transaction value  : KES moved by SUCCESSFUL attempts. It is NOT revenue.
--   Fee income         : fee_charged on successful attempts (the bank's revenue from transactions).
--   Expected fee       : fee per the tariff in force on the day, for successful attempts only (0 on failures).
--   Retry              : the next attempt on the same account with the same type and amount within 30 minutes.
--   Recovered failure  : a failure whose retry succeeded. Unrecovered operational failures are lost
--                        transactions; their tariff fee is "fee income lost to failures".
--   Active customer    : at least one successful transaction in the month.
--   Dormant customer   : onboarded before 1 Aug 2026 and no successful transaction since 3 Aug 2026 (90 days to 31 Oct).

DROP MATERIALIZED VIEW IF EXISTS bank.mv_transactions CASCADE;
CREATE MATERIALIZED VIEW bank.mv_transactions AS
WITH base AS (
    SELECT t.*,
           a.customer_id,
           c.segment,
           ch.channel_group,
           tt.direction,
           tt.category                                         AS type_category,
           fr.failure_owner,
           COALESCE(f.fee_kes, 0)                              AS tariff_fee,
           LEAD(t.txn_ts)           OVER w                     AS next_ts,
           LEAD(t.transaction_type) OVER w                     AS next_type,
           LEAD(t.amount)           OVER w                     AS next_amount,
           LEAD(t.status)           OVER w                     AS next_status,
           LEAD(t.channel)          OVER w                     AS next_channel
    FROM bank.transactions t
    JOIN bank.accounts a            USING (account_id)
    JOIN bank.customers c           USING (customer_id)
    JOIN bank.channels ch           USING (channel)
    JOIN bank.transaction_types tt  USING (transaction_type)
    LEFT JOIN bank.failure_reasons fr USING (failure_reason)
    LEFT JOIN bank.fee_schedule f
           ON f.transaction_type = t.transaction_type AND f.channel = t.channel
          AND t.amount BETWEEN f.band_min_kes AND f.band_max_kes
          AND t.txn_ts::date >= f.effective_from AND (f.effective_to IS NULL OR t.txn_ts::date <= f.effective_to)
    WINDOW w AS (PARTITION BY t.account_id ORDER BY t.txn_ts, t.transaction_id)
)
SELECT transaction_id, account_id, customer_id, segment, txn_ts,
       txn_ts::date                                         AS txn_date,
       date_trunc('month', txn_ts)::date                    AS month,
       EXTRACT(hour FROM txn_ts)::int                       AS hour,
       EXTRACT(isodow FROM txn_ts)::int                     AS iso_dow,
       (EXTRACT(day FROM txn_ts) >= 25 OR EXTRACT(day FROM txn_ts) <= 2) AS is_payday_window,
       channel, channel_group, transaction_type, direction, type_category,
       amount, status, (status = 'Success')                 AS is_success,
       failure_reason, failure_owner,
       COALESCE(failure_owner = 'Operational', FALSE)      AS is_operational_failure,
       processing_ms, fee_charged,
       CASE WHEN status = 'Success' THEN tariff_fee ELSE 0 END AS expected_fee,
       tariff_fee                                           AS tariff_fee_if_successful,
       fee_charged - CASE WHEN status = 'Success' THEN tariff_fee ELSE 0 END AS fee_variance,
       (status = 'Failed' AND next_ts <= txn_ts + interval '30 minutes'
            AND next_type = transaction_type AND next_amount = amount)           AS was_retried,
       CASE WHEN status = 'Failed' AND next_ts <= txn_ts + interval '30 minutes'
                 AND next_type = transaction_type AND next_amount = amount
            THEN next_status END                                                 AS retry_status,
       CASE WHEN status = 'Failed' AND next_ts <= txn_ts + interval '30 minutes'
                 AND next_type = transaction_type AND next_amount = amount
            THEN next_channel END                                                AS retry_channel
FROM base;
CREATE UNIQUE INDEX ON bank.mv_transactions (transaction_id);
CREATE INDEX ON bank.mv_transactions (month);
CREATE INDEX ON bank.mv_transactions (customer_id, month);

-- one row per customer per month in which they existed (May 2025 - Oct 2026)
DROP MATERIALIZED VIEW IF EXISTS bank.mv_customer_month CASCADE;
CREATE MATERIALIZED VIEW bank.mv_customer_month AS
WITH grid AS (
    SELECT c.customer_id, c.segment, gs::date AS month,
           (date_trunc('month', c.onboarding_date) = gs) AS is_new_customer
    FROM bank.customers c
    CROSS JOIN generate_series(DATE '2025-05-01', DATE '2026-10-01', interval '1 month') gs
    WHERE date_trunc('month', c.onboarding_date) <= gs
), agg AS (
    SELECT customer_id, month,
           COUNT(*)                                             AS attempts,
           COUNT(*) FILTER (WHERE is_success)                   AS successful,
           SUM(amount) FILTER (WHERE is_success)                AS value_kes,
           SUM(fee_charged) FILTER (WHERE is_success)           AS fee_income,
           COUNT(*) FILTER (WHERE is_operational_failure)       AS operational_failures,
           COUNT(DISTINCT channel) FILTER (WHERE is_success)    AS channels_used,
           BOOL_OR(is_success AND channel = 'Mobile App')       AS used_app,
           BOOL_OR(is_success AND channel = 'USSD')             AS used_ussd
    FROM bank.mv_transactions GROUP BY 1, 2
)
SELECT g.customer_id, g.segment, g.month, g.is_new_customer,
       COALESCE(a.attempts, 0)              AS attempts,
       COALESCE(a.successful, 0)            AS successful,
       COALESCE(a.value_kes, 0)             AS value_kes,
       COALESCE(a.fee_income, 0)            AS fee_income,
       COALESCE(a.operational_failures, 0)  AS operational_failures,
       COALESCE(a.channels_used, 0)         AS channels_used,
       COALESCE(a.successful, 0) > 0        AS is_active,
       COALESCE(a.used_app, FALSE)          AS used_app,
       COALESCE(a.used_ussd, FALSE)         AS used_ussd
FROM grid g LEFT JOIN agg a USING (customer_id, month);
CREATE UNIQUE INDEX ON bank.mv_customer_month (customer_id, month);

CREATE OR REPLACE VIEW bank.v_monthly_kpis AS
WITH t AS (
    SELECT month,
           COUNT(*)                                                AS attempts,
           COUNT(*) FILTER (WHERE is_success)                      AS successful,
           COUNT(*) FILTER (WHERE is_operational_failure)          AS operational_failures,
           COUNT(*) FILTER (WHERE failure_owner = 'Customer')      AS customer_failures,
           SUM(amount) FILTER (WHERE is_success)                   AS value_kes,
           SUM(fee_charged) FILTER (WHERE is_success)              AS fee_income
    FROM bank.mv_transactions GROUP BY month
), c AS (
    SELECT month, COUNT(*) AS customers_onboarded_to_date, COUNT(*) FILTER (WHERE is_active) AS active_customers,
           COUNT(*) FILTER (WHERE is_new_customer) AS new_customers
    FROM bank.mv_customer_month GROUP BY month
)
SELECT t.month, attempts, successful,
       ROUND(100.0 * successful / attempts, 2)                     AS success_rate_pct,
       ROUND(100.0 * operational_failures / attempts, 2)           AS operational_failure_rate_pct,
       ROUND(100.0 * customer_failures / attempts, 2)              AS customer_failure_rate_pct,
       value_kes, fee_income,
       ROUND(fee_income / successful, 2)                           AS fee_per_successful_txn,
       ROUND(10000.0 * fee_income / value_kes, 2)                  AS fee_yield_bps,
       customers_onboarded_to_date, active_customers, new_customers,
       ROUND(100.0 * active_customers / customers_onboarded_to_date, 1) AS active_rate_pct
FROM t JOIN c USING (month);

CREATE OR REPLACE VIEW bank.v_customer_summary AS
SELECT c.customer_id, c.segment, c.age_group, COALESCE(c.county, 'Unknown') AS county, c.onboarding_date,
       c.onboarding_channel, c.kyc_tier,
       date_trunc('month', c.onboarding_date)::date                       AS onboarding_month,
       COUNT(t.transaction_id) FILTER (WHERE t.is_success)                 AS successful_txns,
       COALESCE(SUM(t.amount) FILTER (WHERE t.is_success), 0)              AS value_kes,
       COALESCE(SUM(t.fee_charged) FILTER (WHERE t.is_success), 0)         AS fee_income,
       COUNT(DISTINCT t.channel) FILTER (WHERE t.is_success)               AS channels_used,
       MODE() WITHIN GROUP (ORDER BY t.channel)                            AS main_channel,
       MAX(t.txn_ts) FILTER (WHERE t.is_success)                           AS last_success_ts,
       COUNT(t.transaction_id) FILTER (WHERE t.is_operational_failure)     AS operational_failures_experienced,
       CASE WHEN c.onboarding_date >= DATE '2026-08-01' THEN 'Too new to assess'
            WHEN MAX(t.txn_ts) FILTER (WHERE t.is_success) IS NULL
              OR MAX(t.txn_ts) FILTER (WHERE t.is_success) < TIMESTAMP '2026-08-03' THEN 'Dormant (90+ days)'
            WHEN MAX(t.txn_ts) FILTER (WHERE t.is_success) < TIMESTAMP '2026-10-02' THEN 'At risk (30-89 days)'
            ELSE 'Active (last 30 days)' END                               AS activity_status
FROM bank.customers c
LEFT JOIN bank.mv_transactions t ON t.customer_id = c.customer_id
GROUP BY c.customer_id;

-- channel x hour grid with zero-filled hours: input for reliability monitoring and the anomaly appendix
DROP MATERIALIZED VIEW IF EXISTS bank.mv_channel_hour CASCADE;
CREATE MATERIALIZED VIEW bank.mv_channel_hour AS
WITH hours AS (
    SELECT ch.channel, h AS hour_ts
    FROM bank.channels ch
    CROSS JOIN generate_series(TIMESTAMP '2025-05-01 00:00', TIMESTAMP '2026-10-31 23:00', interval '1 hour') h
), agg AS (
    SELECT channel, date_trunc('hour', txn_ts) AS hour_ts,
           COUNT(*)                                              AS attempts,
           COUNT(*) FILTER (WHERE is_operational_failure)        AS operational_failures,
           COUNT(*) FILTER (WHERE failure_owner = 'Customer')    AS customer_failures,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY processing_ms) AS p95_ms
    FROM bank.mv_transactions GROUP BY 1, 2
)
SELECT h.channel, h.hour_ts,
       EXTRACT(hour FROM h.hour_ts)::int AS hour_of_day, EXTRACT(isodow FROM h.hour_ts)::int AS iso_dow,
       COALESCE(a.attempts, 0) AS attempts, COALESCE(a.operational_failures, 0) AS operational_failures,
       COALESCE(a.customer_failures, 0) AS customer_failures, a.p95_ms
FROM hours h LEFT JOIN agg a USING (channel, hour_ts);
CREATE UNIQUE INDEX ON bank.mv_channel_hour (channel, hour_ts);
