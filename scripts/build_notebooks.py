"""Write and execute the eight analysis notebooks.

    python scripts/build_notebooks.py            # write + execute
    python scripts/build_notebooks.py --no-run
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import nbformat as nbf

NB_DIR = Path(__file__).resolve().parents[1] / "notebooks"
SETUP = """import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path.cwd().parent))
import numpy as np, pandas as pd, matplotlib.pyplot as plt, seaborn as sns
from src.data_loader import get_engine, query, PROJECT_ROOT
from src import analysis as an
an.set_style()
pd.set_option("display.float_format", "{:,.2f}".format); pd.set_option("display.max_columns", 30)
engine = get_engine()
o = an.sql_output"""


def md(s): return nbf.v4.new_markdown_cell(s.strip())
def code(s): return nbf.v4.new_code_cell(s.strip())


NB = {}
NB["01_transaction_data_understanding"] = [
    md("""# 01 · Transaction data understanding
**Company:** *Tausi Digital Bank*, a **fictional** Kenyan bank. All customer, account and transaction data is **synthetic** (`src/generate_data.py`). It does not describe any real bank, mobile-money provider or customer.

**Why synthetic?** I reviewed PaySim (Lopez-Rojas et al., 2016, CC BY-SA 4.0), the best-known public mobile-money dataset. It is itself simulated, covers only 30 days, and has no channel, status, failure reason, fee or customer fields. It was also built for fraud research, and this project is deliberately **not** about fraud: it is about volume, channels, reliability, fees and customer activity.

**The one real dataset** here is the Central Bank of Kenya's monthly mobile-payments statistics (`data/context/`). They are used only as a market trend line and never mixed into the synthetic data."""),
    code(SETUP),
    code("""query(engine, \"\"\"
SELECT 'customers' t, COUNT(*) n, 'one customer' grain FROM bank.customers
UNION ALL SELECT 'accounts', COUNT(*), 'one account (each customer has one primary account)' FROM bank.accounts
UNION ALL SELECT 'transactions', COUNT(*), 'one attempt: successful or failed; a retry is a new row' FROM bank.transactions
UNION ALL SELECT 'quarantined_transactions', COUNT(*), 'raw rows held back by cleaning, with a reason' FROM bank.quarantined_transactions
UNION ALL SELECT 'fee_schedule', COUNT(*), 'one tariff band: type x channel x amount band x validity period' FROM bank.fee_schedule
UNION ALL SELECT 'system_incidents', COUNT(*), 'one logged outage' FROM bank.system_incidents
UNION ALL SELECT 'context_cbk_mobile_payments', COUNT(*), 'REAL national statistics, one month' FROM bank.context_cbk_mobile_payments
UNION ALL SELECT 'mv_customer_month', COUNT(*), 'derived: one row per onboarded customer per month' FROM bank.mv_customer_month\"\"\")"""),
    md("""## Key definitions (from `sql/03_views.sql`)
| Term | Definition |
|---|---|
| Attempt | one row in `bank.transactions`; a retry is a new attempt |
| Success rate | successful attempts ÷ all attempts |
| Operational failure | failure owned by the bank or a partner: timeout, USSD session dropped, agent float, third-party unavailable, outage |
| Customer failure | insufficient funds, wrong PIN, limit exceeded; reported separately, not a reliability problem |
| **Transaction value** | KES moved by successful attempts. **Not revenue** |
| **Fee income** | fees charged on successful attempts: the bank's transaction revenue |
| Retry | next attempt on the same account, same type and amount, within 30 minutes |
| Active customer | at least one successful transaction in the month |
| Dormant | no successful transaction in the 90 days to 31 Oct 2026 |"""),
    code("""query(engine, \"\"\"SELECT channel, transaction_type, COUNT(*) attempts, ROUND(AVG(amount)) avg_amount_kes,
       ROUND(100.0*AVG(is_success::int),1) success_pct FROM bank.mv_transactions GROUP BY 1,2 ORDER BY 1,3 DESC\"\"\")"""),
    md("## One customer, end to end\nA Salaried customer's account history shows the grain: attempts, a failed attempt followed by a retry, fees only on successful fee-bearing transactions."),
    code("""cid = query(engine, \"\"\"SELECT customer_id FROM bank.mv_transactions WHERE segment='Salaried' AND was_retried
                         AND month = DATE '2026-09-01' ORDER BY transaction_id LIMIT 1\"\"\").iloc[0, 0]
query(engine, f\"\"\"SELECT txn_ts, channel, transaction_type, amount, status, failure_reason, fee_charged, was_retried, retry_status
                    FROM bank.mv_transactions WHERE customer_id = '{cid}' AND month = DATE '2026-09-01' ORDER BY txn_ts\"\"\")"""),
    code("""query(engine, "SELECT segment, COUNT(*) customers, ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),1) pct, MIN(onboarding_date) first_onboarded FROM bank.customers GROUP BY 1 ORDER BY 2 DESC")"""),
    code("query(engine, 'SELECT * FROM bank.fee_schedule ORDER BY transaction_type, channel, band_min_kes, effective_from').head(20)"),
]

