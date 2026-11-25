-- 01_create_tables.sql
-- staging : raw CSVs loaded as TEXT (no constraints), so bad values can be profiled
-- bank    : cleaned, typed and constrained model for analysis and Power BI

DROP SCHEMA IF EXISTS staging CASCADE;
DROP SCHEMA IF EXISTS bank CASCADE;
CREATE SCHEMA staging;
CREATE SCHEMA bank;

CREATE TABLE staging.customers        (customer_id TEXT, segment TEXT, age_group TEXT, county TEXT, onboarding_date TEXT, onboarding_channel TEXT, kyc_tier TEXT);
CREATE TABLE staging.accounts         (account_id TEXT, customer_id TEXT, account_type TEXT, open_date TEXT, account_role TEXT);
CREATE TABLE staging.transactions     (transaction_id TEXT, account_id TEXT, txn_ts TEXT, channel TEXT, transaction_type TEXT, amount TEXT,
                                       status TEXT, failure_reason TEXT, processing_ms TEXT, fee_charged TEXT);
CREATE TABLE staging.fee_schedule     (tariff_id TEXT, transaction_type TEXT, channel TEXT, band_min_kes TEXT, band_max_kes TEXT, fee_kes TEXT,
                                       effective_from TEXT, effective_to TEXT);
CREATE TABLE staging.system_incidents (incident_id TEXT, channel TEXT, start_ts TEXT, end_ts TEXT, severity TEXT);

-- reference tables -------------------------------------------------------------------------------
CREATE TABLE bank.channels (
    channel        VARCHAR(20) PRIMARY KEY,
    channel_group  VARCHAR(20) NOT NULL CHECK (channel_group IN ('Digital', 'Assisted', 'Self-service', 'Partner'))
);
INSERT INTO bank.channels VALUES ('Mobile App', 'Digital'), ('USSD', 'Digital'), ('Internet Banking', 'Digital'),
                                 ('Agent', 'Assisted'), ('ATM', 'Self-service'), ('Wallet Link', 'Partner');

CREATE TABLE bank.transaction_types (
    transaction_type  VARCHAR(30) PRIMARY KEY,
    direction         VARCHAR(6)  NOT NULL CHECK (direction IN ('Credit', 'Debit')),
    category          VARCHAR(20) NOT NULL
);
INSERT INTO bank.transaction_types VALUES
    ('Cash Deposit', 'Credit', 'Cash'), ('Wallet Top-up', 'Credit', 'Wallet'), ('Cash Withdrawal', 'Debit', 'Cash'),
    ('Internal Transfer', 'Debit', 'Transfer'), ('Interbank Transfer', 'Debit', 'Transfer'), ('Send to Mobile Wallet', 'Debit', 'Wallet'),
    ('Bill Payment', 'Debit', 'Payment'), ('Merchant Payment', 'Debit', 'Payment'), ('Airtime Purchase', 'Debit', 'Payment');

CREATE TABLE bank.failure_reasons (
    failure_reason  VARCHAR(30) PRIMARY KEY,
    failure_owner   VARCHAR(12) NOT NULL CHECK (failure_owner IN ('Customer', 'Operational', 'Unknown'))
);
INSERT INTO bank.failure_reasons VALUES
    ('Insufficient funds', 'Customer'), ('Incorrect PIN', 'Customer'), ('Limit exceeded', 'Customer'),
    ('Timeout', 'Operational'), ('USSD session dropped', 'Operational'), ('Agent float unavailable', 'Operational'),
    ('Third-party unavailable', 'Operational'), ('System outage', 'Operational'), ('Unknown', 'Unknown');

-- core tables --------------------------------------------------------------------------------------
CREATE TABLE bank.customers (
    customer_id                VARCHAR(12) PRIMARY KEY,
    segment                    VARCHAR(12) NOT NULL CHECK (segment IN ('Mass Retail', 'Youth', 'Salaried', 'SME', 'Premier')),
    age_group                  VARCHAR(6)  NOT NULL CHECK (age_group IN ('18-24', '25-34', '35-44', '45-54', '55+')),
    county                     VARCHAR(20),                                   -- NULL = not captured at onboarding
    onboarding_date            DATE        NOT NULL,
    onboarding_date_corrected  BOOLEAN     NOT NULL DEFAULT FALSE,
    onboarding_channel         VARCHAR(12) NOT NULL CHECK (onboarding_channel IN ('Mobile App', 'Agent', 'Branch', 'USSD')),
    kyc_tier                   VARCHAR(14) NOT NULL
);

