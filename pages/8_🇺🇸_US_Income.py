"""
US Income Tracker — Katherine's cross-border tax obligations.

Tracks semi-monthly USD pay, Illinois state tax withheld, USD→CAD conversions,
CRA quarterly instalments, year-end CPP (CPT20 election), and the Illinois
Foreign Tax Credit applied on the Canadian return.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta
from math import ceil

from utils.sheets import (
    get_us_payslips, add_us_payslip, add_us_payslips_bulk, delete_us_payslip,
    get_us_instalments, add_us_instalment, delete_us_instalment,
    get_settings, update_settings,
)

st.set_page_config(page_title="US Income Tracker", page_icon="🇺🇸", layout="wide")
st.title("🇺🇸 Katherine's US Income & Tax Tracker")
st.caption(
    "Tracks USD paycheques, Illinois tax withheld, CRA quarterly instalments, "
    "year-end CPP (CPT20), and the Foreign Tax Credit on your Canadian return."
)
st.divider()

# ─── Constants ────────────────────────────────────────────────────────────────

IL_TAX_RATE      = 0.0495          # Illinois flat state income tax
CPP1_RATE        = 0.0595          # Employee share (CPT20 election pays BOTH shares)
CPP2_RATE        = 0.04            # CPP2 additional contribution
CPP_BASIC_EXEMPT = 3_500.0
CPP_YMPE         = 73_200.0        # 2025 Year's Maximum Pensionable Earnings
CPP_YMPE2        = 81_900.0        # 2025 second ceiling
CPP_MAX1         = (CPP_YMPE  - CPP_BASIC_EXEMPT) * CPP1_RATE   # ~$4,145
CPP_MAX2         = (CPP_YMPE2 - CPP_YMPE) * CPP2_RATE           # ~$348
CPP_TOTAL_MAX    = CPP_MAX1 + CPP_MAX2                           # ~$4,493

# Under CPT20 Katherine pays both employee AND employer shares
CPT20_RATE1   = CPP1_RATE * 2      # 11.9 % on CPP1
CPT20_RATE2   = CPP2_RATE * 2      # 8.0 % on CPP2

# 2025 federal + Ontario combined marginal rate used to estimate FTC benefit
# (simplified — actual credit is the lesser of foreign tax paid and Canadian
#  tax attributable to the foreign income; we expose the formula to the user)
FED_BRACKETS = [
    (57_375, 0.15), (114_750, 0.205), (158_519, 0.26),
    (220_000, 0.29), (float("inf"), 0.33),
]
ON_BRACKETS = [
    (51_446, 0.0505), (102_894, 0.0915), (150_000, 0.1116),
    (220_000, 0.1216), (float("inf"), 0.1316),
]

# CRA instalment due dates for the current year
CURRENT_YEAR = date.today().year
INSTALMENT_QUARTERS = {
    "Q1": date(CURRENT_YEAR, 3, 15),
    "Q2": date(CURRENT_YEAR, 6, 15),
    "Q3": date(CURRENT_YEAR, 9, 15),
    "Q4": date(CURRENT_YEAR, 12, 15),
}


# ─── Tax helpers ──────────────────────────────────────────────────────────────

def _bracket_tax(income: float, brackets: list) -> float:
    tax = 0.0
    prev = 0.0
    for ceiling, rate in brackets:
        if income <= prev:
            break
        taxable = min(income, ceiling) - prev
        tax += taxable * rate
        prev = ceiling
    return tax


def _marginal_rate(income: float, brackets: list) -> float:
    prev = 0.0
    for ceiling, rate in brackets:
        if income <= ceiling:
            return rate
        prev = ceiling
    return brackets[-1][1]


def calc_cpp_cpt20(net_income_cad: float) -> dict:
    """
    Under CPT20, Katherine pays both employee + employer CPP contributions
    on her self-employment / foreign employment income.
    Returns a dict with cpp1, cpp2, total, deductible_half.
    """
    net = max(net_income_cad - CPP_BASIC_EXEMPT, 0.0)
    cpp1_base = min(net, CPP_YMPE - CPP_BASIC_EXEMPT)
    cpp1 = cpp1_base * CPT20_RATE1

    cpp2_base = max(0.0, min(net_income_cad, CPP_YMPE2) - CPP_YMPE)
    cpp2 = cpp2_base * CPT20_RATE2

    total = min(cpp1 + cpp2, CPT20_RATE1 * (CPP_YMPE - CPP_BASIC_EXEMPT) + CPT20_RATE2 * (CPP_YMPE2 - CPP_YMPE))
    # The employer half is deductible from income
    deductible_half = total / 2.0
    return {"cpp1": cpp1, "cpp2": cpp2, "total": total, "deductible_half": deductible_half}


def estimate_foreign_tax_credit(il_tax_cad: float, us_income_cad: float,
                                 total_income_cad: float, total_fed_tax: float,
                                 total_on_tax: float) -> dict:
    """
    Simplified FTC estimate.
    Federal FTC = lesser of (IL tax paid) and (fed tax × us_income / total_income).
    Ontario FTC = lesser of (IL tax paid − fed FTC) and (on tax × us_income / total_income).
    """
    if total_income_cad <= 0 or total_fed_tax <= 0:
        return {"fed_ftc": 0.0, "on_ftc": 0.0, "total_ftc": 0.0}

    proportion = us_income_cad / total_income_cad
    max_fed = total_fed_tax * proportion
    fed_ftc = min(il_tax_cad, max_fed)

    max_on  = total_on_tax * proportion
    on_ftc  = min(max(il_tax_cad - fed_ftc, 0.0), max_on)

    return {
        "fed_ftc":   round(fed_ftc, 2),
        "on_ftc":    round(on_ftc, 2),
        "total_ftc": round(fed_ftc + on_ftc, 2),
    }


# ─── Load data ────────────────────────────────────────────────────────────────

payslips    = get_us_payslips()
instalments = get_us_instalments()
settings    = get_settings()

def s(key, default="0"):
    return settings.get(key, default)

# Settings-backed annual Canadian income (used in FTC calculation)
try:
    canadian_gross = float(s("katherine_canadian_gross", "0"))
    canadian_fed_tax = float(s("katherine_canadian_fed_tax", "0"))
    canadian_on_tax  = float(s("katherine_canadian_on_tax",  "0"))
except (ValueError, TypeError):
    canadian_gross = canadian_fed_tax = canadian_on_tax = 0.0

# ─── Year filter ──────────────────────────────────────────────────────────────

all_years = sorted(
    set(payslips["date"].dt.year.tolist() if not payslips.empty else []) |
    {CURRENT_YEAR}
)
selected_year = st.selectbox("📅 Tax Year", all_years, index=all_years.index(CURRENT_YEAR))

# Filter payslips to selected year
yr_payslips = payslips[payslips["date"].dt.year == selected_year].copy() if not payslips.empty else pd.DataFrame(columns=payslips.columns if not payslips.empty else ["id","date","gross_usd","il_tax_usd","usd_cad_rate","notes"])

# ─── Summary metrics ──────────────────────────────────────────────────────────

if not yr_payslips.empty:
    total_gross_usd   = yr_payslips["gross_usd"].sum()
    total_il_tax_usd  = yr_payslips["il_tax_usd"].sum()
    total_fed_tax_usd = yr_payslips["fed_tax_usd"].sum()
    yr_payslips["gross_cad"]    = yr_payslips["gross_usd"]    * yr_payslips["usd_cad_rate"]
    yr_payslips["il_tax_cad"]   = yr_payslips["il_tax_usd"]   * yr_payslips["usd_cad_rate"]
    yr_payslips["fed_tax_cad"]  = yr_payslips["fed_tax_usd"]  * yr_payslips["usd_cad_rate"]
    total_gross_cad   = yr_payslips["gross_cad"].sum()
    total_il_tax_cad  = yr_payslips["il_tax_cad"].sum()
    total_fed_tax_cad = yr_payslips["fed_tax_cad"].sum()
    avg_rate          = yr_payslips["usd_cad_rate"].mean()
    paycheques_logged = len(yr_payslips)
else:
    total_gross_usd = total_il_tax_usd = total_fed_tax_usd = 0.0
    total_gross_cad = total_il_tax_cad = total_fed_tax_cad = 0.0
    avg_rate = 0.0
    paycheques_logged = 0

# Semi-monthly = 24 per year
TOTAL_PAY_PERIODS = 24
periods_remaining = max(0, TOTAL_PAY_PERIODS - paycheques_logged)
annualized_gross_usd   = (total_gross_usd   / paycheques_logged * TOTAL_PAY_PERIODS) if paycheques_logged > 0 else 0.0
annualized_gross_cad   = (total_gross_cad   / paycheques_logged * TOTAL_PAY_PERIODS) if paycheques_logged > 0 else 0.0
annualized_il_tax_cad  = (total_il_tax_cad  / paycheques_logged * TOTAL_PAY_PERIODS) if paycheques_logged > 0 else 0.0
annualized_fed_tax_cad = (total_fed_tax_cad / paycheques_logged * TOTAL_PAY_PERIODS) if paycheques_logged > 0 else 0.0

# CPP (CPT20)
cpp = calc_cpp_cpt20(annualized_gross_cad)

# FTC estimate (needs Canadian income from settings)
total_income_cad = annualized_gross_cad + canadian_gross
total_fed_tax_est = _bracket_tax(total_income_cad, FED_BRACKETS)
total_on_tax_est  = _bracket_tax(total_income_cad, ON_BRACKETS)
ftc = estimate_foreign_tax_credit(
    il_tax_cad      = annualized_il_tax_cad,
    us_income_cad   = annualized_gross_cad,
    total_income_cad= total_income_cad,
    total_fed_tax   = total_fed_tax_est if canadian_fed_tax == 0 else canadian_fed_tax,
    total_on_tax    = total_on_tax_est  if canadian_on_tax  == 0 else canadian_on_tax,
)

# Instalment total paid
yr_instalments = instalments[instalments["date"].dt.year == selected_year] if not instalments.empty else pd.DataFrame(columns=["id","date","amount_cad","quarter","notes"])
instalments_paid_cad = yr_instalments["amount_cad"].sum() if not yr_instalments.empty else 0.0

# Estimated CRA owing (CPP − FTC − instalments already paid)
estimated_cra_owing = max(0.0, cpp["total"] - ftc["total_ftc"] - instalments_paid_cad)

# ─── Top summary row ──────────────────────────────────────────────────────────

m1, m2, m3, m4, m5, m6 = st.columns(6)
with m1:
    st.metric("Paycheques Logged", f"{paycheques_logged} / {TOTAL_PAY_PERIODS}")
with m2:
    st.metric("Gross USD (YTD)", f"${total_gross_usd:,.2f}")
with m3:
    st.metric("Gross CAD (annualized)", f"${annualized_gross_cad:,.0f}")
with m4:
    st.metric("IL Tax Withheld (annualized CAD)", f"${annualized_il_tax_cad:,.0f}")
with m5:
    fed_label = f"${total_fed_tax_usd:,.2f} USD" if total_fed_tax_usd > 0 else "None withheld"
    st.metric("US Federal Tax Withheld (YTD)", fed_label,
              help="Any mistakenly withheld US federal tax — recoverable via a US 1040-NR filing.")
with m6:
    st.metric("Est. Net CRA Owing", f"${estimated_cra_owing:,.0f}",
              help="CPP (CPT20) minus Foreign Tax Credit minus instalments paid")

st.divider()

# ─── Tabs ─────────────────────────────────────────────────────────────────────

tab_log, tab_bulk, tab_tax, tab_instalments, tab_settings = st.tabs([
    "📥 Log Paycheque", "📋 Bulk Entry", "🧮 Tax Summary", "💸 Instalments", "⚙️ Settings"
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Log a single paycheque
# ══════════════════════════════════════════════════════════════════════════════

with tab_log:
    st.subheader("📥 Log a Paycheque")
    st.caption(
        "Enter each semi-monthly pay stub as it arrives. "
        "The USD→CAD rate is the Bank of Canada rate on (or near) your pay date."
    )

    with st.form("us_payslip_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            p_date  = st.date_input("Pay Date", value=date.today())
            p_gross = st.number_input("Gross Pay (USD)", min_value=0.01, step=100.0, format="%.2f")
            p_rate  = st.number_input(
                "USD→CAD Rate",
                min_value=0.5, max_value=3.0, value=1.38, step=0.001, format="%.4f",
                help="Bank of Canada daily rate on your pay date. Check bankofcanada.ca/rates/exchange/daily-exchange-rates/"
            )
        with col2:
            p_il_tax = st.number_input(
                "Illinois Tax Withheld (USD)",
                min_value=0.0, step=10.0, format="%.2f",
                help=f"Illinois flat rate is {IL_TAX_RATE*100:.2f}% of gross."
            )
            p_fed_tax = st.number_input(
                "US Federal Tax Withheld (USD)",
                min_value=0.0, value=0.0, step=10.0, format="%.2f",
                help="Leave at $0 if nothing was withheld — which is normal. "
                     "If your employer accidentally withheld US federal tax, enter it here. "
                     "You can recover it by filing a US 1040-NR."
            )
        with col3:
            st.markdown("**Quick estimate**")
            st.caption("IL Tax estimate (4.95%):")
            st.info(f"**${p_gross * IL_TAX_RATE:,.2f} USD**")
            st.caption("Gross in CAD:")
            st.info(f"**${p_gross * p_rate:,.2f} CAD**")
            if p_fed_tax > 0:
                st.warning(f"⚠️ US federal withheld: **${p_fed_tax:,.2f}** — file 1040-NR to recover.")

        p_notes = st.text_input("Notes (optional)", placeholder="e.g. Period Jan 1–15")

        submitted = st.form_submit_button("➕ Add Paycheque", type="primary", use_container_width=True)
        if submitted:
            add_us_payslip(p_date, p_gross, p_il_tax, p_rate, p_notes, fed_tax_usd=p_fed_tax)
            msg = f"✅ Logged ${p_gross:,.2f} USD (${p_gross * p_rate:,.2f} CAD) on {p_date} at {p_rate:.4f}."
            if p_fed_tax > 0:
                msg += f" Note: ${p_fed_tax:,.2f} US federal tax logged — remember to file 1040-NR."
            st.success(msg)
            st.rerun()

    # Paycheque history
    if not yr_payslips.empty:
        st.divider()
        st.subheader(f"Paycheque History — {selected_year}")
        disp = yr_payslips[["date","gross_usd","il_tax_usd","fed_tax_usd","usd_cad_rate","gross_cad","il_tax_cad","notes"]].copy()
        disp["date"] = disp["date"].dt.strftime("%Y-%m-%d")
        disp.columns = ["Date","Gross USD","IL Tax USD","US Fed Tax USD","Rate","Gross CAD","IL Tax CAD","Notes"]
        for col in ["Gross USD","IL Tax USD","US Fed Tax USD","Gross CAD","IL Tax CAD"]:
            disp[col] = disp[col].apply(lambda x: f"${x:,.2f}")
        disp["Rate"] = disp["Rate"].apply(lambda x: f"{x:.4f}")
        st.dataframe(disp, use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("**Delete a paycheque entry**")
        del_options = {
            f"{row['date'].strftime('%Y-%m-%d')} — ${row['gross_usd']:,.2f} USD ({row['id']})": row["id"]
            for _, row in yr_payslips.iterrows()
        }
        del_sel = st.selectbox("Select entry to delete", ["— pick one —"] + list(del_options.keys()), key="del_payslip_sel")
        if st.button("🗑️ Delete Selected", key="del_payslip_btn"):
            if del_sel != "— pick one —":
                delete_us_payslip(del_options[del_sel])
                st.rerun()
    else:
        st.info("No paycheques logged for this year yet.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Bulk / historical entry
# ══════════════════════════════════════════════════════════════════════════════

with tab_bulk:
    st.subheader("📋 Bulk / Historical Paycheque Entry")
    st.caption(
        "Backfill past pay stubs all at once. Leave Gross USD at $0.00 to skip a row. "
        "IL Tax will default to 4.95% of gross if left at $0.00 and gross is entered."
    )

    bc1, bc2 = st.columns(2)
    with bc1:
        bulk_rows = st.number_input("Number of rows", min_value=1, max_value=50, value=12, step=1)
    with bc2:
        bulk_rate = st.number_input("Default USD→CAD Rate", min_value=0.5, max_value=3.0,
                                    value=1.38, step=0.001, format="%.4f",
                                    help="You can override per-row in the table below.")

    template = pd.DataFrame({
        "date":         [str(date.today())] * int(bulk_rows),
        "gross_usd":    [0.0] * int(bulk_rows),
        "il_tax_usd":   [0.0] * int(bulk_rows),
        "fed_tax_usd":  [0.0] * int(bulk_rows),
        "usd_cad_rate": [float(bulk_rate)] * int(bulk_rows),
        "notes":        [""] * int(bulk_rows),
    })

    edited_bulk = st.data_editor(
        template,
        use_container_width=True,
        hide_index=True,
        column_config={
            "date":         st.column_config.TextColumn("Date (YYYY-MM-DD)"),
            "gross_usd":    st.column_config.NumberColumn("Gross USD", format="%.2f", step=0.01, min_value=0.0),
            "il_tax_usd":   st.column_config.NumberColumn("IL Tax USD", format="%.2f", step=0.01, min_value=0.0),
            "fed_tax_usd":  st.column_config.NumberColumn("US Fed Tax USD", format="%.2f", step=0.01, min_value=0.0,
                                                           help="Leave at $0 if nothing withheld — normal. Enter only if employer mistakenly withheld."),
            "usd_cad_rate": st.column_config.NumberColumn("USD/CAD Rate", format="%.4f", step=0.0001, min_value=0.5),
            "notes":        st.column_config.TextColumn("Notes"),
        },
        num_rows="fixed",
    )

    non_zero_bulk = edited_bulk[edited_bulk["gross_usd"] > 0].copy()
    # Auto-fill IL tax where user left it at 0
    non_zero_bulk["il_tax_usd"] = non_zero_bulk.apply(
        lambda r: r["gross_usd"] * IL_TAX_RATE if r["il_tax_usd"] == 0 else r["il_tax_usd"], axis=1
    )
    fed_withheld_count = (non_zero_bulk["fed_tax_usd"] > 0).sum()
    caption_txt = f"{len(non_zero_bulk)} rows with gross > $0 will be saved."
    if fed_withheld_count > 0:
        caption_txt += f" ⚠️ {fed_withheld_count} row(s) have US federal tax withheld — remember to file 1040-NR."
    st.caption(caption_txt)

    if st.button("💾 Submit All Paycheques", type="primary", use_container_width=True):
        rows_to_save = [
            {"date": r["date"], "gross_usd": r["gross_usd"], "il_tax_usd": r["il_tax_usd"],
             "fed_tax_usd": r["fed_tax_usd"], "usd_cad_rate": r["usd_cad_rate"], "notes": r["notes"]}
            for _, r in non_zero_bulk.iterrows()
        ]
        if rows_to_save:
            with st.spinner(f"Saving {len(rows_to_save)} paycheques…"):
                add_us_payslips_bulk(rows_to_save)
            st.success(f"✅ Saved {len(rows_to_save)} paycheques!")
            st.rerun()
        else:
            st.warning("No rows with gross > $0 to save.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Tax Summary
# ══════════════════════════════════════════════════════════════════════════════

with tab_tax:
    st.subheader("🧮 Year-End Tax Summary")
    st.caption(
        f"Annualized estimates based on {paycheques_logged} paycheques logged "
        f"({periods_remaining} periods remaining in {selected_year})."
    )

    # ── Annualized income ──────────────────────────────────────────────────────
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("##### 🇺🇸 US Income (annualized)")
        st.metric("Gross USD", f"${annualized_gross_usd:,.2f}")
        st.metric("Gross CAD", f"${annualized_gross_cad:,.2f}")
        st.metric("Illinois Tax Withheld (CAD)", f"${annualized_il_tax_cad:,.2f}")
        if annualized_fed_tax_cad > 0:
            st.metric(
                "US Federal Tax Withheld (CAD)", f"${annualized_fed_tax_cad:,.2f}",
                help="Mistakenly withheld — recoverable via US Form 1040-NR. "
                     "This does NOT reduce your Canadian tax directly (use the FTC for that)."
            )
            st.warning(
                f"⚠️ ${total_fed_tax_usd:,.2f} USD in US federal tax was withheld across "
                f"{(yr_payslips['fed_tax_usd'] > 0).sum()} paycheque(s). "
                "File a **US 1040-NR** to recover this amount.",
                icon="⚠️"
            )
        if paycheques_logged > 0:
            st.caption(
                f"Avg USD/CAD rate: **{avg_rate:.4f}** | "
                f"Periods used: {paycheques_logged}/{TOTAL_PAY_PERIODS}"
            )

    with col_b:
        st.markdown("##### 🍁 CPP — CPT20 Election")
        st.info(
            "Because Katherine has no employer withholding CPP, she files **Form CPT20** "
            "to pay both the employee AND employer share (~11.9% CPP1 + 8% CPP2).",
            icon="ℹ️"
        )
        st.metric("CPP1 (employee + employer)",   f"${cpp['cpp1']:,.2f}")
        st.metric("CPP2 (employee + employer)",   f"${cpp['cpp2']:,.2f}")
        st.metric("Total CPP Owing",              f"${cpp['total']:,.2f}")
        st.metric("Deductible Half (employer)",   f"${cpp['deductible_half']:,.2f}",
                  help="The employer-equivalent half is deductible from income on the T1.")

    st.divider()

    # ── Foreign Tax Credit ─────────────────────────────────────────────────────
    st.markdown("##### 🌐 Foreign Tax Credit (Illinois → Canada)")
    st.caption(
        "The Illinois state tax you paid reduces your Canadian tax owing. "
        "Federal FTC is applied first, then any remainder goes to the Ontario credit. "
        "Enter your Canadian employment income in the **⚙️ Settings** tab for a more accurate estimate."
    )

    ftc_c1, ftc_c2, ftc_c3 = st.columns(3)
    with ftc_c1:
        st.metric("Federal FTC",  f"${ftc['fed_ftc']:,.2f}")
    with ftc_c2:
        st.metric("Ontario FTC",  f"${ftc['on_ftc']:,.2f}")
    with ftc_c3:
        st.metric("Total FTC",    f"${ftc['total_ftc']:,.2f}")

    with st.expander("ℹ️ How the FTC is calculated"):
        st.markdown(f"""
