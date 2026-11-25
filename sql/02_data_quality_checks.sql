-- 02_data_quality_checks.sql
-- Part A profiles the RAW staging layer (what arrived). Part B proves the CLEAN bank layer is sound.

-- @query dq_a01_row_counts_and_duplicates
-- Purpose: raw row counts against distinct keys. A gap means duplicate rows or duplicate keys.
SELECT 'customers' AS table_name, COUNT(*) AS raw_rows, COUNT(DISTINCT customer_id) AS distinct_keys,
       COUNT(*) - COUNT(DISTINCT customer_id) AS extra_rows FROM staging.customers
UNION ALL SELECT 'accounts', COUNT(*), COUNT(DISTINCT account_id), COUNT(*) - COUNT(DISTINCT account_id) FROM staging.accounts
UNION ALL SELECT 'transactions', COUNT(*), COUNT(DISTINCT transaction_id), COUNT(*) - COUNT(DISTINCT transaction_id) FROM staging.transactions
UNION ALL SELECT 'fee_schedule', COUNT(*), COUNT(DISTINCT tariff_id), COUNT(*) - COUNT(DISTINCT tariff_id) FROM staging.fee_schedule;

-- @query dq_a02_label_variants
-- Purpose: categorical labels that do not match the reference lists exactly.
SELECT 'transactions.channel' AS field, channel AS raw_label, COUNT(*) AS rows
FROM staging.transactions WHERE channel NOT IN (SELECT channel FROM bank.channels) GROUP BY 2
UNION ALL
SELECT 'transactions.status', status, COUNT(*) FROM staging.transactions WHERE status NOT IN ('Success', 'Failed') GROUP BY 2
UNION ALL
SELECT 'customers.segment', segment, COUNT(*) FROM staging.customers
WHERE segment NOT IN ('Mass Retail', 'Youth', 'Salaried', 'SME', 'Premier') GROUP BY 2
UNION ALL
SELECT 'accounts.account_type', account_type, COUNT(*) FROM staging.accounts
WHERE account_type NOT IN ('Current', 'Savings', 'Fixed Savings', 'Digital Wallet') GROUP BY 2
ORDER BY 1, 3 DESC;

-- @query dq_a03_missing_values
-- Purpose: missing values in fields that matter for analysis.
SELECT 'customers.county' AS field, COUNT(*) FILTER (WHERE county IS NULL) AS missing, COUNT(*) AS rows FROM staging.customers
UNION ALL SELECT 'transactions.failure_reason (failed rows)',
       COUNT(*) FILTER (WHERE failure_reason IS NULL), COUNT(*) FROM staging.transactions WHERE lower(trim(status)) = 'failed'
UNION ALL SELECT 'transactions.failure_reason present on successful rows',
       COUNT(*) FILTER (WHERE failure_reason IS NOT NULL), COUNT(*) FROM staging.transactions WHERE lower(trim(status)) = 'success'
UNION ALL SELECT 'transactions.processing_ms = -1 (not captured)',
       COUNT(*) FILTER (WHERE processing_ms = '-1'), COUNT(*) FROM staging.transactions;

-- @query dq_a04_amount_profile_by_channel
-- Purpose: amount ranges per channel. ATM withdrawals are capped at KES 40,000 per transaction, so larger
-- ATM amounts are keying errors (they are exactly 100x a valid amount); non-positive amounts cannot be trusted.
SELECT channel, COUNT(*) AS rows,
       COUNT(*) FILTER (WHERE amount::numeric <= 0)            AS non_positive,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount::numeric)   AS median_kes,
       PERCENTILE_CONT(0.999) WITHIN GROUP (ORDER BY amount::numeric) AS p99_9_kes,
       MAX(amount::numeric)                                     AS max_kes,
       COUNT(*) FILTER (WHERE amount::numeric > 40000)          AS above_40k
FROM staging.transactions
WHERE channel IN (SELECT channel FROM bank.channels)
GROUP BY 1 ORDER BY 1;

-- @query dq_a05_timestamp_range
-- Purpose: transactions must fall inside the extract window (1 May 2025 - 31 Oct 2026).
SELECT substr(txn_ts, 1, 4) AS year, COUNT(*) AS rows, MIN(txn_ts) AS first_ts, MAX(txn_ts) AS last_ts
FROM staging.transactions GROUP BY 1 ORDER BY 1;

-- @query dq_a06_onboarding_dates
-- Purpose: onboarding dates after the extract date are year typos (2026 typed as 2066).
SELECT COUNT(*) FILTER (WHERE onboarding_date::date > DATE '2026-10-31') AS future_dates,
       MIN(onboarding_date) AS earliest, MAX(onboarding_date) AS latest
