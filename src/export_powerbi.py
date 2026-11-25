"""Export the Power BI model (views in a `powerbi` schema + CSV files) and the KPI reconciliation sheet.

Facts are pre-aggregated to a daily grain (2M attempts -> about 50k rows), which is all the dashboard
needs and keeps the model small. Customer activity uses the customer-month grid.

    python -m src.export_powerbi
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from src.data_loader import PROJECT_ROOT, get_engine, query

OUT = PROJECT_ROOT / "data" / "processed" / "powerbi"

VIEWS = {
    "dim_date": """SELECT d::date AS date, EXTRACT(YEAR FROM d)::int AS year, EXTRACT(MONTH FROM d)::int AS month_number,
                   to_char(d, 'Mon') AS month_short, to_char(d, 'YYYY-MM') AS year_month, date_trunc('month', d)::date AS month_start,
                   EXTRACT(isodow FROM d)::int AS iso_dow, to_char(d, 'Dy') AS weekday,
                   (EXTRACT(day FROM d) >= 25 OR EXTRACT(day FROM d) <= 2) AS is_payday_window
                   FROM generate_series(DATE '2025-05-01', DATE '2026-10-31', INTERVAL '1 day') d""",
    "dim_channel": "SELECT channel, channel_group FROM bank.channels",
    "dim_segment": """SELECT segment, CASE segment WHEN 'Premier' THEN 1 WHEN 'SME' THEN 2 WHEN 'Salaried' THEN 3
                       WHEN 'Mass Retail' THEN 4 ELSE 5 END AS segment_order FROM (SELECT DISTINCT segment FROM bank.customers) s""",
    "dim_customer": """SELECT customer_id, segment, age_group, county, onboarding_channel, kyc_tier,
                       to_char(onboarding_month, 'YYYY-MM') AS onboarding_month, activity_status,
                       successful_txns, ROUND(value_kes, 0) AS value_kes, ROUND(fee_income, 0) AS fee_income
                       FROM bank.v_customer_summary""",
    "fact_txn_daily": """SELECT txn_date AS date, channel, transaction_type, type_category, segment,
                       COUNT(*)                                                    AS attempts,
                       COUNT(*) FILTER (WHERE is_success)                          AS successful,
                       COUNT(*) FILTER (WHERE is_operational_failure)              AS operational_failures,
                       COUNT(*) FILTER (WHERE failure_owner = 'Customer')          AS customer_failures,
                       COALESCE(SUM(amount) FILTER (WHERE is_success), 0)          AS value_kes,
                       COALESCE(SUM(fee_charged) FILTER (WHERE is_success), 0)     AS fee_income,
                       SUM(fee_variance)                                           AS overcharge_kes
                       FROM bank.mv_transactions GROUP BY 1, 2, 3, 4, 5""",
    "fact_failures": """SELECT txn_date AS date, channel, failure_reason, failure_owner,
                       COUNT(*)                                                        AS failures,
                       COUNT(*) FILTER (WHERE was_retried)                             AS retried,
                       COUNT(*) FILTER (WHERE retry_status = 'Success')                AS recovered,
                       COUNT(*) FILTER (WHERE retry_status IS DISTINCT FROM 'Success') AS not_recovered,
                       COALESCE(SUM(tariff_fee_if_successful) FILTER (WHERE failure_owner = 'Operational'
                                AND retry_status IS DISTINCT FROM 'Success'), 0)       AS fees_lost_kes
                       FROM bank.mv_transactions WHERE NOT is_success GROUP BY 1, 2, 3, 4""",
    "fact_channel_hour": """SELECT hour_ts::date AS date, hour_of_day, channel, SUM(attempts) AS attempts,
                       SUM(operational_failures) AS operational_failures
                       FROM bank.mv_channel_hour GROUP BY 1, 2, 3""",
    "fact_customer_month": """SELECT customer_id, segment, month, is_active, is_new_customer, successful,
                       ROUND(value_kes, 0) AS value_kes, fee_income, operational_failures
                       FROM bank.mv_customer_month""",
}


def create_powerbi_schema(engine) -> None:
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS powerbi CASCADE; CREATE SCHEMA powerbi;"))
        for name, sql in VIEWS.items():
            c.execute(text(f"CREATE VIEW powerbi.{name} AS {sql}"))


def export_csv(engine) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name in VIEWS:
        df = query(engine, f"SELECT * FROM powerbi.{name}")
        df.to_csv(OUT / f"{name}.csv", index=False)
        counts[name] = len(df)
    so = PROJECT_ROOT / "reports" / "sql_outputs"
    mk = pd.read_csv(so / "q31_market_context.csv")
    mk.to_csv(OUT / "market_context.csv", index=False)
    counts["market_context"] = len(mk)
    return counts


def write_reconciliation(engine) -> pd.DataFrame:
    rows = [
        ("Attempts, all months", "SELECT COUNT(*) FROM bank.transactions"),
        ("Successful transactions, all months", "SELECT COUNT(*) FROM bank.transactions WHERE status = 'Success'"),
        ("Success rate %", "SELECT ROUND(100.0 * AVG((status = 'Success')::int), 2) FROM bank.transactions"),
        ("Value moved, KES", "SELECT SUM(amount) FROM bank.transactions WHERE status = 'Success'"),
        ("Fee income, KES", "SELECT SUM(fee_charged) FROM bank.transactions WHERE status = 'Success'"),
        ("Operational failure rate %", "SELECT ROUND(100.0 * AVG(is_operational_failure::int), 2) FROM bank.mv_transactions"),
        ("USSD operational failure rate %", "SELECT ROUND(100.0 * AVG(is_operational_failure::int), 2) FROM bank.mv_transactions WHERE channel = 'USSD'"),
        ("Fees lost to unrecovered operational failures, KES", "SELECT SUM(tariff_fee_if_successful) FROM bank.mv_transactions WHERE is_operational_failure AND retry_status IS DISTINCT FROM 'Success'"),
        ("Overcharge to refund, KES", "SELECT SUM(fee_variance) FROM bank.mv_transactions"),
        ("Active customers, Oct 2026", "SELECT active_customers FROM bank.v_monthly_kpis WHERE month = DATE '2026-10-01'"),
        ("Active rate %, Oct 2026", "SELECT active_rate_pct FROM bank.v_monthly_kpis WHERE month = DATE '2026-10-01'"),
        ("Dormant customers (90+ days)", "SELECT COUNT(*) FROM bank.v_customer_summary WHERE activity_status = 'Dormant (90+ days)'"),
        ("Customers, total", "SELECT COUNT(*) FROM bank.customers"),
    ]
    rec = pd.DataFrame([(k, float(query(engine, s).iloc[0, 0])) for k, s in rows], columns=["kpi", "expected_value"])
    rec.to_csv(PROJECT_ROOT / "powerbi" / "kpi_reconciliation.csv", index=False)
    return rec


if __name__ == "__main__":
    eng = get_engine()
    create_powerbi_schema(eng)
    print(export_csv(eng))
    print(write_reconciliation(eng).to_string(index=False))