**Federal FTC** = lesser of:
- Illinois tax paid (CAD): **${annualized_il_tax_cad:,.2f}**
- Canadian federal tax × (US income ÷ total income): **${ftc['fed_ftc']:,.2f}**

**Ontario FTC** = lesser of:
- Remaining IL tax after federal FTC: **${max(annualized_il_tax_cad - ftc['fed_ftc'], 0):,.2f}**
- Ontario tax × (US income ÷ total income): **${ftc['on_ftc']:,.2f}**

*Total income used: **${total_income_cad:,.0f} CAD** (US ${annualized_gross_cad:,.0f} + CA ${canadian_gross:,.0f})*

*Update Canadian income in ⚙️ Settings for a more accurate FTC calculation.*
""")

    st.divider()

    # ── Net CRA balance ────────────────────────────────────────────────────────
    st.markdown("##### 📊 Estimated Net CRA Balance")

    net_c1, net_c2, net_c3, net_c4 = st.columns(4)
    with net_c1:
        st.metric("CPP (CPT20)",       f"${cpp['total']:,.2f}", delta_color="inverse")
    with net_c2:
        st.metric("Foreign Tax Credit", f"-${ftc['total_ftc']:,.2f}")
    with net_c3:
        st.metric("Instalments Paid",   f"-${instalments_paid_cad:,.2f}")
    with net_c4:
        owing = max(0.0, cpp["total"] - ftc["total_ftc"] - instalments_paid_cad)
        refund = max(0.0, -(cpp["total"] - ftc["total_ftc"] - instalments_paid_cad))
        if owing > 0:
            st.metric("Est. Balance Owing", f"${owing:,.2f}", delta=f"-${owing:,.2f}", delta_color="inverse")
        else:
            st.metric("Est. Refund", f"${refund:,.2f}", delta=f"+${refund:,.2f}")

    # Waterfall chart
    waterfall_fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=["relative", "relative", "relative", "total"],
        x=["CPP Owing", "FTC", "Instalments", "Net Balance"],
        y=[cpp["total"], -ftc["total_ftc"], -instalments_paid_cad,
           cpp["total"] - ftc["total_ftc"] - instalments_paid_cad],
        text=[f"${cpp['total']:,.0f}", f"-${ftc['total_ftc']:,.0f}",
              f"-${instalments_paid_cad:,.0f}",
              f"${max(owing, 0):,.0f}" if owing >= 0 else f"-${refund:,.0f}"],
        textposition="outside",
        connector={"line": {"color": "rgb(63,63,63)"}},
        increasing={"marker": {"color": "#ef4444"}},
        decreasing={"marker": {"color": "#22c55e"}},
        totals={"marker": {"color": "#3b82f6"}},
    ))
    waterfall_fig.update_layout(
        title="CRA Balance Waterfall",
        height=380,
        margin=dict(l=20, r=20, t=50, b=20),
        showlegend=False,
    )
    st.plotly_chart(waterfall_fig, use_container_width=True)

    st.info(
        "💡 **Tip:** These are estimates. Your actual T1 balance will depend on the final "
        "exchange rates, your Canadian T4 income, all deductions, and Form CPT20. "
        "Always confirm with a tax professional or CRA My Account.",
        icon="💡"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Quarterly Instalments
# ══════════════════════════════════════════════════════════════════════════════

with tab_instalments:
    st.subheader("💸 CRA Quarterly Instalments")
    st.caption(
        "CRA instalment payments are due **March 15, June 15, September 15, and December 15** each year. "
        "These pre-pay your estimated tax so you avoid interest charges."
    )

    # Recommended instalment amount (split CPP net of FTC into 4)
    net_cpp_after_ftc = max(0.0, cpp["total"] - ftc["total_ftc"])
    recommended_quarterly = net_cpp_after_ftc / 4.0

    rc1, rc2, rc3 = st.columns(3)
    with rc1:
        st.metric("Net CPP after FTC", f"${net_cpp_after_ftc:,.2f}")
    with rc2:
        st.metric("Recommended per Quarter", f"${recommended_quarterly:,.2f}",
                  help="Divide your estimated net CPP owing evenly across 4 quarters.")
    with rc3:
        st.metric("Total Paid YTD", f"${instalments_paid_cad:,.2f}",
                  delta=f"${instalments_paid_cad - net_cpp_after_ftc:,.2f} vs. target",
                  delta_color="normal")

    st.divider()

    # Due date status
    st.markdown("##### 📅 Instalment Due Dates")
    today = date.today()
    quarter_cols = st.columns(4)
    for i, (q, due) in enumerate(INSTALMENT_QUARTERS.items()):
        paid_q = yr_instalments[yr_instalments["quarter"] == q]["amount_cad"].sum() if not yr_instalments.empty else 0.0
        is_past = due < today
        is_today = due == today

        with quarter_cols[i]:
            status = "✅ Paid" if paid_q > 0 else ("⚠️ Overdue" if is_past else ("🔔 Due Today" if is_today else "📅 Upcoming"))
            st.markdown(f"**{q} — {due.strftime('%b %d')}**")
            st.metric("Paid", f"${paid_q:,.2f}", delta=f"target: ${recommended_quarterly:,.2f}")
            st.caption(status)

    st.divider()

    # Log a new instalment payment
    st.markdown("##### ➕ Record an Instalment Payment")
    with st.form("instalment_form", clear_on_submit=True):
        ic1, ic2, ic3 = st.columns(3)
        with ic1:
            i_date    = st.date_input("Payment Date", value=date.today())
            i_amount  = st.number_input("Amount (CAD)", min_value=0.01, step=100.0, format="%.2f")
        with ic2:
            i_quarter = st.selectbox("Quarter", list(INSTALMENT_QUARTERS.keys()))
            i_notes   = st.text_input("Notes (optional)", placeholder="e.g. Paid via My CRA")
        with ic3:
            st.caption("Quick fill")
            st.info(f"Recommended: **${recommended_quarterly:,.2f}**")

        if st.form_submit_button("💸 Record Payment", type="primary", use_container_width=True):
            add_us_instalment(i_date, i_amount, i_quarter, i_notes)
            st.success(f"✅ Recorded ${i_amount:,.2f} CAD instalment for {i_quarter} on {i_date}.")
            st.rerun()

    # Instalment history
    if not yr_instalments.empty:
        st.divider()
        st.markdown("##### Instalment History")
        for _, row in yr_instalments.sort_values("date", ascending=False).iterrows():
            with st.expander(
                f"**{row['date'].strftime('%b %d, %Y')}** — {row['quarter']} — ${row['amount_cad']:,.2f} CAD",
                expanded=False
            ):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.write(f"**Date:** {row['date'].strftime('%B %d, %Y')}")
                    st.write(f"**Quarter:** {row['quarter']}   |   **Amount:** ${row['amount_cad']:,.2f} CAD")
                    if row.get("notes"):
                        st.write(f"**Notes:** {row['notes']}")
                with c2:
                    if st.button("🗑️ Delete", key=f"del_inst_{row['id']}"):
                        delete_us_instalment(str(row["id"]))
                        st.rerun()
    else:
        st.info("No instalment payments recorded yet for this year.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Settings
# ══════════════════════════════════════════════════════════════════════════════

with tab_settings:
    st.subheader("⚙️ Settings — Canadian Income (for FTC Calculation)")
    st.caption(
        "Enter Katherine's expected Canadian employment income and estimated Canadian taxes. "
        "These are used to calculate the proportion of tax attributable to US income "
        "when estimating the Foreign Tax Credit. Leave at $0 if unknown."
    )

    with st.form("us_settings_form"):
        sc1, sc2 = st.columns(2)
        with sc1:
            inp_ca_gross = st.number_input(
                "Katherine's Canadian Gross Income (CAD)",
                min_value=0.0, value=canadian_gross, step=1000.0, format="%.2f",
                help="T4 employment income from Isaac's / Katherine's Canadian job."
            )
        with sc2:
            inp_ca_fed = st.number_input(
                "Estimated Canadian Federal Tax (CAD)",
                min_value=0.0, value=canadian_fed_tax, step=100.0, format="%.2f",
                help="Leave at 0 to use a bracket-based estimate."
            )
            inp_ca_on = st.number_input(
                "Estimated Ontario Provincial Tax (CAD)",
                min_value=0.0, value=canadian_on_tax, step=100.0, format="%.2f",
                help="Leave at 0 to use a bracket-based estimate."
            )

        if st.form_submit_button("💾 Save Settings", type="primary", use_container_width=True):
            update_settings({
                "katherine_canadian_gross":   str(inp_ca_gross),
                "katherine_canadian_fed_tax": str(inp_ca_fed),
                "katherine_canadian_on_tax":  str(inp_ca_on),
            })
            st.success("✅ Settings saved.")
            st.rerun()

    st.divider()
    with st.expander("ℹ️ How this all fits together — Katherine's cross-border tax obligations"):
        st.markdown(f"""
