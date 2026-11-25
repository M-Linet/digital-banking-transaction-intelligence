# Methodology

All customer, account and transaction data in this project is **synthetic**, generated for the fictional *Tausi Digital Bank*. The only real data is the Central Bank of Kenya's monthly mobile-payments statistics, used as market context. This document covers how the data was built, cleaned, validated and analysed, and which choices affect the results.

## 1. Why synthetic data
| Source reviewed | What it offers | Why it was not used directly |
|---|---|---|
| PaySim (Lopez-Rojas, Elmir & Axelsson, 2016; Kaggle `ealaxi/paysim1`, CC BY-SA 4.0) | About 6.4M simulated mobile-money transactions over 30 days (744 hourly steps), calibrated on an African mobile-money service: type, amount, balances, fraud flags | It is itself simulated. It covers 30 days, too short for monthly trends. It has no channel, status, failure reason, fee or customer attributes. It was built for fraud detection, which this project deliberately does not cover. |
| CBK national mobile-payments statistics | Real monthly totals: agents, accounts, agent cash-in/cash-out volume and value | Aggregates only. Loaded as `bank.context_cbk_mobile_payments` for a market trend line. |

PaySim's mix of transfer, cash-out and payment types informed the transaction-type mix. No PaySim rows are used.

## 2. How the data is generated (`src/generate_data.py`, seed 2028)
- **Customers and accounts.** There are 18,000 existing customers (onboarded 2021 – April 2025) and about 670 new customers a month, 30,000 in total. They fall into five segments: Mass Retail 40%, Youth 22%, Salaried 20%, SME 12% and Premier 6%. Each customer has one primary account (Current or Savings), and some also have a digital wallet or a second savings account.
- **Channel preference.** Each customer has a preference across six channels, drawn from their segment and onboarding channel. Each month 2% of the USSD preference moves to the app.
- **Activity.** Customers switch between active and dormant states. Monthly transaction counts depend on segment and personal engagement. Day weights include a weekday pattern and a payday bump (25th – 2nd, ×1.35). Hours follow channel profiles: digital channels peak in the evening, agents keep shop hours.
- **Transaction types and amounts.** Each channel offers specific types. Amounts are drawn from lognormal distributions, scaled by segment. ATM withdrawals are in multiples of KES 500, up to KES 40,000.
- **Failures.** Each attempt gets a probability for each failure reason:
  - **Customer-caused:** insufficient funds (higher for Mass Retail and Youth), wrong PIN (higher on USSD), and limit exceeded (large amounts).
  - **Operational:**
    - timeouts, raised sevenfold during the 10–19 March 2026 app release;
    - USSD session drops, ×2.8 at 17:00–21:00 and ×1.3 on paydays;
    - agent float shortages on withdrawals (6% on paydays and weekends, 1% otherwise);
    - third-party unavailability (interbank, bills, wallet);
    - 14 outages (2–8 hours, 55% failure), of which only **11 are written to the incident log**.
- **Feedback.** Customers who hit operational failures in a month are 1.5× (one failure) or 2.5× (two or more) more likely to become dormant the next month.
- **Retries.** After an operational failure, 55% of customers retry within 1–15 minutes (40% during outages). About 30% of those retries happen on another channel that offers the same type. After a wrong PIN, 60% retry within 3 minutes.
- **Fees.** Fees come from a versioned tariff, applied to successful attempts only. From 1 February 2026, app transfers to mobile wallets of KES 1,000 or less are free, and 30% of small USSD transfers move to the app. Two fee-engine defects are planted:
  1. the old KES 10 fee is still charged on waived transfers from 1 to 14 February 2026;
  2. fees are charged on *failed* wallet and interbank transfers from 2 March to 12 April 2026.

I designed these mechanisms, so their direction is not a discovery. The analysis has to **find and size them** from messy records without being told they exist. It also has to avoid the traps that would mislead: mixing customer and operational failures, treating value as revenue, ignoring activity confounding, and cleaning away a business finding.

