# Real market context (not synthetic)

`cbk_mobile_payments.csv` holds the **Central Bank of Kenya** national mobile-payments statistics, by month: active agents, registered mobile-money accounts, agent cash-in/cash-out transaction volume and value.

- Source: https://www.centralbank.go.ke/national-payments-system/mobile-payments/
- Retrieved: 24 November 2026 (Oct 2024 – Sep 2026, as published on that date).

These are real national aggregates. They are loaded into `bank.context_cbk_mobile_payments` and used only as a market trend line next to the fictional bank's own (synthetic) volumes. They are never mixed into the synthetic data.
