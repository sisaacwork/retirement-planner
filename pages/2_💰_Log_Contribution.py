"""
Log Contribution page — single entry, bulk historical entry, withdrawals, and history.
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from utils.sheets import (
    get_contributions, add_contribution, add_contributions_bulk, delete_contribution,
    get_withdrawals, add_withdrawal, delete_withdrawal,
    get_settings,
)
from utils.constants import ACCOUNT_TYPES, PEOPLE

st.set_page_config(page_title="Log Contribution", page_icon="💰", layout="wide")
st.title("💰 Contributions & Withdrawals")
st.caption("Record money added to or taken from your accounts.")
st.divider()

settings = get_settings()

tab_single, tab_bulk, tab_withdrawal, tab_history = st.tabs([
    "➕ Add Contribution", "📥 Bulk / Historical", "🏧 Withdraw Funds", "📋 History"
])

# ─── Tab 1: Single Entry ──────────────────────────────────────────────────────

with tab_single:
    with st.form("contribution_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            contrib_date = st.date_input("Date", value=date.today(), key="single_date")
            amount       = st.number_input("Amount (CA$)", min_value=0.01, step=50.0, format="%.2f")
        with col2:
            account = st.selectbox("Account", ACCOUNT_TYPES, key="single_acct")
            person  = st.selectbox("Who contributed?", PEOPLE, key="single_person")
        notes = st.text_input("Notes (optional)", placeholder="e.g. Bi-weekly auto-deposit")

        if st.form_submit_button("➕ Add Contribution", type="primary", use_container_width=True):
            if amount <= 0:
                st.error("Please enter a contribution amount greater than $0.")
            else:
                add_contribution(contrib_date, amount, account, person, notes)
                st.success(f"✅ Recorded ${amount:,.2f} to {account} ({person}) on {contrib_date}.")
                st.balloons()


# ─── Tab 2: Bulk / Historical Entry ──────────────────────────────────────────

with tab_bulk:
    st.subheader("📥 Bulk / Historical Contributions")
    st.caption(
        "Use this to backfill past contributions. Each row is one contribution. "
        "Leave the amount at $0.00 to skip that row."
    )

    # Start with a blank table — contributions aren't daily so no date-range prefill
    with st.expander("⚙️ Table settings", expanded=True):
        bc1, bc2 = st.columns(2)
        with bc1:
            num_rows = st.number_input("Number of rows to add", min_value=1, max_value=100,
                                       value=10, step=1, key="bulk_contrib_rows")
        with bc2:
            default_person = st.selectbox("Default person (editable per row)", PEOPLE, key="bulk_contrib_person")

    template = pd.DataFrame({
        "date":       [str(date.today())] * int(num_rows),
        "amount (CA$)": [0.0] * int(num_rows),
        "account":    [ACCOUNT_TYPES[0]] * int(num_rows),
        "person":     [default_person] * int(num_rows),
        "notes":      [""] * int(num_rows),
    })

    edited = st.data_editor(
        template,
        use_container_width=True,
        hide_index=True,
        column_config={
            "date":         st.column_config.TextColumn("Date (YYYY-MM-DD)"),
            "amount (CA$)": st.column_config.NumberColumn("Amount (CA$)", format="%.2f", step=0.01, min_value=0.0),
            "account":      st.column_config.SelectboxColumn("Account", options=ACCOUNT_TYPES),
            "person":       st.column_config.SelectboxColumn("Person", options=PEOPLE),
            "notes":        st.column_config.TextColumn("Notes"),
        },
        num_rows="fixed",
    )

    non_zero = edited[edited["amount (CA$)"] > 0]
    st.caption(f"{len(non_zero)} rows with amounts > $0 will be saved.")

    if st.button("💾 Submit All Contributions", type="primary", use_container_width=True, key="bulk_contrib_submit"):
        rows_to_save = [
            {
                "date":    row["date"],
                "amount":  row["amount (CA$)"],
                "account": row["account"],
                "person":  row["person"],
                "notes":   row["notes"],
            }
            for _, row in non_zero.iterrows()
        ]
        if rows_to_save:
            with st.spinner(f"Saving {len(rows_to_save)} contributions…"):
                add_contributions_bulk(rows_to_save)
            st.success(f"✅ Saved {len(rows_to_save)} contributions!")
            st.rerun()
        else:
            st.warning("No rows with amounts > $0 to save.")


# ─── Tab 3: Withdrawals ───────────────────────────────────────────────────────

with tab_withdrawal:
    st.subheader("🏧 Log a Withdrawal")
    st.caption(
        "Record money you've taken out of an account. "
        "**TFSA note:** withdrawn amounts are added back to your contribution room "
        "on January 1st of the *following* year — not immediately."
    )

    with st.form("withdrawal_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            w_date    = st.date_input("Date", value=date.today(), key="w_date")
            w_amount  = st.number_input("Amount Withdrawn (CA$)", min_value=0.01,
                                        step=50.0, format="%.2f")
        with col2:
            w_account = st.selectbox("Account", ACCOUNT_TYPES, key="w_account")
            w_person  = st.selectbox("Who withdrew?", PEOPLE, key="w_person")
        w_notes = st.text_input("Notes (optional)", key="w_notes",
                                placeholder="e.g. Home down payment, emergency fund")

        if st.form_submit_button("🏧 Record Withdrawal", type="primary", use_container_width=True):
            add_withdrawal(w_date, w_amount, w_account, w_person, w_notes)
            st.success(f"✅ Recorded withdrawal of ${w_amount:,.2f} from {w_account} ({w_person}) on {w_date}.")
            if w_account == "TFSA":
                st.info(
                    f"💡 This ${w_amount:,.2f} TFSA withdrawal will be added back to "
                    f"{w_person}'s contribution room on **January 1, {w_date.year + 1}**."
                )

    st.divider()

    # Withdrawal history
    withdrawals = get_withdrawals()
    if not withdrawals.empty:
        st.subheader("Withdrawal History")

        wf1, wf2 = st.columns(2)
        with wf1:
            wsel_person  = st.multiselect("Person",  PEOPLE,        default=PEOPLE,        key="wf_person")
        with wf2:
            wsel_account = st.multiselect("Account", ACCOUNT_TYPES, default=ACCOUNT_TYPES, key="wf_account")

        wfiltered = withdrawals[
            withdrawals["person"].isin(wsel_person) &
            withdrawals["account"].isin(wsel_account)
        ].sort_values("date", ascending=False)

        if not wfiltered.empty:
            st.info(f"Total withdrawn: **${wfiltered['amount'].sum():,.2f}** across {len(wfiltered)} entries")
            for _, row in wfiltered.iterrows():
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
                        if row["account"] == "TFSA":
                            st.caption(f"Room recovery: Jan 1, {row['date'].year + 1}")
                    with c2:
                        if st.button("🗑️ Delete", key=f"del_w_{row['id']}"):
                            delete_withdrawal(str(row["id"]))
                            st.rerun()
    else:
        st.info("No withdrawals recorded yet.")


# ─── Tab 4: History ───────────────────────────────────────────────────────────

with tab_history:
    contributions = get_contributions()

    if not contributions.empty:
        # Summary
        summary = (
            contributions.groupby(["account", "person"])["amount"]
            .agg(["sum", "count"])
            .rename(columns={"sum": "Total (CA$)", "count": "# Contributions"})
            .reset_index()
        )
        summary["Total (CA$)"] = summary["Total (CA$)"].apply(lambda x: f"${x:,.2f}")
        st.subheader("Summary by Account & Person")
        st.dataframe(summary, use_container_width=True, hide_index=True)

        st.divider()

        # Filters
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            sel_person  = st.multiselect("Person", PEOPLE, default=PEOPLE, key="cf_person")
        with fc2:
            sel_account = st.multiselect("Account", ACCOUNT_TYPES, default=ACCOUNT_TYPES, key="cf_account")
        with fc3:
            year_options = sorted(contributions["date"].dt.year.unique().tolist(), reverse=True)
            sel_years    = st.multiselect("Year", year_options, default=year_options, key="cf_year")

        filtered = contributions[
            contributions["person"].isin(sel_person) &
            contributions["account"].isin(sel_account) &
            contributions["date"].dt.year.isin(sel_years)
        ].copy().sort_values("date", ascending=False)

        if not filtered.empty:
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
        st.info("No contributions logged yet. Use the Single Entry or Bulk Entry tab above.")
