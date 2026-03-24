"""
Dashboard page — detailed analytics, charts, and contribution history.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from utils.sheets import get_contributions, get_returns, get_snapshots, get_settings
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
settings      = get_settings()

balance_df    = current_balance_by_account(contributions, returns, snapshots)
portfolio     = total_balance(balance_df)
cashflows     = build_xirr_cashflows(contributions, returns, snapshots)
rate          = xirr(cashflows)
monthly_avg   = avg_monthly_contribution(contributions)

# ─── Filters ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Filters")
    people_filter = st.multiselect("Person", ["Isaac", "Katherine"], default=["Isaac", "Katherine"])
    accounts_filter = st.multiselect("Account", ["TFSA", "FHSA", "RRSP", "NRSP"],
                                     default=["TFSA", "FHSA", "RRSP", "NRSP"])

def apply_filters(df):
    if df.empty:
        return df
    out = df.copy()
    if "person" in out.columns:
        out = out[out["person"].isin(people_filter)]
    if "account" in out.columns:
        out = out[out["account"].isin(accounts_filter)]
    return out

f_contribs  = apply_filters(contributions)
f_returns   = apply_filters(returns)
f_snapshots = apply_filters(snapshots)

f_balance_df = current_balance_by_account(f_contribs, f_returns, f_snapshots)
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
with c3:
    total_contributions = float(f_contribs["amount"].sum()) if not f_contribs.empty else 0.0
    st.metric("Total Contributed", f"${total_contributions:,.2f}")
with c4:
    total_returns = float(f_returns["amount"].sum()) if not f_returns.empty else 0.0
    sign = "+" if total_returns >= 0 else ""
    st.metric("Total Returns", f"{sign}${total_returns:,.2f}")
with c5:
    market_gain = f_portfolio - total_contributions
    pct = (market_gain / total_contributions * 100) if total_contributions > 0 else 0
    st.metric("Market Gain", f"${market_gain:,.2f}", delta=f"{pct:.1f}%")

st.divider()

# ─── Portfolio history ────────────────────────────────────────────────────────

st.subheader("Portfolio Value Over Time")
history = portfolio_over_time(f_contribs, f_returns, f_snapshots)

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
    st.subheader("Monthly Contributions")
    if not f_contribs.empty:
        monthly = (
            f_contribs.copy()
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
    st.subheader("Contributions by Account")
    if not f_contribs.empty:
        by_account = f_contribs.groupby("account")["amount"].sum().reset_index()
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
