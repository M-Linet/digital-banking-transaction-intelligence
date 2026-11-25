-- 06_fees.sql: fee income, what failures cost in fees, and whether customers were charged correctly.

-- @query q17_fee_income_by_channel_month
-- Q17: where does fee income come from, and how is it trending?
SELECT month,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success) / 1e3, 0)                                            AS total_kes_k,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success AND channel = 'Mobile App') / 1e3, 0)                 AS mobile_app_kes_k,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success AND channel = 'USSD') / 1e3, 0)                       AS ussd_kes_k,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success AND channel = 'Agent') / 1e3, 0)                      AS agent_kes_k,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success AND channel = 'ATM') / 1e3, 0)                        AS atm_kes_k,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success AND channel = 'Internet Banking') / 1e3, 0)           AS internet_banking_kes_k,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success) / COUNT(*) FILTER (WHERE is_success), 2)            AS fee_per_successful_txn
FROM bank.mv_transactions GROUP BY month ORDER BY month;

-- @query q18_fee_income_by_segment
-- Q18: which customer segments pay the fees?
SELECT segment, COUNT(DISTINCT customer_id) AS customers,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success) / 1e6, 2) AS fee_income_kes_m,
       ROUND(100.0 * SUM(fee_charged) FILTER (WHERE is_success) / SUM(SUM(fee_charged) FILTER (WHERE is_success)) OVER (), 1) AS share_of_fees_pct,
       ROUND(SUM(fee_charged) FILTER (WHERE is_success) / COUNT(DISTINCT customer_id), 0) AS fee_per_customer_kes,
       ROUND(100.0 * SUM(amount) FILTER (WHERE is_success) / SUM(SUM(amount) FILTER (WHERE is_success)) OVER (), 1) AS share_of_value_pct
FROM bank.mv_transactions GROUP BY 1 ORDER BY fee_income_kes_m DESC;

-- @query q19_fees_lost_to_failures
-- Q19: what fee income is lost when an operational failure is not recovered by a retry?
-- Lost fee = the tariff fee the transaction would have paid had it succeeded.
SELECT channel, failure_reason,
       COUNT(*)                                                          AS operational_failures,
       COUNT(*) FILTER (WHERE retry_status IS DISTINCT FROM 'Success')   AS not_recovered,
       ROUND(SUM(tariff_fee_if_successful) FILTER (WHERE retry_status IS DISTINCT FROM 'Success'), 0) AS fees_lost_kes,
       ROUND(SUM(amount) FILTER (WHERE retry_status IS DISTINCT FROM 'Success') / 1e6, 1)            AS value_not_completed_kes_m
FROM bank.mv_transactions WHERE is_operational_failure
GROUP BY ROLLUP (channel, failure_reason)
ORDER BY channel NULLS LAST, fees_lost_kes DESC;

-- @query q20_fee_billing_exceptions
-- Q20: were customers charged what the tariff says? Every transaction whose charged fee differs from the
-- tariff fee (0 for failed transactions), grouped by pattern. Positive = customer overcharged.
SELECT CASE WHEN status = 'Failed' THEN 'Fee charged on a failed transaction'
            WHEN fee_variance > 0 THEN 'Charged above tariff' ELSE 'Charged below tariff' END AS issue,
       channel, transaction_type, MIN(txn_date) AS first_seen, MAX(txn_date) AS last_seen,
       COUNT(*) AS transactions, COUNT(DISTINCT customer_id) AS customers_affected,
       SUM(fee_variance) AS overcharge_kes
FROM bank.mv_transactions WHERE fee_variance <> 0
GROUP BY 1, 2, 3 ORDER BY overcharge_kes DESC;

-- @query q21_wallet_fee_waiver
-- Q21: from 1 Feb 2026 app transfers to mobile wallets of KES 1,000 or less are free. Did small transfers
-- move from USSD to the app, and how much fee income was given up?
WITH s AS (
    SELECT CASE WHEN txn_date < DATE '2026-02-01' THEN 'a) Before waiver (Aug 2025 - Jan 2026)' ELSE 'b) After waiver (Feb - Jul 2026)' END AS period,
           channel, COUNT(*) AS transfers, SUM(fee_charged) AS fees_kes, SUM(tariff_fee_if_successful) AS tariff_fees_kes
    FROM bank.mv_transactions
    WHERE is_success AND transaction_type = 'Send to Mobile Wallet' AND amount <= 1000
      AND txn_date BETWEEN DATE '2025-08-01' AND DATE '2026-07-31' AND channel IN ('Mobile App', 'USSD')
    GROUP BY 1, 2
)
SELECT period, channel, transfers,
       ROUND(100.0 * transfers / SUM(transfers) OVER (PARTITION BY period), 1) AS share_of_small_transfers_pct,
       fees_kes, tariff_fees_kes
FROM s ORDER BY period, channel;

-- @query q22_waiver_fee_foregone
-- Q22: fee income given up by the waiver, priced at the old app tariff, by month.
SELECT month, COUNT(*) AS waived_app_transfers,
       SUM(CASE WHEN amount <= 100 THEN 0 ELSE 10 END) AS fee_foregone_kes
FROM bank.mv_transactions
WHERE is_success AND transaction_type = 'Send to Mobile Wallet' AND channel = 'Mobile App' AND amount <= 1000
  AND txn_date >= DATE '2026-02-01'
GROUP BY 1 ORDER BY 1;
