import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "yunex_demo" / "demo_1"))

from sample_data import make_sample  # noqa: E402
from transform import build_gap, build_star, flatten, to_silver  # noqa: E402


@pytest.fixture(scope="module")
def silver():
    ecb, cbar = make_sample("2025-01-01", "2026-03-31")
    return to_silver(ecb, cbar)


@pytest.fixture(scope="module")
def star(silver):
    return build_star(silver)


def test_cbar_is_inverted_to_azn_per_unit(silver):
    usd = silver[(silver.provider == "CBAR") & (silver.base == "USD")]
    assert (usd.quote == "AZN").all()
    assert np.allclose(usd.rate, 1.70, atol=1e-3)  # the peg


def test_no_duplicates_or_bad_rates(silver):
    assert not silver.duplicated(["provider", "date", "base", "quote"]).any()
    assert (silver.rate > 0).all()


def test_foreign_keys_exist(star):
    f = star["fact_fx_metrics"]
    assert f.date_key.isin(star["dim_date"].date_key).all()
    assert f.provider_key.isin(star["dim_provider"].provider_key).all()
    assert f.base_key.isin(star["dim_currency"].currency_key).all()
    assert f.quote_key.isin(star["dim_currency"].currency_key).all()
    assert f.risk_key.isin(star["dim_risk_level"].risk_key).all()


def test_grain_is_unique(star):
    k = ["date_key", "provider_key", "base_key", "quote_key"]
    assert not star["fact_fx_rate"].duplicated(k).any()
    assert not star["fact_fx_metrics"].duplicated(k).any()


def test_pegged_pair_has_no_anomalies_and_low_risk(star):
    f = flatten(star)
    peg = f[(f.provider_code == "CBAR") & (f.pair == "USD/AZN")]
    assert peg.is_anomaly.sum() == 0
    assert set(peg.risk_level) <= {"Low", "Unknown"}


def test_lira_riskier_than_swiss_franc(star):
    f = flatten(star)
    last = f[f.provider_code == "ECB"].sort_values("date").groupby("pair").tail(1).set_index("pair")
    assert last.loc["EUR/TRY", "vol_30d_annual"] > last.loc["EUR/CHF", "vol_30d_annual"]


def test_zscore_has_no_lookahead():
    """A single spike must be flagged on its own day."""
    days = pd.bdate_range("2025-01-01", periods=80)
    rate = np.full(80, 1.0) * (1 + np.random.default_rng(1).normal(0, 0.001, 80)).cumprod()
    rate[60:] *= 1.08  # +8% jump on day 60
    raw = pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "base": "EUR", "quote": "USD", "rate": rate})
    cbar = pd.DataFrame({"date": [], "base": [], "quote": [], "rate": []})
    s = to_silver(raw, cbar)
    from transform import compute_metrics

    m = compute_metrics(s).reset_index(drop=True)
    assert m.loc[60, "is_anomaly"]
    assert m["is_anomaly"].sum() == 1


def test_gap_math():
    d = pd.Timestamp("2026-01-05")
    silver = pd.DataFrame(
        [
            ("ECB", d, "EUR", "USD", 1.10),
            ("CBAR", d, "EUR", "AZN", 1.87),  # 1.87 / 1.70 = 1.10 -> zero gap
            ("CBAR", d, "USD", "AZN", 1.70),
        ],
        columns=["provider", "date", "base", "quote", "rate"],
    )
    g = build_gap(silver)
    assert len(g) == 1
    assert abs(g.gap_pct.iloc[0]) < 1e-9
    assert not g.is_gap_alert.iloc[0]


# ------------------------------------------------------------ payment-term risk
def _series(star, provider, pair):
    f = flatten(star)
    return f[(f.provider_code == provider) & (f.pair == pair)].sort_values("date")


def test_business_days_conversion():
    from horizon import business_days

    assert business_days(7) == 5
    assert business_days(30) == 21
    assert business_days(1) == 1


