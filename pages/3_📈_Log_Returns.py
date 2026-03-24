"""
Log Returns page — single entry, bulk historical entry, and balance snapshots.
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from utils.sheets import (
    get_returns, add_return, add_returns_bulk, delete_return,
    get_snapshots, add_snapshot, delete_snapshot,
)
from utils.constants import ACCOUNT_TYPES, PEOPLE

st.set_page_config(page_title="Log Returns", page_icon="📈", layout="wide")
st.title("📈 Log Returns & Balances")
st.caption(
    "Log your daily Wealthsimple returns, bulk-import historical data, "
    "or record a full account balance snapshot for Canada Life check-ins."
)
st.divider()

tab_single, tab_bulk, tab_snapshots = st.tabs(
    ["📅 Single Entry", "📥 Bulk / Historical Entry", "📸 Balance Snapshots"]
)

# ─── Tab 1: Single Entry ──────────────────────────────────────────────────────

with tab_single:
    st.subheader("Log a Daily Return")
    st.caption("Enter the dollar gain or loss shown in Wealthsimple for a given day. "
               "Use a negative number for a down day.")

    with st.form("return_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            return_date = st.date_input("Date", value=date.today(), key="ret_date")
            amount      = st.number_input("Return Amount (CA$)", step=1.0, format="%.2f",
                                          help="Positive = gain, negative = loss.")
        with col2:
            account = st.selectbox("Account", ACCOUNT_TYPES, key="ret_account")
            person  = st.selectbox("Person", PEOPLE, key="ret_person")
        notes = st.text_input("Notes (optional)", key="ret_notes",
                              placeholder="e.g. Market down day")

        if st.form_submit_button("➕ Add Return", type="primary", use_container_width=True):
            add_return(return_date, amount, account, person, notes)
            sign = "+" if amount >= 0 else ""
            st.success(f"✅ Logged {sign}${amount:,.2f} for {account} ({person}) on {return_date}.")

    st.divider()
    _returns = get_returns()
    if not _returns.empty:
        st.subheader("Recent Returns")
        disp = _returns.sort_values("date", ascending=False).head(10).copy()
        disp["date"]   = disp["date"].dt.strftime("%Y-%m-%d")
        disp["amount"] = disp["amount"].apply(lambda x: f"+${x:,.2f}" if x >= 0 else f"-${abs(x):,.2f}")
        st.dataframe(disp[["date","account","person","amount","notes"]], use_container_width=True, hide_index=True)


# ─── Tab 2: Bulk / Historical Entry ──────────────────────────────────────────

with tab_bulk:
    st.subheader("📥 Bulk / Historical Entry")
    st.caption(
        "Use this to backfill historical returns — e.g. entering every day since Jan 1, 2026. "
        "Fill in the table below, leave rows at $0.00 to skip them, then click **Submit**."
    )

    with st.expander("⚙️ Table settings", expanded=True):
        bc1, bc2, bc3, bc4 = st.columns(4)
        with bc1:
            bulk_start = st.date_input("From date", value=date(2026, 1, 1), key="bulk_start")
        with bc2:
            bulk_end = st.date_input("To date", value=date.today(), key="bulk_end")
        with bc3:
            bulk_account = st.selectbox("Account", ACCOUNT_TYPES, key="bulk_acct")
        with bc4:
            bulk_person = st.selectbox("Person", PEOPLE, key="bulk_person")

    if bulk_start > bulk_end:
        st.error("Start date must be before end date.")
    else:
        num_days = (bulk_end - bulk_start).days + 1

        # Get already-logged returns so we can pre-fill known values
        existing = get_returns()

        all_dates = [bulk_start + timedelta(days=i) for i in range(num_days)]

        prefill_amounts = []
        for d in all_dates:
            if not existing.empty:
                match = existing[
                    (existing["date"].dt.date == d) &
                    (existing["account"] == bulk_account) &
                    (existing["person"] == bulk_person)
                ]
                prefill_amounts.append(float(match["amount"].iloc[0]) if not match.empty else 0.0)
            else:
                prefill_amounts.append(0.0)

        template = pd.DataFrame({
            "date":   [str(d) for d in all_dates],
            "return (CA$)": prefill_amounts,
            "notes":  [""] * num_days,
        })

        st.info(
            f"Showing **{num_days} days** for **{bulk_account} ({bulk_person})**. "
            "Rows left at $0.00 will be skipped on submit. "
            "Already-logged values are pre-filled."
        )

        edited = st.data_editor(
            template,
            use_container_width=True,
            hide_index=True,
            column_config={
                "date":         st.column_config.TextColumn("Date", disabled=True),
                "return (CA$)": st.column_config.NumberColumn("Return (CA$)", format="%.2f", step=0.01),
                "notes":        st.column_config.TextColumn("Notes"),
            },
            num_rows="fixed",
            height=min(400, 40 + num_days * 35),
        )

        non_zero = edited[edited["return (CA$)"] != 0.0]
        st.caption(f"{len(non_zero)} non-zero rows will be saved.")

        if st.button("💾 Submit All", type="primary", use_container_width=True, key="bulk_submit"):
            rows_to_save = [
                {
                    "date":    row["date"],
                    "amount":  row["return (CA$)"],
                    "account": bulk_account,
                    "person":  bulk_person,
                    "notes":   row["notes"],
                }
                for _, row in non_zero.iterrows()
            ]
            if rows_to_save:
                with st.spinner(f"Saving {len(rows_to_save)} entries…"):
                    add_returns_bulk(rows_to_save)
                st.success(f"✅ Saved {len(rows_to_save)} return entries!")
                st.rerun()
            else:
                st.warning("No non-zero rows to save.")


# ─── Tab 3: Balance Snapshots ─────────────────────────────────────────────────

with tab_snapshots:
    st.subheader("Record a Balance Snapshot")
    st.caption(
        "Use this when you check your Canada Life RRSP (or any account) "
        "and want to record the exact balance. This anchors the portfolio "
        "value and improves rate-of-return accuracy."
    )

    with st.form("snapshot_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            snap_date = st.date_input("Date of snapshot", value=date.today(), key="snap_date")
            balance   = st.number_input("Account Balance (CA$)", min_value=0.0,
                                        step=100.0, format="%.2f")
        with col2:
            account = st.selectbox("Account", ACCOUNT_TYPES, key="snap_account")
            person  = st.selectbox("Person", PEOPLE, key="snap_person")
        source = st.selectbox("Source", ["Wealthsimple", "Canada Life", "CRA My Account", "Other"])
        notes  = st.text_input("Notes (optional)", key="snap_notes")

        if st.form_submit_button("📸 Save Snapshot", type="primary", use_container_width=True):
            if balance <= 0:
                st.error("Please enter a balance greater than $0.")
            else:
                add_snapshot(snap_date, account, person, balance, source, notes)
                st.success(
                    f"✅ Saved ${balance:,.2f} snapshot for {account} ({person}) "
                    f"on {snap_date} ({source})."
                )

    st.divider()

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
        st.info("No balance snapshots recorded yet.")
