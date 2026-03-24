"""
Retirement Planner — Main entry point / Home page.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from PIL import Image
import os

from utils.sheets import init_sheets, get_contributions, get_returns, get_snapshots, get_settings
from utils.calculations import (
    current_balance_by_account, total_balance,
    build_xirr_cashflows, xirr,
    avg_monthly_contribution, months_to_milestone,
    portfolio_over_time,
)

# ─── Page config ─────────────────────────────────────────────────────────────

_icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
_page_icon = Image.open(_icon_path) if os.path.exists(_icon_path) else "🏦"

st.set_page_config(
    page_title="Retirement Planner",
    page_icon=_page_icon,
    layout="wide",
    initial_sidebar_state="expanded",
)

# Inject apple-touch-icon so iOS "Add to Home Screen" uses our icon
st.markdown(
    """
    <link rel="apple-touch-icon" sizes="180x180"
          href="https://raw.githubusercontent.com/sisaacwork/retirement-planner/main/assets/icon.png">
    <meta name="apple-mobile-web-app-title" content="Retirement">
    <meta name="apple-mobile-web-app-capable" content="yes">
    """,
    unsafe_allow_html=True,
)

# ─── Init sheets (creates tabs if needed) ─────────────────────────────────────

try:
    init_sheets()
except Exception as e:
    st.error(
        "⚠️ Could not connect to Google Sheets. "
        "Make sure you've added your credentials to `.streamlit/secrets.toml`. "
        f"\n\nError: {e}"
    )
    st.info("See the README for setup instructions.")
    st.stop()

# ─── Load data ────────────────────────────────────────────────────────────────

contributions = get_contributions()
returns       = get_returns()
snapshots     = get_snapshots()
settings      = get_settings()

balance_df = current_balance_by_account(contributions, returns, snapshots)
portfolio  = total_balance(balance_df)

# ─── Header ───────────────────────────────────────────────────────────────────

st.title("🏦 Retirement Planner")
st.caption("Isaac & Katherine · Personal Portfolio Tracker")
st.divider()

# ─── No-data prompt ───────────────────────────────────────────────────────────

if contributions.empty and snapshots.empty:
    st.info(
        "👋 Welcome! Head to **⚙️ Settings** first to enter your birth years and "
        "RRSP/TFSA/FHSA contribution room, then use **💰 Log Contribution** to "
        "record your first contribution."
    )

# ─── Top-level KPI cards ──────────────────────────────────────────────────────

col1, col2, col3, col4 = st.columns(4)

# Total balance
with col1:
    st.metric("💰 Total Portfolio", f"${portfolio:,.2f}")

# XIRR
cashflows = build_xirr_cashflows(contributions, returns, snapshots)
rate = xirr(cashflows)
with col2:
    if rate is not None:
        st.metric("📈 Annualised Return (MWRR)", f"{rate * 100:.2f}%")
    else:
        st.metric("📈 Annualised Return", "—")

# Monthly avg contribution
monthly_contrib = avg_monthly_contribution(contributions)
with col3:
    st.metric("📅 Avg Monthly Contribution", f"${monthly_contrib:,.0f}")

# YTD contributions
if not contributions.empty:
    ytd = contributions[contributions["date"].dt.year == pd.Timestamp.today().year]["amount"].sum()
else:
    ytd = 0.0
with col4:
    st.metric("🗓️ YTD Contributions", f"${ytd:,.0f}")

st.divider()

# ─── Two-column layout: chart + breakdown ─────────────────────────────────────

left, right = st.columns([2, 1])

with left:
    st.subheader("Portfolio Over Time")
    history = portfolio_over_time(contributions, returns, snapshots)
    if not history.empty:
        fig = px.area(
            history, x="date", y="balance",
            labels={"date": "Date", "balance": "Balance (CA$)"},
            color_discrete_sequence=["#2196F3"],
        )
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            yaxis_tickprefix="$",
            yaxis_tickformat=",.0f",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("No data yet — log a contribution or balance snapshot to see your chart.")

with right:
    st.subheader("Account Breakdown")
    if not balance_df.empty:
        balance_df["label"] = balance_df["account"] + " (" + balance_df["person"] + ")"
        fig2 = px.pie(
            balance_df[balance_df["balance"] > 0],
            names="label",
            values="balance",
            color_discrete_sequence=px.colors.qualitative.Set2,
            hole=0.4,
        )
        fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0), showlegend=True)
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.caption("No balances to display yet.")

st.divider()

# ─── Milestone projections ────────────────────────────────────────────────────

st.subheader("🎯 Milestone Projections")

milestones_raw = [
    settings.get("milestone_1", "250000"),
    settings.get("milestone_2", "500000"),
    settings.get("milestone_3", "1000000"),
]
milestones = []
for m in milestones_raw:
    try:
        milestones.append(float(m))
    except ValueError:
        pass

monthly_rate_for_proj = rate / 12 if rate else 0.04 / 12  # fallback 4% annual

proj_cols = st.columns(len(milestones))
for col, milestone in zip(proj_cols, milestones):
    with col:
        if portfolio >= milestone:
            st.metric(f"${milestone:,.0f}", "✅ Reached!")
        else:
            n_months = months_to_milestone(portfolio, monthly_contrib, rate or 0.06, milestone)
            if n_months is not None:
                years  = int(n_months // 12)
                months = int(n_months % 12)
                label  = f"{years}y {months}m" if years > 0 else f"{months} months"
                pct    = min(portfolio / milestone * 100, 100)
                st.metric(f"${milestone:,.0f}", label)
                st.progress(pct / 100, text=f"{pct:.1f}% there")
            else:
                st.metric(f"${milestone:,.0f}", "Enter data to project")

st.divider()

# ─── Recent activity ──────────────────────────────────────────────────────────

st.subheader("🕐 Recent Activity")

recent_events = []

if not contributions.empty:
    for _, row in contributions.tail(5).iterrows():
        recent_events.append({
            "Date":    row["date"].strftime("%b %d, %Y"),
            "Type":    "Contribution",
            "Account": f"{row['account']} ({row['person']})",
            "Amount":  f"+${row['amount']:,.2f}",
        })

if not returns.empty:
    for _, row in returns.tail(5).iterrows():
        sign = "+" if row["amount"] >= 0 else ""
        recent_events.append({
            "Date":    row["date"].strftime("%b %d, %Y"),
            "Type":    "Return",
            "Account": f"{row['account']} ({row['person']})",
            "Amount":  f"{sign}${row['amount']:,.2f}",
        })

if recent_events:
    recent_df = pd.DataFrame(recent_events).sort_values("Date", ascending=False).head(10)
    st.dataframe(recent_df, use_container_width=True, hide_index=True)
else:
    st.caption("No activity logged yet.")

# ─── Per-person summary ───────────────────────────────────────────────────────

if not balance_df.empty:
    st.divider()
    st.subheader("👤 Per-Person Summary")
    person_cols = st.columns(2)
    for idx, person in enumerate(["Isaac", "Katherine"]):
        with person_cols[idx]:
            person_df = balance_df[balance_df["person"] == person]
            person_total = person_df["balance"].sum()
            st.metric(f"**{person}** — Total", f"${person_total:,.2f}")
            if not person_df.empty:
                for _, row in person_df.iterrows():
                    st.write(f"&nbsp;&nbsp;{row['account']}: **${row['balance']:,.2f}**")
