"""
Synthetic data generator for *Tausi Digital Bank*, a FICTIONAL Kenyan bank.

Nothing here describes any real bank, mobile-money provider or customer. PaySim (Lopez-Rojas,
Elmir & Axelsson, 2016; CC BY-SA 4.0) was reviewed first (see reports/methodology.md). It is itself
simulated, covers only 30 days, and has no channel, status, failure reason, fee or customer fields,
so it cannot answer channel, reliability or fee questions.

What the simulation does, May 2025 - Oct 2026 (18 months, hourly timestamps):
- 30,000 customers in five segments, about 40,000 accounts. Each customer has a channel preference
  (Mobile App, USSD, Agent, ATM, Internet Banking, Wallet Link) that drifts from USSD to the app.
- Customers move between active and dormant states; activity follows weekday, hour-of-day and
  payday (25th - 2nd) patterns.
- Every transaction succeeds or fails. Failure reasons are either customer-caused (insufficient
  funds, wrong PIN, limit exceeded) or operational (timeout, USSD session dropped at peak hours,
  agent float shortage at month end, third-party unavailable, system outages, a faulty app release).
- After an operational failure many customers retry within minutes, sometimes on another channel.
- Fees follow a fictional, versioned tariff (fee_schedule). From 1 Feb 2026 app transfers to
  mobile wallets of KES 1,000 or less are free. Two fee-engine defects are simulated: the old fee was
  still charged on waived app transfers for two weeks, and fees were charged on failed transfers
  for six weeks in Mar-Apr 2026.
- An incident log records most outages; a few are deliberately missing from it.
- Controlled data-quality defects are injected into the raw export and counted in a manifest.
No fraud labels are generated: this is an operations and customer-activity dataset.

    python -m src.generate_data --seed 2028
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "synthetic" / "raw"

START, END = pd.Timestamp("2025-05-01"), pd.Timestamp("2026-10-31")
MONTHS = pd.date_range(START, END, freq="MS")
WAIVER_FROM = pd.Timestamp("2026-02-01")
STALE_FEE = (pd.Timestamp("2026-02-01"), pd.Timestamp("2026-02-15"))     # old fee still charged
FEE_ON_FAILED = (pd.Timestamp("2026-03-02"), pd.Timestamp("2026-04-13"))  # fees charged on failures
APP_BUG = (pd.Timestamp("2026-03-10"), pd.Timestamp("2026-03-20"))        # app v5.2 release

SEGMENTS = ["Mass Retail", "Youth", "Salaried", "SME", "Premier"]
SEG_P = [0.40, 0.22, 0.20, 0.12, 0.06]
CHANNELS = ["Mobile App", "USSD", "Agent", "ATM", "Internet Banking", "Wallet Link"]
CH_PREF = np.array([  # App, USSD, Agent, ATM, IB, Wallet
    [0.22, 0.38, 0.20, 0.12, 0.01, 0.07],
    [0.50, 0.25, 0.10, 0.07, 0.00, 0.08],
    [0.40, 0.20, 0.08, 0.20, 0.04, 0.08],
    [0.30, 0.10, 0.15, 0.05, 0.35, 0.05],
    [0.55, 0.05, 0.02, 0.15, 0.20, 0.03]])
TX_RATE = np.array([3.2, 4.4, 6.4, 11.0, 8.0])          # mean transactions per active month
AMT_MULT = np.array([0.8, 0.5, 1.5, 5.0, 4.0])
DORMANT_P = np.array([0.025, 0.035, 0.015, 0.02, 0.01])  # active -> dormant per month
WAKE_P = 0.08

TYPES = ["Cash Deposit", "Cash Withdrawal", "Internal Transfer", "Send to Mobile Wallet", "Bill Payment",
         "Merchant Payment", "Airtime Purchase", "Interbank Transfer", "Wallet Top-up"]
TYPE_BY_CH = {  # channel -> weights over TYPES
    "Mobile App":       [0, 0, .14, .30, .14, .16, .16, .10, 0],
    "USSD":             [0, 0, .12, .38, .14, .12, .24, 0, 0],
    "Agent":            [.45, .55, 0, 0, 0, 0, 0, 0, 0],
    "ATM":              [0, 1, 0, 0, 0, 0, 0, 0, 0],
    "Internet Banking": [0, 0, .30, 0, .25, 0, 0, .45, 0],
    "Wallet Link":      [0, 0, 0, 0, 0, 0, 0, 0, 1]}
AMT = {"Cash Deposit": (3000, 1.0), "Cash Withdrawal": (2500, 0.9), "Internal Transfer": (4000, 1.2),
       "Send to Mobile Wallet": (1200, 1.0), "Bill Payment": (1800, 0.8), "Merchant Payment": (800, 1.0),
       "Airtime Purchase": (100, 0.6), "Interbank Transfer": (15000, 1.1), "Wallet Top-up": (2000, 1.0)}
LATENCY = {"Mobile App": 1800, "USSD": 3500, "Agent": 2500, "ATM": 2200, "Internet Banking": 1500, "Wallet Link": 4000}
HOURS = {  # hour-of-day weights
    "digital": np.array([2, 1, .6, .4, .4, 1, 3, 6, 8, 7, 6, 6, 7, 7, 6, 6, 6, 7, 9, 10, 9, 7, 5, 3.]),
    "branch":  np.array([0, 0, 0, 0, 0, .2, 1, 4, 7, 8, 8, 8, 8, 8, 8, 8, 8, 8, 7, 5, 2, .5, 0, 0.]),
    "atm":     np.array([.5, .3, .2, .2, .2, .5, 2, 5, 7, 7, 7, 7, 8, 7, 7, 7, 8, 9, 9, 8, 6, 4, 2, 1.])}
HOUR_PROFILE = {"Mobile App": "digital", "USSD": "digital", "Internet Banking": "digital", "Wallet Link": "digital",
                "Agent": "branch", "ATM": "atm"}
COUNTIES = ["Nairobi", "Mombasa", "Kiambu", "Nakuru", "Kisumu", "Machakos", "Uasin Gishu", "Kakamega", "Nyeri",
            "Kilifi", "Meru", "Kajiado"]
COUNTY_P = np.array([.30, .10, .10, .08, .07, .06, .06, .05, .04, .04, .05, .05])

BANDS = [(1, 100), (101, 1000), (1001, 5000), (5001, 20000), (20001, 70000), (70001, 250000), (250001, 999999999)]
TARIFF = {  # (type, channel) -> fee per band, KES. Types not listed are free.
    ("Send to Mobile Wallet", "Mobile App"): [0, 10, 25, 45, 75, 110, 150],
    ("Send to Mobile Wallet", "USSD"):       [0, 12, 28, 50, 80, 115, 150],
    ("Cash Withdrawal", "Agent"):            [0, 30, 60, 110, 190, 330, 330],
    ("Cash Withdrawal", "ATM"):              [33, 33, 33, 33, 33, 33, 33],
    ("Interbank Transfer", "Mobile App"):    [20, 20, 50, 80, 100, 150, 250],
    ("Interbank Transfer", "Internet Banking"): [20, 20, 50, 80, 100, 150, 250],
    ("Bill Payment", "Mobile App"):          [15] * 7,
    ("Bill Payment", "USSD"):                [20] * 7,
    ("Bill Payment", "Internet Banking"):    [15] * 7}

# incidents: (channel, start, hours, logged?)
INCIDENTS = [("Mobile App", "2025-06-14 19:00", 3, True), ("USSD", "2025-07-31 18:00", 4, True),
             ("Agent", "2025-08-29 10:00", 6, True), ("Internet Banking", "2025-09-15 09:00", 2, True),
             ("Mobile App", "2025-10-24 20:00", 2, False), ("ATM", "2025-11-30 07:00", 8, True),
             ("USSD", "2025-12-23 17:00", 5, True), ("Wallet Link", "2026-01-12 11:00", 4, True),
             ("Mobile App", "2026-03-13 18:00", 3, True), ("USSD", "2026-05-29 19:00", 3, False),
             ("Agent", "2026-06-30 09:00", 7, True), ("Mobile App", "2026-08-21 20:00", 2, True),
             ("Internet Banking", "2026-09-04 14:00", 3, False), ("USSD", "2026-10-30 18:00", 4, True)]

REASONS = ["Insufficient funds", "Incorrect PIN", "Limit exceeded", "Timeout", "USSD session dropped",
           "Agent float unavailable", "Third-party unavailable", "System outage"]
OPERATIONAL = {"Timeout", "USSD session dropped", "Agent float unavailable", "Third-party unavailable", "System outage"}


def band_index(amount):
    lo = np.array([b[0] for b in BANDS])
    return np.clip(np.searchsorted(lo, amount, side="right") - 1, 0, len(BANDS) - 1)


def tariff_fee(tx_type, channel, amount, ts):
    """Contractual fee per the fictional tariff (vectorised)."""
    fee = np.zeros(len(tx_type))
    bi = band_index(amount)
    for (t, c), fees in TARIFF.items():
        m = (tx_type == t) & (channel == c)
        fee[m] = np.array(fees)[bi[m]]
    waived = (tx_type == "Send to Mobile Wallet") & (channel == "Mobile App") & (amount <= 1000) & (ts >= WAIVER_FROM)
    fee[waived] = 0
    return fee


def make_customers(rng):
    n0, n_new = 18_000, 12_000
    n = n0 + n_new
    seg = rng.choice(5, n, p=SEG_P)
    onb_old = START - pd.to_timedelta(rng.integers(30, 4 * 365, n0), unit="D")
    # acquisitions grow slightly over the window
    w = np.linspace(1, 1.4, len(MONTHS)); w /= w.sum()
    m_idx = rng.choice(len(MONTHS), n_new, p=w)
    onb_new = MONTHS[m_idx] + pd.to_timedelta(rng.integers(0, 28, n_new), unit="D")
    onb = pd.DatetimeIndex(np.concatenate([onb_old.values, onb_new.values]))
    order = np.argsort(onb.values, kind="stable")
    seg, onb = seg[order], onb[order]
    age = np.where(seg == 1, rng.choice(["18-24", "25-34"], n, p=[.7, .3]),
                   rng.choice(["18-24", "25-34", "35-44", "45-54", "55+"], n, p=[.08, .34, .30, .18, .10]))
    digital_era = onb >= pd.Timestamp("2024-01-01")
    onb_ch = np.where(digital_era, rng.choice(["Mobile App", "Agent", "Branch", "USSD"], n, p=[.45, .25, .2, .1]),
                      rng.choice(["Mobile App", "Agent", "Branch", "USSD"], n, p=[.15, .30, .45, .10]))
    onb_ch = np.where(seg >= 3, np.where(rng.random(n) < .7, "Branch", onb_ch), onb_ch)
    kyc = np.where(onb_ch == "USSD", "Tier 1 (lite)", np.where(rng.random(n) < .25, "Tier 2", "Tier 3 (full)"))
    return pd.DataFrame({
        "customer_id": [f"TDB-C{i:06d}" for i in range(1, n + 1)],
        "segment": np.array(SEGMENTS)[seg], "age_group": age,
        "county": rng.choice(COUNTIES, n, p=COUNTY_P / COUNTY_P.sum()),
        "onboarding_date": onb.normalize(), "onboarding_channel": onb_ch, "kyc_tier": kyc}), seg


def make_accounts(rng, cust, seg):
    rows = []
    main_type = np.where(np.isin(seg, [2, 3, 4]), "Current", "Savings")
    k = 1
    for i, c in enumerate(cust.itertuples(index=False)):
        rows.append((f"TDB-A{k:06d}", c.customer_id, main_type[i], c.onboarding_date, "Primary")); k += 1
        extra = rng.random()
        if extra < .22:
            rows.append((f"TDB-A{k:06d}", c.customer_id, "Digital Wallet", c.onboarding_date + pd.Timedelta(days=int(rng.integers(0, 400))), "Secondary")); k += 1
        if rng.random() < (.20 if seg[i] in (2, 4) else .08):
            rows.append((f"TDB-A{k:06d}", c.customer_id, "Savings" if main_type[i] == "Current" else "Fixed Savings",
                         c.onboarding_date + pd.Timedelta(days=int(rng.integers(0, 600))), "Secondary")); k += 1
    acc = pd.DataFrame(rows, columns=["account_id", "customer_id", "account_type", "open_date", "account_role"])
    # secondary accounts that would open after the extract date do not exist yet
    acc = acc[(acc.account_role == "Primary") | (acc.open_date <= END - pd.Timedelta(days=5))].reset_index(drop=True)
    return acc


def day_weights(month):
    days = pd.date_range(month, month + pd.offsets.MonthEnd(0), freq="D")
    dow = np.array([1.0, .95, .97, 1.0, 1.15, 1.1, .8])[days.dayofweek]
    payday = np.where((days.day >= 25) | (days.day <= 2), 1.35, 1.0)
    w = dow * payday
    return days, w / w.sum()


def simulate(seed=2028):
    rng = np.random.default_rng(seed)
    cust, seg = make_customers(rng)
    acc = make_accounts(rng, cust, seg)
    n = len(cust)
    pref = np.vstack([rng.dirichlet(CH_PREF[s] * 25 + 0.05) for s in seg])
    app_onb = cust["onboarding_channel"].eq("Mobile App").to_numpy()
    pref[app_onb, 0] += 0.25
    pref /= pref.sum(1, keepdims=True)
    engagement = rng.gamma(2.0, 0.5, n)
    active = rng.random(n) > 0.15
    onb = cust["onboarding_date"].to_numpy()
    prim = acc[acc.account_role == "Primary"].set_index("customer_id")["account_id"].reindex(cust.customer_id).to_numpy()
    sec = acc[acc.account_role == "Secondary"].groupby("customer_id")["account_id"].first().reindex(cust.customer_id).to_numpy()
    sec_open = acc[acc.account_role == "Secondary"].groupby("customer_id")["open_date"].first().reindex(cust.customer_id).to_numpy()

    parts = []
    op_fail_last = np.zeros(n, int)
    for t, month in enumerate(MONTHS):
        mend = month + pd.offsets.MonthEnd(0)
        exists = onb <= np.datetime64(mend)
        # state transitions and USSD -> app drift
        # customers who hit operational failures last month are more likely to go quiet
        fail_mult = np.select([op_fail_last >= 2, op_fail_last == 1], [2.5, 1.5], 1.0)
        go_dormant = active & (rng.random(n) < DORMANT_P[seg] * fail_mult)
        wake = ~active & (rng.random(n) < WAKE_P)
        active = (active & ~go_dormant) | wake
        drift = pref[:, 1] * 0.02
        pref[:, 1] -= drift; pref[:, 0] += drift
        lam = TX_RATE[seg] * engagement * (1 + 0.01 * t)
        new_this_month = (onb >= np.datetime64(month)) & exists
        lam = np.where(new_this_month, lam * 0.6, lam)
        cnt = np.where(exists & active, rng.poisson(lam), 0)
        ci = np.repeat(np.arange(n), cnt)
        m = len(ci)
        days, dw = day_weights(month)
        day = days[rng.choice(len(days), m, p=dw)]
        # channel per transaction from the customer's current preference
        u = rng.random(m)
        ch = (u[:, None] > np.cumsum(pref[ci], 1)).sum(1).clip(0, 5)
        chan = np.array(CHANNELS)[ch]
        hour = np.zeros(m, int)
        for c in CHANNELS:
            sel = chan == c
            hp = HOURS[HOUR_PROFILE[c]]
            hour[sel] = rng.choice(24, sel.sum(), p=hp / hp.sum())
        ts = day + pd.to_timedelta(hour, unit="h") + pd.to_timedelta(rng.integers(0, 3600, m), unit="s")
        # transactions cannot precede onboarding
        ts = pd.DatetimeIndex(np.maximum(ts.values, onb[ci] + np.timedelta64(9, "h")))
        ttype = np.empty(m, object)
        for c in CHANNELS:
            sel = np.where(chan == c)[0]
            p = np.array(TYPE_BY_CH[c], float); p /= p.sum()
            ttype[sel] = np.array(TYPES)[rng.choice(len(TYPES), len(sel), p=p)]
        med = np.array([AMT[x][0] for x in ttype]); sig = np.array([AMT[x][1] for x in ttype])
        amount = np.exp(np.log(med * AMT_MULT[seg[ci]]) + sig * rng.standard_normal(m))
        amount = np.where(ttype == "Airtime Purchase", np.clip(np.round(amount / 10) * 10, 10, 10_000), amount)
        atm = chan == "ATM"
        amount[atm] = np.clip(np.round(amount[atm] / 500) * 500, 500, 40_000)
        amount = np.clip(np.round(amount), 10, 990_000)
        # after the waiver, some small USSD wallet transfers move to the app
        shift = (ttype == "Send to Mobile Wallet") & (chan == "USSD") & (amount <= 1000) & (ts >= WAIVER_FROM) & (rng.random(m) < 0.30)
        chan = np.where(shift, "Mobile App", chan)
        use_sec = (rng.random(m) < 0.15) & pd.notna(sec[ci]) & (np.asarray(sec_open[ci], dtype="datetime64[ns]") <= ts.values)
        account = np.where(use_sec, sec[ci], prim[ci])
        part = pd.DataFrame({"cust_idx": ci, "account_id": account, "txn_ts": ts, "channel": chan,
                             "transaction_type": ttype, "amount": amount})
        failed, reason = assign_status(rng, failure_probs(rng, part, seg))
        part["status"] = np.where(failed, "Failed", "Success"); part["failure_reason"] = reason
        op = part.failure_reason.isin(OPERATIONAL).to_numpy()
        op_fail_last = np.bincount(ci[op], minlength=n)
        parts.append(part)
    tx = pd.concat(parts, ignore_index=True)
    tx = tx[tx.txn_ts <= END + pd.Timedelta(hours=23, minutes=59)].reset_index(drop=True)
    return rng, cust, seg, acc, tx


def failure_probs(rng, tx, seg):
    """Matrix of per-reason failure probabilities (rows = transactions)."""
    m = len(tx)
    ch, tt, amt, ts = tx.channel.to_numpy(), tx.transaction_type.to_numpy(), tx.amount.to_numpy(), pd.DatetimeIndex(tx.txn_ts)
    s = seg[tx.cust_idx.to_numpy()]
    hour, dom, dow = ts.hour.to_numpy(), ts.day.to_numpy(), ts.dayofweek.to_numpy()
    debit = ~np.isin(tt, ["Cash Deposit", "Wallet Top-up"])
    P = np.zeros((m, len(REASONS)))
    P[:, 0] = np.where(debit, 0.012 * np.array([1.5, 1.6, 1.0, 0.7, 0.5])[s], 0)
    P[:, 1] = np.where(np.isin(ch, ["Mobile App", "USSD", "Agent", "ATM"]), 0.006 * np.where(ch == "USSD", 1.5, 1.0), 0)
    P[:, 2] = np.where(debit, np.where(amt > 100_000, 0.03, 0.001), 0)
    P[:, 3] = pd.Series(ch).map({"Mobile App": .005, "USSD": .006, "Internet Banking": .004, "Agent": .004,
                                 "ATM": .005, "Wallet Link": .006}).to_numpy()
    bug = (ch == "Mobile App") & (ts >= APP_BUG[0]) & (ts < APP_BUG[1])
    P[bug, 3] *= 7
    peak = (hour >= 17) & (hour <= 21)
    payday = (dom >= 25) | (dom <= 2)
    P[:, 4] = np.where(ch == "USSD", 0.012 * np.where(peak, 2.8, 1.0) * np.where(payday, 1.3, 1.0), 0)
    agent_wd = (ch == "Agent") & (tt == "Cash Withdrawal")
    P[:, 5] = np.where(agent_wd, np.where(payday | (dow >= 5), 0.06, 0.01), 0)
    P[:, 6] = np.select([tt == "Interbank Transfer", tt == "Bill Payment", np.isin(tt, ["Send to Mobile Wallet", "Wallet Top-up"])],
                        [0.015, 0.010, 0.008], 0)
    for c, start, hrs, _ in INCIDENTS:
        a = pd.Timestamp(start)
        inside = (ch == c) & (ts >= a) & (ts < a + pd.Timedelta(hours=hrs))
        P[inside, 7] = 0.55
    return P


def assign_status(rng, P):
    total = P.sum(1)
    failed = rng.random(len(P)) < total
    cum = np.cumsum(P / np.where(total == 0, 1, total)[:, None], 1)
    reason_idx = (rng.random(len(P))[:, None] > cum).sum(1).clip(0, len(REASONS) - 1)
    reason = np.where(failed, np.array(REASONS, object)[reason_idx], None)
    return failed, reason


def add_retries(rng, tx, seg):
    f = tx[tx.status == "Failed"]
    op = f.failure_reason.isin(OPERATIONAL).to_numpy()
    pin = (f.failure_reason == "Incorrect PIN").to_numpy()
    p_retry = np.where(op, np.where(f.failure_reason == "System outage", 0.40, 0.55), np.where(pin, 0.60, 0.05))
    r = f[rng.random(len(f)) < p_retry].copy()
    delay = np.where(r.failure_reason == "Incorrect PIN", rng.integers(30, 180, len(r)),
                     np.clip(rng.exponential(240, len(r)), 40, 900)).astype(int)
    r["txn_ts"] = r["txn_ts"] + pd.to_timedelta(delay, unit="s")
    switch = r.failure_reason.isin(OPERATIONAL).to_numpy() & (rng.random(len(r)) < 0.30)
    alt = r.channel.map({"USSD": "Mobile App", "Mobile App": "USSD", "Internet Banking": "Mobile App"}).fillna(r.channel)
    # only switch to a channel that offers the same transaction type
    offered = np.array([TYPE_BY_CH[a][TYPES.index(t)] > 0 for a, t in zip(alt, r.transaction_type)], dtype=bool)
    alt = alt.where(offered, r.channel)
    r["channel"] = np.where(switch, alt, r.channel)
    r = r.drop(columns=["status", "failure_reason"])
    P = failure_probs(rng, r, seg)
    failed, reason = assign_status(rng, P)
    r["status"] = np.where(failed, "Failed", "Success"); r["failure_reason"] = reason
    return pd.concat([tx, r], ignore_index=True).sort_values("txn_ts", kind="stable").reset_index(drop=True)


def latency(rng, tx):
    base = tx.channel.map(LATENCY).to_numpy()
    ts = pd.DatetimeIndex(tx.txn_ts)
    ms = base * np.exp(0.45 * rng.standard_normal(len(tx)))
    ms *= np.where((tx.channel == "USSD") & (ts.hour >= 17) & (ts.hour <= 21), 1.4, 1.0)
    for c, start, hrs, _ in INCIDENTS:
        a = pd.Timestamp(start)
        ms[((tx.channel == c) & (ts >= a) & (ts < a + pd.Timedelta(hours=hrs))).to_numpy()] *= 3
    bug = ((tx.channel == "Mobile App") & (ts >= APP_BUG[0]) & (ts < APP_BUG[1])).to_numpy()
    ms[bug] *= 2
    ms[tx.failure_reason.isin(["Timeout", "System outage"]).to_numpy()] = 30_000
    return np.round(ms).astype(int)


def fee_schedule():
    rows = []
    for (t, c), fees in TARIFF.items():
        for (lo, hi), fee in zip(BANDS, fees):
            if t == "Send to Mobile Wallet" and c == "Mobile App" and hi <= 1000:
                rows.append((t, c, lo, hi, fee, "2025-01-01", "2026-01-31"))
                rows.append((t, c, lo, hi, 0, "2026-02-01", None))
            else:
                rows.append((t, c, lo, hi, fee, "2025-01-01", None))
    fs = pd.DataFrame(rows, columns=["transaction_type", "channel", "band_min_kes", "band_max_kes", "fee_kes", "effective_from", "effective_to"])
    fs.insert(0, "tariff_id", [f"F{i:03d}" for i in range(1, len(fs) + 1)])
    return fs


def inject_defects(rng, cust, acc, tx):
    man = {}
    # customers
    d = cust.sample(60, random_state=1); cust = pd.concat([cust, d]); man["customers_duplicate_rows"] = 60
    i = rng.choice(len(cust), 400, replace=False)
    cust.iloc[i, cust.columns.get_loc("segment")] = cust.iloc[i]["segment"].str.upper().to_numpy()
    man["customers_segment_case"] = 400
    i = rng.choice(len(cust), 700, replace=False); cust.iloc[i, cust.columns.get_loc("county")] = None; man["customers_missing_county"] = 700
    i = rng.choice(len(cust), 15, replace=False)
    cust["onboarding_date"] = cust["onboarding_date"].astype(str)
    cust.iloc[i, cust.columns.get_loc("onboarding_date")] = cust.iloc[i]["onboarding_date"].str.replace(r"^202", "206", regex=True).to_numpy()
    man["customers_onboarding_year_typo"] = 15
    # accounts
    i = rng.choice(len(acc), 300, replace=False)
    acc.iloc[i, acc.columns.get_loc("account_type")] = acc.iloc[i]["account_type"].str.lower().to_numpy(); man["accounts_type_case"] = 300
    # transactions
    tx = tx.copy()
    tx["txn_ts"] = tx["txn_ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
    variants = {"Mobile App": ["mobile app", "MobileApp", "APP"], "USSD": ["ussd", "USSD "], "Internet Banking": ["Internet banking", "IB"],
                "Agent": ["AGENT"], "ATM": ["atm"], "Wallet Link": ["wallet link"]}
    i = rng.choice(len(tx), 30_000, replace=False)
    tx.iloc[i, tx.columns.get_loc("channel")] = [rng.choice(variants[c]) for c in tx.iloc[i]["channel"]]
    man["transactions_channel_label_variants"] = 30_000
    i = rng.choice(len(tx), 8_000, replace=False)
    tx.iloc[i, tx.columns.get_loc("status")] = [rng.choice([s.upper(), s.lower(), s + " "]) for s in tx.iloc[i]["status"]]
    man["transactions_status_variants"] = 8_000
    fidx = np.where(tx.status.str.strip().str.lower() == "failed")[0]
    i = rng.choice(fidx, 900, replace=False); tx.iloc[i, tx.columns.get_loc("failure_reason")] = None; man["transactions_failed_missing_reason"] = 900
    sidx = np.where(tx.status.str.strip().str.lower() == "success")[0]
    i = rng.choice(sidx, 250, replace=False); tx.iloc[i, tx.columns.get_loc("failure_reason")] = "Timeout"; man["transactions_success_with_reason"] = 250
    i = rng.choice(len(tx), 40, replace=False); tx.iloc[i, tx.columns.get_loc("amount")] = -tx.iloc[i]["amount"].to_numpy(); man["transactions_negative_amount"] = 40
    i = rng.choice(np.where(tx.channel == "ATM")[0], 35, replace=False)
    tx.iloc[i, tx.columns.get_loc("amount")] = tx.iloc[i]["amount"].to_numpy() * 100; man["transactions_atm_amount_x100"] = 35
    i = rng.choice(len(tx), 25, replace=False)
    tx.iloc[i, tx.columns.get_loc("txn_ts")] = tx.iloc[i]["txn_ts"].str.replace("2026-", "2036-").str.replace("2025-", "2035-").to_numpy()
    man["transactions_future_timestamp"] = 25
    i = rng.choice(len(tx), 30, replace=False); tx.iloc[i, tx.columns.get_loc("account_id")] = [f"TDB-A9{k:05d}" for k in range(30)]
    man["transactions_orphan_account"] = 30
    i = rng.choice(len(tx), 3_000, replace=False); tx.iloc[i, tx.columns.get_loc("processing_ms")] = -1; man["transactions_latency_sentinel"] = 3_000
    dup = tx.sample(6_000, random_state=2); tx = pd.concat([tx, dup]).sort_values("txn_ts", kind="stable"); man["transactions_duplicate_rows"] = 6_000
    return cust, acc, tx, man


def main(seed=2028):
    rng, cust, seg, acc, tx = simulate(seed)
    tx = add_retries(rng, tx, seg)
    tx["processing_ms"] = latency(rng, tx)
    ts = pd.DatetimeIndex(tx.txn_ts)
    fee = tariff_fee(tx.transaction_type.to_numpy(), tx.channel.to_numpy(), tx.amount.to_numpy(), ts)
    charged = np.where(tx.status == "Success", fee, 0.0)
    # defect 1: old tariff still applied to waived app transfers
    stale = (tx.transaction_type == "Send to Mobile Wallet") & (tx.channel == "Mobile App") & (tx.amount <= 1000) \
        & (ts >= STALE_FEE[0]) & (ts < STALE_FEE[1]) & (tx.status == "Success")
    charged[stale.to_numpy()] = np.where(tx.amount[stale] <= 100, 0, 10)
    # defect 2: fees charged on failed wallet and interbank transfers
    ff = tx.transaction_type.isin(["Send to Mobile Wallet", "Interbank Transfer"]) & tx.channel.isin(["Mobile App", "USSD"]) \
        & (tx.status == "Failed") & (ts >= FEE_ON_FAILED[0]) & (ts < FEE_ON_FAILED[1])
    old = tariff_fee(tx.transaction_type.to_numpy(), tx.channel.to_numpy(), tx.amount.to_numpy(), ts)
    charged[ff.to_numpy()] = old[ff.to_numpy()]
    tx["fee_charged"] = charged
    tx.insert(0, "transaction_id", [f"TX{i:08d}" for i in range(1, len(tx) + 1)])
    truth = {"transactions_clean": int(len(tx)), "stale_fee_rows": int(stale.sum()), "stale_fee_kes": float(charged[stale.to_numpy()].sum()),
             "fee_on_failed_rows": int((ff & (tx.fee_charged > 0)).sum()), "fee_on_failed_kes": float(tx.fee_charged[ff].sum())}
    tx = tx.drop(columns=["cust_idx"])
    cust_raw, acc_raw, tx_raw, man = inject_defects(rng, cust.copy(), acc.copy(), tx)
    RAW.mkdir(parents=True, exist_ok=True)
    cust_raw.to_csv(RAW / "customers.csv", index=False)
    acc_raw.assign(open_date=acc_raw.open_date.dt.date).to_csv(RAW / "accounts.csv", index=False)
    tx_raw[["transaction_id", "account_id", "txn_ts", "channel", "transaction_type", "amount", "status", "failure_reason",
            "processing_ms", "fee_charged"]].to_csv(RAW / "transactions.csv", index=False)
    fee_schedule().to_csv(RAW / "fee_schedule.csv", index=False)
    inc = pd.DataFrame([(f"INC-{k:03d}", c, pd.Timestamp(s), pd.Timestamp(s) + pd.Timedelta(hours=h), "Major" if h >= 4 else "Minor")
                        for k, (c, s, h, logged) in enumerate(INCIDENTS, 1) if logged],
                       columns=["incident_id", "channel", "start_ts", "end_ts", "severity"])
    inc.to_csv(RAW / "system_incidents.csv", index=False)
    pd.DataFrame([(c, s, h, logged) for c, s, h, logged in INCIDENTS], columns=["channel", "start", "hours", "logged"]).to_csv(RAW / "_truth_incidents.csv", index=False)
    (RAW / "injection_manifest.json").write_text(json.dumps({"seed": seed, "injected": man, "truth": truth}, indent=2))
    print(f"customers {len(cust):,}  accounts {len(acc):,}  transactions {len(tx):,} (raw {len(tx_raw):,})")
    print(json.dumps(truth, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--seed", type=int, default=2028)
    main(ap.parse_args().seed)