NB["02_data_quality_and_cleaning"] = [
    md("# 02 · Data quality and cleaning\nProfile the raw extract, apply rules, and prove the clean layer is sound. Rule of thumb: **fix only what is certain, quarantine what would be a guess, log everything.**"),
    code(SETUP),
    code("o('dq_a01_row_counts_and_duplicates')"),
    code("o('dq_a02_label_variants')"),
    code("o('dq_a03_missing_values')"),
    md("### Amounts by channel"),
    code("o('dq_a04_amount_profile_by_channel')"),
    md("""ATM withdrawals are capped at KES 40,000 per transaction, and every ATM amount above that is exactly 100× a valid amount: a keying error. Those rows, the non-positive amounts (whose sign can't be trusted) and orphan account IDs are **quarantined** in `bank.quarantined_transactions` rather than repaired."""),
    code("o('dq_a05_timestamp_range')"),
    code("o('dq_a06_onboarding_dates')"),
    md("""### Rules applied (`src/data_cleaning.py`)
| Problem | Rule | Why |
|---|---|---|
| Exact duplicate rows | keep one | identical in every field |
| Channel, status, segment, account-type label variants | map to the reference list; the build **fails** on an unknown label | no silent new categories |
| Onboarding year 2065 etc. | replace with the customer's first account open date | a trusted field exists |
| Duplicate customer IDs whose copies differ | keep the most complete copy | one copy had a blank county |
| Failed attempt without a reason | `Unknown` | keeps the row, owner unknown |
| Successful attempt with a reason | reason → NULL | a success has no failure reason |
| Processing time −1 | NULL | not captured, not zero |
| Orphan account, timestamp after the extract, non-positive amount, ATM amount > 40,000 | **quarantine** | fixing would be a guess |
| **Fee charged against the tariff** | **kept as recorded** | it is a business finding (possible refunds), analysed in notebook 05 |"""),
    code("o('dq_b03_exception_log_summary')"),
    md("### Did cleaning catch every injected defect?\nThe generator recorded how many defects it injected. The exception log should account for each one."),
    code("""man = json.loads((PROJECT_ROOT / "data/synthetic/raw/injection_manifest.json").read_text())["injected"]
log = o('dq_b03_exception_log_summary').groupby('issue').rows.sum()
pairs = [("transactions_duplicate_rows", "exact duplicate row", "transactions"),
         ("transactions_channel_label_variants", "channel label variant", None),
         ("transactions_status_variants", "status label variant", None),
         ("transactions_failed_missing_reason", "failed transaction without a failure reason", None),
         ("transactions_success_with_reason", "successful transaction carrying a failure reason", None),
         ("transactions_negative_amount", "non-positive amount (sign cannot be trusted)", None),
         ("transactions_atm_amount_x100", "ATM amount above KES 40,000 limit (x100 pattern)", None),
         ("transactions_future_timestamp", "timestamp after extract date (year typo)", None),
         ("transactions_orphan_account", "account_id not in account master", None),
         ("transactions_latency_sentinel", "processing time sentinel (-1)", None),
         ("customers_onboarding_year_typo", "onboarding date outside data range (year typo)", None),
         ("customers_missing_county", "missing county", None),
         ("accounts_type_case", "account type label variant", None)]
ex = o('dq_b03_exception_log_summary')
rows = []
for m, issue, table in pairs:
    sel = ex[ex.issue == issue]
    if table: sel = sel[sel.source_table == table]
    rows.append((m, man[m], int(sel.rows.sum())))
chk = pd.DataFrame(rows, columns=["injected_defect", "injected", "logged"]); chk["difference"] = chk.logged - chk.injected; chk"""),
    md("""Every transaction defect is accounted for exactly. Missing counties are slightly lower than injected because a few blank counties fell on duplicate customer copies, and the more complete copy was kept. (Segment variants are not in this table: upper-casing `SME` changes nothing, so fewer than the 400 injected labels actually differ.)"""),
    code("o('dq_b01_integrity_summary')"),
    code("o('dq_b02_reconciliation')"),
    md("All eight integrity checks return 0, and the semantic layer carries every transaction once: value and fees in the customer-month grid equal the transaction table to the shilling."),
]