def test_longer_term_means_more_downside(star):
    from horizon import horizon_risk

    d = _series(star, "ECB", "EUR/TRY")
    short, long_ = horizon_risk(d, 7), horizon_risk(d, 90)
    assert long_["scaled_pct"] > short["scaled_pct"]
    assert long_["loss_pct"] >= short["loss_pct"]


def test_scaling_rule_is_sqrt_of_time(star):
    from horizon import horizon_risk

    d = _series(star, "ECB", "EUR/TRY")
    one = abs(d["var95_1d"].iloc[-1])
    r = horizon_risk(d, 30)
    assert r["scaled_pct"] == pytest.approx(one * np.sqrt(r["business_days"]))


def test_peg_is_low_risk_for_any_term(star):
    from horizon import horizon_risk

    d = _series(star, "CBAR", "USD/AZN")
    assert all(horizon_risk(d, t)["level"] == "Low" for t in (7, 30, 180))


def test_risk_level_rises_with_term_for_volatile_pair(star):
    from horizon import horizon_risk

    d = _series(star, "ECB", "EUR/TRY")
    order = {"Low": 0, "Medium": 1, "High": 2}
    levels = [order[horizon_risk(d, t)["level"]] for t in (7, 30, 90, 180)]
    assert levels == sorted(levels) and levels[-1] == 2


def test_downside_path_starts_at_last_rate_and_falls(star):
    from horizon import downside_path

    d = _series(star, "ECB", "EUR/USD")
    p = downside_path(d, 21)
    assert len(p) == 22
    assert p["rate"].iloc[0] == pytest.approx(d["rate"].iloc[-1])
    assert p["rate"].is_monotonic_decreasing


def test_short_history_skips_historical_estimate(star):
    from horizon import horizon_risk

    d = _series(star, "ECB", "EUR/USD").tail(40)
    r = horizon_risk(d, 30)
    assert np.isnan(r["hist_pct"])


