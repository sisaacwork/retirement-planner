"""
Log Contribution page — record a new contribution and view/delete history.
"""

import streamlit as st
import pandas as pd
from datetime import date

from utils.sheets import (
    get_contributions, add_contribution, delete_contribution,
    get_settings,
)
from utils.constants import ACCOUNT_TYPES, PEOPLE

st.set_page_config(page_title="Log Contribution", page_icon="💰", layout="wide")
st.title("💰 Log a Contribution")
st.caption("Record money you've added to any of your accounts.")
st.divider()

settings = get_settings()

# ─── Contribution form ────────────────────────────────────────────────────────

with st.form("contribution_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        contrib_date   = st.date_input("Date", value=date.today(), max_value=date.today())
        amount         = st.number_input("Amount (CA$)", min_value=0.01, step=50.0, format="%.2f")
    with col2:
        account        = st.selectbox("Account", ACCOUNT_TYPES)
        person         = st.selectbox("Who contributed?", PEOPLE)
    notes = st.text_input("Notes (optional)", placeholder="e.g. Bi-weekly auto-deposit")

    submitted = st.form_submit_button("➕ Add Contribution", type="primary", use_container_width=True)

    if submitted:
        if amount <= 0:
            st.error("Please enter a contribution amount greater than $0.")
        else:
            add_contribution(contrib_date, amount, account, person, notes)
            st.success(f"✅ Recorded ${amount:,.2f} to {account} ({person}) on {contrib_date}.")
            st.balloons()

st.divider()

# ─── Contribution summary by account ─────────────────────────────────────────

contributions = get_contributions()

if not contributions.empty:
    st.subheader("Summary by Account & Person")
    summary = (
        contributions.groupby(["account", "person"])["amount"]
        .agg(["sum", "count"])
        .rename(columns={"sum": "Total (CA$)", "count": "# Contributions"})
        .reset_index()
    )
    summary["Total (CA$)"] = summary["Total (CA$)"].apply(lambda x: f"${x:,.2f}")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.divider()

    # ─── Full history with delete ─────────────────────────────────────────────

    st.subheader("Contribution History")

    # Filter controls
    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        sel_person  = st.multiselect("Person", PEOPLE, default=PEOPLE, key="cf_person")
    with filter_col2:
        sel_account = st.multiselect("Account", ACCOUNT_TYPES, default=ACCOUNT_TYPES, key="cf_account")
    with filter_col3:
        year_options = sorted(contributions["date"].dt.year.unique().tolist(), reverse=True)
        sel_years    = st.multiselect("Year", year_options, default=year_options, key="cf_year")

    filtered = contributions[
        contributions["person"].isin(sel_person) &
        contributions["account"].isin(sel_account) &
        contributions["date"].dt.year.isin(sel_years)
    ].copy().sort_values("date", ascending=False)

    if not filtered.empty:
        # Show running total at top
        st.info(f"Showing **{len(filtered)}** contributions totalling **${filtered['amount'].sum():,.2f}**")

        for _, row in filtered.iterrows():
            with st.expander(
                f"**{row['date'].strftime('%b %d, %Y')}** — {row['account']} ({row['person']}) — ${row['amount']:,.2f}",
                expanded=False,
            ):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.write(f"**Date:** {row['date'].strftime('%B %d, %Y')}")
                    st.write(f"**Account:** {row['account']}")
                    st.write(f"**Person:** {row['person']}")
                    st.write(f"**Amount:** ${row['amount']:,.2f}")
                    if row.get("notes"):
                        st.write(f"**Notes:** {row['notes']}")
                with c2:
                    if st.button("🗑️ Delete", key=f"del_contrib_{row['id']}"):
                        delete_contribution(str(row["id"]))
                        st.rerun()
    else:
        st.caption("No contributions match the current filters.")
else:
    st.info("No contributions logged yet. Use the form above to add your first one!")