NB["03_volume_and_channel_analysis"] = [
    md("# 03 · Volume and channel analysis\nQuestions 1–8: how much moves, through which channels, and when. **Value (KES moved) is never treated as revenue.**"),
    code(SETUP),
    code("o('q01_headline_kpis')"),
    code("""m = o('q02_monthly_trend'); m['month'] = pd.to_datetime(m.month)
fig, axes = plt.subplots(1, 2, figsize=(12, 3.8))
axes[0].plot(m.month, m.successful / 1e3, color=an.PRIMARY); axes[0].set_title("Successful transactions per month (thousands)")
axes[1].plot(m.month, m.fee_income_kes_k / 1e3, color=an.PRIMARY); axes[1].set_title("Fee income per month (KES M)")
for a in axes: a.set_ylim(0); a.tick_params(axis='x', rotation=45)
an.save(fig, "monthly_volume_and_fees"); m[['month','successful','value_kes_m','fee_income_kes_k','success_rate_pct','active_customers']].tail(6)"""),
    md("Successful transactions grew by about three quarters over 18 months, driven mainly by new customers (the customer base grew from about 18,500 to 30,000). Fee income grew at the same pace, and the fee per successful transaction stayed at about KES 26–27."),
    code("""c = o('q03_channel_mix_over_time'); c['month'] = pd.to_datetime(c.month)
cols = {'mobile_app_pct': 'Mobile App', 'ussd_pct': 'USSD', 'agent_pct': 'Agent', 'atm_pct': 'ATM', 'internet_banking_pct': 'Internet Banking', 'wallet_link_pct': 'Wallet Link'}
fig, ax = plt.subplots(figsize=(10, 4))
ends = sorted(((c[col].iloc[-1], name) for col, name in cols.items()))
pos, last = {}, -99
for v, name in ends:                      # spread end labels so they never overlap
    last = max(v, last + 2.2); pos[name] = last
for col, name in cols.items():
    hi = name in ('Mobile App', 'USSD')
    ax.plot(c.month, c[col], color=an.PRIMARY if name == 'Mobile App' else an.HIGHLIGHT if name == 'USSD' else an.CONTEXT, lw=2.4 if hi else 1.4)
    ax.annotate(f"{name} {c[col].iloc[-1]:.0f}%", (c.month.iloc[-1], pos[name]), xytext=(6, 0), textcoords='offset points', va='center', fontsize=9,
                color=an.INK if hi else an.INK_MUTED)
ax.set_title("Share of successful transactions by channel (%)"); ax.set_ylim(0); ax.set_xlim(right=c.month.iloc[-1] + pd.Timedelta(days=75))
an.save(fig, "channel_mix_trend"); c.iloc[[0, -1]]"""),
    md("The **app gained about 7 points of share and USSD lost about 7**: customers are migrating from USSD to the app. The other channels held steady."),
    code("o('q04_channel_summary')"),
    md("""**Each channel does a different job.** The app carries the most transactions (43%), but Internet Banking moves the most money (about half of all value, mostly SME interbank transfers). Agents carry 12% of volume and earn 21% of fee income (cash withdrawals). USSD has the lowest success rate."""),
    code("""h = o('q06_hour_by_weekday').pivot(index='weekday', columns='hour', values='avg_successful_per_day_slot')
h = h.loc[['Mon','Tue','Wed','Thu','Fri','Sat','Sun']]
fig, ax = plt.subplots(figsize=(12, 3.4))
sns.heatmap(h, cmap=sns.color_palette(an.SEQ_BLUES, as_cmap=True), cbar_kws={'label': 'avg successful per hour'}, ax=ax, linewidths=0.5, linecolor='white')
ax.set_title("When customers transact: average successful transactions per hour slot"); ax.set_xlabel('hour of day'); ax.set_ylabel('')
an.save(fig, "hour_weekday_heatmap");"""),
    code("o('q07_payday_effect')"),
    md("Payday days (the 25th to the 2nd) are about **35% busier** than the rest of the month, for both transactions and value. Agent cash withdrawals follow the same pattern, and that matters for agent float (notebook 04)."),
    code("""v = o('q08_value_vs_fees_by_type').set_index('transaction_type')
fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
v = v.sort_values('share_of_value_pct')
axes[0].barh(v.index, v.share_of_value_pct, color=an.PRIMARY); axes[0].set_title("Share of value moved (%)")
axes[1].barh(v.index, v.share_of_fees_pct, color=an.HIGHLIGHT); axes[1].set_title("Share of fee income (%)")
an.save(fig, "value_vs_fees_by_type"); v.sort_values('fee_income_kes_m', ascending=False)"""),
    md("**Value is not revenue.** Interbank transfers are 56% of value and 42% of fees. Cash withdrawals are only 9% of value but 35% of fee income. Internal transfers move 17% of value and earn nothing. A dashboard that ranks channels by value would point management at the wrong places."),
    code("o('q05_channel_by_type').head(12)"),
]

