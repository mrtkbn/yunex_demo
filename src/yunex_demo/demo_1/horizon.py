"""Risk for a payment term: "how much could the rate fall before I am paid?"

Used by the app; nothing here is stored in the gold layer. The input is one currency pair
(one row per date, sorted) with the columns `rate`, `daily_return` and `var95_1d` from fact_fx_metrics.

Two estimates of the downside over h business days (a fall in the rate, i.e. the risk for
someone who holds the base currency):

  scaled      1-day downside x sqrt(h)         assumes daily moves are independent
  historical  5th percentile of every past h-day change in the rate

The level uses the higher of the two, so it errs on the cautious side.

Optional date range: with `start` and/or `end`, both estimates use only the rates inside that
range (the 1-day downside is then the 5th percentile of the daily moves inside the range).
Without a range, the full history is used and the 1-day downside is the stored `var95_1d`.
"""

import config as C
import numpy as np
import pandas as pd


def business_days(calendar_days: int) -> int:
    """Rates exist only on working days: about 5 observations per 7 calendar days."""
    return max(1, round(calendar_days * 5 / 7))


def level_for(loss_pct: float) -> str:
    if pd.isna(loss_pct):
        return "Unknown"
    if loss_pct < C.HORIZON_LOW:
        return "Low"
    if loss_pct < C.HORIZON_HIGH:
        return "Medium"
    return "High"


def in_range(d: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """Rows of one series between start and end (both inclusive, both optional)."""
    s = d
    if start is not None:
        s = s[s["date"] >= pd.Timestamp(start)]
    if end is not None:
        s = s[s["date"] <= pd.Timestamp(end)]
    return s


def horizon_risk(d: pd.DataFrame, calendar_days: int, start=None, end=None) -> dict:
    h = business_days(calendar_days)
    use_range = start is not None or end is not None

    if use_range:
        s = in_range(d, start, end)
        moves = s["rate"].pct_change(fill_method=None).dropna()
        var1 = max(0.0, -moves.quantile(0.05)) if len(moves) >= C.HORIZON_MIN_OBS else np.nan
        rates, n_obs = s["rate"], len(moves)
    else:
        v = d["var95_1d"].iloc[-1]
        var1 = abs(v) if pd.notna(v) else np.nan
        rates, n_obs = d["rate"], int(d["daily_return"].notna().sum())

    scaled = var1 * np.sqrt(h) if pd.notna(var1) else np.nan

    past = rates.pct_change(h, fill_method=None).dropna()
    hist = max(0.0, -past.quantile(0.05)) if len(past) >= C.HORIZON_MIN_WINDOWS else np.nan

    known = [x for x in (scaled, hist) if pd.notna(x)]
    worst = max(known) if known else np.nan

    return {
        "calendar_days": calendar_days,
        "business_days": h,
        "var1_pct": var1,
        "scaled_pct": scaled,
        "hist_pct": hist,
        "n_obs": n_obs,
        "n_windows": len(past),
        "loss_pct": worst,
        "level": level_for(worst),
    }


def downside_path(d: pd.DataFrame, business_days_ahead: int, var1=None) -> pd.DataFrame:
    """Rate path for the 1-in-20 downside, one point per business day from the last date.

    `var1` is the 1-day downside as a positive fraction; by default the stored var95_1d is used.
    """
    if var1 is None:
        v = d["var95_1d"].iloc[-1]
        var1 = abs(v) if pd.notna(v) else np.nan
    last_date, last_rate = d["date"].iloc[-1], d["rate"].iloc[-1]
    dates = pd.bdate_range(last_date, periods=business_days_ahead + 1)
    k = np.arange(len(dates))
    if pd.isna(var1):
        return pd.DataFrame({"date": dates, "rate": np.nan})
    return pd.DataFrame({"date": dates, "rate": last_rate * (1 - var1 * np.sqrt(k))})


def range_outcome(s: pd.DataFrame, calendar_days: int) -> dict:
    """What actually happened to the rate over the rows in `s` (one series, any date range).

    Everything is a TOTAL over the period, not a daily figure, and is the view of someone
    who holds the base currency (a fall in the rate is a loss):

      change_pct        rate on the last day vs the first day
      worst_drop_pct    biggest fall from a high point to a later low point inside the period
      worst_term_pct    biggest fall over any stretch as long as the payment term
    """
    s = s.sort_values("date").reset_index(drop=True)
    rate = s["rate"].to_numpy(dtype=float)
    out = {
        "n_obs": len(rate),
        "first_date": s["date"].iloc[0],
        "last_date": s["date"].iloc[-1],
        "first_rate": rate[0],
        "last_rate": rate[-1],
        "change_pct": rate[-1] / rate[0] - 1,
        "worst_drop_pct": 0.0,
        "peak_date": None,
        "trough_date": None,
        "peak_rate": np.nan,
        "trough_rate": np.nan,
        "worst_term_pct": np.nan,
        "term_start": None,
        "term_end": None,
        "term_start_rate": np.nan,
        "term_business_days": business_days(calendar_days),
    }

    dd = rate / np.maximum.accumulate(rate) - 1
    t = int(dd.argmin())
    if dd[t] < 0:
        p = int(rate[: t + 1].argmax())
        out.update(
            worst_drop_pct=-float(dd[t]),
            peak_date=s["date"].iloc[p],
            trough_date=s["date"].iloc[t],
            peak_rate=rate[p],
            trough_rate=rate[t],
        )

    h = out["term_business_days"]
    if len(rate) > h:
        w = rate[h:] / rate[:-h] - 1
        i = int(w.argmin())
        out.update(
            worst_term_pct=max(0.0, -float(w[i])),
            term_start=s["date"].iloc[i],
            term_end=s["date"].iloc[i + h],
            term_start_rate=rate[i],
        )
    return out
