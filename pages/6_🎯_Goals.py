"""
Goals & Projections page — milestone progress and time-to-goal estimates.
Users can adjust expected monthly contributions and return rate to model
different scenarios.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import math
from datetime import date

from utils.sheets import get_contributions, get_returns, get_snapshots, get_withdrawals, get_settings
from utils.calculations import (
    current_balance_by_account, total_balance,
    avg_monthly_contribution, months_to_milestone,
)
from utils.constants import ACCOUNT_TYPES, PEOPLE

st.set_page_config(page_title="Goals", page_icon="🎯", layout="wide")
st.title("🎯 Goals & Projections")
st.caption("See how far you are from your milestones and model how different contribution amounts affect your timeline.")
st.divider()

# ─── Load data ────────────────────────────────────────────────────────────────

contributions = get_contributions()
returns       = get_returns()
snapshots     = get_snapshots()
withdrawals   = get_withdrawals()
settings      = get_settings()

def s(key, default="0"):
    return settings.get(key, default)

balance_df = current_balance_by_account(contributions, returns, snapshots, withdrawals)
portfolio  = total_balance(balance_df)

# Historical avg monthly contribution (last 12 months)
hist_monthly = avg_monthly_contribution(contributions, lookback_months=12)

# ─── Load milestones from settings ────────────────────────────────────────────

milestones = []
for key, default in [("milestone_1", "250000"), ("milestone_2", "500000"), ("milestone_3", "1000000")]:
    try:
        milestones.append(float(s(key, default)))
    except (ValueError, TypeError):
        pass

# ─── Scenario inputs ──────────────────────────────────────────────────────────

st.subheader("📐 Projection Assumptions")
st.caption(
    "Adjust these to model different scenarios. The app uses these values to estimate "
    "how long until you hit each milestone — it doesn't change any of your logged data."
)

col_a, col_b, col_c = st.columns(3)

with col_a:
    monthly_total = st.number_input(
        "Monthly Contribution (CA$)",
        min_value=0.0,
        step=100.0,
        format="%.0f",
        value=float(s("monthly_contribution_target", "1000") or 1000),
        help="Total monthly amount across all accounts and both people.",
        key="goal_monthly",
    )

with col_b:
    expected_return = st.slider(
        "Expected Annual Return (%)",
        min_value=0.0,
        max_value=15.0,
        value=6.0,
        step=0.5,
        help="Annualised market return assumption. Historical long-term stock market average is ~7–8%.",
        key="goal_return",
    )

with col_c:
    st.metric("Current Portfolio", f"${portfolio:,.2f}")
    st.metric("Avg Monthly (Last 12 Mo)", f"${hist_monthly:,.0f}")

st.divider()

# ─── Milestone progress ───────────────────────────────────────────────────────

st.subheader("🏁 Milestone Progress")

annual_rate  = expected_return / 100
monthly_rate = annual_rate / 12

if not milestones:
    st.info("No milestones set — go to ⚙️ Settings to configure them.")
else:
    cols = st.columns(len(milestones))
    for col, milestone in zip(cols, milestones):
        with col:
            pct   = min(portfolio / milestone * 100, 100)
            left  = max(milestone - portfolio, 0)

            st.markdown(f"### ${milestone:,.0f}")
            st.progress(pct / 100)
            st.caption(f"**{pct:.1f}%** there · **${left:,.0f}** to go")

            if portfolio >= milestone:
                st.success("✅ Reached!")
            else:
                n = months_to_milestone(portfolio, monthly_total, annual_rate, milestone)
                if n is not None and n > 0:
                    years  = int(n // 12)
                    months = int(round(n % 12))
                    if years > 0 and months > 0:
                        label = f"{years}y {months}m"
                    elif years > 0:
                        label = f"{years} year{'s' if years != 1 else ''}"
                    else:
                        label = f"{months} month{'s' if months != 1 else ''}"

                    target_date = pd.Timestamp.today() + pd.DateOffset(months=int(n))
                    st.metric("Estimated Time", label)
                    st.caption(f"~{target_date.strftime('%B %Y')}")
                elif monthly_total <= 0:
                    st.warning("Set a monthly contribution above to project.")
                else:
                    st.metric("Estimated Time", "—")
                    st.caption("Can't project with current inputs.")

st.divider()

# ─── Per-account breakdown ────────────────────────────────────────────────────

st.subheader("💼 Current Balance by Account")

if not balance_df.empty:
    display = balance_df[balance_df["balance"] > 0].copy()
    display["label"] = display["person"] + " — " + display["account"]
    display = display.sort_values("balance", ascending=False)

    cols_ac = st.columns(len(display))
    for col, (_, row) in zip(cols_ac, display.iterrows()):
        with col:
            pct_of_total = row["balance"] / portfolio * 100 if portfolio > 0 else 0
            st.metric(row["label"], f"${row['balance']:,.2f}")
            st.caption(f"{pct_of_total:.1f}% of portfolio")
else:
    st.caption("No balance data yet — log a contribution or balance snapshot to get started.")

st.divider()

# ─── Projection table ─────────────────────────────────────────────────────────

st.subheader("📅 Year-by-Year Projection")
st.caption(
    f"Starting from ${portfolio:,.0f} today, contributing ${monthly_total:,.0f}/month "
    f"at {expected_return:.1f}% annual return."
)

if monthly_total > 0 or annual_rate > 0:
    proj_rows = []
    balance   = portfolio
    r         = monthly_rate

    for yr in range(1, 31):
        for _ in range(12):
            balance = balance * (1 + r) + monthly_total
        proj_rows.append({
            "Year":           f"Year {yr} ({date.today().year + yr})",
            "Projected Value": f"${balance:,.0f}",
            "Gain vs Today":  f"+${balance - portfolio:,.0f}",
        })

        # Stop once we're well past the last milestone
        if milestones and balance > max(milestones) * 1.1:
            break

    proj_df = pd.DataFrame(proj_rows)
    st.dataframe(proj_df, use_container_width=True, hide_index=True)
else:
    st.caption("Set a monthly contribution or return rate above to generate the projection table.")

st.divider()

# ─── Per-person contribution split ────────────────────────────────────────────

st.subheader("👥 Contribution Split by Person")
st.caption("How much each person has contributed in total, by account.")

if not contributions.empty:
    by_person_account = (
        contributions.groupby(["person", "account"])["amount"]
        .sum()
        .reset_index()
        .sort_values(["person", "amount"], ascending=[True, False])
    )

    for person in PEOPLE:
        pdata = by_person_account[by_person_account["person"] == person]
        if pdata.empty:
            continue
        person_total = pdata["amount"].sum()
        st.markdown(f"**{person}** — Total: ${person_total:,.2f}")
        pcols = st.columns(len(pdata))
        for col, (_, row) in zip(pcols, pdata.iterrows()):
            with col:
                st.metric(row["account"], f"${row['amount']:,.2f}")
        st.write("")
else:
    st.caption("No contributions logged yet.")