NB["04_reliability_and_failures"] = [
    md("# 04 · Reliability and failures\nQuestions 9–16. Customer-caused failures (insufficient funds, wrong PIN, limits) are kept apart from **operational** failures, which the bank and its partners own."),
    code(SETUP),
    code("""r = o('q09_success_rate_by_channel').set_index('channel').sort_values('operational_failure_pct')
fig, ax = plt.subplots(figsize=(9, 3.6))
y = np.arange(len(r)); w = 0.38
ax.barh(y + w/2, r.operational_failure_pct, w, color=an.HIGHLIGHT, label='Operational (bank or partner)')
ax.barh(y - w/2, r.customer_failure_pct, w, color=an.CONTEXT, label='Customer (funds, PIN, limit)')
ax.set_yticks(y, r.index); ax.set_title("Failed attempts by owner, % of attempts"); ax.legend(loc='lower right')
an.save(fig, "failure_rates_by_channel"); r"""),
    md("**USSD is the least reliable channel** (3.3% of attempts fail for operational reasons), followed by agents (2.6%). The app and ATMs are the most reliable."),
    code("o('q10_failure_reasons_by_channel').query('channel in [\"USSD\", \"Agent\", \"Mobile App\"]')"),
    code("""hr = o('q11_failure_by_hour')
fig, ax = plt.subplots(figsize=(10, 3.6))
ax.plot(hr.hour, hr.ussd_pct, color=an.HIGHLIGHT, label='USSD'); ax.plot(hr.hour, hr.mobile_app_pct, color=an.PRIMARY, label='Mobile App')
ax.axvspan(16.5, 21.5, color=an.CONTEXT, alpha=0.2); ax.text(19, ax.get_ylim()[1]*0.92, 'evening peak', ha='center', color=an.INK_MUTED, fontsize=9)
ax.set_title("Operational failure rate by hour of day (%)"); ax.set_xlabel('hour'); ax.set_ylim(0); ax.legend()
an.save(fig, "ussd_failures_by_hour"); hr.loc[[8, 12, 17, 18, 19, 20, 21, 23]]"""),
    md("USSD operational failures more than double in the evening peak (17:00–21:00), when session capacity is under most pressure. The app stays flat through the day."),
    code("o('q12_agent_float_month_end')"),
    md("Agent cash withdrawals fail for lack of float about **6% of the time on payday days and at weekends**, against 1% on ordinary weekdays: agents run out of cash exactly when customers need it most."),
    code("o('q13_retry_and_recovery')"),
    md("""About **half of operational failures are retried within 30 minutes, and about half are recovered** (the retry succeeds). Around 20–30% of retries after a timeout or a dropped USSD session happen on a different channel, usually the app. The other half are lost transactions. During outages, recovery falls to about 19%."""),
    code("o('q14_incident_impact')"),
    md("During logged incidents, the affected channel fails 41–62% of attempts, against under 1% to 5% normally at the same hours."),
    code("""d = o('q15_daily_app_timeouts'); d['txn_date'] = pd.to_datetime(d.txn_date)
fig, ax = plt.subplots(figsize=(10, 3.4))
ax.plot(d.txn_date, d.timeout_pct, color=an.PRIMARY, marker='o', ms=3)
ax.axvspan(pd.Timestamp('2026-03-10'), pd.Timestamp('2026-03-19 23:59'), color=an.HIGHLIGHT, alpha=0.15)
ax.text(pd.Timestamp('2026-03-15'), d.timeout_pct.max()*0.95, 'app release window', ha='center', color=an.HIGHLIGHT, fontsize=9)
ax.set_title("Mobile App: daily timeout rate (%), late Feb - early Apr 2026"); ax.set_ylim(0)
an.save(fig, "app_release_timeouts"); d.sort_values('timeout_pct', ascending=False).head(5)"""),
    md("App timeouts rose about **sevenfold for ten days from 10 March 2026**, then returned to normal: the pattern of a faulty release that was later fixed. The incident log records only a three-hour app outage on 13 March, so the release problem was never logged as an incident."),
    code("""lat = o('q16_latency_by_channel_month')
lat.pivot(index='month', columns='channel', values='p95_ms').tail(6)"""),
]

