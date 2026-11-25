-- 05_reliability.sql: where transactions fail, why, and what the failures cost.
-- Customer-caused failures (insufficient funds, wrong PIN, limit) are separated from operational ones.

-- @query q09_success_rate_by_channel
-- Q9: how reliable is each channel once customer-caused failures are separated out?
SELECT channel, COUNT(*) AS attempts,
       ROUND(100.0 * AVG(is_success::int), 2)                                           AS success_rate_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE failure_owner = 'Customer') / COUNT(*), 2)    AS customer_failure_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE failure_owner = 'Operational') / COUNT(*), 2) AS operational_failure_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE failure_owner = 'Unknown') / COUNT(*), 2)     AS unknown_failure_pct,
       RANK() OVER (ORDER BY COUNT(*) FILTER (WHERE failure_owner = 'Operational')::numeric / COUNT(*) DESC) AS worst_operational_rank
FROM bank.mv_transactions GROUP BY 1 ORDER BY operational_failure_pct DESC;

-- @query q10_failure_reasons_by_channel
-- Q10: which failure reasons dominate on each channel?
SELECT channel, failure_reason, failure_owner, COUNT(*) AS failures,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY channel), 1) AS share_of_channel_failures_pct
FROM bank.mv_transactions WHERE NOT is_success GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;

-- @query q11_failure_by_hour
-- Q11: do operational failures rise at peak hours? Operational failure rate by hour for each digital and assisted channel.
SELECT hour,
       ROUND(100.0 * COUNT(*) FILTER (WHERE channel = 'USSD' AND is_operational_failure) / NULLIF(COUNT(*) FILTER (WHERE channel = 'USSD'), 0), 2) AS ussd_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE channel = 'Mobile App' AND is_operational_failure) / NULLIF(COUNT(*) FILTER (WHERE channel = 'Mobile App'), 0), 2) AS mobile_app_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE channel = 'Agent' AND is_operational_failure) / NULLIF(COUNT(*) FILTER (WHERE channel = 'Agent'), 0), 2) AS agent_pct,
       COUNT(*) FILTER (WHERE channel = 'USSD') AS ussd_attempts
FROM bank.mv_transactions GROUP BY hour ORDER BY hour;

-- @query q12_agent_float_month_end
-- Q12: are agents running out of cash at month end and weekends?
SELECT CASE WHEN is_payday_window THEN 'Payday window (25th-2nd)' ELSE 'Rest of month' END AS period,
       CASE WHEN iso_dow >= 6 THEN 'Weekend' ELSE 'Weekday' END AS day_type,
       COUNT(*) AS agent_withdrawal_attempts,
       COUNT(*) FILTER (WHERE failure_reason = 'Agent float unavailable') AS float_failures,
       ROUND(100.0 * COUNT(*) FILTER (WHERE failure_reason = 'Agent float unavailable') / COUNT(*), 2) AS float_failure_pct
FROM bank.mv_transactions
WHERE channel = 'Agent' AND transaction_type = 'Cash Withdrawal'
GROUP BY 1, 2 ORDER BY 1, 2;

-- @query q13_retry_and_recovery
-- Q13: after a failure, do customers retry, on which channel, and does the retry work?
SELECT failure_owner, failure_reason, COUNT(*) AS failures,
       ROUND(100.0 * AVG(was_retried::int), 1)                                          AS retried_within_30min_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE retry_status = 'Success') / COUNT(*), 1)     AS recovered_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE was_retried AND retry_channel <> channel)
             / NULLIF(COUNT(*) FILTER (WHERE was_retried), 0), 1)                       AS retries_on_other_channel_pct,
       COUNT(*) FILTER (WHERE retry_status IS DISTINCT FROM 'Success')                  AS not_recovered
FROM bank.mv_transactions WHERE NOT is_success
GROUP BY ROLLUP (failure_owner, failure_reason)
ORDER BY failure_owner NULLS LAST, failures DESC;

-- @query q14_incident_impact
-- Q14: what did each logged incident cost? Operational failures during the incident window against the
-- same channel's normal rate for the same hours of the day.
WITH win AS (
    SELECT i.incident_id, i.channel, i.start_ts, i.end_ts, i.severity,
           COUNT(t.*) AS attempts,
           COUNT(t.*) FILTER (WHERE t.is_operational_failure) AS operational_failures,
           COUNT(t.*) FILTER (WHERE t.is_operational_failure AND t.retry_status IS DISTINCT FROM 'Success') AS unrecovered,
           SUM(t.tariff_fee_if_successful) FILTER (WHERE t.is_operational_failure AND t.retry_status IS DISTINCT FROM 'Success') AS fees_lost_kes
    FROM bank.system_incidents i
    LEFT JOIN bank.mv_transactions t ON t.channel = i.channel AND t.txn_ts >= i.start_ts AND t.txn_ts < i.end_ts
    GROUP BY 1, 2, 3, 4, 5
), base AS (
    SELECT channel, hour, AVG(is_operational_failure::int) AS normal_rate
    FROM bank.mv_transactions GROUP BY 1, 2
)
SELECT w.incident_id, w.channel, w.start_ts, ROUND(EXTRACT(epoch FROM w.end_ts - w.start_ts) / 3600, 1) AS hours, w.severity,
       w.attempts, w.operational_failures,
       ROUND(100.0 * w.operational_failures / NULLIF(w.attempts, 0), 1) AS failure_rate_pct,
       ROUND(100.0 * AVG(b.normal_rate), 2) AS normal_rate_same_hours_pct,
       w.unrecovered, COALESCE(w.fees_lost_kes, 0) AS fees_lost_kes
FROM win w JOIN base b ON b.channel = w.channel
     AND b.hour BETWEEN EXTRACT(hour FROM w.start_ts) AND EXTRACT(hour FROM w.end_ts - interval '1 second')
GROUP BY w.incident_id, w.channel, w.start_ts, w.end_ts, w.severity, w.attempts, w.operational_failures, w.unrecovered, w.fees_lost_kes
ORDER BY w.start_ts;

-- @query q15_daily_app_timeouts
-- Q15: the Mobile App had an unusual month in March 2026. Daily timeout rate around the app release.
SELECT txn_date, COUNT(*) AS app_attempts,
       COUNT(*) FILTER (WHERE failure_reason = 'Timeout') AS timeouts,
       ROUND(100.0 * COUNT(*) FILTER (WHERE failure_reason = 'Timeout') / COUNT(*), 2) AS timeout_pct,
       ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY processing_ms)::numeric, 0) AS p95_ms
FROM bank.mv_transactions
WHERE channel = 'Mobile App' AND txn_date BETWEEN DATE '2026-02-24' AND DATE '2026-04-05'
GROUP BY 1 ORDER BY 1;

-- @query q16_latency_by_channel_month
-- Q16: how fast does each channel respond? Median and 95th-percentile processing time by month.
SELECT month, channel,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY processing_ms)::numeric, 0)  AS p50_ms,
       ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY processing_ms)::numeric, 0) AS p95_ms,
       COUNT(*) FILTER (WHERE processing_ms IS NULL) AS not_captured
FROM bank.mv_transactions GROUP BY 1, 2 ORDER BY 2, 1;
