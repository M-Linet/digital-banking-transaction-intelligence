"""Database integrity and reconciliation tests. They need the built database
(`python -m src.pipeline`) and are skipped automatically if PostgreSQL is unreachable."""
import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import analysis as A  # noqa: E402
from src.data_loader import get_engine, query  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    try:
        eng = get_engine()
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM bank.transactions LIMIT 1"))
        return eng
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"database not available: {exc}")


def scalar(engine, sql):
    return query(engine, sql).iloc[0, 0]


def named_query(file, name):
    sql = (ROOT / "sql" / file).read_text()
    return sql.split(f"-- @query {name}")[1].split("-- @query")[0].strip().rstrip(";")


@pytest.mark.parametrize("table,key", [("bank.customers", "customer_id"), ("bank.accounts", "account_id"),
                                       ("bank.transactions", "transaction_id"), ("bank.fee_schedule", "tariff_id")])
def test_primary_keys_unique(engine, table, key):
    assert scalar(engine, f"SELECT COUNT(*) - COUNT(DISTINCT {key}) FROM {table}") == 0


def test_integrity_checks_all_zero(engine):
    df = query(engine, named_query("02_data_quality_checks.sql", "dq_b01_integrity_summary"))
    assert len(df) == 8
    assert (df["failures"] == 0).all(), df[df["failures"] != 0]


def test_semantic_layer_reconciles(engine):
    r = query(engine, named_query("02_data_quality_checks.sql", "dq_b02_reconciliation")).iloc[0]
    assert r.transactions == r.rows_in_semantic_view == r.attempts_in_channel_hour
    assert float(r.value_in_transactions) == pytest.approx(float(r.value_in_customer_month))
    assert float(r.fees_in_transactions) == pytest.approx(float(r.fees_in_customer_month))


def test_quarantine_and_exceptions_agree(engine):
    q = scalar(engine, "SELECT COUNT(*) FROM bank.quarantined_transactions")
    e = scalar(engine, "SELECT COUNT(*) FROM bank.dq_exceptions WHERE action_taken = 'quarantined'")
    assert q == e > 0


def test_fee_variances_match_the_injected_defects(engine):
    """Recomputing fees from the tariff must find exactly the fee-engine defects the generator planted."""
    import json
    truth = json.loads((ROOT / "data/synthetic/raw/injection_manifest.json").read_text())["truth"]
    total = float(scalar(engine, "SELECT SUM(fee_variance) FROM bank.mv_transactions"))
    expected = truth["stale_fee_kes"] + truth["fee_on_failed_kes"]
    assert total <= expected and total >= 0.98 * expected            # a few defect rows were quarantined for other reasons
    assert scalar(engine, "SELECT COUNT(*) FROM bank.mv_transactions WHERE fee_variance < 0") == 0


def test_python_tariff_matches_sql(engine):
    tariff = query(engine, "SELECT * FROM bank.fee_schedule")
    sample = query(engine, """SELECT transaction_type, channel, amount, txn_date, tariff_fee_if_successful
                              FROM bank.mv_transactions WHERE status = 'Success' ORDER BY transaction_id LIMIT 400""")
    py = sample.apply(lambda r: A.tariff_fee(tariff, r.transaction_type, r.channel, float(r.amount), r.txn_date), axis=1)
    assert (py.values == sample.tariff_fee_if_successful.astype(float).values).all()


def test_python_retry_logic_matches_sql(engine):
    tx = query(engine, """SELECT transaction_id, account_id, txn_ts, transaction_type, amount::float AS amount, status, channel, was_retried
                          FROM bank.mv_transactions WHERE account_id IN (SELECT account_id FROM bank.accounts ORDER BY account_id LIMIT 300)""")
    py = A.flag_retries(tx.drop(columns="was_retried")).set_index("transaction_id").was_retried
    sql = tx.set_index("transaction_id").was_retried.fillna(False).astype(bool)
    assert (py.reindex(sql.index).fillna(False).astype(bool) == sql).all()


def test_monthly_kpis_view_matches_transactions(engine):
    v = query(engine, "SELECT attempts, successful, fee_income FROM bank.v_monthly_kpis WHERE month = DATE '2026-10-01'").iloc[0]
    t = query(engine, """SELECT COUNT(*) a, COUNT(*) FILTER (WHERE status='Success') s, SUM(fee_charged) FILTER (WHERE status='Success') f
                         FROM bank.transactions WHERE txn_ts >= '2026-10-01'""").iloc[0]
    assert v.attempts == t.a and v.successful == t.s and float(v.fee_income) == pytest.approx(float(t.f))


def test_powerbi_export_reconciles(engine):
    path = ROOT / "data" / "processed" / "powerbi" / "fact_txn_daily.csv"
    if not path.exists():
        pytest.skip("run python -m src.export_powerbi first")
    f = pd.read_csv(path, usecols=["attempts", "fee_income", "value_kes"])
    assert f.attempts.sum() == scalar(engine, "SELECT COUNT(*) FROM bank.transactions")
    assert f.fee_income.sum() == pytest.approx(float(scalar(engine, "SELECT SUM(fee_charged) FROM bank.transactions WHERE status='Success'")))