NB["05_fee_income_analysis"] = [
    md("# 05 · Fee income\nQuestions 17–22: where fee income comes from, what failures cost in fees, and whether customers were charged correctly."),
    code(SETUP),
    code("""f = o('q17_fee_income_by_channel_month'); f['month'] = pd.to_datetime(f.month)
fig, ax = plt.subplots(figsize=(10, 4))
for col, name, color in [('mobile_app_kes_k','Mobile App',an.PRIMARY), ('internet_banking_kes_k','Internet Banking',an.SECOND),
                          ('agent_kes_k','Agent',an.HIGHLIGHT), ('atm_kes_k','ATM',an.CONTEXT), ('ussd_kes_k','USSD','#8a8983')]:
    ax.plot(f.month, f[col], color=color); ax.annotate(name, (f.month.iloc[-1], f[col].iloc[-1]), xytext=(6, 0), textcoords='offset points', va='center', fontsize=9)
ax.set_title("Fee income by channel (KES thousands per month)"); ax.set_ylim(0); ax.set_xlim(right=f.month.iloc[-1] + pd.Timedelta(days=80))
an.save(fig, "fee_income_by_channel"); f.iloc[[0, -1]]"""),
    code("o('q18_fee_income_by_segment')"),
    md("SME and Premier customers are 18% of the base and pay more than half of all fees (54%), mostly through interbank transfers."),
    code("""fl = o('q19_fees_lost_to_failures')
detail = fl.dropna(subset=['channel', 'failure_reason']).sort_values('fees_lost_kes', ascending=True).tail(8)
fig, ax = plt.subplots(figsize=(9, 3.8))
ax.barh(detail.channel + ' · ' + detail.failure_reason, detail.fees_lost_kes / 1e3, color=an.HIGHLIGHT)
ax.set_title("Fee income lost to unrecovered operational failures (KES thousands, 18 months)")
an.save(fig, "fees_lost_to_failures")
total = fl[fl.channel.isna()].iloc[0]
print(f"Unrecovered operational failures: {int(total.not_recovered):,}; fee income lost: KES {total.fees_lost_kes:,.0f}; value not completed: KES {total.value_not_completed_kes_m:,.0f}M")"""),
    md("""Unrecovered operational failures cost about **KES 0.6M of fee income** over 18 months (about 1.2% of fee income). **Agent float shortages are the single largest item**, because a failed agent withdrawal is a high-fee transaction. The value that never moved (about KES 250M) matters more to customers than the fee does to the bank."""),
    code("o('q20_fee_billing_exceptions')"),
    code("""b = o('q20_fee_billing_exceptions')
print(f"Transactions charged against the tariff: {b.transactions.sum():,}; overcharge to refund: KES {b.overcharge_kes.sum():,.0f}")"""),
    md("""**Two fee-engine defects**, found by recomputing every fee from the tariff:
1. **1–14 Feb 2026:** the KES 10 fee was still charged on app wallet transfers of KES 101–1,000 after they became free on 1 February.
2. **2 Mar – 12 Apr 2026:** fees were charged on *failed* wallet and interbank transfers.

The total is small (about KES 96k), but it affects thousands of customers and is a conduct issue: overcharges should be refunded and the fee engine needs a reconciliation control. **Nothing in the cleaning step "fixed" these fees.** They are kept as charged, because the difference is the finding."""),
    code("""w = o('q21_wallet_fee_waiver')
w"""),
    code("""p = w.pivot(index='period', columns='channel', values='share_of_small_transfers_pct')
fig, ax = plt.subplots(figsize=(7, 3.2))
y = np.arange(len(p))[::-1]; ax.barh(y, p['Mobile App'], color=an.PRIMARY, label='Mobile App', edgecolor='white', linewidth=2)
ax.barh(y, p['USSD'], left=p['Mobile App'], color=an.CONTEXT, label='USSD', edgecolor='white', linewidth=2)
for yi, v in zip(y, p['Mobile App']): ax.text(v/2, yi, f"{v:.0f}%", color='white', va='center', ha='center', fontweight='bold')
ax.set_yticks(y, ['Before (Aug 25 - Jan 26)', 'After (Feb - Jul 26)']); ax.set_xlim(0, 100)
ax.set_title("Small wallet transfers (KES 1,000 or less): share by channel"); ax.legend(loc='lower right', ncol=2, bbox_to_anchor=(1, -0.38))
an.save(fig, "wallet_waiver_effect")
fz = o('q22_waiver_fee_foregone'); print(f"Fee foregone by the waiver, Feb - Oct 2026: KES {fz.fee_foregone_kes.sum():,.0f} (about KES {fz.fee_foregone_kes.mean()/1e3:,.0f}k a month)")"""),
    md("""The waiver did what it was meant to do: the app's share of small wallet transfers rose from about 60% to 75%, moving traffic off USSD, the least reliable channel. It costs about KES 60–70k of fee income a month, roughly 2% of fee income. Customers who switched also stopped paying the USSD fee (KES 12), so the true cost is a little higher; that is a trade-off management chose."""),
]

