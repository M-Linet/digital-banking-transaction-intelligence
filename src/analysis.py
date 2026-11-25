"""Reusable analysis helpers: chart style, data access and digital-banking metric definitions.

Metric functions mirror sql/03_views.sql; tests/test_transformations.py checks them.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.data_loader import PROJECT_ROOT, get_engine, query

FIG_DIR = PROJECT_ROOT / "reports" / "figures"

# Colour roles (validated categorical palette, light mode). Charts use colour sparingly:
# one primary series, one highlight for "the thing the chart is about", grey for context.
PRIMARY = "#2a78d6"     # blue
HIGHLIGHT = "#eb6834"   # orange
SECOND = "#1baf7a"      # aqua
CONTEXT = "#b9b8b0"     # grey for de-emphasised marks
INK = "#2b2b29"
INK_MUTED = "#6b6a64"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ_BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def set_style() -> None:
    mpl.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 150, "savefig.bbox": "tight",
        "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelcolor": INK_MUTED, "axes.edgecolor": "#d8d7d0", "axes.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": "#ecebe6", "grid.linewidth": 0.8, "axes.axisbelow": True,
        "xtick.color": INK_MUTED, "ytick.color": INK_MUTED, "text.color": INK,
        "axes.prop_cycle": mpl.cycler(color=SERIES), "lines.linewidth": 2,
        "legend.frameon": False,
    })


def kes(x: float, digits: int = 1) -> str:
    """Human-readable KES amount: 489.6M, 1.7K."""
    for div, suf in [(1e9, "B"), (1e6, "M"), (1e3, "K")]:
        if abs(x) >= div:
            return f"KES {x / div:.{digits}f}{suf}"
    return f"KES {x:,.0f}"


def kes_axis(ax, axis: str = "y", unit: str = "M") -> None:
    div = {"M": 1e6, "K": 1e3}[unit]
    fmt = mpl.ticker.FuncFormatter(lambda v, _: f"{v / div:,.0f}{unit}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def save(fig, name: str) -> Path:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / f"{name}.png"
    fig.savefig(path)
    return path



# ---------------------------------------------------------------------------
# Metric definitions (same as the SQL semantic layer, sql/03_views.sql)
# ---------------------------------------------------------------------------
OPERATIONAL = {"Timeout", "USSD session dropped", "Agent float unavailable", "Third-party unavailable", "System outage"}


def success_rate(successful: float, attempts: float) -> float:
    """Success rate = successful attempts / all attempts (a retry is a new attempt)."""
    return np.nan if attempts == 0 else successful / attempts


def fee_yield_bps(fee_income: float, value: float) -> float:
    """Fee income per KES 10,000 moved (basis points of value)."""
    return np.nan if value == 0 else 10_000 * fee_income / value


def tariff_fee(tariff: pd.DataFrame, transaction_type: str, channel: str, amount: float, date) -> float:
    """Fee per the tariff in force on `date` (0 if the type/channel is free). Mirrors the SQL join."""
    d = pd.Timestamp(date)
    t = tariff[(tariff.transaction_type == transaction_type) & (tariff.channel == channel)
               & (tariff.band_min_kes <= amount) & (tariff.band_max_kes >= amount)
               & (pd.to_datetime(tariff.effective_from) <= d)
               & (tariff.effective_to.isna() | (pd.to_datetime(tariff.effective_to) >= d))]
    if len(t) > 1:
        raise ValueError("overlapping tariff rows")
    return float(t.fee_kes.iloc[0]) if len(t) else 0.0


def flag_retries(tx: pd.DataFrame, window_min: int = 30) -> pd.DataFrame:
    """For each failed attempt, find whether the next attempt on the same account, with the same type and
    amount, came within `window_min` minutes, and whether it succeeded. Mirrors bank.mv_transactions."""
    df = tx.sort_values(["account_id", "txn_ts", "transaction_id"]).copy()
    nxt = df.groupby("account_id")[["txn_ts", "transaction_type", "amount", "status", "channel"]].shift(-1)
    retried = (df.status == "Failed") & (nxt.txn_ts <= df.txn_ts + pd.Timedelta(minutes=window_min)) \
        & (nxt.transaction_type == df.transaction_type) & (nxt.amount == df.amount)
    df["was_retried"] = retried
    df["retry_status"] = nxt.status.where(retried)
    df["retry_channel"] = nxt.channel.where(retried)
    df["recovered"] = df.retry_status.eq("Success")
    return df.sort_index()


def sql_output(name: str) -> pd.DataFrame:
    """Read a saved SQL result produced by `python -m src.run_sql`."""
    import glob
    hits = glob.glob(str(PROJECT_ROOT / "reports" / "sql_outputs" / f"{name}*.csv"))
    return pd.read_csv(sorted(hits)[0])
