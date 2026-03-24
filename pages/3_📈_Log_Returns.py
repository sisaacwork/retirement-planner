"""
Log Returns page — record daily returns (Wealthsimple) and balance snapshots (Canada Life).
"""

import streamlit as st
import pandas as pd
from datetime import date

from utils.sheets import (
    get_returns, add_return, delete_return,
    get_snapshots, add_snapshot, delete_snapshot,
)
from utils.constants import ACCOUNT_TYPES, PEOPLE

st.set_page_config(page_title="Log Returns", page_icon="📈", layout="wide")
st.title("📈 Log Returns & Balances")
st.caption(
    "Log your daily Wealthsimple returns or record a full account balance snapshot "
    "(useful for Canada Life RRSP check-ins)."
)
st.divider()

tab_returns, tab_snapshots = st.tabs(["📅 Daily Returns", "📸 Balance Snapshots"])

# ─── Tab 1: Daily Returns ─────────────────────────────────────────────────────

with tab_returns:
    st.subheader("Log a Daily Return")
    st.caption(
        "Enter the dollar gain or loss shown in Wealthsimple for a given day. "
        "Use a negative number for a down day."
    )

    with st.form("return_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            return_date = st.date_input("Date", value=date.today(),
                                        max_value=date.today(), key="ret_date")
            amount      = st.number_input("Return Amount (CA$)", step=1.0, format="%.2f",
                                          help="Positive for a gain, negative for a loss.")
        with col2:
            account = st.selectbox("Account", ACCOUNT_TYPES, key="ret_account")
            person  = st.selectbox("Person", PEOPLE, key="ret_person")
        notes = st.text_input("Notes (optional)", key="ret_notes",
                              placeholder="e.g. Market down day")

        submitted = st.form_submit_button("➕ Add Return", type="primary", use_container_width=True)

        if submitted:
            add_return(return_date, amount, account, person, notes)
            sign = "+" if amount >= 0 else ""
            st.success(f"✅ Logged {sign}${amount:,.2f} return for {account} ({person}) on {return_date}.")

    st.divider()

    # ─── Returns history ──────────────────────────────────────────────────────
    returns = get_returns()

    if not returns.empty:
        st.subheader("Returns History")

        fc1, fc2 = st.columns(2)
        with fc1:
            sel_person  = st.multiselect("Person", PEOPLE, default=PEOPLE, key="rf_person")
        with fc2:
            sel_account = st.multiselect("Account", ACCOUNT_TYPES, default=ACCOUNT_TYPES, key="rf_account")

        filtered = returns[
            returns["person"].isin(sel_person) &
            returns["account"].isin(sel_account)
        ].sort_values("date", ascending=False)

        if not filtered.empty:
            net = filtered["amount"].sum()
            sign = "+" if net >= 0 else ""
            st.info(f"Net returns shown: **{sign}${net:,.2f}** across **{len(filtered)}** entries")

            for _, row in filtered.iterrows():
                sign_row = "+" if row["amount"] >= 0 else ""
                colour   = "🟢" if row["amount"] >= 0 else "🔴"
                with st.expander(
                    f"{colour} **{row['date'].strftime('%b %d, %Y')}** — "
                    f"{row['account']} ({row['person']}) — {sign_row}${abs(row['amount']):,.2f}",
                    expanded=False,
                ):
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.write(f"**Date:** {row['date'].strftime('%B %d, %Y')}")
                        st.write(f"**Account:** {row['account']}")
                        st.write(f"**Person:** {row['person']}")
                        st.write(f"**Return:** {sign_row}${row['amount']:,.2f}")
                        if row.get("notes"):
                            st.write(f"**Notes:** {row['notes']}")
                    with c2:
                        if st.button("🗑️ Delete", key=f"del_ret_{row['id']}"):
                            delete_return(str(row["id"]))
                            st.rerun()
        else:
            st.caption("No returns match your filters.")
    else:
        st.info("No returns logged yet.")

# ─── Tab 2: Balance Snapshots ─────────────────────────────────────────────────

with tab_snapshots:
    st.subheader("Record a Balance Snapshot")
    st.caption(
        "Use this when you check your Canada Life RRSP (or any account) "
        "and want to record the exact balance at a point in time. "
        "This anchors the portfolio value and improves rate-of-return accuracy."
    )

    with st.form("snapshot_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            snap_date = st.date_input("Date of snapshot", value=date.today(),
                                      max_value=date.today(), key="snap_date")
            balance   = st.number_input("Account Balance (CA$)", min_value=0.0,
                                        step=100.0, format="%.2f")
        with col2:
            account = st.selectbox("Account", ACCOUNT_TYPES, key="snap_account")
            person  = st.selectbox("Person", PEOPLE, key="snap_person")
        source = st.selectbox("Source", ["Wealthsimple", "Canada Life", "CRA My Account", "Other"])
        notes  = st.text_input("Notes (optional)", key="snap_notes")

        submitted = st.form_submit_button("📸 Save Snapshot", type="primary", use_container_width=True)

        if submitted:
            if balance <= 0:
                st.error("Please enter a balance greater than $0.")
            else:
                add_snapshot(snap_date, account, person, balance, source, notes)
                st.success(
                    f"✅ Saved ${balance:,.2f} snapshot for {account} ({person}) "
                    f"on {snap_date} (source: {source})."
                )

    st.divider()

    # ─── Snapshot history ─────────────────────────────────────────────────────
    snapshots = get_snapshots()

    if not snapshots.empty:
        st.subheader("Snapshot History")

        sc1, sc2 = st.columns(2)
        with sc1:
            sel_person  = st.multiselect("Person", PEOPLE, default=PEOPLE, key="sf_person")
        with sc2:
            sel_account = st.multiselect("Account", ACCOUNT_TYPES, default=ACCOUNT_TYPES, key="sf_account")

        filtered_snaps = snapshots[
            snapshots["person"].isin(sel_person) &
            snapshots["account"].isin(sel_account)
        ].sort_values("date", ascending=False)

        if not filtered_snaps.empty:
            for _, row in filtered_snaps.iterrows():
                with st.expander(
                    f"📸 **{row['date'].strftime('%b %d, %Y')}** — "
                    f"{row['account']} ({row['person']}) — ${row['balance']:,.2f}",
                    expanded=False,
                ):
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.write(f"**Date:** {row['date'].strftime('%B %d, %Y')}")
                        st.write(f"**Account:** {row['account']}")
                        st.write(f"**Person:** {row['person']}")
                        st.write(f"**Balance:** ${row['balance']:,.2f}")
                        st.write(f"**Source:** {row.get('source', '—')}")
                        if row.get("notes"):
                            st.write(f"**Notes:** {row['notes']}")
                    with c2:
                        if st.button("🗑️ Delete", key=f"del_snap_{row['id']}"):
                            delete_snapshot(str(row["id"]))
                            st.rerun()
        else:
            st.caption("No snapshots match your filters.")
    else:
        st.info("No balance snapshots recorded yet.")