NB["06_customer_activity_and_segments"] = [
    md("# 06 · Customer activity and segments\nQuestions 23–31: who is active, who is going quiet, and whether a bad experience matters."),
    code(SETUP),
    code("o('q23_activity_by_segment')"),
    code("""s = o('q24_activity_status').pivot(index='segment', columns='activity_status', values='share_of_segment_pct').fillna(0)
order = ['Active (last 30 days)', 'At risk (30-89 days)', 'Dormant (90+ days)', 'Too new to assess']
s = s[order].sort_values('Dormant (90+ days)')
fig, ax = plt.subplots(figsize=(10, 3.4)); left = np.zeros(len(s))
colors = [an.PRIMARY, '#9ec5f4', an.HIGHLIGHT, an.CONTEXT]
for col, colr in zip(order, colors):
    ax.barh(s.index, s[col], left=left, color=colr, label=col, edgecolor='white', linewidth=2); left += s[col].values
ax.set_xlim(0, 100); ax.set_title("Customer activity status at 31 Oct 2026 (% of segment)"); ax.legend(ncol=4, loc='lower center', bbox_to_anchor=(0.5, -0.42))
an.save(fig, "activity_status_by_segment"); s"""),
    md("About **19% of customers are dormant** (no successful transaction in 90 days), mostly Mass Retail and Youth. Premier, SME and Salaried customers are much more consistently active."),
    code("o('q25_dormancy_by_onboarding_channel')"),
    code("""c = o('q26_new_customer_activation')
curve = c.groupby('months_since_onboarding').apply(lambda g: np.average(g.active_pct, weights=g.cohort_size)).rename('active_pct')
fig, ax = plt.subplots(figsize=(8, 3.4)); ax.plot(curve.index, curve.values, color=an.PRIMARY, marker='o', ms=4)
ax.set_title("New customers (May 2025 - Oct 2026): % active k months after onboarding"); ax.set_xlabel('months since onboarding'); ax.set_ylim(0, 100)
an.save(fig, "new_customer_activation"); curve.round(1).to_frame().T"""),
    md("New customers are less active in their onboarding month (they join part-way through it). From the following month about 72% are active each month, the same rate as the established base."),
    code("""q = o('q28_failures_and_next_month_activity')
q['inactive_next_month_pct'] = 100 - q.active_next_month_pct
pv = q.pivot(index='activity_band', columns='operational_failures_in_month', values='inactive_next_month_pct')
fig, ax = plt.subplots(figsize=(9, 3.6)); x = np.arange(len(pv)); w = 0.26
for i, (col, colr) in enumerate(zip(pv.columns, [an.CONTEXT, '#6da7ec', an.HIGHLIGHT])):
    ax.bar(x + (i-1)*w, pv[col], w, color=colr, edgecolor='white', linewidth=1.5, label={'none': 'no operational failure', '1': '1 operational failure', '2 or more': '2+ operational failures'}[col[3:]])
ax.set_xticks(x, [b[3:] + ' in month' for b in pv.index]); ax.set_title("% inactive the next month, by failures hit this month")
ax.legend(); an.save(fig, "failures_vs_next_month_activity"); q"""),
    md("""**Comparing like with like matters here.** Customers who hit more failures look *more* loyal overall, because busy customers both transact more and hit more failures. Within the same activity band the pattern reverses: among customers making 10+ attempts a month, those who hit two or more operational failures were about **twice as likely to be inactive the next month** (4.4% vs 2.0%). The same pattern holds for 4–9 attempts (6.8% vs 4.4%); the lightest band has too few customers with failures to say. This is an association in observational data, not proof of cause."""),
    code("o('q27_multichannel_customers')"),
    code("o('q29_value_concentration')"),
    md("Value is highly concentrated: the top 10% of customers move 71% of the value but pay only 47% of fees, because the heaviest value (interbank) carries a low fee per shilling."),
    code("o('q30_digital_adoption_by_segment')"),
    code("""mk = o('q31_market_context'); mk['month'] = pd.to_datetime(mk.month)
fig, ax = plt.subplots(figsize=(9, 3.6))
ax.plot(mk.month, mk.bank_volume_index, color=an.PRIMARY, label='Tausi Digital Bank: successful transactions (synthetic)')
ax.plot(mk.month, mk.market_volume_index, color=an.CONTEXT, label='Kenya mobile-money agent transactions (CBK, real)')
ax.axhline(100, color=an.INK_MUTED, lw=0.8, ls=':'); ax.set_title("Growth indexed to May 2025 = 100"); ax.legend(loc='upper left')
an.save(fig, "market_context_index"); mk.tail(3)"""),
    md("Real CBK statistics show the national mobile-money agent market roughly flat over the period, while the fictional bank's volume grew about 73% by September 2026. The comparison is context only: the two series measure different things, and the bank's figures are synthetic."),
]

