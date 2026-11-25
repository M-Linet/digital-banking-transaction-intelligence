"""Cleaning rules for the Tausi Digital Bank raw export.

Rule of thumb: fix only what can be fixed with certainty (using another trusted field where one
exists), quarantine what would need a guess, and log every action to bank.dq_exceptions.
Fee amounts are NOT cleaned: a fee charged against the tariff is a business finding (possible
customer refund), not a data-entry error, so it is kept as recorded and analysed in SQL.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

WINDOW_END = pd.Timestamp("2026-10-31 23:59:59")
SEGMENTS = ["Mass Retail", "Youth", "Salaried", "SME", "Premier"]
ACCOUNT_TYPES = ["Current", "Savings", "Fixed Savings", "Digital Wallet"]
CHANNELS = ["Mobile App", "USSD", "Agent", "ATM", "Internet Banking", "Wallet Link"]
CHANNEL_ALIASES = {"mobileapp": "Mobile App", "app": "Mobile App", "ib": "Internet Banking"}
STATUSES = ["Success", "Failed"]
ATM_LIMIT_KES = 40_000          # per-withdrawal ATM limit in the tariff guide; anything above is a keying error

Exc = list[tuple[str, str, str, str]]


def _key(s) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def normalise_label(value, canonical: list[str], aliases: dict | None = None):
    """Map a messy label to its canonical form; None if it cannot be mapped with certainty."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    lookup = {_key(c): c for c in canonical}
    lookup.update({_key(k): v for k, v in (aliases or {}).items()})
    return lookup.get(_key(value))


def drop_exact_duplicates(df: pd.DataFrame, key: str, table: str, exc: Exc) -> pd.DataFrame:
    dup = df.duplicated(keep="first")
    for k in df.loc[dup, key]:
        exc.append((table, str(k), "exact duplicate row", "removed"))
    return df[~dup].copy()


def _map_column(df, col, canonical, aliases, table, key, issue, exc):
    mapped = df[col].map(lambda v: normalise_label(v, canonical, aliases))
    if mapped.isna().any() and df[col].notna().any():
        bad = sorted(set(df.loc[mapped.isna() & df[col].notna(), col]))
        if bad:
            raise ValueError(f"{table}.{col}: unmapped labels {bad[:5]} - add an alias rule")
    for k in df.loc[mapped.notna() & (mapped != df[col]), key]:
        exc.append((table, str(k), issue, "standardised"))
    df[col] = mapped
    return df


def clean_customers(raw: pd.DataFrame, accounts_raw: pd.DataFrame) -> tuple[pd.DataFrame, Exc]:
    exc: Exc = []
    df = _map_column(raw.copy(), "segment", SEGMENTS, None, "customers", "customer_id", "segment label variant", exc)
    df = drop_exact_duplicates(df, "customer_id", "customers", exc)
    # copies that still differ (e.g. county blank in one copy): keep the most complete row
    df = df.assign(_filled=df.notna().sum(axis=1)).sort_values(["customer_id", "_filled"], ascending=[True, False], kind="stable")
    clash = df.duplicated("customer_id", keep="first")
    for k in df.loc[clash, "customer_id"]:
        exc.append(("customers", k, "duplicate customer_id with differing values", "kept the most complete copy"))
    df = df[~clash].drop(columns="_filled").sort_index()
    onb = pd.to_datetime(df["onboarding_date"], errors="coerce")
    first_open = pd.to_datetime(accounts_raw["open_date"], errors="coerce").groupby(accounts_raw["customer_id"]).min()
    bad = onb.isna() | (onb > WINDOW_END)
    df["onboarding_date_corrected"] = bad
    onb = onb.where(~bad, df["customer_id"].map(first_open))
    for k in df.loc[bad, "customer_id"]:
        exc.append(("customers", k, "onboarding date outside data range (year typo)", "replaced with first account open date"))
    df["onboarding_date"] = onb.dt.date
    for k in df.loc[df["county"].isna(), "customer_id"]:
        exc.append(("customers", k, "missing county", "left NULL (reported as Unknown)"))
    return df[["customer_id", "segment", "age_group", "county", "onboarding_date", "onboarding_date_corrected",
               "onboarding_channel", "kyc_tier"]], exc


def clean_accounts(raw: pd.DataFrame, valid_customers: set) -> tuple[pd.DataFrame, Exc]:
    exc: Exc = []
    df = drop_exact_duplicates(raw, "account_id", "accounts", exc)
    df = _map_column(df, "account_type", ACCOUNT_TYPES, None, "accounts", "account_id", "account type label variant", exc)
    orphan = ~df["customer_id"].isin(valid_customers)
    for k in df.loc[orphan, "account_id"]:
        exc.append(("accounts", k, "customer_id not in customer master", "removed"))
    df["open_date"] = pd.to_datetime(df["open_date"]).dt.date
    return df[~orphan], exc


def clean_transactions(raw: pd.DataFrame, valid_accounts: set) -> tuple[pd.DataFrame, pd.DataFrame, Exc]:
    """Returns (clean transactions, quarantined rows with a reason, exceptions)."""
    exc: Exc = []
    df = drop_exact_duplicates(raw, "transaction_id", "transactions", exc)
    df = _map_column(df, "channel", CHANNELS, CHANNEL_ALIASES, "transactions", "transaction_id", "channel label variant", exc)
    df = _map_column(df, "status", STATUSES, None, "transactions", "transaction_id", "status label variant", exc)
    ts = pd.to_datetime(df["txn_ts"], errors="coerce")
    amt = pd.to_numeric(df["amount"], errors="coerce")
    rules = [
        (~df["account_id"].isin(valid_accounts), "account_id not in account master"),
        (ts.isna() | (ts > WINDOW_END), "timestamp after extract date (year typo)"),
        (amt.isna() | (amt <= 0), "non-positive amount (sign cannot be trusted)"),
        ((df["channel"] == "ATM") & (amt > ATM_LIMIT_KES), f"ATM amount above KES {ATM_LIMIT_KES:,} limit (x100 pattern)"),
    ]
    reason = pd.Series(None, index=df.index, dtype=object)
    for mask, why in rules:
        reason = reason.where(reason.notna() | ~mask, why)
    q = raw.loc[reason[reason.notna()].index].assign(quarantine_reason=reason[reason.notna()])
    for k, why in zip(q["transaction_id"], q["quarantine_reason"]):
        exc.append(("transactions", k, why, "quarantined"))
    keep = reason.isna()
    df, ts, amt = df[keep].copy(), ts[keep], amt[keep]
    df["txn_ts"], df["amount"] = ts, amt
    miss = (df["status"] == "Failed") & df["failure_reason"].isna()
    stray = (df["status"] == "Success") & df["failure_reason"].notna()
    for k in df.loc[miss, "transaction_id"]:
        exc.append(("transactions", k, "failed transaction without a failure reason", "set to 'Unknown'"))
    for k in df.loc[stray, "transaction_id"]:
        exc.append(("transactions", k, "successful transaction carrying a failure reason", "reason set to NULL"))
    df.loc[miss, "failure_reason"] = "Unknown"
    df.loc[stray, "failure_reason"] = None
    ms = pd.to_numeric(df["processing_ms"], errors="coerce")
    sentinel = ms.notna() & (ms <= 0)
    for k in df.loc[sentinel, "transaction_id"]:
        exc.append(("transactions", k, "processing time sentinel (-1)", "set to NULL"))
    df["processing_ms"] = ms.where(~sentinel).astype("Int64")
    df["fee_charged"] = pd.to_numeric(df["fee_charged"])
    return df, q, exc