FROM staging.customers;

-- @query dq_b01_integrity_summary
-- Purpose: business rules that must hold in the clean layer. Every row must show 0 failures.
SELECT 'transaction before its account was opened' AS check_name, COUNT(*) AS failures
FROM bank.transactions t JOIN bank.accounts a USING (account_id) WHERE t.txn_ts::date < a.open_date
UNION ALL SELECT 'transaction before the customer was onboarded', COUNT(*)
FROM bank.transactions t JOIN bank.accounts a USING (account_id) JOIN bank.customers c USING (customer_id)
WHERE t.txn_ts::date < c.onboarding_date
UNION ALL SELECT 'account opened before customer onboarding', COUNT(*)
FROM bank.accounts a JOIN bank.customers c USING (customer_id) WHERE a.open_date < c.onboarding_date
UNION ALL SELECT 'customer without exactly one primary account', COUNT(*)
FROM (SELECT customer_id FROM bank.customers c LEFT JOIN bank.accounts a USING (customer_id)
      GROUP BY customer_id HAVING COUNT(*) FILTER (WHERE a.account_role = 'Primary') <> 1) x
UNION ALL SELECT 'transaction outside the extract window', COUNT(*)
FROM bank.transactions WHERE txn_ts < TIMESTAMP '2025-05-01' OR txn_ts >= TIMESTAMP '2026-11-01'
UNION ALL SELECT 'ATM amount above KES 40,000', COUNT(*) FROM bank.transactions WHERE channel = 'ATM' AND amount > 40000
UNION ALL SELECT 'transaction type not offered on its channel (no tariff or catalogue match)', COUNT(*)
FROM bank.transactions WHERE (channel, transaction_type) NOT IN (
    VALUES ('Mobile App', 'Internal Transfer'), ('Mobile App', 'Send to Mobile Wallet'), ('Mobile App', 'Bill Payment'),
           ('Mobile App', 'Merchant Payment'), ('Mobile App', 'Airtime Purchase'), ('Mobile App', 'Interbank Transfer'),
           ('USSD', 'Internal Transfer'), ('USSD', 'Send to Mobile Wallet'), ('USSD', 'Bill Payment'), ('USSD', 'Merchant Payment'),
           ('USSD', 'Airtime Purchase'), ('Internet Banking', 'Internal Transfer'), ('Internet Banking', 'Bill Payment'),
           ('Internet Banking', 'Interbank Transfer'), ('Agent', 'Cash Deposit'), ('Agent', 'Cash Withdrawal'),
           ('ATM', 'Cash Withdrawal'), ('Wallet Link', 'Wallet Top-up'))
UNION ALL SELECT 'transaction matching more than one tariff row', COUNT(*)
FROM (SELECT t.transaction_id FROM bank.transactions t JOIN bank.fee_schedule f
        ON f.transaction_type = t.transaction_type AND f.channel = t.channel AND t.amount BETWEEN f.band_min_kes AND f.band_max_kes
       AND t.txn_ts::date >= f.effective_from AND (f.effective_to IS NULL OR t.txn_ts::date <= f.effective_to)
      GROUP BY 1 HAVING COUNT(*) > 1) x;

-- @query dq_b02_reconciliation
-- Purpose: the semantic layer must carry every transaction exactly once (no join fan-out), and the
-- customer-month grid must hold the same successful value and fees as the transaction table.
SELECT (SELECT COUNT(*) FROM bank.transactions)                                   AS transactions,
       (SELECT COUNT(*) FROM bank.mv_transactions)                                AS rows_in_semantic_view,
       (SELECT SUM(amount) FROM bank.transactions WHERE status = 'Success')       AS value_in_transactions,
       (SELECT SUM(value_kes) FROM bank.mv_customer_month)                        AS value_in_customer_month,
       (SELECT SUM(fee_charged) FROM bank.transactions WHERE status = 'Success')  AS fees_in_transactions,
       (SELECT SUM(fee_income) FROM bank.mv_customer_month)                       AS fees_in_customer_month,
       (SELECT SUM(attempts) FROM bank.mv_channel_hour)                           AS attempts_in_channel_hour;

-- @query dq_b03_exception_log_summary
-- Purpose: everything the cleaning step changed, removed or quarantined, by table and issue.
SELECT source_table, issue, action_taken, COUNT(*) AS rows
FROM bank.dq_exceptions GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;