# ------------------------------------------------------------ optional date range
def _toy(n=200, seed=3):
    """100 calm days followed by 100 jumpy days."""
    rng = np.random.default_rng(seed)
    r = np.r_[rng.normal(0, 0.001, n // 2), rng.normal(0, 0.01, n // 2)]
    d = pd.DataFrame({"date": pd.bdate_range("2025-01-01", periods=n), "rate": 100 * np.cumprod(1 + r)})
    d["daily_return"] = d["rate"].pct_change()
    d["var95_1d"] = d["daily_return"].rolling(90, min_periods=60).quantile(0.05)
    return d


def test_range_uses_only_the_selected_dates():
    from horizon import horizon_risk

    d = _toy()
    calm = horizon_risk(d, 30, d.date.iloc[0], d.date.iloc[99])
    wild = horizon_risk(d, 30, d.date.iloc[100], d.date.iloc[-1])
    assert calm["scaled_pct"] < wild["scaled_pct"]
    assert calm["level"] == "Low" and wild["level"] == "High"


def test_range_ignores_rows_outside_it():
    from horizon import horizon_risk

    d = _toy()
    a = horizon_risk(d, 30, d.date.iloc[100], d.date.iloc[-1])
    b = horizon_risk(d.iloc[100:], 30, d.date.iloc[100], d.date.iloc[-1])
    assert a["scaled_pct"] == pytest.approx(b["scaled_pct"])
    assert a["hist_pct"] == pytest.approx(b["hist_pct"])


def test_too_short_range_is_unknown():
    from horizon import horizon_risk

    d = _toy()
    r = horizon_risk(d, 30, d.date.iloc[0], d.date.iloc[20])
    assert r["level"] == "Unknown"


def test_no_range_is_unchanged_by_the_new_parameters():
    from horizon import horizon_risk

    d = _toy()
    assert horizon_risk(d, 30)["scaled_pct"] == pytest.approx(abs(d["var95_1d"].iloc[-1]) * np.sqrt(21))


# ------------------------------------------------------------ the numbers behind the downsides
def test_one_day_moves_reproduce_the_1_day_downside(star):
    from horizon import horizon_risk, one_day_moves

    d = _series(star, "ECB", "EUR/TRY")
    m = one_day_moves(d)
    assert len(m) == 90
    assert -m["move"].quantile(0.05) == pytest.approx(horizon_risk(d, 30)["var1_pct"])


def test_past_periods_reproduce_the_historical_downside(star):
    from horizon import horizon_risk, past_periods

    d = _series(star, "ECB", "EUR/TRY")
    r = horizon_risk(d, 30)
    p = past_periods(d, 30)
    assert len(p) == r["n_windows"]
    assert -p["move"].quantile(0.05) == pytest.approx(r["hist_pct"])
    assert ((p["end"] - p["start"]).dt.days > 0).all()


def test_tables_follow_the_date_range(star):
    from horizon import horizon_risk, one_day_moves, past_periods

    d = _series(star, "ECB", "EUR/TRY")
    start, end = d.date.iloc[100], d.date.iloc[-1]
    r = horizon_risk(d, 30, start, end)
    assert -one_day_moves(d, start, end)["move"].quantile(0.05) == pytest.approx(r["var1_pct"])
    assert -past_periods(d, 30, start, end)["move"].quantile(0.05) == pytest.approx(r["hist_pct"])


def test_overlapping_bad_stretches_count_as_one_episode():
    from horizon import bad_episodes

    p = pd.DataFrame(
        {
            "start": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-08", "2026-06-01", "2026-06-02"]),
            "end": pd.to_datetime(["2026-02-05", "2026-02-06", "2026-02-08", "2026-07-01", "2026-07-02"]),
            "move": [-0.04, -0.05, -0.03, -0.06, -0.02],
        }
    )
    e = bad_episodes(p, loss_pct=0.025, calendar_days=30)  # everything below -2.5% is "worse than the cut"
    assert list(e["stretches"]) == [3, 1]  # the -2% stretch is not beyond the cut
    assert list(e["worst"]) == [-0.05, -0.06]
    assert e["start"].iloc[0] == pd.Timestamp("2026-01-05") and e["end"].iloc[0] == pd.Timestamp("2026-02-08")


def test_cut_rank_sits_between_neighbours():
    from horizon import cut_rank

    assert cut_rank(90) == pytest.approx(5.45)  # between the 5th and 6th worst of 90
    assert cut_rank(61) == pytest.approx(4.0)  # exactly the 4th worst


# ------------------------------------------------------------ totals over a period
def _prices(values):
    return pd.DataFrame({"date": pd.bdate_range("2026-01-05", periods=len(values)), "rate": values})


def test_range_outcome_totals():
    from horizon import range_outcome

    o = range_outcome(_prices([100, 110, 90, 95, 120]), calendar_days=3)  # 3 calendar days = 2 business days
    assert o["change_pct"] == pytest.approx(0.20)
    assert o["worst_drop_pct"] == pytest.approx(1 - 90 / 110)
    assert (o["peak_rate"], o["trough_rate"]) == (110, 90)
    assert o["term_business_days"] == 2
    assert o["worst_term_pct"] == pytest.approx(1 - 95 / 110)  # 110 -> 95 over 2 days


def test_range_outcome_when_rate_only_rises():
    from horizon import range_outcome

    o = range_outcome(_prices([100, 101, 103, 106]), calendar_days=3)
    assert o["worst_drop_pct"] == 0.0 and o["peak_date"] is None
    assert o["worst_term_pct"] == 0.0


def test_range_outcome_short_series_has_no_term_figure():
    from horizon import range_outcome

    o = range_outcome(_prices([100, 99]), calendar_days=30)
    assert np.isnan(o["worst_term_pct"])
    assert o["worst_drop_pct"] == pytest.approx(0.01)
