"""
Dashboard page — detailed analytics, charts, and contribution history.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from utils.sheets import get_contributions, get_returns, get_snapshots, get_withdrawals, get_settings
from utils.calculations import (
    current_balance_by_account, total_balance,
    build_xirr_cashflows, xirr,
    avg_monthly_contribution, months_to_milestone,
    portfolio_over_time,
)

st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")
st.title("📊 Dashboard")
st.divider()

# ─── Load ─────────────────────────────────────────────────────────────────────

contributions = get_contributions()
returns       = get_returns()
snapshots     = get_snapshots()
withdrawals   = get_withdrawals()
settings      = get_settings()

balance_df    = current_balance_by_account(contributions, returns, snapshots, withdrawals)
portfolio     = total_balance(balance_df)
cashflows     = build_xirr_cashflows(contributions, returns, snapshots, withdrawals)
rate          = xirr(cashflows)
monthly_avg   = avg_monthly_contribution(contributions)

# ─── Filters ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Filters")
    people_filter   = st.multiselect("Person",  ["Isaac", "Katherine"], default=["Isaac", "Katherine"])
    accounts_filter = st.multiselect("Account", ["TFSA", "FHSA", "RRSP", "NRSP"],
                                     default=["TFSA", "FHSA", "RRSP", "NRSP"])

    # Year filter — built from years that actually have contribution or return data
    _data_years = sorted(set(
        list(contributions["date"].dt.year.unique() if not contributions.empty else []) +
        list(returns["date"].dt.year.unique()       if not returns.empty       else [])
    ), reverse=True)
    year_options  = ["All time"] + [str(y) for y in _data_years]
    _default_idx  = year_options.index(str(pd.Timestamp.today().year)) \
                    if str(pd.Timestamp.today().year) in year_options else 0
    year_filter   = st.selectbox("Year", year_options, index=_default_idx)

def apply_filters(df):
    if df.empty:
        return df
    out = df.copy()
    if "person" in out.columns:
        out = out[out["person"].isin(people_filter)]
    if "account" in out.columns:
        out = out[out["account"].isin(accounts_filter)]
    return out

f_contribs    = apply_filters(contributions)
f_returns     = apply_filters(returns)
f_snapshots   = apply_filters(snapshots)
f_withdrawals = apply_filters(withdrawals)

# Apply year filter to flow data (contributions, returns, withdrawals).
# Balance is cumulative so we always compute it from all history.
def _filter_year(df, yr):
    """Filter a DataFrame to a specific year, safely handling empty frames."""
    if df.empty or "date" not in df.columns:
        return df
    return df[df["date"].dt.year == yr]

if year_filter != "All time":
    _yr = int(year_filter)
    f_contribs_yr    = _filter_year(f_contribs,    _yr)
    f_returns_yr     = _filter_year(f_returns,      _yr)
    f_withdrawals_yr = _filter_year(f_withdrawals,  _yr)
    f_snapshots_yr   = _filter_year(f_snapshots,    _yr)
else:
    f_contribs_yr    = f_contribs
    f_returns_yr     = f_returns
    f_withdrawals_yr = f_withdrawals
    f_snapshots_yr   = f_snapshots

# Balance always uses full history regardless of year filter
f_balance_df = current_balance_by_account(f_contribs, f_returns, f_snapshots, f_withdrawals)
f_portfolio  = total_balance(f_balance_df)

# Full portfolio history (needed for start-of-year balance lookup)
full_history = portfolio_over_time(f_contribs, f_returns, f_snapshots, f_withdrawals)

# ─── KPI row ──────────────────────────────────────────────────────────────────

c1, c2, c3, c4, c5 = st.columns(5)

with c1:
    st.metric("Total Balance", f"${f_portfolio:,.2f}")
with c2:
    if rate is not None:
        st.metric("MWRR (Annualised)", f"{rate * 100:.2f}%",
                  help="Money-Weighted Rate of Return (XIRR) across all contributions.")
    else:
        st.metric("MWRR (Annualised)", "—")
_year_label = year_filter if year_filter != "All time" else "All Time"

total_contributions_all = float(f_contribs["amount"].sum()) if not f_contribs.empty else 0.0
total_withdrawals_all   = float(f_withdrawals["amount"].sum()) if not f_withdrawals.empty else 0.0
net_invested = total_contributions_all - total_withdrawals_all
market_gain  = f_portfolio - net_invested

with c3:
    total_contributions = float(f_contribs_yr["amount"].sum()) if not f_contribs_yr.empty else 0.0
    st.metric(f"Contributed ({_year_label})", f"${total_contributions:,.2f}")
with c4:
    # Compute returns from balance history, not from the logged returns table.
    # This is accurate even if old return entries were saved without withdrawal correction.
    # Formula: end_balance - start_balance - contributions_in_period + withdrawals_in_period
    if year_filter != "All time":
        _yr = int(year_filter)
        year_start_ts = pd.Timestamp(f"{_yr}-01-01")
        if not full_history.empty:
            prior = full_history[full_history["date"] < year_start_ts]
            start_balance = float(prior.iloc[-1]["balance"]) if not prior.empty else 0.0
        else:
            start_balance = 0.0
        total_withdrawals_yr = float(f_withdrawals_yr["amount"].sum()) if not f_withdrawals_yr.empty else 0.0
        period_returns = f_portfolio - start_balance - total_contributions + total_withdrawals_yr
    else:
        period_returns = market_gain

    sign = "+" if period_returns >= 0 else ""
    st.metric(
        f"Returns ({_year_label})",
        f"{sign}${period_returns:,.2f}",
        help="Computed as: end balance − start balance − contributions + withdrawals. "
             "This reflects pure investment performance, independent of logged return entries.",
    )
with c5:
    pct = (market_gain / net_invested * 100) if net_invested > 0 else 0
    st.metric("Market Gain (All Time)", f"${market_gain:,.2f}", delta=f"{pct:.1f}%")

st.divider()

# ─── Portfolio history ────────────────────────────────────────────────────────

st.subheader(f"Portfolio Value Over Time{' — ' + year_filter if year_filter != 'All time' else ''}")
history = portfolio_over_time(f_contribs_yr, f_returns_yr, f_snapshots_yr, f_withdrawals_yr)

if not history.empty:
    fig = px.area(history, x="date", y="balance",
                  color_discrete_sequence=["#2196F3"],
                  labels={"date": "Date", "balance": "Balance (CA$)"})
    fig.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f",
                      margin=dict(l=0, r=0, t=20, b=0))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.caption("Not enough data for a chart yet.")

st.divider()

# ─── Contributions analysis ───────────────────────────────────────────────────

col_a, col_b = st.columns(2)

with col_a:
    st.subheader(f"Monthly Contributions ({_year_label})")
    if not f_contribs_yr.empty:
        monthly = (
            f_contribs_yr.copy()
            .assign(month=lambda x: x["date"].dt.to_period("M").astype(str))
            .groupby("month")["amount"]
            .sum()
            .reset_index()
        )
        fig3 = px.bar(monthly, x="month", y="amount",
                      labels={"month": "Month", "amount": "CA$"},
                      color_discrete_sequence=["#4CAF50"])
        fig3.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f",
                           margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.caption("No contributions logged yet.")

with col_b:
    st.subheader(f"Contributions by Account ({_year_label})")
    if not f_contribs_yr.empty:
        by_account = f_contribs_yr.groupby("account")["amount"].sum().reset_index()
        fig4 = px.pie(by_account, names="account", values="amount",
                      color_discrete_sequence=px.colors.qualitative.Set2, hole=0.35)
        fig4.update_layout(margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig4, use_container_width=True)
    else:
        st.caption("No contributions logged yet.")

st.divider()

# ─── Weekly return rate ────────────────────────────────────────────────────────

st.subheader("Weekly Return Rate")
st.caption(
    "Week-over-week investment return with contributions and withdrawals factored out — "
    "shows pure market performance without the noise of large deposits or withdrawals."
)

if not full_history.empty and len(full_history) >= 14:
    _hw = full_history.copy()
    _hw["week"] = _hw["date"].dt.to_period("W")
    _w_end   = _hw.groupby("week")["balance"].last()
    _w_start = _hw.groupby("week")["balance"].first()

    _cw_dict = {}
    if not f_contribs.empty:
        _cwf = f_contribs.copy()
        _cwf["week"] = _cwf["date"].dt.to_period("W")
        _cw_dict = _cwf.groupby("week")["amount"].sum().to_dict()

    _ww_dict = {}
    if not f_withdrawals.empty:
        _wwf = f_withdrawals.copy()
        _wwf["week"] = _wwf["date"].dt.to_period("W")
        _ww_dict = _wwf.groupby("week")["amount"].sum().to_dict()

    _week_rows = []
    for _wk in sorted(_w_end.index):
        _sb = _w_start[_wk]
        if _sb <= 0:
            continue
        _ret_pct = (_w_end[_wk] - _sb - _cw_dict.get(_wk, 0) + _ww_dict.get(_wk, 0)) / _sb * 100
        # Skip weeks with implausibly large swings (usually the very first week
        # when the starting balance is near zero, making % return meaningless)
        if abs(_ret_pct) > 15:
            continue
        _week_rows.append({"Week": _wk.start_time, "Return (%)": round(_ret_pct, 3)})

    if _week_rows:
        _wk_df = pd.DataFrame(_week_rows)
        _wk_df["4-week avg"] = _wk_df["Return (%)"].rolling(4, min_periods=1).mean()

        fig_w = go.Figure()
        fig_w.add_bar(
            x=_wk_df["Week"], y=_wk_df["Return (%)"],
            name="Weekly Return",
            marker_color=["#4CAF50" if v >= 0 else "#F44336" for v in _wk_df["Return (%)"]],
        )
        fig_w.add_scatter(
            x=_wk_df["Week"], y=_wk_df["4-week avg"],
            mode="lines", name="4-week avg",
            line=dict(color="#2196F3", width=2),
        )
        fig_w.add_hline(y=0, line_color="gray", line_width=1)
        fig_w.update_layout(
            yaxis_ticksuffix="%", yaxis_title="Weekly Return (%)", xaxis_title="Week",
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", y=1.05),
        )
        st.plotly_chart(fig_w, use_container_width=True)
    else:
        st.caption("Not enough weekly data yet — add more balance snapshots over time.")
else:
    st.caption("At least two weeks of balance history needed for this chart.")

st.divider()

# ─── Returns analysis ─────────────────────────────────────────────────────────

st.subheader(f"Monthly Returns ({_year_label})")
st.caption(
    "Derived from balance snapshots — contributions and withdrawals are factored out "
    "so only investment gains and losses remain. This is accurate even if older entries "
    "in your returns log pre-date the withdrawal correction fix."
)

# Derive monthly returns from balance history rather than logged return entries.
_hist_for_returns = history if not history.empty else pd.DataFrame()

if not _hist_for_returns.empty:
    _h = _hist_for_returns.copy()
    _h["month"] = _h["date"].dt.to_period("M")
    _monthly_end   = _h.groupby("month")["balance"].last()
    _monthly_start = _h.groupby("month")["balance"].first()

    _c_monthly = {}
    if not f_contribs_yr.empty:
        _cm = f_contribs_yr.copy()
        _cm["month"] = _cm["date"].dt.to_period("M")
        _c_monthly = _cm.groupby("month")["amount"].sum().to_dict()

    _w_monthly = {}
    if not f_withdrawals_yr.empty:
        _wm = f_withdrawals_yr.copy()
        _wm["month"] = _wm["date"].dt.to_period("M")
        _w_monthly = _wm.groupby("month")["amount"].sum().to_dict()

    _ret_rows = []
    for _mo in sorted(_monthly_end.index):
        _ret_amt = (
            _monthly_end[_mo]
            - _monthly_start[_mo]
            - _c_monthly.get(_mo, 0)
            + _w_monthly.get(_mo, 0)
        )
        _ret_rows.append({"month": str(_mo), "amount": _ret_amt})

    if _ret_rows:
        _ret_df = pd.DataFrame(_ret_rows)
        _ret_df["colour"] = _ret_df["amount"].apply(lambda x: "Positive" if x >= 0 else "Negative")
        fig5 = px.bar(_ret_df, x="month", y="amount", color="colour",
                      color_discrete_map={"Positive": "#4CAF50", "Negative": "#F44336"},
                      labels={"month": "Month", "amount": "Net Return (CA$)", "colour": ""})
        fig5.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f",
                           margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig5, use_container_width=True)
    else:
        st.caption("Not enough balance data to derive returns yet.")
else:
    st.caption("No balance history available yet — add balance snapshots in Log Returns.")

st.divider()

# ─── Contribution history table ───────────────────────────────────────────────

st.subheader("Contribution History")

tab1, tab2, tab3 = st.tabs(["Contributions", "Returns", "Balance Snapshots"])

with tab1:
    if not f_contribs.empty:
        display = f_contribs.copy()
        display["date"]   = display["date"].dt.strftime("%Y-%m-%d")
        display["amount"] = display["amount"].apply(lambda x: f"${x:,.2f}")
        st.dataframe(
            display[["date", "account", "person", "amount", "notes"]],
            use_container_width=True, hide_index=True
        )
    else:
        st.caption("No contributions logged.")

with tab2:
    if not f_returns.empty:
        display = f_returns.copy()
        display["date"]   = display["date"].dt.strftime("%Y-%m-%d")
        display["amount"] = display["amount"].apply(lambda x: f"+${x:,.2f}" if x >= 0 else f"-${abs(x):,.2f}")
        st.dataframe(
            display[["date", "account", "person", "amount", "notes"]],
            use_container_width=True, hide_index=True
        )
    else:
        st.caption("No returns logged.")

with tab3:
    if not f_snapshots.empty:
        display = f_snapshots.copy()
        display["date"]    = display["date"].dt.strftime("%Y-%m-%d")
        display["balance"] = display["balance"].apply(lambda x: f"${x:,.2f}")
        st.dataframe(
            display[["date", "account", "person", "balance", "source", "notes"]],
            use_container_width=True, hide_index=True
        )
    else:
        st.caption("No balance snapshots logged.")

st.divider()

# ─── Per-person breakdown ─────────────────────────────────────────────────────

st.subheader("Contributions by Person")
if not f_contribs.empty:
    by_person = f_contribs.groupby(["person", "account"])["amount"].sum().reset_index()
    fig6 = px.bar(by_person, x="person", y="amount", color="account",
                  barmode="stack",
                  color_discrete_sequence=px.colors.qualitative.Pastel,
                  labels={"amount": "CA$", "person": "Person", "account": "Account"})
    fig6.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f",
                       margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig6, use_container_width=True)
else:
    st.caption("No contributions logged yet.")