CREATE TABLE bank.accounts (
    account_id    VARCHAR(12) PRIMARY KEY,
    customer_id   VARCHAR(12) NOT NULL REFERENCES bank.customers,
    account_type  VARCHAR(15) NOT NULL CHECK (account_type IN ('Current', 'Savings', 'Fixed Savings', 'Digital Wallet')),
    open_date     DATE        NOT NULL,
    account_role  VARCHAR(9)  NOT NULL CHECK (account_role IN ('Primary', 'Secondary'))
);
CREATE UNIQUE INDEX one_primary_account ON bank.accounts (customer_id) WHERE account_role = 'Primary';

CREATE TABLE bank.fee_schedule (
    tariff_id         VARCHAR(5)    PRIMARY KEY,
    transaction_type  VARCHAR(30)   NOT NULL REFERENCES bank.transaction_types,
    channel           VARCHAR(20)   NOT NULL REFERENCES bank.channels,
    band_min_kes      NUMERIC(12,2) NOT NULL,
    band_max_kes      NUMERIC(12,2) NOT NULL CHECK (band_max_kes >= band_min_kes),
    fee_kes           NUMERIC(8,2)  NOT NULL CHECK (fee_kes >= 0),
    effective_from    DATE          NOT NULL,
    effective_to      DATE                                                    -- NULL = still in force
);

CREATE TABLE bank.transactions (
    transaction_id    VARCHAR(10)   PRIMARY KEY,
    account_id        VARCHAR(12)   NOT NULL REFERENCES bank.accounts,
    txn_ts            TIMESTAMP     NOT NULL,                                 -- Africa/Nairobi local time
    channel           VARCHAR(20)   NOT NULL REFERENCES bank.channels,
    transaction_type  VARCHAR(30)   NOT NULL REFERENCES bank.transaction_types,
    amount            NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    status            VARCHAR(7)    NOT NULL CHECK (status IN ('Success', 'Failed')),
    failure_reason    VARCHAR(30)   REFERENCES bank.failure_reasons,
    processing_ms     INTEGER       CHECK (processing_ms > 0),                -- NULL = not captured
    fee_charged       NUMERIC(8,2)  NOT NULL CHECK (fee_charged >= 0),
    CHECK ((status = 'Failed') = (failure_reason IS NOT NULL))
);
CREATE INDEX ON bank.transactions (account_id, txn_ts);
CREATE INDEX ON bank.transactions (txn_ts);

CREATE TABLE bank.quarantined_transactions (
    transaction_id  TEXT, account_id TEXT, txn_ts TEXT, channel TEXT, transaction_type TEXT, amount TEXT,
    status TEXT, failure_reason TEXT, processing_ms TEXT, fee_charged TEXT,
    quarantine_reason VARCHAR(80) NOT NULL
);

CREATE TABLE bank.system_incidents (
    incident_id  VARCHAR(8)  PRIMARY KEY,
    channel      VARCHAR(20) NOT NULL REFERENCES bank.channels,
    start_ts     TIMESTAMP   NOT NULL,
    end_ts       TIMESTAMP   NOT NULL CHECK (end_ts > start_ts),
    severity     VARCHAR(5)  NOT NULL CHECK (severity IN ('Minor', 'Major'))
);

-- real, public market context (CBK), kept apart from the synthetic data
CREATE TABLE bank.context_cbk_mobile_payments (
    month                         DATE PRIMARY KEY,
    active_agents                 INTEGER       NOT NULL,
    registered_accounts_millions  NUMERIC(8,2)  NOT NULL,
    transactions_millions         NUMERIC(8,2)  NOT NULL,
    value_kes_billions            NUMERIC(10,2) NOT NULL
);

CREATE TABLE bank.dq_exceptions (
    exception_id  BIGSERIAL PRIMARY KEY,
    source_table  VARCHAR(30) NOT NULL,
    record_key    VARCHAR(30) NOT NULL,
    issue         VARCHAR(90) NOT NULL,
    action_taken  VARCHAR(80) NOT NULL,
    logged_at     TIMESTAMP NOT NULL DEFAULT now()
);
