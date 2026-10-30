"""Step 2 - TRANSFORM: RAW csv -> SILVER (clean, unified) -> GOLD (star schema + metrics).

Usage:
    python src/transform.py            # reads/writes MinIO
    python src/transform.py --local    # reads/writes ./data
"""

import argparse

import config as C
import numpy as np
import pandas as pd
from storage import Store, default_local

PAIR_KEYS = ["provider", "base", "quote"]


# ---------------------------------------------------------------- SILVER
def to_silver(ecb_raw: pd.DataFrame, cbar_raw: pd.DataFrame) -> pd.DataFrame:
    """Unify both providers into one table with one convention: 1 <base> = <rate> <quote>.

    ECB  : already 1 EUR = x <quote>.
    CBAR : requested with base=AZN (1 AZN = x <currency>); we invert it so the
           row reads 1 <currency> = x AZN, which is how people think about it.
    """
    ecb = ecb_raw.copy()
    ecb["provider"] = "ECB"

    cbar = cbar_raw.copy()
    cbar["provider"] = "CBAR"
    cbar["base"], cbar["quote"] = cbar["quote"], cbar["base"]  # swap -> <currency>/AZN
    cbar["rate"] = 1.0 / cbar["rate"]

    df = pd.concat([ecb, cbar], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("base", "quote"):
        df[col] = df[col].str.upper().str.strip()
    df["rate"] = pd.to_numeric(df["rate"], errors="coerce")

    df = df.dropna(subset=["rate"])
    df = df[df["rate"] > 0]
    df = df.drop_duplicates(subset=["provider", "date", "base", "quote"])
    return df.sort_values(PAIR_KEYS + ["date"]).reset_index(drop=True)[["provider", "date", "base", "quote", "rate"]]


# ---------------------------------------------------------------- METRICS
def compute_metrics(silver: pd.DataFrame) -> pd.DataFrame:
    """Risk metrics per (provider, base, quote) series."""
    s = silver.sort_values(PAIR_KEYS + ["date"]).copy()
    w = C.WINDOW

    rate = s.groupby(PAIR_KEYS, sort=False)["rate"]
    s["daily_return"] = rate.pct_change(fill_method=None)
    s["log_return"] = np.log(s["rate"] / rate.shift(1))
    s["ret_7d"] = rate.pct_change(7, fill_method=None)
    s["ret_30d"] = rate.pct_change(30, fill_method=None)

    # range of the rate over the window, relative to its average
    rmax = rate.transform(lambda x: x.rolling(w, min_periods=w).max())
    rmin = rate.transform(lambda x: x.rolling(w, min_periods=w).min())
    rmean = rate.transform(lambda x: x.rolling(w, min_periods=w).mean())
    s["range_30d_pct"] = (rmax - rmin) / rmean

    ret = s.groupby(PAIR_KEYS, sort=False)["daily_return"]
    s["vol_30d"] = ret.transform(lambda x: x.rolling(w, min_periods=w).std())
    s["vol_30d_annual"] = s["vol_30d"] * np.sqrt(C.TRADING_DAYS)

    # z-score of today's move vs the PREVIOUS window (no look-ahead)
    mean_prev = ret.transform(lambda x: x.rolling(w, min_periods=w).mean().shift(1))
    std_prev = ret.transform(lambda x: x.rolling(w, min_periods=w).std().shift(1))
    z = (s["daily_return"] - mean_prev) / std_prev.where(std_prev >= 1e-9)
    s["zscore"] = z.mask(std_prev < 1e-9, 0.0)  # flat series (peg) -> z = 0
    s["is_anomaly"] = s["zscore"].abs() > C.Z_THRESHOLD

    # historical 1-day Value-at-Risk at 95%: "on a bad day we lose at least this much"
    s["var95_1d"] = ret.transform(lambda x: x.rolling(C.VAR_WINDOW, min_periods=60).quantile(0.05))

    # risk level from volatility terciles across ALL series
    v = s["vol_30d_annual"].dropna()
    q1, q2 = v.quantile([1 / 3, 2 / 3]) if len(v) else (np.nan, np.nan)
    s["risk_level"] = np.select(
        [s["vol_30d_annual"].isna(), s["vol_30d_annual"] <= q1, s["vol_30d_annual"] <= q2],
        ["Unknown", "Low", "Medium"],
        default="High",
    )
    return s


# ---------------------------------------------------------------- GAP FACT
def build_gap(silver: pd.DataFrame) -> pd.DataFrame:
    """Reconcile the two official sources.

    CBAR gives AZN per EUR and AZN per <Q>. Dividing them gives the <Q> per EUR rate
    that CBAR implies. We compare it with the rate ECB publishes for the same day.
    Small gaps are normal (the banks fix rates at different times of day);
    large gaps point to a data problem or a market jump between fixings.
    """
    ecb = silver[silver.provider == "ECB"][["date", "quote", "rate"]].rename(columns={"rate": "ecb_rate"})
    cbar = silver[silver.provider == "CBAR"]
    eur = cbar[cbar.base == "EUR"][["date", "rate"]].rename(columns={"rate": "azn_per_eur"})
    oth = cbar[cbar.base != "EUR"][["date", "base", "rate"]].rename(columns={"base": "quote", "rate": "azn_per_quote"})
    m = oth.merge(eur, on="date").merge(ecb, on=["date", "quote"])
    m["cbar_implied_rate"] = m["azn_per_eur"] / m["azn_per_quote"]
    m["gap_pct"] = (m["cbar_implied_rate"] / m["ecb_rate"] - 1) * 100
    m["is_gap_alert"] = m["gap_pct"].abs() > C.GAP_ALERT_PCT
    return m[["date", "quote", "ecb_rate", "cbar_implied_rate", "gap_pct", "is_gap_alert"]]


# ---------------------------------------------------------------- STAR
def build_star(silver: pd.DataFrame) -> dict[str, pd.DataFrame]:
    m = compute_metrics(silver)
    gap = build_gap(silver)

    # --- dimensions
    dates = pd.DatetimeIndex(sorted(m["date"].unique()))
    dim_date = pd.DataFrame(
        {
            "date_key": dates.strftime("%Y%m%d").astype(int),
            "date": dates,
            "year": dates.year,
            "quarter": dates.quarter,
            "month": dates.month,
            "month_name": dates.strftime("%b"),
            "week": dates.isocalendar().week.to_numpy(),
            "weekday": dates.strftime("%a"),
            "is_month_end": dates.is_month_end,
        }
    )

    codes = sorted(set(m["base"]) | set(m["quote"]))
    missing = [c for c in codes if c not in C.CURRENCY_INFO]
    if missing:
        raise ValueError(f"Add {missing} to CURRENCY_INFO in config.py")
    dim_currency = pd.DataFrame(
        [(i + 1, c, *C.CURRENCY_INFO[c]) for i, c in enumerate(codes)],
        columns=["currency_key", "currency_code", "name", "region", "fx_regime", "category"],
    )

    dim_provider = pd.DataFrame(
        [(i + 1, k, *v) for i, (k, v) in enumerate(C.PROVIDERS.items())],
        columns=["provider_key", "provider_code", "name", "coverage", "usage_note"],
    )

    dim_risk = pd.DataFrame(
        [
            (0, "Unknown", "Not enough history yet (first 30 days)"),
            (1, "Low", "Calm: bottom third of volatility"),
            (2, "Medium", "Normal: middle third of volatility"),
            (3, "High", "Unstable: top third of volatility"),
        ],
        columns=["risk_key", "risk_level", "description"],
    )

    # --- key lookups
    ckey = dict(zip(dim_currency.currency_code, dim_currency.currency_key, strict=True))
    pkey = dict(zip(dim_provider.provider_code, dim_provider.provider_key, strict=True))
    rkey = dict(zip(dim_risk.risk_level, dim_risk.risk_key, strict=True))

    keys = pd.DataFrame(
        {
            "date_key": m["date"].dt.strftime("%Y%m%d").astype(int),
            "provider_key": m["provider"].map(pkey),
            "base_key": m["base"].map(ckey),
            "quote_key": m["quote"].map(ckey),
        },
        index=m.index,
    )

    # --- facts
    fact_fx_rate = pd.concat([keys, m[["rate"]]], axis=1)

    metric_cols = [
        "daily_return",
        "log_return",
        "ret_7d",
        "ret_30d",
        "range_30d_pct",
        "vol_30d",
        "vol_30d_annual",
        "zscore",
        "is_anomaly",
        "var95_1d",
    ]
    fact_fx_metrics = pd.concat([keys, m["risk_level"].map(rkey).rename("risk_key"), m[metric_cols]], axis=1)

    fact_rate_gap = pd.DataFrame(
        {
            "date_key": gap["date"].dt.strftime("%Y%m%d").astype(int),
            "currency_key": gap["quote"].map(ckey),
            **{c: gap[c] for c in ["ecb_rate", "cbar_implied_rate", "gap_pct", "is_gap_alert"]},
        }
    )

    return {
        "dim_date": dim_date,
        "dim_currency": dim_currency,
        "dim_provider": dim_provider,
        "dim_risk_level": dim_risk,
        "fact_fx_rate": fact_fx_rate.reset_index(drop=True),
        "fact_fx_metrics": fact_fx_metrics.reset_index(drop=True),
        "fact_rate_gap": fact_rate_gap.reset_index(drop=True),
    }


def flatten(star: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Join the star back into one wide table (handy for charts and pandas demos)."""
    cur = star["dim_currency"]
    f = star["fact_fx_metrics"].merge(star["fact_fx_rate"])
    f = f.merge(star["dim_date"][["date_key", "date"]], on="date_key")
    f = f.merge(star["dim_provider"][["provider_key", "provider_code"]], on="provider_key")
    f = f.merge(
        cur[["currency_key", "currency_code"]].rename(columns={"currency_key": "base_key", "currency_code": "base"}),
        on="base_key",
    )
    f = f.merge(
        cur[["currency_key", "currency_code", "name", "fx_regime"]].rename(
            columns={"currency_key": "quote_key", "currency_code": "quote", "name": "quote_name"}
        ),
        on="quote_key",
    )
    f = f.merge(star["dim_risk_level"][["risk_key", "risk_level"]], on="risk_key")
    f["pair"] = f["base"] + "/" + f["quote"]
    return f.sort_values(["provider_code", "pair", "date"]).reset_index(drop=True)


# ---------------------------------------------------------------- MAIN
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    args = ap.parse_args()
    store = Store(local=args.local or default_local())

    stamp = f"{C.START}_{C.END}"
    ecb = store.get_csv("raw", f"ecb/rates_{stamp}.csv")
    cbar = store.get_csv("raw", f"cbar/rates_{stamp}.csv")

    silver = to_silver(ecb, cbar)
    store.put_parquet("silver", "fx_rates.parquet", silver)
    print(f"silver/fx_rates.parquet: {len(silver)} rows")

    star = build_star(silver)
    for name, df in star.items():
        store.put_parquet("gold", f"{name}.parquet", df)
        print(f"gold/{name}.parquet: {len(df)} rows")


if __name__ == "__main__":
    main()
