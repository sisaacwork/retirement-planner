"""
Log Returns page.

Primary workflow: enter a balance → app auto-calculates the return.
Manual return entry is available as a fallback.
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from utils.sheets import (
    get_contributions, get_returns, get_snapshots, get_withdrawals,
    add_return, add_returns_bulk, delete_return,
    add_snapshot, add_snapshots_bulk,
    delete_snapshot,
)
from utils.calculations import (
    calculate_return_from_balance,
    derive_returns_from_balance_series,
)
from utils.constants import ACCOUNT_TYPES, PEOPLE

st.set_page_config(page_title="Log Returns", page_icon="📈", layout="wide")
st.title("📈 Log Returns & Balances")
st.caption(
    "Enter your end-of-day or end-of-week balance and the app calculates the return automatically. "
    "Manual return entry is also available if needed."
)
st.divider()

# ─── Load once ────────────────────────────────────────────────────────────────

contributions = get_contributions()
snapshots     = get_snapshots()
withdrawals   = get_withdrawals()
returns       = get_returns()

tab_balance, tab_bulk, tab_manual, tab_history = st.tabs([
    "💰 Enter Balance",
    "📥 Bulk / Historical Balances",
    "✏️ Manual Return Entry",
    "📋 History",
])

# ═══════════════════════════════════════════════════════════════════════════════
# Tab 1 — Enter Balance (auto-calculates return)
# ═══════════════════════════════════════════════════════════════════════════════

with tab_balance:
    st.subheader("Enter Today's Balance")
    st.caption(
        "Check your Wealthsimple or Canada Life app, enter the account balance, "
        "and the return since your last entry is calculated automatically."
    )

    col1, col2 = st.columns(2)
    with col1:
        bal_account = st.selectbox("Account", ACCOUNT_TYPES, key="bal_account")
        bal_person  = st.selectbox("Person",  PEOPLE,        key="bal_person")
    with col2:
        bal_date    = st.date_input("Date", value=date.today(), key="bal_date")
        bal_source  = st.selectbox("Source", ["Wealthsimple", "Canada Life", "CRA My Account", "Other"],
                                   key="bal_source")

    bal_amount = st.number_input(
        "Account Balance (CA$)",
        min_value=0.0, step=100.0, format="%.2f",
        key="bal_amount",
        help="Enter the total value of this account as shown in your app.",
    )
    bal_notes = st.text_input("Notes (optional)", key="bal_notes")

    # Live preview of the derived return
    if bal_amount > 0:
        info = calculate_return_from_balance(
            new_balance   = bal_amount,
            new_date      = bal_date,
            account       = bal_account,
            person        = bal_person,
            contributions = contributions,
            snapshots     = snapshots,
            withdrawals   = withdrawals,
        )

        st.divider()
        if info["return_amount"] is not None:
            ret   = info["return_amount"]
            sign  = "+" if ret >= 0 else ""
            color = "green" if ret >= 0 else "red"
            emoji = "📈" if ret >= 0 else "📉"

            prev_str = f"${info['prev_balance']:,.2f} on {info['prev_date'].strftime('%b %d, %Y')}"
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.metric("Previous Balance", prev_str)
            with mc2:
                st.metric("New Balance", f"${bal_amount:,.2f}")
            with mc3:
                st.metric(
                    f"{emoji} Implied Return",
                    f"{sign}${ret:,.2f}",
                    delta=f"contributions factored in: ${info['contrib_between']:,.2f}"
                    if info["contrib_between"] != 0 else None,
                )
        else:
            st.info(
                "No previous balance found for this account — this will be saved as your "
                "**opening balance**. Future entries will calculate returns from here."
            )

    st.divider()

    if st.button("💾 Save Balance & Return", type="primary", use_container_width=True, key="bal_submit"):
        if bal_amount <= 0:
            st.error("Please enter a balance greater than $0.")
        else:
            info = calculate_return_from_balance(
                new_balance   = bal_amount,
                new_date      = bal_date,
                account       = bal_account,
                person        = bal_person,
                contributions = contributions,
                snapshots     = snapshots,
                withdrawals   = withdrawals,
            )

            # Always save the snapshot
            add_snapshot(bal_date, bal_account, bal_person, bal_amount, bal_source, bal_notes)

            # Save the derived return if we had a prior balance to compare against
            if info["return_amount"] is not None:
                ret  = info["return_amount"]
                sign = "+" if ret >= 0 else ""
                note = f"Auto-derived from balance entry. {bal_notes}".strip()
                add_return(bal_date, ret, bal_account, bal_person, note)
                st.success(
                    f"✅ Saved balance **${bal_amount:,.2f}** and derived return **{sign}${ret:,.2f}** "
                    f"for {bal_account} ({bal_person}) on {bal_date}."
                )
            else:
                st.success(
                    f"✅ Saved opening balance **${bal_amount:,.2f}** for {bal_account} ({bal_person}) "
                    f"on {bal_date}. Future entries will calculate returns from here."
                )
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 2 — Bulk / Historical Balances
# ═══════════════════════════════════════════════════════════════════════════════

with tab_bulk:
    st.subheader("📥 Bulk / Historical Balances")
    st.caption(
        "Enter a series of balances (e.g. every day since Jan 1, 2026). "
        "The app will calculate the return between each consecutive entry automatically, "
        "accounting for any contributions you've already logged."
    )

    with st.expander("⚙️ Table settings", expanded=True):
        bc1, bc2, bc3, bc4 = st.columns(4)
        with bc1:
            bulk_start = st.date_input("From date", value=date(2026, 1, 1), key="bulk_start")
        with bc2:
            bulk_end   = st.date_input("To date",   value=date.today(),     key="bulk_end")
        with bc3:
            bulk_account = st.selectbox("Account", ACCOUNT_TYPES, key="bulk_acct")
        with bc4:
            bulk_person  = st.selectbox("Person",  PEOPLE,        key="bulk_person")
        bulk_source = st.selectbox("Source", ["Wealthsimple", "Canada Life", "CRA My Account", "Other"],
                                   key="bulk_source")

    if bulk_start > bulk_end:
        st.error("Start date must be before end date.")
    else:
        num_days  = (bulk_end - bulk_start).days + 1
        all_dates = [bulk_start + timedelta(days=i) for i in range(num_days)]

        # Pre-fill any balances already in snapshots for this account/person
        prefill = {}
        if not snapshots.empty:
            mask = (snapshots["account"] == bulk_account) & (snapshots["person"] == bulk_person)
            for _, row in snapshots[mask].iterrows():
                prefill[row["date"].date()] = float(row["balance"])

        template = pd.DataFrame({
            "date":        [str(d) for d in all_dates],
            "balance (CA$)": [prefill.get(d, 0.0) for d in all_dates],
            "notes":       [""] * num_days,
        })

        non_zero_count = sum(1 for d in all_dates if prefill.get(d, 0.0) != 0)
        st.info(
            f"Showing **{num_days} days** for **{bulk_account} ({bulk_person})**. "
            "Enter a balance for each day you have data — leave at $0.00 to skip. "
            f"**{non_zero_count}** days already have a saved balance (pre-filled)."
        )

        edited = st.data_editor(
            template,
            use_container_width=True,
            hide_index=True,
            column_config={
                "date":          st.column_config.TextColumn("Date", disabled=True),
                "balance (CA$)": st.column_config.NumberColumn("Balance (CA$)", format="%.2f",
                                                                step=0.01, min_value=0.0),
                "notes":         st.column_config.TextColumn("Notes"),
            },
            num_rows="fixed",
            height=min(420, 40 + num_days * 35),
        )

        # Only process rows with a balance entered
        entries_to_save = edited[edited["balance (CA$)"] > 0].copy()
        st.caption(f"**{len(entries_to_save)}** rows with balances will be processed on submit.")

        if st.button("💾 Calculate Returns & Save All", type="primary",
                     use_container_width=True, key="bulk_bal_submit"):
            if entries_to_save.empty:
                st.warning("No balances entered.")
            else:
                raw_entries = [
                    {"date": row["date"], "balance": row["balance (CA$)"], "notes": row["notes"]}
                    for _, row in entries_to_save.iterrows()
                ]

                with st.spinner("Calculating returns and saving…"):
                    results = derive_returns_from_balance_series(
                        entries            = raw_entries,
                        account            = bulk_account,
                        person             = bulk_person,
                        contributions      = contributions,
                        existing_snapshots = snapshots,
                        withdrawals        = withdrawals,
                    )

                    # Save all snapshots in one call
                    snap_rows = [
                        {"date": r["date"], "account": bulk_account, "person": bulk_person,
                         "balance": r["balance"], "source": bulk_source,
                         "notes": entries_to_save[entries_to_save["date"] == str(r["date"])]["notes"].values[0]
                         if str(r["date"]) in entries_to_save["date"].values else ""}
                        for r in results
                    ]
                    add_snapshots_bulk(snap_rows)

                    # Save all derived returns (skip the opening entry with no prior)
                    return_rows = [
                        {"date": r["date"], "amount": r["return_amount"],
                         "account": bulk_account, "person": bulk_person,
                         "notes": "Auto-derived from balance entry"}
                        for r in results if r["return_amount"] is not None
                    ]
                    if return_rows:
                        add_returns_bulk(return_rows)

                n_returns = len([r for r in results if r["return_amount"] is not None])
                st.success(
                    f"✅ Saved **{len(results)}** balance snapshots and "
                    f"**{n_returns}** derived returns."
                )

                # Show a preview of what was calculated
                preview_data = []
                for r in results:
                    sign = "+" if (r["return_amount"] or 0) >= 0 else ""
                    preview_data.append({
                        "Date":    str(r["date"]),
                        "Balance": f"${r['balance']:,.2f}",
                        "Return":  f"{sign}${r['return_amount']:,.2f}" if r["return_amount"] is not None else "Opening balance",
                        "Contributions factored in": f"${r['contrib_between']:,.2f}" if r["contrib_between"] else "—",
                    })
                st.dataframe(pd.DataFrame(preview_data), use_container_width=True, hide_index=True)
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 3 — Manual Return Entry (fallback)
# ═══════════════════════════════════════════════════════════════════════════════

with tab_manual:
    st.subheader("✏️ Manual Return Entry")
    st.caption(
        "Use this only if you know the exact return amount and don't want to enter a balance. "
        "For most cases the **Enter Balance** tab is easier."
    )

    with st.form("return_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            return_date = st.date_input("Date", value=date.today(), key="ret_date")
            amount      = st.number_input("Return Amount (CA$)", step=1.0, format="%.2f",
                                          help="Positive = gain, negative = loss.")
        with col2:
            account = st.selectbox("Account", ACCOUNT_TYPES, key="ret_account")
            person  = st.selectbox("Person",  PEOPLE,        key="ret_person")
        notes = st.text_input("Notes (optional)", key="ret_notes")

        if st.form_submit_button("➕ Add Return", type="primary", use_container_width=True):
            add_return(return_date, amount, account, person, notes)
            sign = "+" if amount >= 0 else ""
            st.success(f"✅ Logged {sign}${amount:,.2f} for {account} ({person}) on {return_date}.")


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 4 — History
# ═══════════════════════════════════════════════════════════════════════════════

with tab_history:
    hist_tab1, hist_tab2 = st.tabs(["Returns", "Balance Snapshots"])

    with hist_tab1:
        if not returns.empty:
            hc1, hc2 = st.columns(2)
            with hc1:
                sel_person  = st.multiselect("Person",  PEOPLE,        default=PEOPLE,        key="rf_person")
            with hc2:
                sel_account = st.multiselect("Account", ACCOUNT_TYPES, default=ACCOUNT_TYPES, key="rf_account")

            filtered = returns[
                returns["person"].isin(sel_person) &
                returns["account"].isin(sel_account)
            ].sort_values("date", ascending=False)

            if not filtered.empty:
                net  = filtered["amount"].sum()
                sign = "+" if net >= 0 else ""
                st.info(f"Net returns: **{sign}${net:,.2f}** across **{len(filtered)}** entries")

                disp = filtered.copy()
                disp["date"]   = disp["date"].dt.strftime("%Y-%m-%d")
                disp["amount"] = disp["amount"].apply(
                    lambda x: f"+${x:,.2f}" if x >= 0 else f"-${abs(x):,.2f}"
                )
                st.dataframe(
                    disp[["date", "account", "person", "amount", "notes"]],
                    use_container_width=True, hide_index=True,
                )

                st.divider()
                st.caption("Delete individual entries:")
                for _, row in filtered.head(20).iterrows():
                    sign_r = "+" if row["amount"] >= 0 else ""
                    col_a, col_b = st.columns([5, 1])
                    with col_a:
                        st.write(f"{row['date'].strftime('%b %d, %Y')} · {row['account']} ({row['person']}) · {sign_r}${abs(row['amount']):,.2f}")
                    with col_b:
                        if st.button("🗑️", key=f"del_ret_{row['id']}"):
                            delete_return(str(row["id"]))
                            st.rerun()
        else:
            st.info("No returns logged yet.")

    with hist_tab2:
        if not snapshots.empty:
            sc1, sc2 = st.columns(2)
            with sc1:
                sel_person_s  = st.multiselect("Person",  PEOPLE,        default=PEOPLE,        key="sf_person")
            with sc2:
                sel_account_s = st.multiselect("Account", ACCOUNT_TYPES, default=ACCOUNT_TYPES, key="sf_account")

            filtered_s = snapshots[
                snapshots["person"].isin(sel_person_s) &
                snapshots["account"].isin(sel_account_s)
            ].sort_values("date", ascending=False)

            if not filtered_s.empty:
                disp_s = filtered_s.copy()
                disp_s["date"]    = disp_s["date"].dt.strftime("%Y-%m-%d")
                disp_s["balance"] = disp_s["balance"].apply(lambda x: f"${x:,.2f}")
                st.dataframe(
                    disp_s[["date", "account", "person", "balance", "source", "notes"]],
                    use_container_width=True, hide_index=True,
                )

                st.divider()
                st.caption("Delete individual snapshots:")
                for _, row in filtered_s.head(20).iterrows():
                    col_a, col_b = st.columns([5, 1])
                    with col_a:
                        st.write(f"{row['date'].strftime('%b %d, %Y')} · {row['account']} ({row['person']}) · ${row['balance']:,.2f}")
                    with col_b:
                        if st.button("🗑️", key=f"del_snap_{row['id']}"):
                            delete_snapshot(str(row["id"]))
                            st.rerun()
        else:
            st.info("No balance snapshots recorded yet.")