**Katherine's tax situation at a glance:**

Katherine is employed by a US company. Her paycheques are in USD, and her employer withholds Illinois state income tax (flat {IL_TAX_RATE*100:.2f}%). There is **no US federal income tax**, and **no FICA** (Social Security / Medicare) withheld because the employer doesn't cross the Canada-US Totalization Agreement threshold for her.

**What Katherine owes in Canada:**

1. **CPP contributions (Form CPT20)** — Because no employer withholds CPP, she pays *both* the employee and employer shares when she files. That's ~11.9% on CPP1 earnings and ~8% on CPP2 (2025 rates), up to the annual maximum (~${CPT20_RATE1*(CPP_YMPE-CPP_BASIC_EXEMPT) + CPT20_RATE2*(CPP_YMPE2-CPP_YMPE):,.0f}).

2. **Income tax on her US earnings** — Her USD income converts to CAD and is included on her T1 alongside any Canadian employment income. The combined marginal rate depends on her total income.

3. **Foreign Tax Credit** — The Illinois state tax she already paid reduces her Canadian tax bill. The federal FTC is applied first; any remainder reduces her Ontario tax. The credit is limited to the Canadian tax *attributable* to the US income (pro-rated by income proportion).

4. **Quarterly instalments** — CRA requires quarterly instalment payments if your balance owing exceeds ~$3,000. Instalments are due March 15, June 15, September 15, and December 15. These pre-pay the expected year-end balance so interest doesn't accumulate.

**What Katherine does NOT owe:**
- US federal income tax (below filing threshold / treaty protection)
- FICA (Social Security / Medicare) — covered by the Totalization Agreement
- Illinois covers only state tax — no local taxes apply

**Recommended workflow:**
1. Log each paycheque here as it arrives with the Bank of Canada rate for that day.
2. Make CRA instalment payments quarterly — record them in the Instalments tab.
3. At year-end, file Form CPT20 with your T1 and claim the Form T2209 (Federal FTC) and ON428 (Ontario FTC).
""")