## 3. Injected data-quality problems and rules
| Table | Problem (injected) | Rule | Logged |
|---|---|---|---|
| customers | exact duplicate rows (60) | normalise labels, then drop exact duplicates; when copies still differ, keep the most complete copy | 56 + 4 |
| customers | segment upper-cased (400) | map to the reference list | 346 (54 were `SME`, unchanged) |
| customers | missing county (700) | keep NULL, report as *Unknown* | 696 (4 fell on dropped duplicate copies) |
| customers | onboarding year 2065 etc. (15) | replace with the first account open date | 15 |
| accounts | account type lower-cased (300) | map to the reference list | 300 |
| transactions | exact duplicate rows (6,000) | keep one copy | 6,000 |
| transactions | channel variants such as `APP`, `MobileApp`, `IB` (30,000) | alias map; the build **fails** on an unknown label | 30,000 |
| transactions | status variants (8,000) | map | 8,000 |
| transactions | failed with no reason (900) / success with a reason (250) | `Unknown` / NULL | 900 / 250 |
| transactions | processing time −1 (3,000) | NULL | 3,000 |
| transactions | orphan account (30), future year (25), negative amount (40), ATM ×100 (35) | **quarantine** | 130 |

Notebook 02 reconciles the exception log with `injection_manifest.json` programmatically.

**Fees are deliberately not cleaned.** A fee that differs from the tariff is internally consistent data: a valid amount on a valid row. It is the *finding*, a refund liability. It is kept as recorded and measured in SQL (`q20`).

## 4. Validation
- **`dq_b01`:** eight integrity checks, all returning 0. They cover transactions before account opening or onboarding, accounts before onboarding, exactly one primary account per customer, the extract window, the ATM limit, type offered on the channel, and no transaction matching two tariff rows.
- **`dq_b02`:** the semantic view has exactly as many rows as the transaction table. Value and fees in the customer-month grid equal the transaction table, and the channel-hour grid holds every attempt.
- **Python mirrors.** Python versions of the tariff lookup and the retry inference (`src/analysis.py`) are tested against the SQL results.
- **Planted defects.** The fee variance found in SQL matches the fee defects the generator planted (within 2%, because a few defect rows were quarantined for other reasons).
- **Anomaly check.** The anomaly model was checked against the generator's private outage list, which includes the three unlogged outages.
- **Power BI.** The dashboard cards match `powerbi/kpi_reconciliation.csv` exactly.
- **Tests.** 30 pytest tests.

## 5. Metric definitions (`sql/03_views.sql`)
| Metric | Definition | Why |
|---|---|---|
| Attempt | one row in `bank.transactions` | retries are real load on the system and real customer friction |
| Success rate | successful ÷ attempts | |
| Operational / customer failure rate | failures by owner ÷ attempts | a wrong PIN is not a reliability problem |
| Transaction value | Σ amount of successful attempts | money moved, **not revenue** |
| Fee income | Σ fee charged on successful attempts | the bank's transaction revenue |
| Expected fee | tariff fee in force on the date, 0 if failed | basis for the billing audit |
| Fee variance | fee charged − expected fee | positive = overcharge |
| Retry | next attempt on the same account, same type and amount, ≤ 30 minutes later | inferred; no session ID in the extract |
| Fee income lost to failures | tariff fee of unrecovered operational failures | what the bank would have earned |
| Active customer (month) | ≥ 1 successful transaction | |
| Dormant | no successful transaction in the 90 days to 31 Oct 2026 (customers onboarded before 1 Aug 2026) | |

## 6. Analytical choices that change the answer
1. **Separating failure owners.** Customer-caused failures are about half of all failures. Without separating them, USSD's reliability problem is diluted and the app looks worse than it is.
2. **Value vs revenue.** Ranking by value points to Internet Banking. Ranking by fees points to the app and agents. Both are reported, always labelled.
3. **Stratifying the failure–inactivity link.** Busy customers both hit more failures and stay active. Comparing within activity bands (1–3, 4–9 and 10+ attempts a month) reverses the raw pattern.
4. **Retry window.** 30 minutes covers almost all generated retries (≤ 15 minutes) with a margin. A longer window would start to capture unrelated repeat payments.
5. **Isolation Forest settings.** Hours with fewer than 10 expected attempts are skipped, the failure rate is smoothed, and features are standardised within each channel. Without these, quiet night hours on small channels dominate the flags.

## 7. Limitations
- Synthetic data: the analysis demonstrates method, not facts about a real bank.
- No balances, so insufficient funds is a recorded reason rather than a derived one.
- No cost data: fee income is revenue, not profit.
- Retries are inferred; a real system would provide a session or idempotency key.
- The failure–inactivity and waiver effects are observational associations.
- The CBK series counts national agent cash-in/cash-out, a different measure from a bank's transactions, so it is used only as an indexed trend.
