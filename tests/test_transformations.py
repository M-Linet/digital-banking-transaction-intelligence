"""Unit tests for the cleaning rules and metric definitions. No database needed."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import analysis as A  # noqa: E402
from src import data_cleaning as dc  # noqa: E402


# ---------------------------------------------------------------- labels
@pytest.mark.parametrize("raw,expected", [
    ("Mobile App", "Mobile App"), ("mobile app", "Mobile App"), ("MobileApp", "Mobile App"), ("APP", "Mobile App"),
    ("ussd", "USSD"), ("USSD ", "USSD"), ("IB", "Internet Banking"), ("Internet banking", "Internet Banking"), ("atm", "ATM"),
])
def test_channel_label_normalisation(raw, expected):
    assert dc.normalise_label(raw, dc.CHANNELS, dc.CHANNEL_ALIASES) == expected


def test_unknown_channel_fails_the_build():
    raw = pd.DataFrame({"transaction_id": ["T1"], "account_id": ["A1"], "txn_ts": ["2025-06-01 10:00:00"], "channel": ["Branch Teller"],
                        "transaction_type": ["Cash Deposit"], "amount": ["100"], "status": ["Success"], "failure_reason": [None],
                        "processing_ms": ["1000"], "fee_charged": ["0"]})
    with pytest.raises(ValueError, match="unmapped"):
        dc.clean_transactions(raw, {"A1"})


# ---------------------------------------------------------------- customers
def _customers():
    raw = pd.DataFrame({
        "customer_id": ["C1", "C2", "C3", "C3", "C4", "C4"],
        "segment": ["mass retail", "SME", "PREMIER", "Premier", "Youth", "Youth"],
        "age_group": ["25-34"] * 6,
        "county": ["Nairobi", "Kisumu", "Nakuru", "Nakuru", None, "Meru"],
        "onboarding_date": ["2024-03-05", "2065-01-10", "2025-07-01", "2025-07-01", "2025-08-01", "2025-08-01"],
        "onboarding_channel": ["Branch"] * 6, "kyc_tier": ["Tier 2"] * 6})
    accounts = pd.DataFrame({"customer_id": ["C1", "C2", "C2", "C3", "C4"],
                             "open_date": ["2024-03-05", "2025-01-10", "2025-09-01", "2025-07-01", "2025-08-01"]})
    return dc.clean_customers(raw, accounts)


def test_customer_duplicates_resolved_after_label_cleaning():
    df, exc = _customers()
    assert sorted(df.customer_id) == ["C1", "C2", "C3", "C4"]
    assert df.set_index("customer_id").loc["C3", "segment"] == "Premier"        # copies matched once labels were fixed
    assert df.set_index("customer_id").loc["C4", "county"] == "Meru"            # kept the more complete copy
    assert any(e[2] == "duplicate customer_id with differing values" for e in exc)


def test_onboarding_typo_replaced_by_first_account_open_date():
    df, _ = _customers()
    r = df.set_index("customer_id").loc["C2"]
    assert str(r.onboarding_date) == "2025-01-10" and bool(r.onboarding_date_corrected)


# ---------------------------------------------------------------- transactions
def _tx(**over):
    base = {"transaction_id": "T1", "account_id": "A1", "txn_ts": "2025-06-01 10:00:00", "channel": "Mobile App",
            "transaction_type": "Send to Mobile Wallet", "amount": "500", "status": "Success", "failure_reason": None,
            "processing_ms": "1500", "fee_charged": "10"}
    base.update(over)
    return base


def test_quarantine_rules():
    raw = pd.DataFrame([
        _tx(transaction_id="T1"),
        _tx(transaction_id="T2", account_id="A9"),                                  # orphan account
        _tx(transaction_id="T3", txn_ts="2036-06-01 10:00:00"),                     # future year typo
        _tx(transaction_id="T4", amount="-500"),                                    # sign cannot be trusted
        _tx(transaction_id="T5", channel="ATM", transaction_type="Cash Withdrawal", amount="250000"),  # x100 ATM
        _tx(transaction_id="T6", channel="ATM", transaction_type="Cash Withdrawal", amount="40000"),   # at the limit: fine
    ])
    ok, q, exc = dc.clean_transactions(raw, {"A1"})
    assert sorted(ok.transaction_id) == ["T1", "T6"]
    assert sorted(q.transaction_id) == ["T2", "T3", "T4", "T5"]
    assert q.set_index("transaction_id").loc["T5", "quarantine_reason"].startswith("ATM amount")


def test_status_and_reason_consistency():
    raw = pd.DataFrame([
        _tx(transaction_id="T1", status="FAILED", failure_reason=None),             # failed, no reason
        _tx(transaction_id="T2", status="success ", failure_reason="Timeout"),      # success with reason
        _tx(transaction_id="T3", status="Failed", failure_reason="Timeout", processing_ms="-1"),
    ])
    ok, _, _ = dc.clean_transactions(raw, {"A1"})
    ok = ok.set_index("transaction_id")
    assert ok.loc["T1", "status"] == "Failed" and ok.loc["T1", "failure_reason"] == "Unknown"
    assert ok.loc["T2", "status"] == "Success" and pd.isna(ok.loc["T2", "failure_reason"])
    assert pd.isna(ok.loc["T3", "processing_ms"])


def test_fee_is_not_cleaned():
    """A fee charged against the tariff is a business finding, so cleaning must keep it as recorded."""
    raw = pd.DataFrame([_tx(transaction_id="T1", status="Failed", failure_reason="Timeout", fee_charged="25")])
    ok, _, _ = dc.clean_transactions(raw, {"A1"})
    assert ok.fee_charged.iloc[0] == 25


# ---------------------------------------------------------------- metrics
TARIFF = pd.DataFrame({
    "transaction_type": ["Send to Mobile Wallet"] * 3, "channel": ["Mobile App"] * 3,
    "band_min_kes": [101, 101, 1001], "band_max_kes": [1000, 1000, 5000], "fee_kes": [10, 0, 25],
    "effective_from": ["2025-01-01", "2026-02-01", "2025-01-01"], "effective_to": ["2026-01-31", None, None]})


def test_tariff_fee_respects_bands_and_effective_dates():
    assert A.tariff_fee(TARIFF, "Send to Mobile Wallet", "Mobile App", 500, "2026-01-31") == 10
    assert A.tariff_fee(TARIFF, "Send to Mobile Wallet", "Mobile App", 500, "2026-02-01") == 0     # waiver
    assert A.tariff_fee(TARIFF, "Send to Mobile Wallet", "Mobile App", 2000, "2026-03-01") == 25
    assert A.tariff_fee(TARIFF, "Internal Transfer", "Mobile App", 2000, "2026-03-01") == 0        # free type


def test_retry_detection():
    t = pd.Timestamp("2026-03-01 10:00")
    tx = pd.DataFrame({
        "transaction_id": ["T1", "T2", "T3", "T4", "T5"], "account_id": ["A1", "A1", "A2", "A2", "A2"],
        "txn_ts": [t, t + pd.Timedelta(minutes=3), t, t + pd.Timedelta(minutes=45), t + pd.Timedelta(minutes=50)],
        "transaction_type": ["Bill Payment"] * 5, "amount": [500, 500, 800, 800, 900],
        "status": ["Failed", "Success", "Failed", "Success", "Success"], "channel": ["USSD", "Mobile App", "USSD", "USSD", "USSD"]})
    r = A.flag_retries(tx).set_index("transaction_id")
    assert r.loc["T1", "was_retried"] and r.loc["T1", "recovered"] and r.loc["T1", "retry_channel"] == "Mobile App"
    assert not r.loc["T3", "was_retried"]                     # next attempt came after 30 minutes
    assert not r.loc["T2", "was_retried"]                     # successes are never "retried"


def test_rates():
    assert A.success_rate(965, 1000) == pytest.approx(0.965)
    assert np.isnan(A.success_rate(0, 0))
    assert A.fee_yield_bps(51.7e6, 29.3e9) == pytest.approx(17.6, abs=0.1)
