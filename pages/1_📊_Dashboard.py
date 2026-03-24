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
if year_filter != "All time":
    _yr = int(year_filter)
    f_contribs_yr    = f_contribs[f_contribs["date"].dt.year       == _yr]
    f_returns_yr     = f_returns[f_returns["date"].dt.year          == _yr]
    f_withdrawals_yr = f_withdrawals[f_withdrawals["date"].dt.year  == _yr] \
                       if not f_withdrawals.empty else f_withdrawals
    # For the chart, narrow snapshots to the selected year
    f_snapshots_yr   = f_snapshots[f_snapshots["date"].dt.year      == _yr]
else:
    f_contribs_yr    = f_contribs
    f_returns_yr     = f_returns
    f_withdrawals_yr = f_withdrawals
    f_snapshots_yr   = f_snapshots

# Balance always uses full history regardless of year filter
f_balance_df = current_balance_by_account(f_contribs, f_returns, f_snapshots, f_withdrawals)
f_portfolio  = total_balance(f_balance_df)

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
with c3:
    total_contributions = float(f_contribs_yr["amount"].sum()) if not f_contribs_yr.empty else 0.0
    st.metric(f"Contributed ({_year_label})", f"${total_contributions:,.2f}")
with c4:
    total_returns = float(f_returns_yr["amount"].sum()) if not f_returns_yr.empty else 0.0
    sign = "+" if total_returns >= 0 else ""
    st.metric(f"Returns ({_year_label})", f"{sign}${total_returns:,.2f}")
with c5:
    # Market gain = balance - net invested (contributions minus withdrawals).
    # Always uses all-time data since balance is cumulative.
    total_contributions_all = float(f_contribs["amount"].sum()) if not f_contribs.empty else 0.0
    total_withdrawals_all   = float(f_withdrawals["amount"].sum()) if not f_withdrawals.empty else 0.0
    net_invested = total_contributions_all - total_withdrawals_all
    market_gain  = f_portfolio - net_invested
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

# ─── Returns analysis ────────────────────────────────────────────────────────

st.subheader("Daily Returns Log")
if not f_returns.empty:
    ret_monthly = (
        f_returns.copy()
        .assign(month=lambda x: x["date"].dt.to_period("M").astype(str))
        .groupby("month")["amount"]
        .sum()
        .reset_index()
    )
    ret_monthly["colour"] = ret_monthly["amount"].apply(lambda x: "Positive" if x >= 0 else "Negative")
    fig5 = px.bar(ret_monthly, x="month", y="amount", color="colour",
                  color_discrete_map={"Positive": "#4CAF50", "Negative": "#F44336"},
                  labels={"month": "Month", "amount": "Net Return (CA$)", "colour": ""})
    fig5.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f",
                       margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig5, use_container_width=True)
else:
    st.caption("No return entries yet.")

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