NB["07_anomaly_investigation"] = [
    md("""# 07 · Anomaly investigation (optional extension)
**Question:** can unusual operational hours be found from the data alone, without the incident log?

This is **not** fraud detection, and there are no fraud labels. The unit is a **channel-hour**: attempts, operational failure rate and 95th-percentile latency. An Isolation Forest flags hours that look unlike the rest. The incident log is used only *afterwards*, to see what the model found."""),
    code(SETUP),
    code("""from sklearn.ensemble import IsolationForest
ch = query(engine, "SELECT * FROM bank.mv_channel_hour")
ch['hour_ts'] = pd.to_datetime(ch.hour_ts)
# expected attempts for this channel, weekday and hour (median over the window)
ch['expected'] = ch.groupby(['channel', 'iso_dow', 'hour_of_day']).attempts.transform('median')
busy = ch.expected >= 10           # skip hours that are normally almost empty (e.g. agents at 3 a.m.)
d = ch[busy].copy()
d['volume_ratio'] = np.log1p(d.attempts) - np.log1p(d.expected)
d['op_fail_rate'] = (d.operational_failures + 0.5) / (d.attempts + 30)      # smoothed so quiet hours don't dominate
d['latency_ratio'] = np.log(d.p95_ms.fillna(d.groupby('channel').p95_ms.transform('median')) / d.groupby('channel').p95_ms.transform('median'))
feats = ['volume_ratio', 'op_fail_rate', 'latency_ratio']
# standardise within channel so each channel is judged against its own normal
Z = d.groupby('channel')[feats].transform(lambda s: (s - s.median()) / (s.quantile(.75) - s.quantile(.25) + 1e-9))
iso = IsolationForest(n_estimators=300, contamination=0.003, random_state=7).fit(Z)
d['score'] = -iso.score_samples(Z); d['flag'] = iso.predict(Z) == -1
print(f"channel-hours scored: {len(d):,}; flagged: {d.flag.sum()}")"""),
    md("Flagged hours that are next to each other on the same channel are grouped into **episodes**."),
    code("""f = d[d.flag].sort_values(['channel', 'hour_ts'])
f['episode'] = (f.groupby('channel').hour_ts.diff() != pd.Timedelta(hours=1)).cumsum()
ep = f.groupby('episode').agg(channel=('channel', 'first'), start=('hour_ts', 'min'), end=('hour_ts', 'max'),
                              hours=('hour_ts', 'size'), attempts=('attempts', 'sum'), op_failures=('operational_failures', 'sum'),
                              max_score=('score', 'max')).reset_index(drop=True)
ep['op_fail_pct'] = (100 * ep.op_failures / ep.attempts).round(1)
inc = query(engine, "SELECT * FROM bank.system_incidents")
def match(r):
    m = inc[(inc.channel == r.channel) & (pd.to_datetime(inc.start_ts) <= r.end + pd.Timedelta(hours=1)) & (pd.to_datetime(inc.end_ts) >= r.start)]
    return m.incident_id.iloc[0] if len(m) else None
ep['logged_incident'] = ep.apply(match, axis=1)
ep.sort_values('max_score', ascending=False).head(25)"""),
    code("""logged_found = ep.logged_incident.dropna().nunique()
print(f"Logged incidents: {len(inc)}; found by the model: {logged_found}")
unlogged = ep[ep.logged_incident.isna() & (ep.op_fail_pct >= 20) & (ep.op_failures >= 15)]
print("Episodes with at least 15 operational failures and a 20%+ failure rate that are NOT in the incident log:")
unlogged[['channel', 'start', 'end', 'hours', 'attempts', 'op_fail_pct']]"""),
    md("""**What the model found.** It recovers all 11 logged incidents from the data alone. It also flags **three high-failure episodes that are not in the incident log** (an app outage in October 2025, a USSD outage in May 2026 and an Internet Banking outage in September 2026). These are the most useful output: outages that customers felt but nobody recorded, so they were never reviewed. The remaining flags are mostly unusually busy or quiet hours with normal failure rates (for example public-holiday patterns), which are worth a glance but not an escalation.

*Validation (possible only because the data is synthetic):* the generator kept a private list of every outage it injected, including three that were deliberately left out of the log."""),
    code("""truth = pd.read_csv(PROJECT_ROOT / 'data/synthetic/raw/_truth_incidents.csv'); truth['start'] = pd.to_datetime(truth.start)
truth['end'] = truth.start + pd.to_timedelta(truth.hours, unit='h')
truth['found_by_model'] = truth.apply(lambda t: bool(((ep.channel == t.channel) & (ep.start <= t.end) & (ep.end >= t.start)).any()), axis=1)
truth"""),
    code("""fig, axes = plt.subplots(2, 1, figsize=(11, 5), sharex=True)
for ax, c in zip(axes, ['USSD', 'Mobile App']):
    s = d[d.channel == c].set_index('hour_ts').op_fail_rate.resample('D').max() * 100
    ax.plot(s.index, s.values, color=an.CONTEXT, lw=1)
    fl = d[(d.channel == c) & d.flag]
    ax.scatter(fl.hour_ts, 100 * fl.op_fail_rate, color=an.HIGHLIGHT, s=18, zorder=3, label='flagged hour')
    ax.set_title(f"{c}: worst hourly operational failure rate each day (%)"); ax.set_ylim(0)
axes[0].legend(loc='upper left'); an.save(fig, "anomaly_timeline");"""),
    md("""**Limits.** A slow-burning problem is harder to see hour by hour: the March 2026 app release raised timeouts to 3–4% for ten days, which barely moves a single hour, but the daily view in notebook 04 shows it clearly. Hourly anomaly detection and daily trend monitoring are complementary. Isolation Forest also has no notion of cause and a contamination rate that I chose (0.3% of hours). With real data, flagged hours would go to the operations team for review, and their feedback (real incident or not) would tune the threshold. It is a monitoring aid, not an alarm system."""),
]

