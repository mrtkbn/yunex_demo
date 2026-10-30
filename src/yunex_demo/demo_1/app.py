"""Step 3 - SERVE: Streamlit demo on top of the GOLD star schema.

Run:  streamlit run src/app.py
Set FX_LOCAL=1 to read ./data instead of MinIO.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from horizon import downside_path, horizon_risk, in_range, range_outcome
from storage import Store, default_local
from transform import flatten

TABLES = [
    "dim_date",
    "dim_currency",
    "dim_provider",
    "dim_risk_level",
    "fact_fx_rate",
    "fact_fx_metrics",
    "fact_rate_gap",
]
RISK_COLOR = {"Low": "#2a9d8f", "Medium": "#e9a23b", "High": "#d1495b", "Unknown": "#8d99ae"}
LOOKBACK = {"3 months": 90, "6 months": 180, "12 months": 365, "All": None}


def level_style(v):
    return f"color: {RISK_COLOR.get(v, 'inherit')}; font-weight: 600"


st.set_page_config(page_title="FX Risk & Anomaly Monitor", layout="wide")


@st.cache_data(show_spinner="Loading gold tables...")
def load():
    store = Store(local=default_local())
    star = {t: store.get_parquet("gold", f"{t}.parquet") for t in TABLES}
    return star, flatten(star)


star, flat = load()

st.title("FX Risk & Anomaly Monitor")
st.caption(
    "Official ECB and Central Bank of Azerbaijan rates (via Frankfurter) -> MinIO -> pandas star schema. "
    "Flags unusual currency moves and shows how risky it is to wait for a payment."
)

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    provider = st.radio("Source", ["ECB", "CBAR"], help="ECB: 1 EUR = x currency. CBAR: 1 currency = x AZN.")
    pairs = sorted(flat.loc[flat.provider_code == provider, "pair"].unique())
    pair = st.selectbox("Currency pair", pairs, index=pairs.index("EUR/TRY") if "EUR/TRY" in pairs else 0)
    amount = st.number_input("Invoice size (in base currency)", 1000, 10_000_000, 10_000, step=1000)

    d = flat[(flat.provider_code == provider) & (flat.pair == pair)].sort_values("date")
    last_date = d["date"].max().date()
    pay_date = st.date_input(
        "Expected payment date",
        value=last_date + dt.timedelta(days=30),
        min_value=last_date + dt.timedelta(days=1),
        max_value=last_date + dt.timedelta(days=365),
        help="When you expect to be paid (or to pay). The longer the wait, the more the rate can move.",
    )
    st.caption("Don't know the date yet? The table below shows the risk for every term.")

    use_range = st.checkbox(
        "Calculate the risk from a date range",
        help="Optional. Off: the full history is used. On: only the rates between the two dates are used.",
    )
    range_start = range_end = None
    if use_range:
        first_date = d["date"].min().date()
        range_start = st.date_input(
            "From", value=first_date, min_value=first_date, max_value=last_date, key="range_from"
        )
        range_end = st.date_input("To", value=last_date, min_value=first_date, max_value=last_date, key="range_to")
        if range_start > range_end:
            st.error("'From' must be before 'To'. Using the full history instead.")
            use_range, range_start, range_end = False, None, None

    history = st.select_slider(
        "History shown on the chart",
        options=list(LOOKBACK),
        value="12 months",
        disabled=use_range,
        help="Disabled while a date range is on: the chart then shows the range.",
    )

last = d.iloc[-1]
days = (pay_date - last_date).days
risk = horizon_risk(d, days, range_start, range_end)
sel = in_range(d, range_start, range_end) if use_range else d
sel_vol = sel["rate"].pct_change(fill_method=None).std() * C.TRADING_DAYS**0.5
period = range_outcome(sel, days) if len(sel) >= 2 else None  # what happened, in total, over the period

# ---------------------------------------------------------------- KPIs
recent = d[d["date"] >= d["date"].max() - pd.Timedelta(days=90)]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Latest rate", f"{last.rate:,.4f}", f"{last.daily_return:+.2%}" if pd.notna(last.daily_return) else None)
if use_range:
    c2.metric(
        "Volatility in range",
        f"{sel_vol:.1%}" if pd.notna(sel_vol) else "n/a",
        help="How much the rate moved day to day between your two dates, scaled to a year.",
    )
else:
    c2.metric(
        "Recent behaviour",
        last.risk_level,
        help="Low / Medium / High from the last 30 days of volatility, compared with all currencies. Not tied to your payment date.",
    )
c3.metric(
    f"Risk for {days} days",
    risk["level"],
    help=f"Low below {C.HORIZON_LOW:.0%} of the invoice, High at {C.HORIZON_HIGH:.0%} or more, using a 1-in-20 downside "
    "until your payment date.",
)
if use_range:
    c4.metric("Anomalies in range", int(sel.is_anomaly.sum()))
else:
    c4.metric("Anomalies, last 90 days", int(recent.is_anomaly.sum()))

if use_range:
    st.caption(f"Risk calculated from {range_start:%d %b %Y} to {range_end:%d %b %Y} ({risk['n_obs']} daily moves).")
if risk["level"] == "Unknown":
    st.warning(
        f"Not enough data to estimate the risk. A date range needs at least {C.HORIZON_MIN_OBS} daily moves "
        "(about 6 weeks): choose a wider range."
        if use_range
        else "Not enough history to estimate the risk."
    )

value_quote = amount * last.rate  # what the invoice is worth in the quote currency
if pd.notna(risk["loss_pct"]) and risk["loss_pct"] < 1e-9:
    st.info(
        f"**What it means:** this rate has not moved in the history we hold. It is pegged or fixed by policy, "
        f"so there is no measurable rate risk for {days} days. Policy can change, so this is not a guarantee."
    )
elif pd.notna(risk["loss_pct"]):
    loss_quote = value_quote * risk["loss_pct"]  # 1-in-20 fall in that value by the payment date
    st.info(
        f"**What it means:** {amount:,.0f} {last.base} is worth about {value_quote:,.0f} {last.quote} today. "
        f"If you are paid on {pay_date:%d %b %Y} ({days} days away), in about 1 case out of 20 it will be worth "
        f"**at least {loss_quote:,.0f} {last.quote} less in total** ({risk['loss_pct']:.1%}, not per day). "
        f"This is the risk for someone holding {last.base}. It is an estimate, not a forecast."
    )
    hist_txt = (
        f"{risk['hist_pct']:.1%} (from {risk['n_windows']} past periods)"
        if pd.notna(risk["hist_pct"])
        else "not available (too little history)"
    )
    one_day = "the 1-day risk in your range" if use_range else "today's 1-day risk"
    st.caption(
        f"How it is estimated, for {risk['business_days']} business days: scaling {one_day} gives "
        f"{risk['scaled_pct']:.1%}; looking at every past period of the same length gives {hist_txt}. "
        "The higher of the two is used."
    )

# ---------------------------------------------------------------- main chart
fig = go.Figure()
fig.add_scatter(x=d.date, y=d.rate, mode="lines", name="Rate", line=dict(color="#264653"))
a = d[d.is_anomaly]
fig.add_scatter(
    x=a.date,
    y=a.rate,
    mode="markers",
    name="Anomaly (|z| > 3)",
    marker=dict(color="#d1495b", size=10, symbol="diamond"),
    hovertemplate="%{x|%d %b %Y}<br>rate %{y:.4f}<br>z=%{customdata:.1f}<extra></extra>",
    customdata=a.zscore,
)
path = downside_path(d, risk["business_days"], risk["var1_pct"])
if path["rate"].notna().any():
    fig.add_scatter(
        x=path.date,
        y=path.rate,
        mode="lines",
        name="1-in-20 downside until payment",
        line=dict(color="#e9a23b", dash="dash", width=2),
        hovertemplate="%{x|%d %b %Y}<br>downside rate %{y:.4f}<extra></extra>",
    )
if period and period["peak_date"] is not None:
    fig.add_scatter(
        x=[period["peak_date"], period["trough_date"]],
        y=[period["peak_rate"], period["trough_rate"]],
        mode="lines+markers",
        name="Biggest fall in the period",
        line=dict(color="#d1495b", width=3),
        marker=dict(size=7),
        hovertemplate="%{x|%d %b %Y}<br>rate %{y:.4f}<extra></extra>",
    )
pay_ts = pd.Timestamp(pay_date)
fig.add_shape(type="line", x0=pay_ts, x1=pay_ts, y0=0, y1=1, yref="paper", line=dict(color="#8d99ae", dash="dot"))
fig.add_scatter(
    x=[pay_ts],
    y=[d.rate.iloc[-1]],
    mode="markers+text",
    text=["payment"],
    textposition="top center",
    marker=dict(color="#8d99ae", size=8),
    showlegend=False,
    hoverinfo="skip",
)
fig.update_layout(title=f"{pair}  ({provider})", height=400, margin=dict(t=50, b=20), legend=dict(orientation="h"))
if use_range:
    x0, x1 = pd.Timestamp(range_start), pd.Timestamp(range_end)
    fig.add_shape(
        type="rect",
        x0=x0,
        x1=x1,
        y0=0,
        y1=1,
        yref="paper",
        layer="below",
        fillcolor="rgba(141,153,174,0.15)",
        line_width=0,
    )
    chart_range = [x0 - pd.Timedelta(days=5), pay_ts + pd.Timedelta(days=7)]
elif LOOKBACK[history]:
    chart_range = [pd.Timestamp(last_date) - pd.Timedelta(days=LOOKBACK[history]), pay_ts + pd.Timedelta(days=7)]
else:
    chart_range = None
if chart_range:
    fig.update_xaxes(range=chart_range)
st.plotly_chart(fig, width="stretch")
st.caption(
    "Red diamonds = unusually large moves in the past. The dashed line shows how far the rate could fall by the "
    "payment date in 1 case out of 20." + (" The grey band is your date range." if use_range else "")
)

# ---------------------------------------------------------------- totals over the period
if period:
    st.subheader(
        f"What happened between {period['first_date']:%d %b %Y} and {period['last_date']:%d %b %Y}"
        if use_range
        else "What happened over the full history"
    )
    q = last.quote
    p1, p2, p3 = st.columns(3)

    p1.metric(
        f"Total change for {amount:,.0f} {last.base}",
        f"{amount * (period['last_rate'] - period['first_rate']):+,.0f} {q}",
        f"{period['change_pct']:+.1%}",
        help="Value of the invoice on the last day of the period minus its value on the first day.",
    )

    has_drop = period["peak_date"] is not None
    p2.metric(
        "Biggest fall from a high point",
        f"{amount * (period['peak_rate'] - period['trough_rate']) if has_drop else 0:,.0f} {q}",
        f"-{period['worst_drop_pct']:.1%}" if has_drop else None,
        help="The largest total loss between a high point and a later low point inside the period. Marked in red on the chart.",
    )

    has_term = pd.notna(period["worst_term_pct"]) and period["worst_term_pct"] > 0
    term_money = amount * period["term_start_rate"] * period["worst_term_pct"] if has_term else 0
    p3.metric(
        f"Worst {days}-day stretch"
        if pd.notna(period["worst_term_pct"])
        else f"Worst {days}-day stretch (period too short)",
        f"{term_money:,.0f} {q}" if pd.notna(period["worst_term_pct"]) else "n/a",
        f"-{period['worst_term_pct']:.1%}" if has_term else None,
        help="The largest total loss over any stretch as long as your payment term, inside the period.",
    )

    notes = [
        f"Totals for an invoice of {amount:,.0f} {last.base}, for someone holding {last.base} (a fall in the rate is a loss)."
    ]
    if has_drop:
        notes.append(
            f"Biggest fall: {period['peak_date']:%d %b %Y} ({period['peak_rate']:.4f}) to "
            f"{period['trough_date']:%d %b %Y} ({period['trough_rate']:.4f})."
        )
    if has_term:
        notes.append(f"Worst {days}-day stretch: {period['term_start']:%d %b %Y} to {period['term_end']:%d %b %Y}.")
    notes.append("These are past outcomes, not forecasts.")
    st.caption(" ".join(notes))

# ---------------------------------------------------------------- risk by term
st.subheader("Risk by payment term")
terms = sorted(set(C.HORIZON_TERMS + [days]))
rows = []
for t in terms:
    r = horizon_risk(d, t, range_start, range_end)
    rows.append(
        {
            "Payment in": f"{t} days" + ("  (your date)" if t == days else ""),
            "Business days": r["business_days"],
            "Downside (scaling)": r["scaled_pct"],
            "Downside (past periods)": r["hist_pct"],
            "Used": r["loss_pct"],
            "Risk": r["level"],
            f"Total at risk ({last.quote})": amount * last.rate * r["loss_pct"] if pd.notna(r["loss_pct"]) else None,
        }
    )
terms_df = pd.DataFrame(rows)
st.dataframe(
    terms_df.style.format(
        {
            "Downside (scaling)": "{:.1%}",
            "Downside (past periods)": "{:.1%}",
            "Used": "{:.1%}",
            f"Total at risk ({last.quote})": "{:,.0f}",
        },
        na_rep="n/a",
    ).map(level_style, subset=["Risk"]),
    hide_index=True,
    width="stretch",
)
st.caption(
    "The longer you wait, the larger the possible fall. If you cannot set the payment date, read the row "
    "that matches the longest wait you could face."
)

# ---------------------------------------------------------------- volatility + league table
left, right = st.columns(2)
with left:
    fv = go.Figure()
    fv.add_scatter(x=d.date, y=d.vol_30d_annual, mode="lines", line=dict(color="#e9a23b"))
    fv.update_layout(title="30-day annualised volatility", height=300, margin=dict(t=50, b=20), yaxis_tickformat=".0%")
    if use_range:
        fv.update_xaxes(range=[pd.Timestamp(range_start), pd.Timestamp(range_end)])
    st.plotly_chart(fv, width="stretch")
with right:
    st.markdown(f"**All currencies at a glance, for a {days}-day wait**")
    league = []
    for p, g in flat[flat.provider_code == provider].sort_values("date").groupby("pair"):
        r = horizon_risk(g, days, range_start, range_end)
        league.append(
            {
                "pair": p,
                "rate": g.rate.iloc[-1],
                "Recent behaviour": g.risk_level.iloc[-1],
                "Downside": r["loss_pct"],
                "Risk for term": r["level"],
            }
        )
    league = pd.DataFrame(league).sort_values("Downside", ascending=False)
    st.dataframe(
        league.style.format({"rate": "{:.4f}", "Downside": "{:.1%}"}, na_rep="n/a").map(
            level_style, subset=["Recent behaviour", "Risk for term"]
        ),
        hide_index=True,
        width="stretch",
    )

# ---------------------------------------------------------------- reconciliation
st.subheader("Do the two official sources agree?")
gap = (
    star["fact_rate_gap"]
    .merge(star["dim_date"][["date_key", "date"]])
    .merge(star["dim_currency"][["currency_key", "currency_code"]])
)
cur = st.selectbox("Currency", sorted(gap.currency_code.unique()), key="gapcur")
g = gap[gap.currency_code == cur].sort_values("date")
fg = go.Figure()
fg.add_bar(x=g.date, y=g.gap_pct, marker_color=["#d1495b" if x else "#8d99ae" for x in g.is_gap_alert])
fg.update_layout(
    title=f"EUR/{cur}: CBAR-implied minus ECB, in %", height=280, margin=dict(t=50, b=20), yaxis_ticksuffix="%"
)
st.plotly_chart(fg, width="stretch")
st.caption(
    "CBAR gives AZN per EUR and AZN per currency; dividing them yields the EUR cross-rate CBAR implies. "
    "Red bars = gap above 1%: a data-quality or timing alert."
)

# ---------------------------------------------------------------- star schema
with st.expander("Data model (star schema)"):
    for t in TABLES:
        st.markdown(f"**{t}**  ({len(star[t]):,} rows)")
        st.dataframe(star[t].head(5), hide_index=True, width="stretch")
