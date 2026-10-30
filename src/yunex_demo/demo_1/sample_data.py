"""Synthetic rate generator: offline fallback for the demo and fixture for tests.

Output matches the Frankfurter v2 CSV shape: date, base, quote, rate.
"""

import numpy as np
import pandas as pd

# (start level, daily volatility) per EUR quote
EUR_QUOTES = {
    "USD": (1.08, 0.004),
    "PLN": (4.30, 0.003),
    "GBP": (0.85, 0.004),
    "CHF": (0.95, 0.003),
    "TRY": (35.0, 0.009),
    "CZK": (25.0, 0.003),
    "JPY": (160.0, 0.005),
}
PEG_USD_AZN = 1.70


def make_sample(start="2025-01-01", end="2026-10-30", seed=7):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, end)
    ecb_rows, cbar_rows = [], []

    eur = {}
    for q, (level, vol) in EUR_QUOTES.items():
        ret = rng.normal(0.0, vol, len(days))
        if q == "TRY":
            ret += 0.0008  # lira drifts weaker vs EUR
        # a few injected shocks so the demo always has anomalies
        for i in rng.choice(len(days), 3, replace=False):
            ret[i] += rng.choice([-1, 1]) * vol * 6
        eur[q] = level * np.exp(np.cumsum(ret))

    for q, series in eur.items():
        for d, r in zip(days, series, strict=True):
            ecb_rows.append((d.strftime("%Y-%m-%d"), "EUR", q, round(float(r), 5)))

    # CBAR is quoted with base=AZN: 1 AZN = x <quote>.
    # AZN is pegged to USD, so USD/AZN is flat and the others follow the EUR crosses.
    usd = eur["USD"]
    for q in ["USD", "EUR", "TRY", "PLN", "GBP", "CHF", "CZK", "JPY"]:
        for i, d in enumerate(days):
            if q == "USD":
                per_azn = 1 / PEG_USD_AZN
            elif q == "EUR":
                per_azn = 1 / (PEG_USD_AZN * usd[i])  # EUR per AZN
            else:
                # quote per EUR / USD per EUR  -> quote per USD ; divide by 1.70 for per AZN
                per_azn = (eur[q][i] / usd[i]) / PEG_USD_AZN
            cbar_rows.append((d.strftime("%Y-%m-%d"), "AZN", q, round(float(per_azn), 6)))

    cols = ["date", "base", "quote", "rate"]
    cbar = pd.DataFrame(cbar_rows, columns=cols)

    # CBAR and ECB fix at different times of day -> small gaps; add a few real mismatches
    noise = np.exp(rng.normal(0.0, 0.002, len(cbar)))
    noise[rng.choice(len(cbar), 12, replace=False)] *= 1.03
    non_peg = cbar["quote"] != "USD"
    cbar.loc[non_peg, "rate"] = (cbar.loc[non_peg, "rate"] * noise[non_peg.to_numpy()]).round(6)
    return pd.DataFrame(ecb_rows, columns=cols), cbar


if __name__ == "__main__":
    e, c = make_sample()
    print(e.head(), "\n", c.head(), "\n", len(e), len(c))