NB["08_business_recommendations"] = [
    md("# 08 · Business recommendations\nSizing the actions from the analysis."),
    code(SETUP),
    code("""k = o('q01_headline_kpis'); k"""),
    code("""fl = o('q19_fees_lost_to_failures'); tot = fl[fl.channel.isna()].iloc[0]
agent = fl[(fl.channel == 'Agent') & (fl.failure_reason == 'Agent float unavailable')].iloc[0]
ussd = fl[(fl.channel == 'USSD') & fl.failure_reason.isna()].iloc[0]
b = o('q20_fee_billing_exceptions'); fz = o('q22_waiver_fee_foregone')
dormant = o('q24_activity_status'); dm = dormant[dormant.activity_status == 'Dormant (90+ days)']
summary = pd.DataFrame([
    ("Unrecovered operational failures (18 months)", f"{int(tot.not_recovered):,} attempts", f"KES {tot.fees_lost_kes/1e3:,.0f}k fees; KES {tot.value_not_completed_kes_m:,.0f}M not moved"),
    ("  of which agent float shortages", f"{int(agent.not_recovered):,}", f"KES {agent.fees_lost_kes/1e3:,.0f}k fees"),
    ("  of which USSD (all operational reasons)", f"{int(ussd.not_recovered):,}", f"KES {ussd.fees_lost_kes/1e3:,.0f}k fees"),
    ("Fee-engine overcharges to refund", f"{b.transactions.sum():,} transactions / {b.customers_affected.sum():,} customer-cases", f"KES {b.overcharge_kes.sum():,.0f}"),
    ("Wallet-transfer waiver cost (Feb - Oct 2026)", f"{fz.waived_app_transfers.sum():,} free transfers", f"KES {fz.fee_foregone_kes.sum()/1e3:,.0f}k"),
    ("Dormant customers (90+ days)", f"{dm.customers.sum():,}", "re-activation target"),
], columns=['item', 'volume', 'value'])
summary"""),
    md("""## Recommendations

| # | Recommendation | Evidence | Measure |
|---|---|---|---|
| 1 | **Agent float management for payday and weekends**: float top-up alerts and liquidity support for high-volume agents from the 24th of each month and on Fridays | Float failures 6% on payday days and weekends vs 1% otherwise; largest source of lost fees (q12, q19) | Agent float failure rate below 2% on payday days |
| 2 | **USSD peak-hour capacity**: raise session capacity with the network partner for 17:00–21:00 and keep moving small transfers to the app | USSD operational failures double at the evening peak; USSD is the least reliable channel (q9, q11) | USSD operational failure rate at 17:00–21:00 |
| 3 | **Refund the fee-engine overcharges and add a daily tariff reconciliation** | KES 96k charged against the tariff across about 4,000 transactions in two defects (q20) | Zero tariff variances in the daily check |
| 4 | **Release controls for the app**: staged roll-out and automatic rollback when the timeout rate exceeds twice its normal level | Timeouts rose sevenfold for 10 days after a release; not in the incident log (q15) | Release-related timeout spikes caught within hours |
| 5 | **Log every outage, using anomaly monitoring as a safety net** | The anomaly model found high-failure episodes that are missing from the incident log (nb 07) | Share of anomaly episodes with a matching incident record |
| 6 | **Win back dormant customers and follow up on bad experiences**: reactivation offers for the dormant 18%, and proactive contact after two or more operational failures in a month | Customers with 2+ failures are about twice as likely to be inactive the next month at the same activity level (q28) | 90-day reactivation rate; next-month activity after failures |

**What the data cannot tell us:** why individual customers go dormant (no survey), competitor pricing, the network partner's own capacity data, and costs (fee income is revenue, not profit)."""),
]


def main(run: bool = True) -> None:
    NB_DIR.mkdir(exist_ok=True)
    for name, cells in NB.items():
        nb = nbf.v4.new_notebook(); nb["cells"] = cells
        nb["metadata"]["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
        path = NB_DIR / f"{name}.ipynb"; nbf.write(nb, path)
        if run:
            print("executing", path.name, flush=True)
            subprocess.run([sys.executable, "-m", "jupyter", "nbconvert", "--to", "notebook", "--execute", "--inplace",
                            "--ExecutePreprocessor.timeout=1200", str(path)], check=True, capture_output=True)


if __name__ == "__main__":
    main(run="--no-run" not in sys.argv)
